"""BlacksmithState — the typed state carried through the graph and persisted by the
checkpointer (PRD §4).

LangGraph reads the annotations here to wire reducers: ``errors`` accumulates across
nodes (append), every other field is last-write-wins. The state is intentionally a
plain ``TypedDict`` (not a pydantic model) because that is what LangGraph's reducer /
serialization machinery expects; the rich types it references (PRD, WorkUnit) come
from the contract module.
"""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from blacksmith.contract import PRD, WorkUnit


class Status(StrEnum):
    """Run status (PRD §4). A str-enum so it serializes cleanly through the checkpointer."""

    PENDING = "pending"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    AWAITING_PR_APPROVAL = "awaiting_pr_approval"
    HALTED = "halted"
    # Terminal status for a human-GATED unit that implemented successfully: its work is
    # parked behind a draft PR for manual QA, distinct from a failed/rejected HALTED run
    # (work discarded) and from a fully-approved DONE run.
    AWAITING_QA = "awaiting_qa"
    DONE = "done"


class TestResults(TypedDict):
    passed: bool
    output: str
    command: str


class Approvals(TypedDict, total=False):
    plan: bool
    pr: bool


class ErrorRecord(TypedDict):
    node: str
    message: str


class ReviewFinding(TypedDict, total=False):
    """One finding from the review node (WU-REVIEW-NODE), parsed from the reviewer
    model's fenced JSON verdict. ``severity`` is ``"blocking"`` (a real correctness/
    regression bug — flips ``review_clean`` to False) or ``"advisory"`` (surfaced but
    non-gating; style/taste concerns are meant to stay out of both)."""

    severity: str
    file: str
    detail: str


class LevelBuild(TypedDict, total=False):
    """One unit's fan-out build outcome, produced by a ``build_unit`` worker and appended
    to ``level_builds`` (a reducer key — the only thing a parallel worker writes, so
    concurrent workers never race on a last-write-wins field). The join reads these to
    cherry-pick each unit's commit onto the combined branch in declaration order.

    ``ok`` is False when the unit's implement or test gate failed (the join then halts the
    whole level without cherry-picking anything). ``level`` tags which level the build
    belongs to, so a later level's join filters out an earlier level's records."""

    unit_id: str
    title: str
    level: int
    clone_path: str
    branch: str
    files_touched: list[str]
    diff_summary: str
    test_command: str
    ok: bool
    error: str


class UnitResult(TypedDict, total=False):
    """One unit's own outcome, retained so a multi-unit PR can summarize each unit's
    changes (not just the last unit's). Appended to ``unit_results`` when a unit's gate
    passes; ``implementation``/``test_results`` are last-write-wins and hold only the
    most recent unit, which is why the per-unit record is captured here instead."""

    unit_id: str
    title: str
    files_touched: list[str]
    diff_summary: str
    test_command: str


class BlacksmithState(TypedDict, total=False):
    """State persisted per-run by the checkpointer. All keys optional — the graph
    fills them in as it advances."""

    prd_path: str
    prd: PRD
    work_units: list[WorkUnit]
    # Dependency levels (frontiers) from blacksmith.planner.execution_levels: the level
    # engine walks these in order and, within each level, builds the units sequentially in
    # declaration order. Flattening them in order yields ``work_units`` (topo order).
    execution_levels: list[list[WorkUnit]]
    selected_unit: WorkUnit
    # Level position the sequential implement->gate loop is currently on (multi-unit run):
    # ``level_cursor`` indexes into ``execution_levels`` and ``unit_in_level`` into that
    # level's units. Replaces the prior flat ``unit_cursor`` (level grouping lives in the
    # planner, never in the contract).
    level_cursor: int
    unit_in_level: int
    # True when the run's isolation manager is a CloneManager, which is what enables the
    # parallel fan-out (cloning a per-unit build clone from the combined-branch tip). A
    # legacy WorktreeManager run leaves this unset and stays on the sequential path, so a
    # multi-unit level there is still built one unit at a time on the shared worktree.
    fanout: bool
    # Per-unit fan-out build outcomes for the current multi-unit level, appended by the
    # parallel ``build_unit`` workers and drained by ``join_level`` (which cherry-picks the
    # passing units' commits onto the combined branch). A reducer key so concurrent workers
    # never collide; each record is tagged with its ``level`` so a later join ignores
    # earlier levels' records.
    level_builds: Annotated[list[LevelBuild], operator.add]
    plan: dict[str, Any]
    # One implementation plan per auto-gated unit (WU-PLAN-ALL-UNITS), surfaced together behind
    # the single approve_plan gate so a multi-unit PRD's later units are no longer built with no
    # plan and no approval. Human-gated units are absent (they get manual QA via a draft PR).
    plans: list[dict[str, Any]]
    worktree_path: str
    # The single shared branch every unit's commits land on, so one combined PR can be
    # opened against it. Set once when the run's lone worktree is created.
    branch: str
    # The target repo's default branch (``[target].default_branch``), seeded once by
    # ``prepare_worktree`` when the graph is wired with it (production via build_graph_for).
    # The combined PR is opened against it (``gh pr create --base``); absent on a graph
    # compiled without it, in which case the PR node passes no ``--base`` and gh falls back
    # to the repo's own default, exactly as before.
    default_branch: str
    # The shared worktree's HEAD, captured ONCE by ``prepare_worktree`` right after it
    # creates the run's clone (WU-PR-DIFF-CAPTURE) -- i.e. before any unit's commits land.
    # The run base ref for the ``approve_pr`` gate's combined diff (every unit's commits vs
    # this ref, not just the last unit's). Seeded only when the graph is wired with a
    # HitlConfig (production via build_graph_for); absent on a graph compiled without one,
    # in which case the gate's payload carries no diff_text exactly as before.
    pr_base_ref: str
    implementation: dict[str, Any]
    # Escalation (WU-ESCALATE-ON-FAIL): a gate failure discards the failed attempt and
    # re-implements the SAME unit once with the stronger model. ``pre_implement_ref`` is the
    # shared worktree's HEAD captured just before THIS unit's implement attempt, so resetting
    # to it throws away only the failed attempt's commit while every prior unit's committed
    # work is preserved. It is recorded only when the executor can actually escalate (exposes
    # ``run_implement_escalate``); a plain test double leaves it unset, which keeps the prior
    # "a gate failure halts" behaviour unchanged.
    pre_implement_ref: str
    # True once this unit has been re-implemented with the escalation model, so escalation
    # happens at most once per unit (a second gate failure routes to human_halt). Reset by
    # the level engine when it advances to the next unit.
    escalated: bool
    # Self-heal loop (WU-GATE-SELF-HEAL). Seeded once by prepare_worktree from
    # ``config.limits`` ONLY when the graph is wired with limits (production); absent on a
    # graph compiled without them, which keeps the loop OFF and every existing test's
    # behaviour unchanged. ``max_fix_attempts`` caps same-model error-feedback retries;
    # ``max_run_cost_usd`` (or None) is the optional hard spend ceiling. Routing reads these
    # from state because LangGraph edge functions are pure (state) -> str and get no config.
    limits: dict[str, Any]
    # Same-model gate-failure retries already spent on the CURRENT unit (WU-GATE-SELF-HEAL).
    # Reset to 0 by the level engine when it advances to the next unit, so the budget is
    # per-unit, mirroring ``escalated``.
    fix_attempts: int
    # The failing gate's output, fed back into the next implement attempt's prompt so the
    # retry (and the escalation) can actually FIX the error instead of re-running blind. Set
    # by the fix-retry/escalation prep, cleared when the level engine advances to a new unit.
    last_gate_output: str
    # Recoverable implement-continuation loop. When an implement attempt hits its turn budget
    # (``error_kind == "max_turns"``) it HALTs, but unlike a gate failure the partial work is
    # kept and the attempt is continued rather than discarded.
    #   ``implement_error_kind`` — why the last implement HALTED ("max_turns" | "other"), read
    #     by routing to decide whether a continuation is possible; cleared by the continuation
    #     prep so a fresh cap is classified anew.
    #   ``implement_continuations`` — continuations already spent on the CURRENT unit, bounded
    #     by ``limits.max_implement_continuations``; reset to 0 by the level engine per unit
    #     (mirrors ``fix_attempts``).
    #   ``resume_partial_implement`` — a TRANSIENT flag set only on the continue_implement ->
    #     implement edge: tells implement to KEEP the partial worktree and finish it (rather
    #     than reset + restart). Every other edge into implement clears it, so it never leaks.
    implement_error_kind: str
    implement_continuations: int
    resume_partial_implement: bool
    test_results: TestResults
    pr_url: str | None
    approvals: Approvals
    status: Status
    errors: Annotated[list[ErrorRecord], operator.add]
    # Per-unit results, accumulated (one record appended when each unit's gate passes)
    # so a multi-unit PR body can attribute each unit's files/summary to that unit rather
    # than lumping everything under the last unit's last-write-wins ``implementation``.
    unit_results: Annotated[list[UnitResult], operator.add]
    # Append-only per-call cost/usage ledger (WU-COST-EVENTS). One event per model call —
    # the plan node and EVERY implement attempt (including the escalation retry) append
    # exactly one record. A reducer key (like ``errors``/``unit_results``) so the events
    # accumulate across nodes rather than being clobbered last-write-wins. The run-end
    # report sums THIS ledger, so a multi-unit run reports every unit's (and every
    # escalation attempt's) spend instead of only plan + the final unit's
    # last-write-wins ``implementation`` slice.
    cost_events: Annotated[list[dict], operator.add]
    # Additive post-gate review loop (WU-REVIEW-NODE). Set by the ``review`` node, which
    # runs a stronger model over the CURRENT unit's diff after the gate has already
    # passed. ``review_clean`` is last-write-wins (only the current unit's latest
    # verdict matters for routing a future revision loop); ``review_findings`` is a
    # reducer so every review call's findings (across units, and across any future
    # revision retries) accumulate rather than being clobbered, mirroring
    # ``cost_events``/``errors``.
    review_clean: bool
    review_findings: Annotated[list[ReviewFinding], operator.add]
    # Review loop wiring (WU-REVIEW-LOOP). ``review_enabled`` is seeded once by
    # prepare_worktree from ``config.review.enabled`` ONLY when the graph is wired with a
    # ReviewConfig (production); absent on a graph compiled without one, which keeps the
    # review step OFF and every existing test's behaviour unchanged (mirrors ``limits``/
    # the self-heal loop). ``review_revisions`` counts review-driven revision attempts
    # already spent on the CURRENT unit, bounded by ``limits.max_review_revisions`` and
    # reset to 0 by the level engine when it advances to the next unit, mirroring
    # ``fix_attempts`` -- but this is a SEPARATE, independent bound from the self-heal
    # loop and never touches its counters.
    review_enabled: bool
    # Panel size for the post-gate review (WU-REVIEW-PANEL-NODE), seeded once by
    # ``prepare_worktree`` from ``config.review.panel_size`` alongside ``review_enabled``
    # (same ReviewConfig, same seeding condition). Defaults to 1 — a graph compiled
    # without a ReviewConfig leaves this unset, and the ``review`` node treats a missing
    # value as 1, which is BYTE-FOR-BYTE today's single-reviewer behaviour (one
    # ``run_review`` call, the current neutral prompt). Values > 1 fan the review node
    # out into that many ``run_review`` calls, each with a distinct emphasis from a
    # built-in rotation, whose findings are aggregated (WU-REVIEW-PANEL-AGGREGATE) into
    # the same ``review_clean``/``review_findings`` keys the revise loop already consumes.
    review_panel_size: int
    review_revisions: int
    # Report-only RUN-WIDE count of review-driven revisions (WU-REVIEW-RENDER), for the PR
    # body / gate view's "resolved via revision" line. A reducer (operator.add), UNLIKE the
    # per-unit ``review_revisions`` above: the sequential ``prepare_review_revision`` adds 1
    # per revision, and each fan-out ``build_unit`` worker adds its own revision count -- so
    # concurrent workers never race on it (they can't write the last-write-wins
    # ``review_revisions``) and the total spans every unit of the run. Never read by routing
    # or any loop bound; purely for display. Renderers fall back to ``review_revisions`` when
    # it is absent (old checkpoints / states with no fan-out revision).
    review_revisions_total: Annotated[int, operator.add]
    # This unit's most recent review call's raw findings (WU-REVIEW-LOOP), last-write-wins
    # -- unlike ``review_findings``, which accumulates every call across the whole run --
    # so the revision-feedback and unresolved-findings-retention logic can read exactly the
    # CURRENT verdict without wading through that cross-unit/cross-revision history.
    review_current_findings: list[ReviewFinding]
    # Blocking findings the review loop gave up on -- revisions exhausted or over budget
    # (WU-REVIEW-LOOP) -- for a unit that still proceeds to next_unit/approve_pr rather
    # than halting. A reducer (like ``unit_results``) so a multi-unit run's carried-forward
    # findings from more than one unit all surface rather than being clobbered.
    unresolved_review_findings: Annotated[list[ReviewFinding], operator.add]
