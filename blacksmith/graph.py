"""The v0 blacksmith graph: skeleton + checkpointer (PRD §4).

Wires the full node/edge topology and compiles it with a SQLite checkpointer. The
two human approval gates (``approve_plan`` / ``approve_pr``) are real LangGraph
interrupt nodes (``blacksmith.nodes.hitl``, WU-07): each halts via ``interrupt()``
with state preserved by the checkpointer and resumes on an injected approval.

The nodes delegate to their units (executor WU-04, worktree WU-05, gate WU-06,
PR WU-08, plan WU-09, implement WU-10). Their dependencies — executor, worktree
manager, gate, PR runner — are injected at build time; an unset one leaves that node
a status-only pass-through, which keeps the deterministic graph tests dependency-free.

Conditional edges are real:
- after ``approve_plan`` / ``approve_pr``: a rejection routes to ``human_halt`` (the
  gate never auto-proceeds on a "no");
- after ``implement``: human-gated units (integration/ui) bypass the auto gate (§4);
- after ``test_gate``: a pass routes to PR approval, a fail to ``human_halt``.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Send

from blacksmith.config import CriticConfig, IndexConfig, LimitsConfig, ReviewConfig, SBFLConfig
from blacksmith.contract import PRD, ContractError, PRDContract, WorkUnit, parse_prd
from blacksmith.executor import Executor
from blacksmith.gate import FixResult, GateError, GateResult
from blacksmith.memory import current_store, record_lesson
from blacksmith.nodes.hitl import approve_plan, approve_pr
from blacksmith.nodes.implement import conventional_commit_message, implement
from blacksmith.nodes.plan import plan
from blacksmith.nodes.pr import Runner, open_draft_pr, open_pr
from blacksmith.nodes.review import review as _run_review
from blacksmith.planner import execution_levels
from blacksmith.sandbox import SandboxError, SandboxManager
from blacksmith.sbfl import collect_suspicious_locations, format_suspicious_locations
from blacksmith.state import BlacksmithState, Status
from blacksmith.worktree import (
    Clone,
    CloneManager,
    Worktree,
    WorktreeError,
    WorktreeManager,
    branch_for,
)

# A gate callable: (worktree_path, layer) -> GateResult. Injected so tests can
# control pass/fail; production passes blacksmith.gate.run_gate.
GateFn = Callable[[str, str | None], GateResult]

# A fix callable: (worktree_path, layer) -> FixResult. The deterministic, model-free
# auto-fix step run between implement (commit) and the gate; production passes
# blacksmith.gate.run_fix. Injected like the gate so tests can control it; an unset one
# leaves auto_fix a pass-through (the gate then runs on the agent's commit unchanged).
FixFn = Callable[[str, str | None], FixResult]

# The run's isolation manager. In production this is a CloneManager (each run gets a
# throwaway local clone with its own .git, which kills the self-targeting hazard — the
# agent can never reach the real checkout), but a WorktreeManager (the prior linked-
# worktree model) is still accepted and used by some tests. Both expose the surface the
# graph needs — ``create(unit_id) -> obj(path, branch, repo_path)`` plus ``repo_path`` —
# so prepare_worktree is identical for either; only cleanup_worktree differs.
IsolationManager = WorktreeManager | CloneManager


@dataclass(frozen=True)
class UnitDeps:
    """The one dependency bundle every unit-build path consumes.

    A single frozen bundle handed to BOTH the sequential ``implement`` node AND the fan-out
    ``build_unit`` worker, built once in ``build_graph``. It exists to kill a recurring class
    of wired-but-dark bug (review findings #1/#2): the parallel path used to be a hand-copied
    second implement path, so a feature threaded into the sequential node (index/sandbox) was
    silently omitted from fan-out. With one bundle there is no second place to forget — a new
    unit-build dep added here reaches both paths, and ``build_unit``'s inner ``implement`` call
    uses ``implement_kwargs`` so it can never diverge from what the sequential node passes.

    Carries only the deps the unit-build steps (implement + gate + fix) consume. The run-level
    ``worktree_manager`` is NOT here (it is also used by prepare_worktree/cleanup and the
    fan-out clone creation), and review enablement flows through state (seeded by
    prepare_worktree, threaded into the fan-out Send payload), mirroring the sequential review
    node — so "is review on" keeps a single source of truth."""

    executor: Executor | None = None
    gate: GateFn | None = None
    fix: FixFn | None = None
    index_config: IndexConfig | None = None
    sandbox: SandboxManager | None = None
    sandbox_exec_timeout_s: int | None = None

    def implement_kwargs(self) -> dict:
        """The kwargs for an ``implement(...)`` call — the SAME set for the sequential node
        and the fan-out worker's inner call, so the two can never drift. ``executor`` and
        ``index_config`` always ride along (both ``None`` collapses ``_node_with`` back to a
        bare pass-through, so a dependency-free graph is byte-for-byte unchanged); the sandbox
        and its per-command timeout ride along only when a sandbox is wired at all."""
        kwargs: dict = {"executor": self.executor, "index_config": self.index_config}
        if self.sandbox is not None:
            kwargs["sandbox"] = self.sandbox
            kwargs["sandbox_exec_timeout_s"] = self.sandbox_exec_timeout_s
        return kwargs


# --- placeholder nodes (real bodies arrive in WU-04..WU-10) ------------------
# Each returns a partial state update; advancing `status` keeps the run inspectable.


def ingest_prd(state: BlacksmithState) -> dict:
    """Load + validate the PRD from ``prd_path`` (AC-1). Degrades to a no-op when a
    prd is already in state or no path is given, so deterministic tests need neither."""
    if state.get("prd") is not None:
        return {"status": Status.PENDING}
    prd_path = state.get("prd_path")
    if not prd_path:
        return {"status": Status.PENDING}
    try:
        prd = parse_prd(prd_path)
    except ContractError as exc:
        return {"status": Status.HALTED, "errors": [{"node": "ingest_prd", "message": str(exc)}]}
    return {"prd": prd, "status": Status.PENDING}


def prepare_worktree(
    state: BlacksmithState,
    *,
    worktree_manager: IsolationManager | None = None,
    limits: LimitsConfig | None = None,
    review: ReviewConfig | None = None,
    sandbox: SandboxManager | None = None,
    default_branch: str | None = None,
) -> dict:
    """Create the run's ONE shared clone/branch and seed the level execution plan.

    In production the isolation manager is a CloneManager, so this creates a throwaway
    local clone (own .git, origin pointed at the real remote) — the agent works there and
    can never touch the source checkout. Every unit is built sequentially on this single
    clone (so each unit's committed changes are visible to the next unit's implement step)
    and the run opens one combined PR against its branch. The plan comes from
    ``planner.execution_levels``; the implement->gate loop walks it level by level (and,
    within a level, in declaration order) via ``level_cursor``/``unit_in_level`` and
    re-enters ``implement`` (never this node), so the clone is created exactly once. A
    single-unit PRD reduces to the prior behaviour: one level, one unit, one clone, one PR.
    The isolation path is stored under ``worktree_path`` (the name predates the clone
    model).

    When ``sandbox`` is injected AND ``sandbox.config.enabled`` (WU-SANDBOX-LIFECYCLE),
    this also starts the run's ONE sandbox container over the freshly-created clone —
    reused across every unit built on this clone, never recreated per-unit. Best-effort:
    a start failure is swallowed here (it disables the sandbox tool for the run, an
    additive self-verify channel) and NEVER halts the run — the test gate remains the
    sole authoritative pass/fail backstop regardless. Left unset (``sandbox=None``, the
    default, every graph compiled without one) or disabled, this is a byte-for-byte
    no-op."""
    if worktree_manager is None:
        return {}  # skeleton pass-through
    prd = state.get("prd")
    levels = execution_levels(prd.contract) if prd is not None else []
    units = [unit for level in levels for unit in level]
    unit = units[0] if units else state.get("selected_unit")
    if unit is None:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "prepare_worktree", "message": "no selected_unit"}],
        }
    worktree = worktree_manager.create(unit.id)
    update: dict = {
        "worktree_path": str(worktree.path),
        "branch": worktree.branch,
        "selected_unit": unit,
        "level_cursor": 0,
        "unit_in_level": 0,
        # Only a CloneManager can clone a per-unit build clone from the combined-branch
        # tip, so fan-out is gated on it. A WorktreeManager run leaves this False and a
        # multi-unit level is still built sequentially on the one shared worktree.
        "fanout": isinstance(worktree_manager, CloneManager),
    }
    if levels:
        update["execution_levels"] = levels
        update["work_units"] = units
    # Seed the self-heal limits into state ONCE (WU-GATE-SELF-HEAL). Only when limits are
    # wired in (production via build_graph_for); a graph compiled without them seeds nothing,
    # so routing's max_fix_attempts reads 0 and the loop stays OFF — every graph-level test
    # that does not opt in keeps its exact prior behaviour. Routing needs these in state
    # because LangGraph edge functions are pure (state) -> str and receive no config.
    if limits is not None:
        update["limits"] = {
            "max_fix_attempts": limits.max_fix_attempts,
            "max_run_cost_usd": limits.max_run_cost_usd,
            "max_review_revisions": limits.max_review_revisions,
            "max_implement_turns": limits.max_implement_turns,
            "max_implement_continuations": limits.max_implement_continuations,
        }
        update["fix_attempts"] = 0
        update["implement_continuations"] = 0
    # Seed the review loop's on/off switch (and its panel size, WU-REVIEW-PANEL-NODE) into
    # state ONCE (WU-REVIEW-LOOP), same pattern as ``limits`` above: only when the graph is
    # wired with a ReviewConfig (production). A graph compiled without one leaves
    # ``review_enabled``/``review_panel_size`` unset, so route_after_test_gate's PASS
    # branch never routes to ``review`` and every existing test's behaviour is unchanged.
    if review is not None:
        update["review_enabled"] = review.enabled
        update["review_panel_size"] = review.panel_size
        update["review_revisions"] = 0
    # Seed the target repo's default branch into state ONCE (same config->state pattern as
    # ``limits``/``review`` above), so the PR node can open the combined PR against it
    # (``gh pr create --base``). Only when the graph is wired with it (production via
    # build_graph_for); a graph compiled without it leaves ``default_branch`` unset, so the
    # PR node passes no ``--base`` and gh falls back to the repo default exactly as before.
    if default_branch is not None:
        update["default_branch"] = default_branch
    # Start the run's ONE sandbox container over this clone (WU-SANDBOX-LIFECYCLE), reused
    # across every unit built on it. Additive and opt-in: a graph compiled without a sandbox
    # (``sandbox=None``, every existing test) or one wired with ``enabled=False`` is a
    # byte-for-byte no-op. A start failure never halts the run -- it just leaves the sandbox
    # tool unavailable for this run; the test gate is unaffected either way.
    if sandbox is not None and sandbox.config.enabled:
        try:
            sandbox.start(worktree.path)
        except SandboxError:
            pass
    return update


def auto_fix(state: BlacksmithState, *, fix: FixFn | None = None) -> dict:
    """Deterministic, model-free formatting/auto-fix run between implement and the gate.

    Runs the target's ``fix_cmd`` (if configured) in the shared worktree and folds the
    result into the unit's commit BEFORE ``test_gate`` verifies it — so a mechanical
    ``cargo fmt --check`` / ``prettier --check`` failure is fixed for free instead of
    triggering a model retry and escalation. It mutates the worktree but reports no state
    update: the (possibly amended) commit is the only effect, and the gate that follows is
    the verdict. Best-effort — a missing ``fix_cmd`` or a failing fixer is a no-op that flows
    straight on. Without a ``fix`` injected (skeleton / deterministic tests) it is a pure
    pass-through, so the gate runs on exactly the agent's commit as before."""
    if fix is None:
        return {}
    worktree_path = state.get("worktree_path")
    unit = state.get("selected_unit")
    if not worktree_path or unit is None:
        return {}
    layer = unit.layers[0] if unit.layers else None
    try:
        fix(worktree_path, layer)
    except GateError:
        # A missing/invalid toolchain is surfaced by the gate, not here — don't double-halt.
        pass
    return {}


def test_gate(state: BlacksmithState, *, gate: GateFn | None = None) -> dict:
    if gate is None:
        return {"status": Status.TESTING}  # skeleton pass-through
    worktree_path = state.get("worktree_path")
    unit = state.get("selected_unit")
    if not worktree_path or unit is None:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "test_gate", "message": "no worktree_path/selected_unit"}],
        }
    layer = unit.layers[0] if unit.layers else None
    try:
        result = gate(worktree_path, layer)
    except GateError as exc:
        return {"status": Status.HALTED, "errors": [{"node": "test_gate", "message": str(exc)}]}
    impl = state.get("implementation") or {}
    update: dict = {"test_results": result.as_test_results(), "status": Status.TESTING}
    if not result.passed:
        # Name the failed unit so the halt message identifies which unit broke the run.
        update["errors"] = [
            {"node": "test_gate", "message": f"gate failed for unit {unit.id}"}
        ]
        # If a same-model retry or escalation WOULD have run but the cost cap blocked it,
        # say so explicitly, so a budget halt never looks like a plain second failure
        # (WU-GATE-SELF-HEAL). Checked before the lesson guard, which uses the same routing.
        if _blocked_by_budget(state):
            cap = (state.get("limits") or {}).get("max_run_cost_usd")
            update["errors"].append(
                {
                    "node": "test_gate",
                    "message": f"cost cap ${cap:.2f} reached (spent ${_run_cost(state):.2f}); "
                    "halting without further retries",
                }
            )
        # Record a lesson only on the path that actually halts the run: a failure that still
        # retries or escalates is not yet a lesson. Memory is optional and additive — it never
        # changes routing (``_will_recover`` is the SAME condition the routing uses).
        if not _will_recover(state):
            _record_gate_lesson(state, unit, result, impl)
    else:
        # Retain this unit's own result so the combined PR body can summarize each unit's
        # changes. ``implementation`` is last-write-wins (only the latest unit), so capture
        # the per-unit record here and append it via the unit_results reducer.
        update["unit_results"] = [
            {
                "unit_id": unit.id,
                "title": unit.title,
                "files_touched": list(impl.get("files_touched") or []),
                "diff_summary": impl.get("diff_summary", ""),
                "test_command": result.command,
            }
        ]
    return update


def _record_gate_lesson(
    state: BlacksmithState, unit: WorkUnit, result: GateResult, impl: dict
) -> None:
    """Write a concise gate-failure lesson to the long-term Store, scoped to this repo.

    No-op when no Store is configured or the PRD/contract is unavailable, so a store-less
    run behaves exactly as today. Failures here never affect the run's outcome."""
    store = current_store()
    prd = state.get("prd")
    if store is None or prd is None:
        return
    reason = (result.output or "").strip() or f"gate failed for unit {unit.id}"
    lesson = {
        "unit_id": unit.id,
        "title": unit.title,
        "reason": reason,
        "files_touched": list(impl.get("files_touched") or []),
    }
    try:
        record_lesson(store, prd.contract, lesson)
    except Exception:
        pass


def _will_escalate(state: BlacksmithState) -> bool:
    """Whether a gate failure on THIS attempt will escalate-and-retry rather than halt.

    True for a first-attempt failure that can still escalate: the executor recorded a
    ``pre_implement_ref`` (so it is able to re-implement with the stronger model) and the
    unit has not escalated yet. The single source of truth for both the escalation ROUTING
    (``route_after_test_gate``) and the lesson-recording guard (``test_gate``) — a pure
    refactor of the previously duplicated inline check, with identical behavior.
    """
    return bool(state.get("pre_implement_ref")) and not state.get("escalated")


def _events_cost(events: list[dict] | None) -> float:
    """Sum the USD spend across a list of append-only cost-ledger events."""
    return sum(float(event.get("cost_usd") or 0) for event in events or [])


def _run_cost(state: BlacksmithState) -> float:
    """Total USD spent so far this run — the sum of the append-only cost ledger."""
    return _events_cost(state.get("cost_events"))


def _within_budget(state: BlacksmithState) -> bool:
    """True if the run may still spend on recovery (WU-GATE-SELF-HEAL): no cap configured,
    or total spend so far is still under the configured ``max_run_cost_usd``."""
    cap = (state.get("limits") or {}).get("max_run_cost_usd")
    return cap is None or _run_cost(state) < cap


def _retry_eligible(state: BlacksmithState) -> bool:
    """A same-model fix retry is structurally available for this unit, IGNORING the cost cap:
    the loop is enabled with retries left, the unit has not escalated, and there is a
    ``pre_implement_ref`` to reset to (the capability gate escalation also uses)."""
    if state.get("escalated") or not state.get("pre_implement_ref"):
        return False
    max_fix = int((state.get("limits") or {}).get("max_fix_attempts", 0) or 0)
    return state.get("fix_attempts", 0) < max_fix


def _can_fix_retry(state: BlacksmithState) -> bool:
    """Whether a gate failure routes to a same-model fix retry before any escalation
    (WU-GATE-SELF-HEAL): structurally eligible AND still within the cost cap."""
    return _retry_eligible(state) and _within_budget(state)


def _can_continue_implement(state: BlacksmithState) -> bool:
    """Whether an implement HALT routes to a continuation rather than terminally halting.

    Only a turn-cap halt (``implement_error_kind == "max_turns"``) is recoverable this way —
    a genuine error still halts. Bounded by ``limits.max_implement_continuations`` and the
    shared cost cap. This is a SEPARATE bound from the gate self-heal counters (a turn cap is
    a budget-shaped failure on the implement step, not a gate failure), so it never touches
    ``fix_attempts``/``escalated``."""
    if state.get("implement_error_kind") != "max_turns":
        return False
    max_cont = int((state.get("limits") or {}).get("max_implement_continuations", 0) or 0)
    return state.get("implement_continuations", 0) < max_cont and _within_budget(state)


def _will_recover(state: BlacksmithState) -> bool:
    """A gate failure recovers (fix-retry or escalation) rather than terminally halting.
    The single source of truth for both the lesson-recording guard and what halting means."""
    return _can_fix_retry(state) or (_will_escalate(state) and _within_budget(state))


def _blocked_by_budget(state: BlacksmithState) -> bool:
    """A gate failure that WOULD have retried or escalated but for the cost cap — so the halt
    can be labelled as a budget halt rather than a plain repeated failure."""
    if _within_budget(state):
        return False
    return _retry_eligible(state) or _will_escalate(state)


def _next_position(
    levels: list[list[WorkUnit]], level: int, index: int
) -> tuple[int, int] | None:
    """The level engine's step: from ``(level, index)`` return the next unit's position,
    or ``None`` when the plan is exhausted.

    It walks within the current level (declaration order) until that level is drained, then
    advances to the next level. For a plan whose levels are all size 1 (every chain built
    today) this degenerates to "advance to the next level", so the walk is identical to the
    prior flat cursor — the behaviour-preserving property the swap relies on."""
    if level < len(levels) and index + 1 < len(levels[level]):
        return level, index + 1
    if level + 1 < len(levels):
        return level + 1, 0
    return None


def next_unit(state: BlacksmithState) -> dict:
    """Advance the level engine to the next unit on the SAME shared worktree/branch.

    Reached only after the current unit's gate passed, so the previous units' commits are
    already in the shared worktree when the next unit's implement step runs. Within a level
    the units are built sequentially in declaration order; once a level is drained the walk
    moves to the next level."""
    levels = state.get("execution_levels") or []
    nxt = _next_position(levels, state.get("level_cursor", 0), state.get("unit_in_level", 0))
    if nxt is None:  # defensive: routing only sends us here when a unit remains
        return {}
    level, index = nxt
    return {
        "level_cursor": level,
        "unit_in_level": index,
        "selected_unit": levels[level][index],
        # Escalation AND the self-heal loop are per-unit: clear the previous unit's recovery
        # state so the new unit gets its own (at most one) escalation and its own fresh
        # fix-retry budget, and never inherits the previous unit's gate-output feedback.
        "escalated": False,
        "fix_attempts": 0,
        "last_gate_output": "",
        # The post-gate review loop is likewise per-unit (WU-REVIEW-LOOP): the new unit has
        # not been reviewed yet and gets its own fresh revision budget.
        "review_clean": False,
        "review_revisions": 0,
        # The implement-continuation loop is per-unit too: the new unit gets its own fresh
        # continuation budget and starts NOT resuming a partial attempt.
        "implement_continuations": 0,
        "resume_partial_implement": False,
        "status": Status.IMPLEMENTING,
    }


def prepare_escalation(state: BlacksmithState) -> dict:
    """A gate failure on the first attempt: discard it and re-implement once with the
    stronger model (WU-ESCALATE-ON-FAIL).

    Resets the shared worktree to ``pre_implement_ref`` — the HEAD captured just before this
    unit's implement attempt — so the failed attempt's commit is thrown away while every prior
    unit's committed work is preserved. Marks the unit ``escalated`` so the next implement uses
    ``config.models.implement_escalate`` and escalation can happen at most once per unit, then
    routes back to ``implement``. The re-gated result then proceeds (pass) or halts (a second
    failure)."""
    worktree_path = state.get("worktree_path")
    ref = state.get("pre_implement_ref")
    if worktree_path and ref:
        _git_run(worktree_path, "reset", "--hard", ref)
        _git_run(worktree_path, "clean", "-fd")
    # This resets the worktree, so it is NOT a continuation of a partial attempt — clear the
    # transient resume flag so the escalated re-implement starts clean.
    return {"escalated": True, "resume_partial_implement": False, "status": Status.IMPLEMENTING}


def prepare_fix_retry(state: BlacksmithState, *, sbfl: SBFLConfig | None = None) -> dict:
    """A gate failure with same-model retries left: discard the failed attempt and
    re-implement the SAME unit on the cheap first-attempt model with the gate output fed
    back (WU-GATE-SELF-HEAL).

    Resets the shared worktree to ``pre_implement_ref`` exactly like ``prepare_escalation``
    (so only this attempt's commit is thrown away while prior units' committed work is
    preserved), but leaves ``escalated`` unset so the cheap model is used again and the
    single escalation stays available once the retries are spent. Records the gate output as
    ``last_gate_output`` (the next implement prompt feeds it back) and bumps the per-unit
    ``fix_attempts`` counter so the loop is bounded.

    ``sbfl`` is the additive, opt-in fault-localization channel (WU-SBFL-WIRE): when
    provided AND ``sbfl.enabled``, the failing worktree is spectrum-analysed BEFORE it is
    reset and the ranked suspicious ``file:line`` locations are APPENDED to the same
    ``last_gate_output`` the raw gate text already travels on, so the next implement attempt
    sees them alongside the failure. Left unset (the default, every existing test) or
    disabled, no collection runs and ``last_gate_output`` is byte-for-byte the gate output.
    Best-effort: empty locations append nothing."""
    worktree_path = state.get("worktree_path")
    ref = state.get("pre_implement_ref")
    gate_output = (state.get("test_results") or {}).get("output", "")
    # SBFL runs against the FAILING worktree, before the reset below throws the attempt away.
    if sbfl is not None and sbfl.enabled and worktree_path:
        locations = collect_suspicious_locations(
            worktree_path,
            coverage_cmd=sbfl.coverage_cmd,
            coverage_json=sbfl.coverage_json,
            junit_xml=sbfl.junit_xml,
            limit=sbfl.max_locations,
        )
        block = format_suspicious_locations(locations)
        if block:
            gate_output = f"{gate_output}\n\n{block}" if gate_output else block
    if worktree_path and ref:
        _git_run(worktree_path, "reset", "--hard", ref)
        _git_run(worktree_path, "clean", "-fd")
    return {
        "fix_attempts": state.get("fix_attempts", 0) + 1,
        "last_gate_output": gate_output,
        # Worktree was reset — a fresh re-implement, not a partial continuation.
        "resume_partial_implement": False,
        "status": Status.IMPLEMENTING,
    }


def prepare_implement_continuation(state: BlacksmithState) -> dict:
    """An implement attempt hit its turn budget with continuations left and within budget:
    CONTINUE it (recoverable turn-cap recovery), rather than discarding the run.

    Unlike ``prepare_fix_retry``/``prepare_escalation``, this deliberately does NOT reset the
    worktree: the capped attempt's partial work is real and worth keeping, so it is left in
    place and ``resume_partial_implement`` tells the next implement to FINISH it (with a fresh
    turn budget) instead of restarting. Bumps ``implement_continuations`` so the loop is
    bounded, and clears ``implement_error_kind`` so the next attempt's outcome is classified
    afresh. Same model as the capped attempt — a turn cap is a budget problem, not a
    capability one, so it stays orthogonal to the gate-failure escalation."""
    return {
        "implement_continuations": state.get("implement_continuations", 0) + 1,
        "resume_partial_implement": True,
        "implement_error_kind": "",
        "status": Status.IMPLEMENTING,
    }


# --- post-gate review loop (WU-REVIEW-LOOP) -----------------------------------
# Additive, bounded loop on the test gate's PASS branch (never the FAILURE branch): a
# stronger model adversarially reviews the just-passed unit's diff (blacksmith.nodes.review,
# WU-REVIEW-NODE). A blocking finding revises the unit (feeding the findings back into the
# next implement prompt exactly as a fix-retry feeds back the gate output) and re-gates;
# once revisions are exhausted or the run is over budget, the unit still proceeds to
# next_unit/approve_pr -- carrying its unresolved findings forward -- rather than halting.


def review_node(
    state: BlacksmithState,
    *,
    executor: Executor | None = None,
    index_config: IndexConfig | None = None,
) -> dict:
    """Thin wrapper around ``blacksmith.nodes.review.review`` for the graph.

    Surfaces THIS call's findings under a plain, last-write-wins key
    (``review_current_findings``) alongside the review node's own ``review_findings``
    (a reducer that keeps accumulating the full cross-unit/cross-revision history
    unchanged). The revision/retention routing below reads ``review_current_findings``
    so it never has to reconstruct "this call's verdict" out of that growing history.

    ``index_config`` wires the SAME in-process blacksmith-index MCP server (search_code +
    read_symbol) into the reviewer's tool surface as ``implement`` gets (WU-REVIEW-INDEX):
    additive and off unless ``index_config.enabled``.
    """
    result = _run_review(state, executor=executor, index_config=index_config)
    if "review_findings" in result:
        result = {**result, "review_current_findings": result["review_findings"]}
    return result


def _format_review_feedback(findings: list[dict]) -> str:
    """Render this call's BLOCKING findings as feedback text for the next implement
    prompt -- fed back via ``last_gate_output``, the exact same channel a fix-retry uses
    to feed back the gate's output, so no implement-prompt change is needed."""
    blocking = [f for f in findings if f.get("severity") == "blocking"]
    lines = [f"- {f.get('file', '(unknown file)')}: {f.get('detail', '')}" for f in blocking]
    return (
        "A REVIEWER flagged blocking issue(s) in your PASSING diff. Fix the CODE (never "
        "weaken tests/assertions to silence the finding); the unit will be re-gated and "
        "re-reviewed after your fix:\n" + "\n".join(lines)
    )


def prepare_review_revision(state: BlacksmithState) -> dict:
    """A blocking review finding, with revisions remaining and within budget: feed the
    findings into the next implement prompt (via ``last_gate_output``, exactly as
    ``prepare_fix_retry`` feeds back the gate output), bump ``review_revisions``, and
    reset this unit's review verdict so the loop re-reviews after the next gate pass.

    Unlike ``prepare_fix_retry``/``prepare_escalation``, this does NOT reset the shared
    worktree: the unit already PASSED the test gate, so its passing commit is kept and the
    next implement attempt revises it in place, committing the fix as a further commit."""
    findings = state.get("review_current_findings") or []
    return {
        "review_revisions": state.get("review_revisions", 0) + 1,
        # Run-wide report-only tally for the "resolved via revision" line (reducer; the
        # per-unit review_revisions above is reset each unit, so it can't total the run).
        "review_revisions_total": 1,
        "review_clean": False,
        "last_gate_output": _format_review_feedback(findings),
        # A review revision feeds back via last_gate_output, not the partial-continuation
        # channel — clear the resume flag so implement uses the review feedback, not the
        # "finish your partial work" nudge.
        "resume_partial_implement": False,
        "status": Status.IMPLEMENTING,
    }


def finalize_review(state: BlacksmithState) -> dict:
    """Review-driven revisions are exhausted or the run is over budget: retain this
    unit's outstanding BLOCKING findings in ``unresolved_review_findings`` and proceed --
    never ``human_halt``. Routing to next_unit/approve_pr happens on the outgoing edge."""
    findings = state.get("review_current_findings") or []
    blocking = [f for f in findings if f.get("severity") == "blocking"]
    return {"unresolved_review_findings": blocking}


def _can_review_revise(state: BlacksmithState) -> bool:
    """Whether a blocking review finding may still trigger a revision retry
    (WU-REVIEW-LOOP): revisions remain under ``limits.max_review_revisions`` AND the run
    is within its cost cap. Mirrors ``_can_fix_retry``'s shape but is a SEPARATE,
    independent bound -- it reuses ``_within_budget`` (the shared cost-cap check) and
    never touches the self-heal/escalation counters."""
    max_revisions = int((state.get("limits") or {}).get("max_review_revisions", 0) or 0)
    return state.get("review_revisions", 0) < max_revisions and _within_budget(state)


# --- parallel fan-out (WU-PARALLEL-FANOUT) -----------------------------------
# A multi-unit level is built concurrently: the engine fans out one ``build_unit`` per
# unit via LangGraph ``Send``, each in its OWN clone of the combined-branch tip; the
# ``join_level`` barrier then cherry-picks the passing units' commits onto the shared
# combined branch in declaration order. A size-1 level (every chain built today) skips
# all of this and stays on the sequential implement->gate path — the behaviour-preserving
# property the existing suite pins down.


def _fanout_review(state: BlacksmithState, sub: dict, deps: UnitDeps) -> dict:
    """Run the post-gate reviewer on a fan-out unit's passing build clone (review finding #1).

    Runs the SAME read-only reviewer the sequential path uses (``blacksmith.nodes.review.review``)
    over the unit's already-committed diff on its OWN build clone, and returns its raw output
    (``review_clean`` / ``review_findings`` / ``cost_events``). The caller (``build_unit``) owns
    what to do with it: surface the findings, and — when revisions remain — feed the blocking
    ones back and re-implement in place (A-i). Off (``review_enabled`` unset / False, threaded
    via the Send payload) this is a no-op returning ``{}``."""
    if not state.get("review_enabled"):
        return {}
    # Panel size rides in from the Send payload (seeded from config.review.panel_size).
    sub["review_panel_size"] = state.get("review_panel_size")
    return _run_review(sub, executor=deps.executor, index_config=deps.index_config)


def build_unit(
    state: BlacksmithState,
    *,
    deps: UnitDeps | None = None,
    worktree_manager: IsolationManager | None = None,
) -> dict:
    """Fan-out worker: build ONE unit of a multi-unit level in its OWN clone, gate it, and
    (when review is enabled) review its passing diff there. Returns only reducer keys
    (``level_builds`` + the review findings + ``cost_events``), never a last-write-wins field,
    so the concurrent workers in a level never race on the shared state.

    Takes the SAME ``UnitDeps`` bundle the sequential ``implement`` node is bound with, and
    its inner ``implement`` call uses ``deps.implement_kwargs()`` — the identical kwargs the
    sequential node passes — so the two implement paths cannot drift (index/sandbox reach the
    fan-out worker for free, review findings #2). ``deps`` unset (deterministic tests) yields
    an empty bundle and the prior single-attempt/no-executor behaviour.

    The per-unit clone is taken from the run's shared clone at its combined-branch tip
    (``shared_clone_path``), so the unit sees every prior level's merged changes while
    writing to a working tree no sibling touches. The existing ``implement`` and
    ``test_gate`` bodies are reused against a per-unit sub-state; their outcome is recorded
    for the join to cherry-pick (on pass) or halt on (on fail).

    A gate failure self-heals on the SAME model before it is recorded (WU-GATE-SELF-HEAL):
    while same-model retries remain (``limits.max_fix_attempts``) and the run is within its
    ``max_run_cost_usd`` ceiling, the build clone is reset HARD to its base tip and the unit
    is re-implemented WITH the gate output fed back, then re-gated — mirroring the sequential
    path's fix-retry on this isolated build clone. A turn-cap halt likewise recovers here,
    mirroring the sequential ``continue_implement``: the clone's partial work is KEPT (not
    reset) and the attempt is continued with a fresh budget, bounded by
    ``limits.max_implement_continuations`` and the cost cap — so recovery from a turn cap does
    not depend on whether a unit landed in a parallel level. Only escalation stays
    sequential-only (the sequential path owns the single stronger-model retry); the fan-out
    worker loops the cheap model. Both knobs ride along in the ``Send`` payload (``limits`` +
    the run's pre-level spend), so a fan-out run wired without limits keeps its prior
    behaviour: one attempt, then record/halt.

    After a gate pass, the post-gate reviewer runs on the build clone (review finding #1). A
    blocking finding with revisions left (``limits.max_review_revisions`` + the cost cap)
    re-implements the unit IN PLACE with the findings fed back, then re-gates and re-reviews —
    mirroring the sequential ``prepare_review_revision`` on this isolated clone. Once revisions
    are exhausted or the review is clean the unit is finalized (its outstanding blocking
    findings carried forward, never halting on them). Because the join cherry-picks a single
    commit, any commits the revise loop stacked are squashed into one before returning. A run
    wired without review (or ``max_review_revisions=0``) reviews at most once / not at all,
    exactly as before."""
    deps = deps or UnitDeps()
    unit = state["selected_unit"]
    level = state.get("level_cursor", 0)
    shared = state["shared_clone_path"]
    record: dict = {"unit_id": unit.id, "title": unit.title, "level": level}
    # This worker's OWN ledger events (one per implement attempt, including retries). Returned
    # under the ``cost_events`` reducer key so each concurrent worker's spend merges into the
    # run ledger the cost report/metrics sum — otherwise a fan-out level's spend is lost.
    cost_events: list[dict] = []
    try:
        clone = worktree_manager.create_from(shared, unit.id)
    except WorktreeError as exc:
        record.update(ok=False, error=f"unit {unit.id}: could not create build clone: {exc}")
        return {"level_builds": [record], "cost_events": cost_events}

    record["clone_path"] = str(clone.path)
    record["branch"] = clone.branch
    # The build clone's combined-branch tip: a same-model fix retry resets HARD to here between
    # attempts (exactly like the sequential ``prepare_fix_retry``), discarding the failed
    # attempt's commit while keeping every prior level's merged work the clone was taken from.
    base_ref = _git_run(clone.path, "rev-parse", "HEAD").stdout.strip()

    limits = state.get("limits") or {}
    max_fix = int(limits.get("max_fix_attempts", 0) or 0)
    cap = limits.get("max_run_cost_usd")
    # Whole-run spend before this level (threaded in via the Send payload) plus this worker's
    # own attempt spend, so the per-unit retry honours the SAME cost ceiling the sequential
    # path checks. Fan-out workers run concurrently and cannot see each other's in-flight spend,
    # so each charges its own attempts against the shared pre-level baseline.
    run_cost = float(state.get("level_cost_base") or 0.0)

    sub: dict = {
        "prd": state["prd"],
        "selected_unit": unit,
        "worktree_path": str(clone.path),
        "branch": clone.branch,
        # Pass the limits through so the worker's implement honours the configurable turn
        # budget (max_implement_turns) AND the turn-cap continuation loop below (bounded by
        # max_implement_continuations). Escalation stays sequential-only; a turn cap recovers.
        "limits": limits,
    }
    max_cont = int(limits.get("max_implement_continuations", 0) or 0)
    max_review_revisions = int(limits.get("max_review_revisions", 0) or 0)
    review_enabled = bool(state.get("review_enabled"))
    fix_attempts = 0
    review_revisions = 0
    continuations = 0
    last_gate_output = ""
    # A gate-fail retry resets HARD to here. It starts at the clone's base tip and advances to
    # each passing commit, so a gate failure DURING a review revision rewinds to the last
    # PASSING commit (not the whole unit), mirroring the sequential pre_implement_ref update.
    reset_ref = base_ref
    # Accumulated across every review pass (reducer semantics, like the sequential review_findings
    # reducer); ``unresolved`` holds the LATEST pass's blocking findings (what still needs eyes).
    review_findings: list[dict] = []
    unresolved: list[dict] = []
    while True:
        # Feed the prior attempt's gate output back into the prompt (empty on the first attempt,
        # so it runs blind); ``implement`` routes it through ``_implement_prompt``'s prior_failure
        # path and commits with ``conventional_commit_message``.
        sub["last_gate_output"] = last_gate_output
        # SAME kwargs the sequential implement node is bound with (index/sandbox included),
        # so a feature can never be live sequentially yet dark on fan-out (review finding #2).
        impl = implement(sub, **deps.implement_kwargs())
        impl_data = impl.get("implementation") or {}
        record["files_touched"] = list(impl_data.get("files_touched") or [])
        record["diff_summary"] = impl_data.get("diff_summary", "")
        # Collect THIS attempt's event for the ledger, and add its cost to the budget running
        # total. ``run_cost`` carries the pre-level baseline (already in the ledger) so the
        # budget check stays whole-run; only the new events are propagated up, never the baseline.
        events = impl.get("cost_events") or []
        cost_events.extend(events)
        run_cost += _events_cost(events)
        if impl.get("status") == Status.HALTED:
            within_budget = cap is None or run_cost < cap
            # A turn-cap halt is RECOVERABLE here too, mirroring the sequential
            # ``continue_implement``: KEEP this clone's partial work (do NOT reset to base_ref)
            # and re-implement with a fresh budget and a "finish it" nudge, bounded by
            # ``max_implement_continuations`` and the cost cap. Only escalation stays
            # sequential-only; recovery from a turn cap must not depend on whether a unit
            # happened to land in a parallel level.
            if (
                impl.get("implement_error_kind") == "max_turns"
                and continuations < max_cont
                and within_budget
            ):
                continuations += 1
                sub["resume_partial_implement"] = True
                continue
            message = impl["errors"][0]["message"] if impl.get("errors") else "implement failed"
            record.update(ok=False, error=f"unit {unit.id}: {message}")
            return {"level_builds": [record], "cost_events": cost_events}

        sub["implementation"] = impl_data
        # Deterministic auto-fix in this unit's own clone before gating it (WU-FIX-CMD): the same
        # fix-then-verify order as the sequential auto_fix -> test_gate, run on EVERY attempt so a
        # self-heal retry re-fixes too. Best-effort; never halts the unit.
        if deps.fix is not None:
            try:
                deps.fix(str(clone.path), unit.layers[0] if unit.layers else None)
            except GateError:
                pass
        gate_update = test_gate(sub, gate=deps.gate)
        results = gate_update.get("test_results") or {}
        record["test_command"] = results.get("command", "")
        if results.get("passed"):
            # This attempt passed the gate: it is the reset target for any later gate-fail
            # retry, so a subsequent review revision that breaks the tests rewinds to HERE
            # (the passing commit) rather than discarding the whole unit.
            reset_ref = _git_run(clone.path, "rev-parse", "HEAD").stdout.strip()
            # Post-gate review on the build clone (review finding #1). Accumulate every pass's
            # findings; ``unresolved`` tracks the LATEST pass's blocking set.
            review_out = _fanout_review(state, sub, deps)
            events = review_out.get("cost_events") or []
            cost_events.extend(events)
            run_cost += _events_cost(events)
            findings = review_out.get("review_findings") or []
            review_findings.extend(findings)
            unresolved = [f for f in findings if f.get("severity") == "blocking"]
            within_budget = cap is None or run_cost < cap
            # A-i in-worker revision: a blocking finding with revisions left re-implements the
            # unit IN PLACE (no reset — the unit passed the gate, so its commit is kept and the
            # fix is committed on top), feeding the findings back exactly as the sequential
            # ``prepare_review_revision`` does, then re-gates and re-reviews. Bounded by
            # ``max_review_revisions`` and the shared cost cap; a run wired without either
            # (limits/review off) never enters here, so it stays surface-only/no-review.
            if unresolved and review_revisions < max_review_revisions and within_budget:
                review_revisions += 1
                last_gate_output = _format_review_feedback(findings)
                sub["resume_partial_implement"] = False
                continue
            # Clean, revisions exhausted, or over budget: this unit is done — leave the loop and
            # finalize below (retaining ``unresolved`` for the PR, never halting on it).
            break

        # Gate failed: retry on the cheap model while retries remain AND the run is within its
        # cost cap (the same gate as the sequential ``_can_fix_retry``, minus escalation). Reset
        # to ``reset_ref`` — the base tip until a gate pass, then the last passing commit — so
        # only the failed attempt is discarded (first-pass behaviour is byte-identical to before),
        # then re-implement with the gate output fed back.
        last_gate_output = results.get("output", "")
        within_budget = cap is None or run_cost < cap
        if fix_attempts < max_fix and within_budget:
            fix_attempts += 1
            _git_run(clone.path, "reset", "--hard", reset_ref)
            _git_run(clone.path, "clean", "-fd")
            # The clone was reset — the next attempt is a fresh re-implement, not a
            # continuation of partial work, so clear the resume nudge.
            sub["resume_partial_implement"] = False
            continue
        error = f"gate failed for unit {unit.id}"
        if cap is not None and not within_budget and fix_attempts < max_fix:
            error += (
                f"; cost cap ${cap:.2f} reached (spent ${run_cost:.2f}), "
                "halting without further retries"
            )
        record.update(ok=False, error=error)
        return {"level_builds": [record], "cost_events": cost_events}

    # Unit done (gate passed; review clean, revisions exhausted, or over budget).
    record["ok"] = True
    # The join cherry-picks a SINGLE FETCH_HEAD, so if the revise loop left more than one commit
    # on top of the clone's base (an in-place revision commit atop the first passing commit),
    # collapse them into ONE carrying the unit's full cumulative diff, and re-derive the file
    # list/summary from it so the PR body and join conflict-attribution reflect the whole unit.
    count = _git_run(clone.path, "rev-list", "--count", f"{base_ref}..HEAD").stdout.strip()
    if count.isdigit() and int(count) > 1:
        _git_run(clone.path, "reset", "--soft", base_ref)
        _git_run(clone.path, "commit", "-m", conventional_commit_message(unit))
        names = _git_run(clone.path, "diff", "--name-only", f"{base_ref}..HEAD").stdout
        record["files_touched"] = [ln for ln in names.splitlines() if ln]
        record["diff_summary"] = _git_run(clone.path, "diff", "--stat", f"{base_ref}..HEAD").stdout
    result_update: dict = {"level_builds": [record], "cost_events": cost_events}
    if review_enabled:
        result_update["review_findings"] = review_findings
        result_update["unresolved_review_findings"] = unresolved
        # Report-only run-wide tally (reducer) for the PR's "resolved via revision" line: this
        # worker's own revision count, summed across the level's concurrent workers. Can't use
        # the last-write-wins ``review_revisions`` (workers would race); this reducer can't.
        result_update["review_revisions_total"] = review_revisions
    return result_update


def _git_run(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _cleanup_builds(builds: list[dict]) -> None:
    """Remove the per-unit build clones (best effort) once the level is joined."""
    for build in builds:
        path = build.get("clone_path")
        if path:
            shutil.rmtree(path, ignore_errors=True)


def _conflict_message(
    level: int, unit_id: str, conflicted: list[str], placed: dict[str, str], picked: list[str]
) -> str:
    """A clear halt message that names BOTH clashing units AND the file(s) they fought
    over, so a human can see exactly which two same-level units collided."""
    files = ", ".join(conflicted) if conflicted else "a shared file"
    others = [placed[f] for f in conflicted if f in placed]
    if not others:
        others = picked  # fall back to every already-applied unit in the level
    other = ", ".join(dict.fromkeys(others)) or "an earlier unit"
    return (
        f"level {level} cherry-pick conflict on {files}: units {other} and {unit_id} "
        "changed the same file(s) — the run halts and opens no PR."
    )


def join_level(state: BlacksmithState) -> dict:
    """Fan-out barrier: cherry-pick each passing unit's commit onto the shared combined
    branch in declaration order, then advance the level engine.

    Runs once, after all of the level's ``build_unit`` workers complete (so no concurrent
    write to the shared branch). If ANY unit failed its implement/gate the whole level
    halts with nothing cherry-picked (no partial level lands); if two units changed the
    same file the cherry-pick conflicts and the run halts naming both units and the file."""
    levels = state.get("execution_levels") or []
    level = state.get("level_cursor", 0)
    level_units = levels[level] if level < len(levels) else []
    order = {unit.id: index for index, unit in enumerate(level_units)}
    builds = [b for b in (state.get("level_builds") or []) if b.get("level") == level]
    builds.sort(key=lambda b: order.get(b["unit_id"], len(order)))

    # (3) Any failed implement/gate halts the level BEFORE any cherry-pick — nothing lands.
    failed = [b for b in builds if not b.get("ok")]
    if failed:
        _cleanup_builds(builds)
        names = ", ".join(b["unit_id"] for b in failed)
        detail = "; ".join(b.get("error", b["unit_id"]) for b in failed)
        return {
            "status": Status.HALTED,
            "errors": [
                {"node": "join_level", "message": f"level {level} halted: {names} ({detail})"}
            ],
        }

    shared = state["worktree_path"]
    picked: list[str] = []
    placed: dict[str, str] = {}
    for build in builds:
        fetch = _git_run(shared, "fetch", build["clone_path"], build["branch"])
        if fetch.returncode != 0:
            _cleanup_builds(builds)
            return {
                "status": Status.HALTED,
                "errors": [
                    {
                        "node": "join_level",
                        "message": f"level {level}: could not fetch unit "
                        f"{build['unit_id']}'s commit: {fetch.stderr.strip()}",
                    }
                ],
            }
        cherry = _git_run(shared, "cherry-pick", "FETCH_HEAD")
        if cherry.returncode != 0:
            conflicted = [
                line
                for line in _git_run(shared, "diff", "--name-only", "--diff-filter=U")
                .stdout.splitlines()
                if line
            ]
            _git_run(shared, "cherry-pick", "--abort")
            _cleanup_builds(builds)
            return {
                "status": Status.HALTED,
                "errors": [
                    {
                        "node": "join_level",
                        "message": _conflict_message(
                            level, build["unit_id"], conflicted, placed, picked
                        ),
                    }
                ],
            }
        picked.append(build["unit_id"])
        for changed in build.get("files_touched", []):
            placed[changed] = build["unit_id"]

    _cleanup_builds(builds)
    unit_results = [
        {
            "unit_id": b["unit_id"],
            "title": b["title"],
            "files_touched": list(b.get("files_touched") or []),
            "diff_summary": b.get("diff_summary", ""),
            "test_command": b.get("test_command", ""),
        }
        for b in builds
    ]
    update: dict = {
        "unit_results": unit_results,
        "status": Status.TESTING,
        "level_cursor": level + 1,
    }
    # Seed the next level's sequential state so a size-1 level after this fan-out runs its
    # implement step on the shared clone unchanged (the fan-out router ignores these).
    if level + 1 < len(levels):
        update["selected_unit"] = levels[level + 1][0]
        update["unit_in_level"] = 0
    return update


def human_halt(state: BlacksmithState) -> dict:
    return {"status": Status.HALTED}


def cleanup_worktree(
    state: BlacksmithState,
    *,
    worktree_manager: IsolationManager | None = None,
    sandbox: SandboxManager | None = None,
) -> dict:
    """Remove the run's isolation directory on a terminal path (PRD §5). Cleanup must
    never fail the run.

    For a CloneManager the clone owns its own .git, so removing the directory is total
    cleanup — there is no source-repo branch to keep or delete (the branch lives only in
    the clone and, once pushed, on the real remote). For the legacy WorktreeManager the
    branch lives in the shared source repo, so the worktree's branch is kept when a PR
    was opened (the PR needs it) and deleted otherwise so re-runs don't collide.

    Stops the run's sandbox container FIRST (WU-SANDBOX-LIFECYCLE), best-effort and
    unconditional on every terminal path -- including after a halt, since ``human_halt``
    routes here too -- so a started container is never leaked. A graph compiled without a
    sandbox, or one wired with ``enabled=False``, is a byte-for-byte no-op; ``stop()`` is
    idempotent, so this is safe even when ``start`` was never called or already failed."""
    if sandbox is not None and sandbox.config.enabled:
        try:
            sandbox.stop()
        except SandboxError:
            pass
    if worktree_manager is None:
        return {}
    worktree_path = state.get("worktree_path")
    unit = state.get("selected_unit")
    if not worktree_path or unit is None:
        return {}
    # The run's one shared branch (multi-unit); falls back to the unit's branch for a
    # state that predates the shared-branch field.
    branch = state.get("branch") or branch_for(unit.id)
    try:
        if isinstance(worktree_manager, CloneManager):
            worktree_manager.remove(
                Clone(
                    path=Path(worktree_path),
                    branch=branch,
                    repo_path=worktree_manager.repo_path,
                )
            )
        else:
            worktree_manager.remove(
                Worktree(
                    path=Path(worktree_path),
                    branch=branch,
                    repo_path=worktree_manager.repo_path,
                ),
                delete_branch=not state.get("pr_url"),
            )
    except WorktreeError:
        pass
    return {}


# --- conditional routing -----------------------------------------------------


def route_after_approve_plan(state: BlacksmithState) -> str:
    """Proceed only on plan approval; a rejection halts (PRD §4)."""
    return "prepare_worktree" if state.get("approvals", {}).get("plan") else "human_halt"


def route_after_approve_pr(state: BlacksmithState) -> str:
    """Proceed only on PR approval; a rejection halts (PRD §5: never auto-merge)."""
    return "open_pr" if state.get("approvals", {}).get("pr") else "human_halt"


def _route_or_halt(next_node: str) -> Callable[[BlacksmithState], str]:
    """Route forward, unless a node has set status to HALTED — then short-circuit to
    human_halt so an errored node never flows into an approval gate."""

    def route(state: BlacksmithState) -> str:
        return "human_halt" if state.get("status") == Status.HALTED else next_node

    return route


def route_after_implement(state: BlacksmithState) -> str:
    """Route a just-implemented unit (PRD §4).

    A failed implement (status HALTED) discards the work via ``human_halt`` — UNLESS it was a
    recoverable turn-cap halt with continuations left (``_can_continue_implement``), which
    routes to ``continue_implement`` to finish the partial work instead. A human-gated unit
    (any ``human`` layer) that implemented successfully bypasses the automated gate and
    instead opens a DRAFT PR for manual QA (``open_draft_pr`` -> AWAITING_QA, branch kept).
    An auto-gated unit proceeds to ``auto_fix`` (the deterministic formatter step), which
    then flows into the automated ``test_gate``."""
    if state.get("status") == Status.HALTED:
        if _can_continue_implement(state):
            return "continue_implement"
        return "human_halt"
    prd = state.get("prd")
    unit = state.get("selected_unit")
    if prd is not None and unit is not None and prd.contract.gate_for(unit) == "human":
        return "open_draft_pr"
    return "auto_fix"


def _next_unit_or_approve(state: BlacksmithState) -> str:
    """The plain post-gate advance decision (PRD §4): loop to ``next_unit`` while units
    remain in the level plan (shared branch), else proceed to the single PR-approval gate.

    Shared by a gate pass with the review loop OFF (or already clean), and by the review
    loop's own clean/exhausted exits (WU-REVIEW-LOOP) — one place decides "what's next"."""
    levels = state.get("execution_levels") or []
    nxt = _next_position(levels, state.get("level_cursor", 0), state.get("unit_in_level", 0))
    return "next_unit" if nxt is not None else "approve_pr"


def route_after_test_gate(state: BlacksmithState) -> str:
    """Deterministic routing on the test result — a graph edge, not a model decision.

    A failed gate halts. On a pass, if the additive post-gate review loop is enabled
    (``config.review.enabled``, WU-REVIEW-LOOP) and this unit is not yet review-clean,
    route to ``review`` instead of the plain next_unit/approve_pr decision; otherwise (the
    loop is off, or was already clean) proceed exactly as before.

    A gate failure recovers in a bounded order before it halts:
    1. Self-heal (WU-GATE-SELF-HEAL): while same-model retries remain for this unit and the
       run is within its cost cap, re-implement on the cheap model WITH the gate output fed
       back (``fix_retry``).
    2. Escalation (WU-ESCALATE-ON-FAIL): once the retries are spent, re-implement once with
       the stronger model (still within the cost cap).
    3. Halt: retries spent AND already escalated, or no ``pre_implement_ref`` (a test double
       that cannot escalate), or the cost cap is reached — halt with no PR.

    This FAILURE branch (and its counters) is untouched by the review loop — the review
    loop is a separate bounded loop on the PASS branch only."""
    results = state.get("test_results") or {}
    if not results.get("passed"):
        if _can_fix_retry(state):
            return "fix_retry"
        if _will_escalate(state) and _within_budget(state):
            return "escalate"
        return "human_halt"
    if state.get("review_enabled") and not state.get("review_clean"):
        return "review"
    return _next_unit_or_approve(state)


def route_after_review(state: BlacksmithState) -> str:
    """Route after the additive post-gate review call (WU-REVIEW-LOOP).

    A clean verdict proceeds exactly like a gate pass with no review (the plain
    next_unit/approve_pr decision). A blocking finding revises the unit -- bounded by
    ``limits.max_review_revisions`` and the cost cap -- before re-gating and re-reviewing.
    Once revisions are exhausted or the run is over budget, the unit still proceeds
    (carrying its unresolved findings forward via ``finalize_review``) rather than halting
    -- the review loop never routes to ``human_halt``."""
    if state.get("review_clean"):
        return _next_unit_or_approve(state)
    if _can_review_revise(state):
        return "prepare_review_revision"
    return "finalize_review"


def _is_fanout_level(state: BlacksmithState, level: int) -> bool:
    """A level is built in parallel only when fan-out is enabled (CloneManager) AND the
    level holds more than one unit. A size-1 level always takes the sequential path."""
    if not state.get("fanout"):
        return False
    levels = state.get("execution_levels") or []
    return 0 <= level < len(levels) and len(levels[level]) > 1


def _fanout_sends(state: BlacksmithState, level: int) -> list[Send]:
    """One ``Send`` per unit in the level: each carries the unit plus the shared clone's
    combined-branch tip, so its ``build_unit`` worker clones an isolated build tree.

    The self-heal limits and the run's spend-so-far ride along too (``level_cost_base`` is
    a payload-only field), so each worker's bounded same-model fix retry honours
    ``max_fix_attempts`` and the ``max_run_cost_usd`` cap. A run wired without limits sends
    ``limits=None`` and the worker takes a single attempt, exactly as before. The review
    switch + panel size ride along the same way so the worker runs the post-gate reviewer on
    its build clone (review finding #1); a run wired without review sends ``None`` and the
    worker skips review, exactly as before."""
    levels = state.get("execution_levels") or []
    return [
        Send(
            "build_unit",
            {
                "prd": state.get("prd"),
                "selected_unit": unit,
                "level_cursor": level,
                "shared_clone_path": state.get("worktree_path"),
                "limits": state.get("limits"),
                "level_cost_base": _run_cost(state),
                # Thread the review switch + panel size through so the worker runs the post-gate
                # reviewer on its build clone (review finding #1); absent -> the worker skips
                # review, exactly as before this fix.
                "review_enabled": state.get("review_enabled"),
                "review_panel_size": state.get("review_panel_size"),
            },
        )
        for unit in levels[level]
    ]


def _route_to_level(state: BlacksmithState) -> str | list[Send]:
    """Build the level at ``level_cursor``: fan out (multi-unit + CloneManager), run the
    sequential ``implement`` path (size-1 level), or finish at ``approve_pr`` when the plan
    is exhausted. Shared by every level entry point so the decision lives in one place."""
    levels = state.get("execution_levels") or []
    # No level plan seeded (the skeleton / dependency-free graph tests, where
    # prepare_worktree is a pass-through): fall through to the sequential implement path
    # exactly as before — the level engine only engages once a plan exists.
    if not levels:
        return "implement"
    level = state.get("level_cursor", 0)
    if level >= len(levels):
        return "approve_pr"
    if _is_fanout_level(state, level):
        return _fanout_sends(state, level)
    return "implement"


def route_after_prepare(state: BlacksmithState) -> str | list[Send]:
    """Enter the first level (or halt if prepare_worktree errored)."""
    if state.get("status") == Status.HALTED:
        return "human_halt"
    return _route_to_level(state)


def route_after_next_unit(state: BlacksmithState) -> str | list[Send]:
    """The sequential engine advanced to a new position; build it sequentially or, when it
    crossed into a multi-unit level under CloneManager, fan that level out instead."""
    return _route_to_level(state)


def route_after_join(state: BlacksmithState) -> str | list[Send]:
    """After a fan-out level joins: halt on conflict/failure, else build the next level
    (fan-out or sequential) or finish at the PR-approval gate."""
    if state.get("status") == Status.HALTED:
        return "human_halt"
    return _route_to_level(state)


# --- assembly ----------------------------------------------------------------


def _open_pr_node(fn, pr_runner: Runner | None, executor: Executor | None = None):
    """Bind a PR node's command runner (default subprocess; fake in tests) AND the run's
    executor at build time. ``executor`` (WU-PR-SUMMARY-WIRE) is the SAME instance already
    constructed by ``build_graph_for`` — no separate wiring — and drives the additive,
    fail-open title/summary synthesis inside ``_open_pr``. ``None`` (the default, every
    existing test) leaves both PR nodes byte-for-byte unchanged."""
    if pr_runner is None and executor is None:
        return fn

    def node(state: BlacksmithState) -> dict:
        kwargs = {}
        if pr_runner is not None:
            kwargs["runner"] = pr_runner
        if executor is not None:
            kwargs["executor"] = executor
        return fn(state, **kwargs)

    return node


def _node_with(fn, **injected):
    """Bind dependencies at build time. If nothing is injected, return the bare node
    (a status-only pass-through), keeping deterministic graph tests dependency-free."""
    if all(value is None for value in injected.values()):
        return fn

    def node(state: BlacksmithState) -> dict:
        return fn(state, **injected)

    return node


def build_graph(
    *,
    pr_runner: Runner | None = None,
    executor: Executor | None = None,
    worktree_manager: IsolationManager | None = None,
    gate: GateFn | None = None,
    limits: LimitsConfig | None = None,
    fix: FixFn | None = None,
    review: ReviewConfig | None = None,
    sandbox: SandboxManager | None = None,
    index: IndexConfig | None = None,
    sbfl: SBFLConfig | None = None,
    critic: CriticConfig | None = None,
    default_branch: str | None = None,
) -> StateGraph:
    """Construct (but do not compile) the v0 graph topology.

    ``limits`` enables the gate self-heal loop (WU-GATE-SELF-HEAL): when provided it is
    seeded into state by ``prepare_worktree`` so routing can bound the retries. Left unset
    (every graph wired without it) the loop is OFF and routing behaves exactly as before.

    ``review`` enables the additive post-gate review loop (WU-REVIEW-LOOP) the same way:
    when provided, ``config.review.enabled`` is seeded into state so ``route_after_test_gate``
    can route a PASS to ``review`` instead of straight to next_unit/approve_pr. Left unset
    (every graph wired without it) the review node is never entered.

    ``sandbox`` wires the run's additive, opt-in self-verify container (WU-SANDBOX-LIFECYCLE):
    when provided AND ``sandbox.config.enabled``, ``prepare_worktree`` starts it once over the
    run's clone (reused across every unit) and ``cleanup_worktree`` stops it, best-effort, on
    every terminal path. Left unset (the default, every existing test) or disabled, neither
    node touches it and the graph behaves exactly as today -- the test gate's pass/fail
    semantics are never affected either way.

    ``index`` wires the additive, opt-in repo-map injection into the plan node's SHARED
    system prompt (WU-PLAN-REPO-MAP): when provided AND ``index.enabled``, the plan node
    builds the target repo's map once and reuses it across every unit's plan call, the
    SAME on/off switch that gates the implementer's indexing. The target repo path is
    read straight off ``worktree_manager`` (the same repo the run's isolation is cloned
    from) — the index is read-only over it and never writes it. Left unset (the default,
    every existing test) or disabled, the plan system prompt is byte-for-byte unchanged.

    ``sbfl`` wires the additive, opt-in fault-localization channel (WU-SBFL-WIRE) into the
    ``fix_retry`` node the SAME opt-in-forwarding way as review/index/sandbox: when provided
    AND ``sbfl.enabled``, ``prepare_fix_retry`` spectrum-analyses the failing worktree before
    it resets and appends the ranked suspicious locations to the gate output fed back to the
    next implement attempt. Left unset (the default, every existing test) or disabled, the
    fix-retry feedback is byte-for-byte unchanged — no collection runs and the gate's
    pass/fail decision is never touched.

    ``critic`` wires the additive, opt-in plan critic loop (WU-PLAN-CRITIC-LOOP) into the
    ``plan`` node the SAME opt-in-forwarding way as review/index/sandbox/sbfl: when provided
    AND ``critic.enabled``, the plan node critiques each auto-gated unit's plan and, while
    disapproved and revisions remain (``critic.max_plan_revisions``), re-plans it with the
    critique fed back — bounded, and it PROCEEDS (never halts) once the budget is exhausted.
    Left unset (the default, every existing test) or disabled, the plan node makes zero
    critic calls and its output is byte-for-byte unchanged.

    ``default_branch`` is the target repo's default branch (``[target].default_branch``):
    when provided, ``prepare_worktree`` seeds it into state so the PR node opens the combined
    PR against it (``gh pr create --base``). Left unset (the default, every existing test)
    the PR node passes no ``--base`` and gh falls back to the repo's own default."""
    graph = StateGraph(BlacksmithState)

    plan_repo_path = str(worktree_manager.repo_path) if worktree_manager is not None else None
    graph.add_node("ingest_prd", ingest_prd)
    graph.add_node(
        "plan",
        _node_with(
            plan,
            executor=executor,
            index_config=index,
            repo_path=plan_repo_path,
            critic=critic,
        ),
    )
    graph.add_node("approve_plan", approve_plan)
    graph.add_node(
        "prepare_worktree",
        _node_with(
            prepare_worktree,
            worktree_manager=worktree_manager,
            limits=limits,
            review=review,
            sandbox=sandbox,
            default_branch=default_branch,
        ),
    )
    # ONE dependency bundle for BOTH unit-build paths (the sequential ``implement`` node and
    # the fan-out ``build_unit`` worker). Before this, each opt-in feature was threaded into the
    # implement node by hand and the fan-out worker was a second copy that silently omitted
    # index/sandbox (review findings #1/#2). With a single bundle a new unit-build dep reaches
    # both paths for free, and build_unit's inner implement call uses ``implement_kwargs`` so it
    # can never diverge from what the sequential node passes. index_config drives the repo-map
    # injection + the search_code tool (WU-REPO-MAP-INJECT / WU-SEARCH-TOOL); the sandbox
    # (WU-SANDBOX-IMPLEMENT) grants the run_command tool and carries its own exec timeout. An
    # all-None bundle (tests) collapses ``_node_with`` back to a bare pass-through for implement,
    # so a dependency-free graph is byte-for-byte unchanged.
    unit_deps = UnitDeps(
        executor=executor,
        gate=gate,
        fix=fix,
        index_config=index,
        sandbox=sandbox,
        sandbox_exec_timeout_s=sandbox.config.exec_timeout_s if sandbox is not None else None,
    )
    graph.add_node("implement", _node_with(implement, **unit_deps.implement_kwargs()))
    graph.add_node("auto_fix", _node_with(auto_fix, fix=fix))
    graph.add_node("test_gate", _node_with(test_gate, gate=gate))
    graph.add_node(
        "build_unit",
        _node_with(build_unit, deps=unit_deps, worktree_manager=worktree_manager),
    )
    graph.add_node("join_level", join_level)
    graph.add_node("next_unit", next_unit)
    graph.add_node("escalate", prepare_escalation)
    graph.add_node("fix_retry", _node_with(prepare_fix_retry, sbfl=sbfl))
    graph.add_node("continue_implement", prepare_implement_continuation)
    graph.add_node("review", _node_with(review_node, executor=executor, index_config=index))
    graph.add_node("prepare_review_revision", prepare_review_revision)
    graph.add_node("finalize_review", finalize_review)
    graph.add_node("approve_pr", approve_pr)
    graph.add_node("open_pr", _open_pr_node(open_pr, pr_runner, executor))
    graph.add_node("open_draft_pr", _open_pr_node(open_draft_pr, pr_runner, executor))
    graph.add_node("human_halt", human_halt)
    graph.add_node(
        "cleanup_worktree",
        _node_with(cleanup_worktree, worktree_manager=worktree_manager, sandbox=sandbox),
    )

    graph.add_edge(START, "ingest_prd")
    graph.add_conditional_edges(
        "ingest_prd", _route_or_halt("plan"), {"plan": "plan", "human_halt": "human_halt"}
    )
    graph.add_conditional_edges(
        "plan",
        _route_or_halt("approve_plan"),
        {"approve_plan": "approve_plan", "human_halt": "human_halt"},
    )
    graph.add_conditional_edges(
        "approve_plan",
        route_after_approve_plan,
        {"prepare_worktree": "prepare_worktree", "human_halt": "human_halt"},
    )
    graph.add_conditional_edges(
        "prepare_worktree",
        route_after_prepare,
        {
            "implement": "implement",
            "build_unit": "build_unit",
            "approve_pr": "approve_pr",
            "human_halt": "human_halt",
        },
    )
    graph.add_conditional_edges(
        "implement",
        route_after_implement,
        {
            "auto_fix": "auto_fix",
            "human_halt": "human_halt",
            "open_draft_pr": "open_draft_pr",
            "continue_implement": "continue_implement",
        },
    )
    # Turn-cap recovery: an implement attempt that ran out of turns continues from its partial
    # work (kept, not reset) with a fresh budget, then re-enters implement (WU recoverable-impl).
    graph.add_edge("continue_implement", "implement")
    # Deterministic auto-fix runs between implement and the gate, then always flows into it.
    # A unit with no fix_cmd makes auto_fix a pass-through, so this is the prior implement->gate
    # path with one (usually free) step inserted.
    graph.add_edge("auto_fix", "test_gate")
    graph.add_conditional_edges(
        "test_gate",
        route_after_test_gate,
        {
            "approve_pr": "approve_pr",
            "human_halt": "human_halt",
            "next_unit": "next_unit",
            "fix_retry": "fix_retry",
            "escalate": "escalate",
            "review": "review",
        },
    )
    # Self-heal: discard the failed attempt, reset the worktree, and re-implement the SAME
    # unit on the cheap model with the gate output fed back, then re-gate (WU-GATE-SELF-HEAL).
    graph.add_edge("fix_retry", "implement")
    # Escalation: once the retries are spent, re-implement the SAME unit once with the
    # stronger model, then re-gate (WU-ESCALATE-ON-FAIL).
    graph.add_edge("escalate", "implement")
    # Post-gate review loop (WU-REVIEW-LOOP): clean proceeds like a gate pass with review off;
    # a blocking finding revises (bounded) and re-implements the SAME unit in place, then
    # re-gates and re-reviews; exhausted/over-budget still proceeds, never halts.
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "next_unit": "next_unit",
            "approve_pr": "approve_pr",
            "prepare_review_revision": "prepare_review_revision",
            "finalize_review": "finalize_review",
        },
    )
    graph.add_edge("prepare_review_revision", "implement")
    graph.add_conditional_edges(
        "finalize_review",
        _next_unit_or_approve,
        {"next_unit": "next_unit", "approve_pr": "approve_pr"},
    )
    # Loop: a size-1 next level builds on the same shared worktree/branch (no re-plan, no
    # new worktree), so its implement step sees the prior units' committed changes. A
    # multi-unit next level (CloneManager) instead fans out into per-unit build clones.
    graph.add_conditional_edges(
        "next_unit",
        route_after_next_unit,
        {
            "implement": "implement",
            "build_unit": "build_unit",
            "approve_pr": "approve_pr",
        },
    )
    # Fan-out: each build_unit worker reports its outcome; the join barrier runs once and
    # cherry-picks the passing units onto the combined branch (or halts on conflict/fail).
    graph.add_edge("build_unit", "join_level")
    graph.add_conditional_edges(
        "join_level",
        route_after_join,
        {
            "implement": "implement",
            "build_unit": "build_unit",
            "approve_pr": "approve_pr",
            "human_halt": "human_halt",
        },
    )
    graph.add_conditional_edges(
        "approve_pr",
        route_after_approve_pr,
        {"open_pr": "open_pr", "human_halt": "human_halt"},
    )
    graph.add_edge("open_pr", "cleanup_worktree")
    # A human-gated unit's draft PR is terminal too: cleanup keeps the branch (pr_url set).
    graph.add_edge("open_draft_pr", "cleanup_worktree")
    graph.add_edge("human_halt", "cleanup_worktree")
    graph.add_edge("cleanup_worktree", END)

    return graph


def blacksmith_serde() -> JsonPlusSerializer:
    """Serializer that registers blacksmith's own state types.

    Without this, checkpointing a PRD / WorkUnit / Status logs "Deserializing
    unregistered type ... will be blocked in a future version" — a forward-compat
    hazard for pause/resume (AC-2) once the graph persists rich state (WU-09+).
    Registering them explicitly keeps the allowlist tight (only blacksmith's types).
    """
    return JsonPlusSerializer(allowed_msgpack_modules=[Status, PRD, PRDContract, WorkUnit])


def build_checkpointer(db_path: str | Path) -> SqliteSaver:
    """Open a file-backed SQLite checkpointer (PRD §12 decision 1).

    A fresh instance pointed at the same path re-attaches to existing checkpoints,
    which is how a run resumes after a process restart.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn, serde=blacksmith_serde())
    saver.setup()
    return saver


def compile_graph(
    checkpointer: SqliteSaver,
    *,
    interrupt_before: Sequence[str] = (),
    pr_runner: Runner | None = None,
    executor: Executor | None = None,
    worktree_manager: IsolationManager | None = None,
    gate: GateFn | None = None,
    fix: FixFn | None = None,
    store: BaseStore | None = None,
    limits: LimitsConfig | None = None,
    review: ReviewConfig | None = None,
    sandbox: SandboxManager | None = None,
    index: IndexConfig | None = None,
    sbfl: SBFLConfig | None = None,
    critic: CriticConfig | None = None,
    default_branch: str | None = None,
) -> CompiledStateGraph:
    """Compile the graph with a checkpointer.

    The HITL halts come from dynamic ``interrupt()`` calls inside ``approve_plan`` /
    ``approve_pr`` (WU-07), so no static ``interrupt_before`` is needed by default;
    the parameter remains for tests or extra inspection points. The dependency params
    (``executor``, ``worktree_manager``, ``gate``, ``fix``, ``pr_runner``) are injected by
    the CLI in production and faked in tests; unset ones leave that node a pass-through (an
    unset ``fix`` makes ``auto_fix`` a no-op, so the gate runs on the agent's commit as-is).

    ``store`` is an optional persistent long-term memory Store (WU-STORE-WIRING),
    forwarded to ``.compile(store=...)``. It is a SEPARATE, additive channel from the
    checkpointer; ``store=None`` (the default) compiles exactly as before.

    ``limits`` enables the gate self-heal loop (WU-GATE-SELF-HEAL); ``None`` (the default,
    every test that does not opt in) leaves the loop off and routing unchanged.

    ``review`` enables the additive post-gate review loop (WU-REVIEW-LOOP); ``None`` (the
    default) leaves the review node unreachable and routing unchanged.

    ``sandbox`` wires the run's additive, opt-in self-verify container (WU-SANDBOX-LIFECYCLE);
    ``None`` (the default) or one with ``config.enabled=False`` leaves ``prepare_worktree``/
    ``cleanup_worktree`` byte-for-byte unchanged -- no container is ever started or stopped.

    ``index`` wires the additive, opt-in repo-map injection into the plan node's shared
    system prompt (WU-PLAN-REPO-MAP); ``None`` (the default) or one with ``enabled=False``
    leaves the plan system prompt byte-for-byte unchanged -- the same switch that gates the
    implementer's indexing.

    ``sbfl`` wires the additive, opt-in fault-localization channel (WU-SBFL-WIRE) into the
    ``fix_retry`` node; ``None`` (the default) or one with ``enabled=False`` leaves the
    fix-retry feedback byte-for-byte unchanged and never touches the gate's decision.

    ``critic`` wires the additive, opt-in plan critic loop (WU-PLAN-CRITIC-LOOP) into the
    ``plan`` node; ``None`` (the default) or one with ``enabled=False`` leaves the plan node's
    output byte-for-byte unchanged -- no critic call, no re-plan, and the ``approve_plan``
    gate's semantics are never touched.

    ``default_branch`` (``[target].default_branch``) is seeded into state by
    ``prepare_worktree`` so the PR node opens the combined PR against it; ``None`` (the
    default) leaves the PR base unset and gh falls back to the repo's own default.
    """
    return build_graph(
        pr_runner=pr_runner,
        executor=executor,
        worktree_manager=worktree_manager,
        gate=gate,
        limits=limits,
        fix=fix,
        review=review,
        sandbox=sandbox,
        index=index,
        sbfl=sbfl,
        critic=critic,
        default_branch=default_branch,
    ).compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before),
        store=store,
    )
