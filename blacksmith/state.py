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
    worktree_path: str
    # The single shared branch every unit's commits land on, so one combined PR can be
    # opened against it. Set once when the run's lone worktree is created.
    branch: str
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
