"""Parent scheduling, approvals, joins and persistence for Blacksmith runs.

Every sequential/parallel unit invokes the same compiled ``unit_graph``. The
parent selects units and consumes keyed outcomes; attempt recovery is a child
state machine. Standalone node/routing helpers remain for legacy callers.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Send

from blacksmith.commands import command_scope_for_state, run_command
from blacksmith.config import (
    CriticConfig,
    HitlConfig,
    IndexConfig,
    LimitsConfig,
    ReviewConfig,
    SBFLConfig,
    reject_enabled_sandbox,
)
from blacksmith.context import selected_plan
from blacksmith.contract import PRD, ContractError, PRDContract, WorkUnit, parse_prd
from blacksmith.executor import Executor
from blacksmith.gate import FixResult, GateError, GateResult
from blacksmith.gate_receipts import FixReceipt, GateCommandReceipt, GateLayerReceipt, GateReceipt
from blacksmith.memory import current_store, record_lesson
from blacksmith.nodes.hitl import approve_plan, approve_pr, prepare_pr_approval
from blacksmith.nodes.implement import implement as implement
from blacksmith.nodes.plan import plan
from blacksmith.nodes.pr import Runner, open_draft_pr, open_pr
from blacksmith.nodes.review import review as _run_review
from blacksmith.path_policy import PolicyViolation, compile_path_rules, policy_for_state
from blacksmith.receipts import ReceiptError
from blacksmith.run_context import RunContextError, context_from_state
from blacksmith.sandbox import SandboxManager
from blacksmith.sbfl import collect_suspicious_locations, format_suspicious_locations
from blacksmith.scheduling import execution_batches
from blacksmith.state import BlacksmithState, Status
from blacksmith.unit_state import new_unit_attempt, next_attempt
from blacksmith.verification import fix_candidate, run_candidate_gate
from blacksmith.worktree import (
    Clone,
    CloneManager,
    RunCloneManager,
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
    """Dependencies consumed by the shared sequential/parallel unit graph.

    Optional index, sandbox and SBFL settings reach the same stages in both
    scheduling shapes. Workspace managers belong to preparation; model/commit
    phase selection is owned by the graph rather than a configurable dependency.
    """

    executor: Executor | None = None
    gate: GateFn | None = None
    fix: FixFn | None = None
    index_config: IndexConfig | None = None
    sandbox: SandboxManager | None = None
    sandbox_exec_timeout_s: int | None = None
    sbfl: SBFLConfig | None = None

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
        compile_path_rules(prd.contract.untouchables)
    except (ContractError, PolicyViolation) as exc:
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
    hitl: HitlConfig | None = None,
) -> dict:
    """Create the run's ONE shared clone/branch and seed the level execution plan.

    In production the isolation manager is a CloneManager, so this creates a throwaway
    local clone (own .git, origin pointed at the real remote) — the agent works there and
    can never touch the source checkout. Every unit is built sequentially on this single
    clone (so each unit's committed changes are visible to the next unit's implement step)
    and the run opens one combined PR against its branch. The plan comes from
    ``scheduling.execution_batches``; the implement->gate loop walks it batch by batch (and,
    within a level, in declaration order) via ``level_cursor``/``unit_in_level`` and
    re-enters ``implement`` (never this node), so the clone is created exactly once. A
    single-unit PRD reduces to the prior behaviour: one level, one unit, one clone, one PR.
    The isolation path is stored under ``worktree_path`` (the name predates the clone
    model).

    Enabled sandbox settings are unsupported until workspace ownership and real
    Docker timeout/restart evidence are hardened. Reject before any clone/native
    effect; disabled/unwired managers remain inert."""
    reject_enabled_sandbox(sandbox is not None and sandbox.config.enabled)
    if worktree_manager is None:
        return {}  # skeleton pass-through
    prd = state.get("prd")
    scheduling_root = worktree_manager.repo_path
    if state.get("run_context"):
        snapshot = context_from_state(state)
        prd, scheduling_root = snapshot.prd(), snapshot.planning_path
    levels = execution_batches(prd.contract, root=scheduling_root) if prd is not None else []
    units = [unit for level in levels for unit in level]
    unit = units[0] if units else state.get("selected_unit")
    if unit is None:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "prepare_worktree", "message": "no selected_unit"}],
        }
    try:
        if state.get("run_context"):
            context = context_from_state(state)
            context.verify(selected_repo=worktree_manager.repo_path)
            worktree_manager = RunCloneManager(context)
        worktree = worktree_manager.create(unit.id)
    except (WorktreeError, RunContextError) as exc:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "prepare_worktree", "message": str(exc)}],
        }
    update: dict = {
        "scheduling_version": 1,
        "unit_graph_version": 1,
        "review_version": 1,
        "approval_version": 1,
        "publication_version": 1,
        "publication_receipt": None,
        "pr_approval_round": 0,
        "pr_approval_proposal": None,
        "pr_approval": None,
        "join_graph_version": 1,
        "prepared_level": None,
        "level_bases": {},
        "join_destinations": {},
        **_new_unit_state(state, unit, worktree.path),
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
            "max_parallel_units": limits.max_parallel_units,
            "max_fix_attempts": limits.max_fix_attempts,
            "max_run_cost_usd": limits.max_run_cost_usd,
            "max_review_revisions": limits.max_review_revisions,
            "max_implement_turns": limits.max_implement_turns,
            "max_implement_continuations": limits.max_implement_continuations,
        }
        update["fix_attempts"] = 0
        update["implement_continuations"] = 0
        if limits.max_run_cost_usd is not None:
            update["limits"]["max_run_cost_usd"] += state.get("budget_amendment_usd", 0)
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
    # Capture the shared worktree's HEAD right after creation, i.e. before any unit's
    # commits land on it (WU-PR-DIFF-CAPTURE) -- the run base ref the approve_pr gate diffs
    # the combined branch against. Only when the graph is wired with a HitlConfig
    # (production via build_graph_for), the SAME opt-in-forwarding pattern as
    # limits/review/default_branch above; a graph compiled without one leaves
    # ``pr_base_ref`` unset, so the gate's payload carries no diff_text exactly as before.
    # Best-effort: a git failure here just leaves the ref unset (no diff, never a halt).
    if hitl is not None:
        ref_result = _git_run(str(worktree.path), "rev-parse", "HEAD")
        if ref_result.returncode == 0:
            update["pr_base_ref"] = ref_result.stdout.strip()
    # S17 deliberately starts no sandbox; enabled settings refuse before cloning.
    return update


def auto_fix(state: BlacksmithState, *, fix: FixFn | None = None) -> dict:
    """Run frozen formatters on the attempt's owned commit before verification.

    Version-2 execution persists ownership/formatter evidence and halts on dirty or
    infrastructure failures. A clean nonzero formatter exit may proceed to the gate.
    Legacy injected helpers retain their two-argument API.
    """
    if state.get("run_context"):
        try:
            receipt = fix_candidate(state)
            update = {"fix_receipt": receipt}
            if receipt.eligible:
                update["implementation"] = {
                    **(state.get("implementation") or {}),
                    "candidate_commit": receipt.candidate_commit,
                }
                return update
            result = run_candidate_gate(
                {**state, **update},
                failure=receipt.message,
                failure_outcome="timeout"
                if receipt.outcome == "timeout"
                else "dirty"
                if receipt.outcome == "dirty"
                else "error",
            )
        except GateError as exc:
            result = run_candidate_gate(state, failure=str(exc))
            update = {}
        return {
            **update,
            "gate_receipt": result.receipt,
            "test_results": result.as_test_results(),
            "status": Status.HALTED,
            "errors": [{"node": "auto_fix", "message": result.output}],
        }
    if fix is None:
        return {}
    worktree_path = state.get("worktree_path")
    unit = state.get("selected_unit")
    if not worktree_path or unit is None:
        return {}
    layer = unit.layers[0] if unit.layers else None
    try:
        base = None
        if state.get("prd") is not None:
            head = _git_run(worktree_path, "rev-parse", "HEAD")
            if head.returncode:
                raise PolicyViolation(f"cannot audit formatter: {head.stderr}")
            base = state.get("pre_implement_ref") or head.stdout.strip()
        fix(worktree_path, layer)
        if base:
            policy_for_state(state).audit(base)
    except PolicyViolation as exc:
        return {
            "status": Status.HALTED,
            "implement_error_kind": "policy_violation",
            "errors": [{"node": "auto_fix", "message": str(exc)}],
        }
    except GateError:
        # A missing/invalid toolchain is surfaced by the gate, not here — don't double-halt.
        pass
    return {}


def test_gate(state: BlacksmithState, *, gate: GateFn | None = None) -> dict:
    if state.get("status") == Status.HALTED:
        return {
            "status": Status.HALTED,
            "test_results": {"passed": False, "output": "attempt halted", "command": ""},
        }
    if gate is None and not state.get("run_context"):
        return {"status": Status.TESTING}  # skeleton pass-through
    worktree_path = state.get("worktree_path")
    unit = state.get("selected_unit")
    if not worktree_path or unit is None:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "test_gate", "message": "no worktree_path/selected_unit"}],
        }
    try:
        if state.get("run_context"):
            result = run_candidate_gate(state)
        else:
            layers = list(dict.fromkeys(unit.layers)) or [None]
            results = []
            for layer in layers:
                result = gate(worktree_path, layer)
                results.append(result)
                if not result.passed:
                    break
            result = GateResult(
                all(r.passed for r in results),
                "\n".join(r.output for r in results),
                " && ".join(r.command for r in results),
            )
    except ReceiptError:
        raise
    except Exception as exc:
        return {
            "status": Status.HALTED,
            "test_results": {"passed": False, "output": str(exc), "command": ""},
            "gate_receipt": None,
            "errors": [{"node": "test_gate", "message": str(exc)}],
        }
    impl = state.get("implementation") or {}
    update: dict = {"test_results": result.as_test_results(), "status": Status.TESTING}
    if result.receipt is not None:
        update["gate_receipt"] = result.receipt
        if result.receipt.outcome in {"error", "timeout", "dirty"}:
            update["status"] = Status.HALTED
    if not result.passed:
        # Name the failed unit so the halt message identifies which unit broke the run.
        update["errors"] = [{"node": "test_gate", "message": f"gate failed for unit {unit.id}"}]
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
        if result.receipt is not None:
            update["last_passing_commit"] = result.receipt.candidate_commit
        elif (Path(worktree_path) / ".git").exists():
            update["last_passing_commit"] = _git_run(
                worktree_path, "rev-parse", "HEAD"
            ).stdout.strip()
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
    if result.receipt is not None and update.get("unit_results"):
        update["unit_results"][0]["gate_receipt"] = result.receipt
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
    if state.get("run_context"):
        from blacksmith.budget import BudgetLedger
        return BudgetLedger(context_from_state(state)).balance().charged_usd
    return _events_cost(state.get("cost_events")) + float(state.get("run_cost_base") or 0)


def _within_budget(state: BlacksmithState) -> bool:
    """True if the run may still spend on recovery (WU-GATE-SELF-HEAL): no cap configured,
    or total spend so far is still under the configured ``max_run_cost_usd``."""
    if state.get("run_context"):
        from blacksmith.budget import BudgetLedger
        balance = BudgetLedger(context_from_state(state)).balance()
        return not balance.unacknowledged_overrun and (
            balance.remaining_usd is None or balance.remaining_usd > 0)
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


def _next_position(levels: list[list[WorkUnit]], level: int, index: int) -> tuple[int, int] | None:
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
        **_new_unit_state(state, levels[level][index], state.get("worktree_path")),
        "level_cursor": level,
        "unit_in_level": index,
        "selected_unit": levels[level][index],
    }


def _new_unit_state(state, unit, path):
    # Status-only legacy helpers use synthetic paths. Real/v2 Git failures stop.
    base = None
    if path and (state.get("run_context") or (Path(path) / ".git").exists()):
        base = _git_run(path, "rev-parse", "HEAD").stdout.strip()
    return new_unit_attempt(unit.id, base)


def _recovery_base(state):
    return (
        state.get("last_passing_commit")
        or state.get("attempt_base_commit")
        or state.get("pre_implement_ref")
    )


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
    ref = _recovery_base(state)
    if worktree_path and ref:
        _git_run(worktree_path, "reset", "--hard", ref)
        _git_run(worktree_path, "clean", "-fd")
    # This resets the worktree, so it is NOT a continuation of a partial attempt — clear the
    # transient resume flag so the escalated re-implement starts clean.
    return next_attempt(state, "escalate", base_commit=ref)


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
    ref = _recovery_base(state)
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
    return next_attempt(state, "fix", base_commit=ref, feedback=gate_output)


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
    return next_attempt(state, "continuation")


# --- post-gate review loop (WU-REVIEW-LOOP) -----------------------------------
# Additive, bounded loop on the test gate's PASS branch (never the FAILURE branch): a
# stronger model adversarially reviews the just-passed unit's diff (blacksmith.nodes.review,
# WU-REVIEW-NODE). A blocking finding revises the unit (feeding the findings back into the
# next implement prompt exactly as a fix-retry feeds back the gate output) and re-gates;
# exhausted blocking findings halt with artifacts retained. Unavailable review halts
# immediately and can be retried only through explicit recovery within allowance.


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
    result = _run_review({**state, "review_enabled": bool(state.get("review_enabled"))},
                         executor=executor, index_config=index_config)
    if "review_findings" in result:
        result = {**result, "review_current_findings": result["review_findings"]}
    return result


def _format_review_feedback(findings: list[dict]) -> str:
    """Render this call's BLOCKING findings as feedback text for the next implement
    prompt, kept separate from deterministic gate output."""
    blocking = [f for f in findings if f.get("severity") == "blocking"]
    lines = [f"- {f.get('file', '(unknown file)')}: {f.get('detail', '')}" for f in blocking]
    return (
        "A REVIEWER flagged blocking issue(s) in your PASSING diff. Fix the CODE (never "
        "weaken tests/assertions to silence the finding); the unit will be re-gated and "
        "re-reviewed after your fix:\n" + "\n".join(lines)
    )


def prepare_review_revision(state: BlacksmithState) -> dict:
    """A blocking review finding, with revisions remaining and within budget: feed the
    findings into the next implement prompt via ``review_feedback``, bump ``review_revisions``, and
    reset this unit's review verdict so the loop re-reviews after the next gate pass.

    Unlike ``prepare_fix_retry``/``prepare_escalation``, this does NOT reset the shared
    worktree: the unit already PASSED the test gate, so its passing commit is kept and the
    next implement attempt revises it in place, committing the fix as a further commit."""
    findings = state.get("review_current_findings") or []
    return {
        **next_attempt(
            state,
            "review",
            base_commit=_recovery_base(state),
            feedback=_format_review_feedback(findings),
        ),
        # Run-wide report-only tally for the "resolved via revision" line (reducer; the
        # per-unit review_revisions above is reset each unit, so it can't total the run).
        "review_revisions_total": 1,
    }


def finalize_review(state: BlacksmithState) -> dict:
    """Exhausted blocking review halts and retains the exact unresolved evidence."""
    findings = state.get("review_current_findings") or []
    blocking = [f for f in findings if f.get("severity") == "blocking"]
    return {"unresolved_review_findings": blocking, "status": Status.HALTED,
            "unit_failed_stage": "review",
            "errors": [{"node": "review", "message": "blocking review unresolved; "
                        "revision allowance or run budget exhausted"}]}


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
    """Legacy direct helper; production invokes the one shared compiled unit graph."""
    from blacksmith.unit_graph import build_unit_graph, unit_runner

    update = unit_runner(build_unit_graph(deps or UnitDeps(), worktree_manager))(state)
    return {**update, "level_builds": list(update["unit_outcomes"].values())}


def _git_run(cwd: str, *args: str):
    # Conflicts/fetch errors have explicit stage handling; all other failures halt.
    checked = args[0] not in {"fetch", "cherry-pick"} or args == ("cherry-pick", "--abort")
    result = run_command(["git", "-C", str(cwd), *args], check=checked)
    return result.require_complete_output()


def _cleanup_builds(builds: list[dict]) -> None:
    """Remove the per-unit build clones (best effort) once the level is joined."""
    for build in builds:
        path = build.get("clone_path")
        if path:
            shutil.rmtree(path, ignore_errors=True)


def join_level(state: BlacksmithState) -> dict:
    from blacksmith.joins import join_level as step
    return step(state)


def advance_joined_level(state: BlacksmithState) -> dict:
    from blacksmith.unit_graph import _result_view
    level = state.get("level_cursor", 0)
    levels = state.get("execution_levels") or []
    results = [_result_view(state["unit_outcomes"][u.id]) for u in levels[level]]
    update = {"unit_results": results, "level_cursor": level + 1, "status": Status.TESTING}
    if level + 1 < len(levels):
        unit = levels[level + 1][0]
        update.update(_new_unit_state(state, unit, state["worktree_path"]))
        update.update(selected_unit=unit, unit_in_level=0)
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

    Enabled sandbox managers are rejected before cleanup effects. Historical
    containers have no verified ownership evidence and are not automatically
    stopped/deleted by this disabled feature. Disabled/unwired managers are inert."""
    reject_enabled_sandbox(sandbox is not None and sandbox.config.enabled)
    if state.get("run_context") or state.get("status") == Status.HALTED:
        return {}  # Versioned runs retain every workspace; collection is explicit future work.
    if worktree_manager is None:
        return {}
    _cleanup_builds([row for row in (state.get("unit_outcomes") or {}).values()
                     if row.get("clone_path") != state.get("worktree_path")])
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


def route_after_plan(state: BlacksmithState) -> str:
    if state.get("status") == Status.HALTED:
        return "human_halt"
    return "plan" if state.get("planning_complete") is False else "approve_plan"


def route_after_approve_plan(state: BlacksmithState) -> str:
    """Proceed only on plan approval; a rejection halts (PRD §4)."""
    return ("prepare_worktree" if state.get("status") != Status.HALTED
            and state.get("approvals", {}).get("plan") else "human_halt")


def route_after_approve_pr(state: BlacksmithState) -> str:
    """Proceed only on PR approval; a rejection halts (PRD §5: never auto-merge)."""
    if state.get("status") == Status.HALTED or state.get("approvals", {}).get("pr") is not True:
        return "human_halt"
    return "open_draft_pr" if state.get("publication_draft") else "open_pr"


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
        return "final_gate"
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
    if state.get("status") == Status.HALTED:
        return "human_halt"
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
    Exhausted blockers route to finalize_review, which halts and preserves evidence.
    An unavailable result halts immediately without another reviewer call."""
    if state.get("status") == Status.HALTED:
        return "human_halt"
    receipt = state.get("review_receipt")
    if receipt is not None and receipt.status == "unavailable":
        return "human_halt"
    if state.get("review_clean") or (receipt and receipt.status == "disabled"):
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
            "execute_unit",
            {
                "prd": state.get("prd"),
                "state_version": state.get("state_version"),
                "recovery_version": state.get("recovery_version"),
                "run_id": state.get("run_id"),
                "run_context": state.get("run_context"),
                "selected_unit": unit,
                "selected_plan": selected_plan(state, unit),
                **{key: state[key] for key in (
                    "plans", "plans_by_unit_id", "planning_version", "planning_prd_sha256",
                    "plan_cursor", "planning_complete", "plan_approval", "approved_plans_sha256",
                    "approvals",
                ) if key in state},
                "unit_results": state.get("unit_results") or [],
                "planning_path": state.get("planning_path"),
                "level_cursor": level,
                "shared_clone_path": state.get("worktree_path"),
                "limits": state.get("limits"),
                "level_cost_base": _run_cost(state),
                # Thread the review switch + panel size through so the worker runs the post-gate
                # reviewer on its build clone (review finding #1); absent -> the worker skips
                # review, exactly as before this fix.
                "review_enabled": state.get("review_enabled"),
                "review_version": state.get("review_version"),
                "review_panel_size": state.get("review_panel_size"),
            },
        )
        for unit in levels[level]
    ]


def _route_to_level(state: BlacksmithState) -> str | list[Send]:
    """Build the level at ``level_cursor``: fan out (multi-unit + CloneManager), run the
    sequential ``implement`` path (size-1 level), or finish at ``approve_pr`` when the plan
    is exhausted. Shared by every level entry point so the decision lives in one place."""
    if state.get("status") == Status.HALTED:
        return "human_halt"
    from blacksmith.scheduling import validate_schedule
    validate_schedule(state)
    levels = state.get("execution_levels") or []
    # No level plan seeded (the skeleton / dependency-free graph tests, where
    # prepare_worktree is a pass-through): fall through to the sequential implement path
    # exactly as before — the level engine only engages once a plan exists.
    if not levels:
        return "execute_unit"
    level = state.get("level_cursor", 0)
    if level >= len(levels):
        return "final_gate"
    if state.get("prepared_level") != level:
        return "prepare_level"
    if _is_fanout_level(state, level):
        return _fanout_sends(state, level)
    return "execute_unit"


def route_after_prepare(state: BlacksmithState) -> str | list[Send]:
    """Enter the first level (or halt if prepare_worktree errored)."""
    if state.get("status") == Status.HALTED:
        return "human_halt"
    return _route_to_level(state)


def route_after_next_unit(state: BlacksmithState) -> str | list[Send]:
    """The sequential engine advanced to a new position; build it sequentially or, when it
    crossed into a multi-unit level under CloneManager, fan that level out instead."""
    return _route_to_level(state)


def route_after_join(state: BlacksmithState) -> str:
    if state.get("status") == Status.HALTED:
        return "human_halt"
    return "integration_gate" if state.get("join_complete") else "join_level"


def route_after_final_gate(state: BlacksmithState) -> str:
    if state.get("status") == Status.HALTED:
        return "human_halt"
    return "prepare_pr_approval"


def route_after_unit(state: BlacksmithState) -> str:
    """Parents select/consume outcomes; all attempt recovery lives in the child."""
    if state.get("status") == Status.HALTED:
        return "human_halt"
    if state.get("recovery_queue"):
        return "recovery_dispatch"
    if _is_fanout_level(state, state.get("level_cursor", 0)):
        return "join_level"
    unit = state.get("selected_unit")
    outcome = (state.get("unit_outcomes") or {}).get(unit.id if unit else "__none__")
    if not outcome or not outcome["ok"]:
        return "human_halt"
    if outcome["human"]:
        return "final_gate"
    next_node = _next_unit_or_approve(state)
    return "final_gate" if next_node == "approve_pr" else next_node


# --- assembly ----------------------------------------------------------------


def _open_pr_node(fn, pr_runner: Runner | None, executor: Executor | None = None, *, draft=False):
    """Bind a PR node's command runner (default subprocess; fake in tests) AND the run's
    executor at build time. ``executor`` (WU-PR-SUMMARY-WIRE) is the SAME instance already
    constructed by ``build_graph_for`` — no separate wiring — and drives the additive,
    fail-open title/summary synthesis inside ``_open_pr``. ``None`` (the default, every
    existing test) leaves both PR nodes byte-for-byte unchanged."""

    def node(state: BlacksmithState) -> dict:
        kwargs = {}
        if pr_runner is not None:
            kwargs["runner"] = pr_runner
        if executor is not None:
            kwargs["executor"] = executor
        with command_scope_for_state(state, fn.__name__):
            from blacksmith.publication_approval import ApprovalError, hold
            from blacksmith.publication_approval import verify as verify_approval
            try:
                verify_approval(state, draft=draft)
            except (ApprovalError, GateError, ContractError, RunContextError) as exc:
                return hold(state, exc, node=fn.__name__)
            if state.get("run_context") or state.get("state_version") is not None:
                from blacksmith.integration import final_gate, verify_final_gate
                try:
                    verify_final_gate(state)
                except ReceiptError:
                    raise
                except Exception as exc:
                    return final_gate(state, failure=exc)
            return fn(state, **kwargs)

    return node


def _node_with(fn, **injected):
    """Bind dependencies and per-node immutable command scope, including fan-out."""

    def node(state: BlacksmithState) -> dict:
        kwargs = injected if any(value is not None for value in injected.values()) else {}
        from blacksmith.run_clock import failure_scope
        with failure_scope():
            with command_scope_for_state(state, fn.__name__) as scope, failure_scope(scope.context):
                return fn(state, **kwargs)

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
    hitl: HitlConfig | None = None,
) -> StateGraph:
    """Construct (but do not compile) the v0 graph topology.

    ``limits`` enables the gate self-heal loop (WU-GATE-SELF-HEAL): when provided it is
    seeded into state by ``prepare_worktree`` so routing can bound the retries. Left unset
    (every graph wired without it) the loop is OFF and routing behaves exactly as before.

    ``review`` enables the additive post-gate review loop (WU-REVIEW-LOOP) the same way:
    when provided, ``config.review.enabled`` is seeded into state so ``route_after_test_gate``
    can route a PASS to ``review`` instead of straight to next_unit/approve_pr. Left unset
    (every graph wired without it) review records disabled without a model call.

    ``sandbox`` may be absent or disabled. An enabled manager is rejected before
    topology construction; no container/tool is started or advertised. Historical
    data remains inspectable, but enabling requires the deferred ownership work.

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
    the PR node passes no ``--base`` and gh falls back to the repo's own default.

    ``hitl`` wires the additive, opt-in combined-diff display (WU-PR-DIFF-CAPTURE) into
    ``prepare_worktree`` (to capture ``pr_base_ref``) and the ``approve_pr`` gate (to compute
    and surface the bounded ``diff_text``) the SAME opt-in-forwarding way as
    review/index/sandbox/sbfl/critic: when provided AND ``hitl.pr_diff_max_bytes > 0``, the
    gate's interrupt payload gains a ``diff_text`` key. Left unset (the default, every
    existing test) or with a zero byte ceiling, the payload is byte-for-byte unchanged --
    the approver's bool contract and routing are never touched either way."""
    reject_enabled_sandbox(sandbox is not None and sandbox.config.enabled)
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
            checkpointed=True,
            max_prd_bytes=limits.max_prd_bytes if limits is not None else 262144,
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
            hitl=hitl,
        ),
    )
    from blacksmith.unit_graph import build_unit_graph, unit_runner

    unit_deps = UnitDeps(
        executor=executor,
        gate=gate,
        fix=fix,
        index_config=index,
        sandbox=sandbox,
        sandbox_exec_timeout_s=sandbox.config.exec_timeout_s if sandbox is not None else None,
        sbfl=sbfl,
    )
    child = build_unit_graph(unit_deps, worktree_manager)
    graph.add_node("execute_unit", unit_runner(child))
    from blacksmith.recovery_control import dispatch, route_dispatch
    graph.add_node("recovery_dispatch", dispatch)
    graph.add_conditional_edges("recovery_dispatch", route_dispatch)
    graph.add_node("recovery_prepare", lambda state: {})
    graph.add_edge("recovery_prepare", "recovery_dispatch")
    from blacksmith.integration import final_gate, integration_gate
    from blacksmith.joins import prepare_level

    graph.add_node("prepare_level", _node_with(prepare_level))
    graph.add_node("join_level", _node_with(join_level))
    graph.add_node("integration_gate", _node_with(integration_gate, gate=gate))
    graph.add_node("advance_joined_level", _node_with(advance_joined_level))
    graph.add_node("final_gate", _node_with(final_gate, gate=gate))
    graph.add_node("prepare_pr_approval", _node_with(prepare_pr_approval, hitl=hitl))
    graph.add_node("next_unit", _node_with(next_unit))
    graph.add_node("approve_pr", _node_with(approve_pr, hitl=hitl))
    graph.add_node("open_pr", _open_pr_node(open_pr, pr_runner, executor))
    graph.add_node("open_draft_pr", _open_pr_node(open_draft_pr, pr_runner, executor, draft=True))
    graph.add_node("human_halt", human_halt)
    graph.add_node(
        "cleanup_worktree",
        _node_with(cleanup_worktree, worktree_manager=worktree_manager, sandbox=sandbox),
    )
    graph.add_edge(START, "ingest_prd")
    graph.add_conditional_edges(
        "ingest_prd", _route_or_halt("plan"), {"plan": "plan", "human_halt": "human_halt"},
    )
    graph.add_conditional_edges(
        "plan", route_after_plan,
        {"plan": "plan", "approve_plan": "approve_plan", "human_halt": "human_halt"},
    )
    graph.add_conditional_edges("approve_plan", route_after_approve_plan)
    graph.add_conditional_edges("prepare_worktree", route_after_prepare)
    graph.add_conditional_edges("prepare_level", route_after_prepare)
    graph.add_conditional_edges("execute_unit", route_after_unit)
    graph.add_conditional_edges("next_unit", route_after_next_unit)
    graph.add_conditional_edges(
        "join_level", route_after_join,
        {"join_level": "join_level", "integration_gate": "integration_gate",
         "human_halt": "human_halt"},
    )
    graph.add_conditional_edges("integration_gate", _route_or_halt("advance_joined_level"))
    graph.add_conditional_edges("advance_joined_level", route_after_next_unit)
    graph.add_conditional_edges(
        "final_gate", route_after_final_gate,
        {"prepare_pr_approval": "prepare_pr_approval", "human_halt": "human_halt"},
    )
    graph.add_conditional_edges("prepare_pr_approval", _route_or_halt("approve_pr"),
                                {"approve_pr": "approve_pr", "human_halt": "human_halt"})
    graph.add_conditional_edges(
        "approve_pr",
        route_after_approve_pr,
        {"open_pr": "open_pr", "open_draft_pr": "open_draft_pr", "human_halt": "human_halt"},
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
    from blacksmith.review_receipts import PanelResult, ReviewPatch, ReviewReceipt, ValidatedFinding
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            Status,
            PRD,
            PRDContract,
            WorkUnit,
            GateReceipt,
            FixReceipt,
            GateCommandReceipt,
            GateLayerReceipt,
            ReviewReceipt,
            ReviewPatch,
            PanelResult,
            ValidatedFinding,
        ]
    )


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
    hitl: HitlConfig | None = None,
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
    default) records disabled review without admitting reviewer calls.

    ``sandbox`` must be absent or disabled. Enabled managers reject before graph
    construction; no container is started/stopped and no execution tool is granted.

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

    ``hitl`` wires the additive, opt-in combined-diff display (WU-PR-DIFF-CAPTURE) into
    ``prepare_worktree``/``approve_pr``; ``None`` (the default) or one with
    ``pr_diff_max_bytes == 0`` leaves the gate's interrupt payload byte-for-byte unchanged
    -- the approver's bool contract and routing are never touched either way.
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
        hitl=hitl,
    ).compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before),
        store=store,
    ).with_config(
        max_concurrency=(limits or LimitsConfig()).max_parallel_units,
        configurable={"blacksmith_limits": (limits or LimitsConfig()).model_dump()},
    )
