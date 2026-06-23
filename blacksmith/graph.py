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

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from blacksmith.contract import PRD, ContractError, PRDContract, WorkUnit, parse_prd
from blacksmith.executor import Executor
from blacksmith.gate import GateError, GateResult
from blacksmith.nodes.hitl import approve_plan, approve_pr
from blacksmith.nodes.implement import implement
from blacksmith.nodes.plan import plan
from blacksmith.nodes.pr import Runner, open_draft_pr, open_pr
from blacksmith.planner import execution_levels
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

# The run's isolation manager. In production this is a CloneManager (each run gets a
# throwaway local clone with its own .git, which kills the self-targeting hazard — the
# agent can never reach the real checkout), but a WorktreeManager (the prior linked-
# worktree model) is still accepted and used by some tests. Both expose the surface the
# graph needs — ``create(unit_id) -> obj(path, branch, repo_path)`` plus ``repo_path`` —
# so prepare_worktree is identical for either; only cleanup_worktree differs.
IsolationManager = WorktreeManager | CloneManager

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
    state: BlacksmithState, *, worktree_manager: IsolationManager | None = None
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
    model)."""
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
    }
    if levels:
        update["execution_levels"] = levels
        update["work_units"] = units
    return update


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
    update: dict = {"test_results": result.as_test_results(), "status": Status.TESTING}
    if not result.passed:
        # Name the failed unit so the halt message identifies which unit broke the run.
        update["errors"] = [
            {"node": "test_gate", "message": f"gate failed for unit {unit.id}"}
        ]
    else:
        # Retain this unit's own result so the combined PR body can summarize each unit's
        # changes. ``implementation`` is last-write-wins (only the latest unit), so capture
        # the per-unit record here and append it via the unit_results reducer.
        impl = state.get("implementation") or {}
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
        "status": Status.IMPLEMENTING,
    }


def human_halt(state: BlacksmithState) -> dict:
    return {"status": Status.HALTED}


def cleanup_worktree(
    state: BlacksmithState, *, worktree_manager: IsolationManager | None = None
) -> dict:
    """Remove the run's isolation directory on a terminal path (PRD §5). Cleanup must
    never fail the run.

    For a CloneManager the clone owns its own .git, so removing the directory is total
    cleanup — there is no source-repo branch to keep or delete (the branch lives only in
    the clone and, once pushed, on the real remote). For the legacy WorktreeManager the
    branch lives in the shared source repo, so the worktree's branch is kept when a PR
    was opened (the PR needs it) and deleted otherwise so re-runs don't collide."""
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

    A failed implement (status HALTED) discards the work via ``human_halt``. A human-gated
    unit (any ``human`` layer) that implemented successfully bypasses the automated gate and
    instead opens a DRAFT PR for manual QA (``open_draft_pr`` -> AWAITING_QA, branch kept).
    An auto-gated unit proceeds to the automated ``test_gate`` unchanged."""
    if state.get("status") == Status.HALTED:
        return "human_halt"
    prd = state.get("prd")
    unit = state.get("selected_unit")
    if prd is not None and unit is not None and prd.contract.gate_for(unit) == "human":
        return "open_draft_pr"
    return "test_gate"


def route_after_test_gate(state: BlacksmithState) -> str:
    """Deterministic routing on the test result — a graph edge, not a model decision.

    A failed gate halts. On a pass, loop to ``next_unit`` while units remain in the level
    plan (shared branch); on the last unit of the last level, proceed to the single
    PR-approval gate."""
    results = state.get("test_results") or {}
    if not results.get("passed"):
        return "human_halt"
    levels = state.get("execution_levels") or []
    nxt = _next_position(levels, state.get("level_cursor", 0), state.get("unit_in_level", 0))
    return "next_unit" if nxt is not None else "approve_pr"


# --- assembly ----------------------------------------------------------------


def _open_pr_node(fn, pr_runner: Runner | None):
    """Bind a PR node's command runner at build time (default subprocess; fake in tests)."""
    if pr_runner is None:
        return fn

    def node(state: BlacksmithState) -> dict:
        return fn(state, runner=pr_runner)

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
) -> StateGraph:
    """Construct (but do not compile) the v0 graph topology."""
    graph = StateGraph(BlacksmithState)

    graph.add_node("ingest_prd", ingest_prd)
    graph.add_node("plan", _node_with(plan, executor=executor))
    graph.add_node("approve_plan", approve_plan)
    graph.add_node(
        "prepare_worktree", _node_with(prepare_worktree, worktree_manager=worktree_manager)
    )
    graph.add_node("implement", _node_with(implement, executor=executor))
    graph.add_node("test_gate", _node_with(test_gate, gate=gate))
    graph.add_node("next_unit", next_unit)
    graph.add_node("approve_pr", approve_pr)
    graph.add_node("open_pr", _open_pr_node(open_pr, pr_runner))
    graph.add_node("open_draft_pr", _open_pr_node(open_draft_pr, pr_runner))
    graph.add_node("human_halt", human_halt)
    graph.add_node(
        "cleanup_worktree", _node_with(cleanup_worktree, worktree_manager=worktree_manager)
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
        _route_or_halt("implement"),
        {"implement": "implement", "human_halt": "human_halt"},
    )
    graph.add_conditional_edges(
        "implement",
        route_after_implement,
        {
            "test_gate": "test_gate",
            "human_halt": "human_halt",
            "open_draft_pr": "open_draft_pr",
        },
    )
    graph.add_conditional_edges(
        "test_gate",
        route_after_test_gate,
        {
            "approve_pr": "approve_pr",
            "human_halt": "human_halt",
            "next_unit": "next_unit",
        },
    )
    # Loop: the next unit is built on the same shared worktree/branch (no re-plan, no
    # new worktree), so its implement step sees the prior units' committed changes.
    graph.add_edge("next_unit", "implement")
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
) -> CompiledStateGraph:
    """Compile the graph with a checkpointer.

    The HITL halts come from dynamic ``interrupt()`` calls inside ``approve_plan`` /
    ``approve_pr`` (WU-07), so no static ``interrupt_before`` is needed by default;
    the parameter remains for tests or extra inspection points. The dependency params
    (``executor``, ``worktree_manager``, ``gate``, ``pr_runner``) are injected by the
    CLI in production and faked in tests; unset ones leave that node a pass-through.
    """
    return build_graph(
        pr_runner=pr_runner,
        executor=executor,
        worktree_manager=worktree_manager,
        gate=gate,
    ).compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before),
    )
