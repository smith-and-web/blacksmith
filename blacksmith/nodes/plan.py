"""Plan node — plan every auto-gated work unit up front (PRD §4 node 2).

Produces an implementation plan for EACH auto-gated unit (WU-PLAN-ALL-UNITS), so a
multi-unit PRD surfaces a plan for ALL of its units behind the single ``approve_plan``
gate — not just the first (the prior behaviour, which left units 2..N building with no
plan and no approval, undercutting the plan gate for multi-unit PRDs). Human-gated units
are skipped: they get manual QA via a draft PR, so no implementation plan is needed (this
mirrors ``route_after_implement``). Each plan call uses the executor's plan model tier
(PRD §8) with the untouchables in the system prompt as the static constitution context;
that prompt is built ONCE and reused across the per-unit calls so it caches (AC-8).

``select_unit`` remains the DAG cycle/unsatisfiable guard (and is reused by the planner).

When ``config.index.enabled`` (WU-PLAN-REPO-MAP), the SAME shared system prompt also gets
a repo map of the target repo (``blacksmith.index.build_repo_map``, reused verbatim from
the codebase-indexing PRD) built ONCE — not once per unit — and injected as static
context, so it caches across the per-unit calls exactly like the constitution/lessons
sections above. Off by default; disabled the system prompt is byte-for-byte unchanged.

The SAME ``config.index.enabled`` switch (WU-PLAN-SEARCH-TOOL) also grants the plan tier
the ``search_code`` tool (the same in-process MCP tool built over ``index.search_code``
for the implementer) alongside its existing read-only Read/Glob/Grep, so a plan session
can find symbols/usages in one indexed call instead of many greps. No separate toggle,
no new write/shell tool, and the tool is only wired when a target ``repo_path`` is given
(no worktree exists yet at plan time). Disabled, the plan tool surface is byte-for-byte
unchanged.

Without an executor wired (skeleton/tests), the node is a pass-through that only advances
``status`` — the real decomposition runs only when an executor is injected at graph-build
time. This keeps the deterministic graph tests independent of any model call.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from blacksmith.config import IndexConfig
from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.index import SEARCH_CODE_TOOL_NAME, build_repo_map, create_index_mcp_server
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
# The planner is read-only. Block writes/exec AND sub-agent spawning: an unblocked ``Agent``
# let the planner delegate to sub-agents that re-explore the repo on their own — blind to the
# repo map / search_code index, inflating turns and cost (observed: 5 Agent calls in a 133-turn
# plan). Mirrors the implementer's disallowed set (minus MultiEdit, which the SDK no longer
# knows — see the review node). Keeps Read/Glob/Grep + the index tool as the only surface.
_PLAN_BLOCKED = [
    "Write", "Edit", "NotebookEdit",
    "Bash", "BashOutput", "KillShell", "Agent", "Task", "ToolSearch", "WebSearch", "WebFetch",
]

# search_code tool (WU-PLAN-SEARCH-TOOL): ADDITIVE and off by default, gated on the SAME
# [index].enabled switch as the implementer indexing (blacksmith.nodes.implement) -- no
# separate toggle. Only when the index is enabled AND a repo_path is given (the target
# repo -- no worktree exists yet at plan time) is the planner granted the search_code tool
# alongside its existing read-only Read/Glob/Grep; no write/shell tool is ever added, and
# with the index disabled the tool surface is byte-for-byte unchanged from before this unit.
_INDEX_SERVER_NAME = "blacksmith-index"  # must match blacksmith.index's server name
_SEARCH_TOOL_NAME = f"mcp__{_INDEX_SERVER_NAME}__{SEARCH_CODE_TOOL_NAME}"


def select_unit(contract: PRDContract, completed: Sequence[str] = ()) -> WorkUnit | None:
    """The lowest unit (declaration/topological order) whose deps are all completed."""
    done = set(completed)
    for unit in contract.work_units:
        if unit.id not in done and all(dep in done for dep in unit.depends_on):
            return unit
    return None


def plan(
    state: BlacksmithState,
    *,
    executor: Executor | None = None,
    index_config: IndexConfig | None = None,
    repo_path: str | Path | None = None,
) -> dict:
    if executor is None:
        return {"status": Status.AWAITING_PLAN_APPROVAL}  # skeleton pass-through

    prd = state.get("prd")
    if prd is None:
        return {"status": Status.HALTED, "errors": [{"node": "plan", "message": "no prd in state"}]}

    contract = prd.contract
    # Cycle / unsatisfiable-DAG guard (preserved from the single-unit selector): if no unit is
    # ever buildable, halt here rather than producing plans for an unexecutable PRD.
    if select_unit(contract) is None:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "plan", "message": "no work unit with satisfied dependencies"}],
        }

    # Plan EVERY auto-gated unit, in declaration (topological) order. Human-gated units are
    # skipped — they build for manual QA behind a draft PR, no implementation plan needed.
    auto_units = [u for u in contract.work_units if contract.gate_for(u) != "human"]
    # Built ONCE and reused across the per-unit calls so the static constitution/lessons/repo-map
    # context caches across them (AC-8) instead of being re-sent (or rebuilt) for each unit.
    system_prompt = _system_prompt(
        contract, _prior_lessons(contract), _build_repo_map(repo_path, index_config)
    )
    # search_code tool wiring (WU-PLAN-SEARCH-TOOL): built ONCE and reused across the
    # per-unit calls below, exactly like system_prompt above -- not once per unit.
    index_enabled = bool(index_config is not None and index_config.enabled and repo_path)
    allowed_tools = list(_PLAN_READ_ONLY)
    mcp_servers: dict = {}
    if index_enabled:
        allowed_tools.append(_SEARCH_TOOL_NAME)
        mcp_servers[_INDEX_SERVER_NAME] = create_index_mcp_server(
            repo_path, exclude=index_config.exclude
        )
    plans: list[dict] = []
    cost_events: list[dict] = []
    for unit in auto_units:
        call_kwargs: dict = {
            "system_prompt": system_prompt,
            "allowed_tools": allowed_tools,
            "disallowed_tools": _PLAN_BLOCKED,
            "max_turns": _PLAN_MAX_TURNS,
            "raise_on_error": False,  # surface failures (e.g. max-turns) into state, don't crash
        }
        if mcp_servers:
            call_kwargs["mcp_servers"] = mcp_servers
        result = executor.run_plan(_plan_prompt(unit), **call_kwargs)
        # Ledger this attempt's spend on EVERY return (including the halt below), so a plan that
        # fails partway still counts the calls it made.
        cost_events.append(cost_event("plan", unit.id, result))
        if result.is_error:
            return {
                "status": Status.HALTED,
                "cost_events": cost_events,
                "errors": [
                    {"node": "plan", "message": f"plan call failed for {unit.id}: {result.text}"}
                ],
            }
        plans.append(
            {
                "unit_id": unit.id,
                "title": unit.title,
                "layers": list(unit.layers),
                "target_modules": list(unit.target_modules),
                "test_contract": unit.test_contract,
                "steps": result.text,
                "cost_usd": result.cost_usd,
                "usage": usage_breakdown(result.usage),
            }
        )
    return {
        "work_units": list(contract.work_units),
        # Representative selection (prepare_worktree recomputes the real one from the level plan).
        "selected_unit": auto_units[0] if auto_units else select_unit(contract),
        "plans": plans,
        "cost_events": cost_events,
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


def _build_repo_map(
    repo_path: str | Path | None, index_config: IndexConfig | None
) -> str | None:
    """Build the repo map from the TARGET repo if the index is enabled, else ``None``
    (WU-PLAN-REPO-MAP).

    ADDITIVE and off by default: with no ``index_config`` (or ``enabled=False``, the
    default) this never calls into ``blacksmith.index``, so the disabled path has zero
    behavioural change. Unlike the implement node (which maps the run's worktree), this
    maps ``repo_path`` — the target repo itself — directly, since no worktree exists yet
    at plan time; ``build_repo_map`` is read-only, so this never writes the target repo.
    """
    if index_config is None or not index_config.enabled or not repo_path:
        return None
    return (
        build_repo_map(
            repo_path, max_bytes=index_config.max_map_bytes, exclude=index_config.exclude
        )
        or None
    )


def _system_prompt(
    contract: PRDContract,
    lessons: list[dict] | None = None,
    repo_map: str | None = None,
) -> str:
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
    if repo_map:
        prompt += (
            "\n\nREPO MAP — a structural outline of the target repository (tracked files "
            "and their top-level symbols, WU-CODE-INDEX), given up front as static context "
            "so you can orient before exploring:\n"
            f"{repo_map}\n\n"
            "USE THE INDEX FIRST. You have a `search_code` tool that returns ranked symbol "
            "definitions and usages from this repo in ONE call. To locate code, prefer the map "
            "above and `search_code` over Read/Glob/Grep — reach for Grep/Glob only when the "
            "index doesn't answer the question. This is exactly what the index exists to "
            "replace: scanning the repo with dozens of greps is slow and expensive."
        )
    return prompt
