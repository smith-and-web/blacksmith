"""Human-in-the-loop interrupt nodes — the plan and PR approval gates (PRD §4).

``approve_plan`` and ``approve_pr`` are real LangGraph interrupt nodes. Each surfaces
a payload (the plan; or the implementation diff + test results) to the caller via
``interrupt()``, halts with state preserved by the checkpointer, and resumes when an
approval decision is injected with ``Command(resume=...)``. Approval proceeds; a
rejection is recorded and the graph routes to ``human_halt`` — the gate never
auto-proceeds on a "no" (PRD §5: PRs are never auto-merged).

The injected resume value is a bool (the CLI prompt in WU-11 sends y/n); a
``{"approved": bool}`` mapping is also accepted.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from blacksmith.state import BlacksmithState, Status


def approve_plan(state: BlacksmithState) -> dict:
    decision = _decide(
        {
            "gate": "plan",
            "unit": _unit_summary(state.get("selected_unit")),
            "plan": state.get("plan"),
        }
    )
    approvals = {**state.get("approvals", {}), "plan": decision}
    if not decision:
        return {"approvals": approvals, "status": Status.HALTED}
    return {"approvals": approvals}


def approve_pr(state: BlacksmithState) -> dict:
    decision = _decide(
        {
            "gate": "pr",
            "unit": _unit_summary(state.get("selected_unit")),
            "implementation": state.get("implementation"),
            "test_results": state.get("test_results"),
        }
    )
    approvals = {**state.get("approvals", {}), "pr": decision}
    if not decision:
        return {"approvals": approvals, "status": Status.HALTED}
    return {"approvals": approvals}


def _decide(payload: dict[str, Any]) -> bool:
    """Halt at the gate, surfacing ``payload``; return the injected approval as a bool."""
    answer = interrupt(payload)
    if isinstance(answer, dict):
        return bool(answer.get("approved"))
    return bool(answer)


def _unit_summary(unit: Any) -> dict[str, Any] | None:
    if unit is None:
        return None
    return {"id": unit.id, "title": unit.title, "layers": list(unit.layers)}
