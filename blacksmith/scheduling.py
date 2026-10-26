"""Deterministic scheduling projections and finite invocation bounds.

Dependency frontiers remain a contract property. Execution batches additionally
serialize potentially overlapping target subtrees without adding dependency edges.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from unicodedata import normalize

from blacksmith.config import LimitsConfig
from blacksmith.contract import ContractError, PRDContract, WorkUnit, parse_prd
from blacksmith.planner import execution_levels
from blacksmith.receipts import ReceiptError
from blacksmith.run_context import context_from_state


def _target_root(target: str) -> tuple[str, ...]:
    # Treat a pattern's containing subtree as its whole possible write set.
    # Unknown/root patterns therefore serialize conservatively, never prove disjointness.
    parts = PurePosixPath(target).parts
    literal = []
    for part in parts:
        if any(char in part for char in "*?["):
            break
        literal.append(normalize("NFD", part).casefold())
    return tuple(literal)


def _roots(unit: WorkUnit, root: Path | None):
    for target in unit.target_modules:
        parts = _target_root(target)
        yield parts
        if root is not None:
            # Resolve only the literal pattern prefix against the frozen planning
            # tree. Internal symlink aliases cannot be mistaken for disjoint targets.
            literal = []
            for part in PurePosixPath(target).parts:
                if any(char in part for char in "*?["):
                    break
                literal.append(part)
            resolved = root.joinpath(*literal).resolve()
            yield tuple(normalize("NFD", part).casefold() for part in resolved.parts)


def targets_overlap(left: WorkUnit, right: WorkUnit, *, root: Path | None = None) -> bool:
    for a in _roots(left, root):
        for b in _roots(right, root):
            if a[:len(b)] == b or b[:len(a)] == a:
                return True
    return False


def execution_batches(
    contract: PRDContract, *, root: Path | None = None,
) -> list[list[WorkUnit]]:
    """Keep every dependency barrier; conflicting declarations run in order.

    A disjoint unit can share a batch with earlier units even when an intervening
    declaration must wait. Every conflicting pair has a strict earlier/later batch.
    The immutable contract, dependencies and planner frontiers are never changed.
    """
    result = []
    for frontier in execution_levels(contract):
        batches: list[list[WorkUnit]] = []
        placed: list[tuple[WorkUnit, int]] = []
        for unit in frontier:
            index = max((batch + 1 for prior, batch in placed
                         if targets_overlap(prior, unit, root=root)), default=0)
            while len(batches) <= index:
                batches.append([])
            batches[index].append(unit)
            placed.append((unit, index))
        result.extend(batches)
    return result


def validate_schedule(state) -> None:
    if not state.get("run_context") or not state.get("worktree_path"):
        return
    if state.get("scheduling_version") != 1:
        raise ReceiptError(
            "legacy_scheduling_requires_manual_recovery: bounded execution batches "
            "absent; checkpoints/artifacts/budgets retained")
    context = context_from_state(state)
    expected = execution_batches(context.prd().contract, root=context.planning_path)
    if state.get("execution_levels") != expected:
        raise ReceiptError("execution batches differ from immutable contract scheduling")


def invocation_config(state, *, fallback_limits=None) -> dict:
    """Read saved limits and unit count afresh for each parent/child invocation."""
    if state.get("run_context"):
        context = context_from_state(state)
        limits = context.execution_config().limits
        count = len(context.prd().contract.work_units)
    else:
        limits = LimitsConfig.model_validate(state.get("limits") or fallback_limits or {})
        prd = state.get("prd")
        if prd is None and state.get("prd_path"):
            try:
                prd = parse_prd(state["prd_path"])
            except ContractError:
                # The normal ingest node retains its existing invalid-input halt.
                # No unit/model work can run under this bootstrap-only allowance.
                prd = None
        count = len(prd.contract.work_units) if prd else max(1, len(state.get("work_units") or []))
    return {
        "max_concurrency": limits.max_parallel_units,
        "recursion_limit": 100 + 20 * count * (
            1 + limits.max_fix_attempts + 1 + limits.max_review_revisions
        ) * (1 + limits.max_implement_continuations),
    }


@contextmanager
def node_execution(config):
    """Bound node work without starving LangGraph's concurrent-stream waiter.

    LangGraph1.2.9 submits its custom-stream waiter to the same background pool as
    node tasks. A separate owned node pool preserves all configured unit slots and
    allows max_parallel_units=1 to make progress. Queued nodes cancel on exit;
    running nodes finish cancellation/cleanup before the caller can capture work.
    """
    from langgraph._internal._constants import CONFIG_KEY_RUNNER_SUBMIT
    from langgraph.pregel._executor import BackgroundExecutor

    with BackgroundExecutor(config) as submit:
        def owned_submit(fn, *args, **kwargs):
            kwargs["__cancel_on_exit__"] = True
            return submit(fn, *args, **kwargs)

        # Coordination needs one streaming waiter plus a checkpoint-I/O slot.
        # The independent node executor above still has exactly the configured cap.
        yield {**config, "max_concurrency": max(2, config["max_concurrency"]), "configurable": {
            **config.get("configurable", {}),
            CONFIG_KEY_RUNNER_SUBMIT: lambda: owned_submit,
        }}
