"""Plan node — select the next work unit and plan it (PRD §4 node 2).

v0 selects exactly one unit: the lowest in the dependency DAG with no unmet deps
(``select_unit``). It then asks the executor (plan model tier, PRD §8) to produce an
implementation plan for that unit, with the untouchables in the system prompt as the
static constitution context.

Without an executor wired (skeleton/tests), the node is a pass-through that only
advances ``status`` — the real decomposition runs only when an executor is injected
at graph-build time. This keeps the deterministic graph tests independent of any
model call.
"""

from __future__ import annotations

from collections.abc import Sequence

from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.state import BlacksmithState, Status

# Planning is read-only reasoning: give it a turn budget (the agentic SDK rarely
# finishes in one) but block all writes — at plan time there is no worktree yet, so
# the agent's cwd would be blacksmith's own repo.
_PLAN_MAX_TURNS = 8
_PLAN_READ_ONLY = ["Read", "Glob", "Grep"]
_PLAN_BLOCKED = ["Write", "Edit", "Bash"]  # known write/exec tool names


def select_unit(contract: PRDContract, completed: Sequence[str] = ()) -> WorkUnit | None:
    """The lowest unit (declaration/topological order) whose deps are all completed."""
    done = set(completed)
    for unit in contract.work_units:
        if unit.id not in done and all(dep in done for dep in unit.depends_on):
            return unit
    return None


def plan(state: BlacksmithState, *, executor: Executor | None = None) -> dict:
    if executor is None:
        return {"status": Status.AWAITING_PLAN_APPROVAL}  # skeleton pass-through

    prd = state.get("prd")
    if prd is None:
        return {"status": Status.HALTED, "errors": [{"node": "plan", "message": "no prd in state"}]}

    unit = select_unit(prd.contract)
    if unit is None:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "plan", "message": "no work unit with satisfied dependencies"}],
        }

    result = executor.run_plan(
        _plan_prompt(unit),
        system_prompt=_system_prompt(prd.contract),
        allowed_tools=_PLAN_READ_ONLY,
        disallowed_tools=_PLAN_BLOCKED,
        max_turns=_PLAN_MAX_TURNS,
    )
    return {
        "work_units": list(prd.contract.work_units),
        "selected_unit": unit,
        "plan": {
            "unit_id": unit.id,
            "title": unit.title,
            "target_modules": list(unit.target_modules),
            "test_contract": unit.test_contract,
            "steps": result.text,
        },
        "status": Status.AWAITING_PLAN_APPROVAL,
    }


def _plan_prompt(unit: WorkUnit) -> str:
    return (
        "Produce a concise, step-by-step implementation plan for this work unit. "
        "List the steps only — do not write code yet.\n\n"
        f"Unit {unit.id}: {unit.title}\n"
        f"Layers: {', '.join(unit.layers)}\n"
        f"Target modules: {', '.join(unit.target_modules)}\n"
        f"Test contract (must be satisfied): {unit.test_contract}"
    )


def _system_prompt(contract: PRDContract) -> str:
    untouchables = "\n".join(f"- {item}" for item in contract.untouchables)
    return (
        f"You are blacksmith's planner for the {contract.component} project "
        f"({contract.primary_target_repo}). Plan precisely and minimally.\n\n"
        "CONSTITUTION — these are inviolable; never plan work that touches them without "
        f"explicit human sign-off:\n{untouchables}"
    )
