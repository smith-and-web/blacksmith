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
from typing import Any

from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.memory import current_store, recent_lessons, repo_namespace
from blacksmith.state import BlacksmithState, Status

# How many prior gate-failure lessons to surface to the planner (most recent first).
_LESSONS_LIMIT = 5


def usage_breakdown(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """Per-call token breakdown from ``ExecutorResult.usage`` (WU-COST-INSTRUMENT).

    Pulls the four figures the run report cares about — uncached ``input_tokens``,
    ``output_tokens`` and the two cache counters — so the node can persist them next to
    the ``cost_usd`` it already stores. Returns ``None`` when no usage is reported (no
    executor wired, or a call that returned no usage), so the report degrades to
    "tokens: unavailable" rather than crashing.
    """
    if not usage:
        return None
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
    }


def cost_event(node: str, unit_id: str, result: Any) -> dict:
    """One append-only ledger record for a single model call (WU-COST-EVENTS).

    Captures the spend/usage of THIS call — ``cost_usd``, ``num_turns`` (persisted here,
    as it is not stored in any state slice today) and the per-call usage breakdown — keyed
    by the ``node`` that made the call and the ``unit_id`` it was working on. Appended to
    ``state["cost_events"]`` so a multi-unit run's report sums every call rather than only
    the last unit's last-write-wins slice. ``usage`` is ``None`` when the call reported no
    usage (handled as zeros by the report), so a missing usage never crashes the run.

    ``session_id`` is the per-call session id (a small REFERENCE, never transcript content):
    it lets a run later locate the call's ``<transcripts_dir>/<session_id>.jsonl`` file
    (WU-TRANSCRIPT-CAPTURE). ``None`` when the call reported no session id.
    """
    return {
        "node": node,
        "unit_id": unit_id,
        "model": result.model,
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "usage": usage_breakdown(result.usage),
        "session_id": result.session_id,
    }

# Planning is read-only reasoning: give it a turn budget (the agentic SDK rarely
# finishes in one) but block all writes — at plan time there is no worktree yet, so
# the agent's cwd would be blacksmith's own repo. 8 was too tight for PRDs that span
# large files (cli.py + state.py + resume/checkpointer reading) — the planner ran out
# mid-exploration and the SDK raised "Reached maximum number of turns". Read-only
# Sonnet turns are cheap, so the budget is generous.
_PLAN_MAX_TURNS = 20
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
        system_prompt=_system_prompt(prd.contract, _prior_lessons(prd.contract)),
        allowed_tools=_PLAN_READ_ONLY,
        disallowed_tools=_PLAN_BLOCKED,
        max_turns=_PLAN_MAX_TURNS,
        raise_on_error=False,  # surface failures (e.g. max-turns) into state, don't crash the graph
    )
    if result.is_error:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "plan", "message": f"plan call failed: {result.text}"}],
        }
    return {
        "work_units": list(prd.contract.work_units),
        "selected_unit": unit,
        "plan": {
            "unit_id": unit.id,
            "title": unit.title,
            "target_modules": list(unit.target_modules),
            "test_contract": unit.test_contract,
            "steps": result.text,
            "cost_usd": result.cost_usd,
            "usage": usage_breakdown(result.usage),
        },
        "cost_events": [cost_event("plan", unit.id, result)],
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


def _prior_lessons(contract: PRDContract) -> list[dict]:
    """Recent gate-failure lessons for this repo, or ``[]`` when no store is configured.

    Memory is optional: with no Store bound (``current_store()`` returns ``None``) or an
    empty Store, this returns ``[]`` and the system prompt is byte-for-byte what it was
    before this unit existed.
    """
    store = current_store()
    if store is None:
        return []
    return recent_lessons(store, repo_namespace(contract), _LESSONS_LIMIT)


def _render_lesson(lesson: dict) -> str:
    files = ", ".join(lesson.get("files_touched") or []) or "—"
    return (
        f"- [{lesson.get('unit_id', '?')}] {lesson.get('title', '')}: "
        f"{lesson.get('reason', '')} (files touched: {files})"
    )


def _system_prompt(contract: PRDContract, lessons: list[dict] | None = None) -> str:
    untouchables = "\n".join(f"- {item}" for item in contract.untouchables)
    prompt = (
        f"You are blacksmith's planner for the {contract.component} project "
        f"({contract.primary_target_repo}). Plan precisely and minimally.\n\n"
        "CONSTITUTION — these are inviolable; never plan work that touches them without "
        f"explicit human sign-off:\n{untouchables}"
    )
    if lessons:
        rendered = "\n".join(_render_lesson(lesson) for lesson in lessons)
        prompt += (
            "\n\nPRIOR LESSONS ON THIS REPO — gate failures from earlier runs against "
            "this repo; learn from them and avoid repeating these mistakes:\n"
            f"{rendered}"
        )
    return prompt
