"""One pure initialization/reset contract for sequential and parallel unit state."""

from __future__ import annotations

from typing import Literal

from blacksmith.receipts import encoded
from blacksmith.state import Status, UnitAttemptState

CONTINUATION_FEEDBACK = (
    "Your previous attempt ran out of turns. Retain its saved partial changes, read the "
    "target modules, and finish the remaining work within the fresh turn budget."
)


def _identity(state):
    # Preserve S04's call/commit identities for checkpoints created before this slice.
    return encoded(
        [
            state["fix_attempts"],
            state["escalated"],
            state["review_revisions"],
            state["implement_continuations"],
        ]
    )


def new_unit_attempt(unit_id: str, base_commit: str | None) -> UnitAttemptState:
    """Return fresh unit-owned values; never accept a parent's transient fields."""
    if not unit_id.strip():
        raise ValueError("unit identity cannot be empty")
    state: UnitAttemptState = {
        "current_unit_id": unit_id,
        "unit_base_commit": base_commit,
        "attempt_base_commit": base_commit,
        "last_passing_commit": None,
        "attempt_id": "",
        "attempt_number": 1,
        "model_tier": "initial",
        "fix_attempts": 0,
        "implement_continuations": 0,
        "review_revisions": 0,
        "last_gate_output": "",
        "review_feedback": "",
        "continuation_feedback": "",
        "gate_receipt": None,
        "fix_receipt": None,
        "review_receipt": None,
        "test_results": None,
        "implementation": {},
        "pending_implementation": None,
        "pre_implement_ref": None,
        "escalated": False,
        "implement_error_kind": "",
        "resume_partial_implement": False,
        "review_clean": False,
        "review_current_findings": [],
        "status": Status.IMPLEMENTING,
    }
    state["attempt_id"] = _identity(state)
    return state


def next_attempt(
    state,
    kind: Literal["fix", "escalate", "continuation", "review"],
    *,
    base_commit=None,
    feedback=None,
) -> UnitAttemptState:
    """Advance an attempt without carrying an earlier candidate's passing evidence."""
    if kind not in {"fix", "escalate", "continuation", "review"}:
        raise ValueError("unknown attempt transition")
    unit_id = state.get("current_unit_id") or getattr(
        state.get("selected_unit"), "id", "__legacy__"
    )
    result = new_unit_attempt(unit_id, state.get("unit_base_commit"))
    result.update({key: state[key] for key in UnitAttemptState.__annotations__ if key in state})
    result.update(
        {
            "attempt_number": state.get(
                "attempt_number",
                1
                + int(bool(state.get("escalated")))
                + state.get("fix_attempts", 0)
                + state.get("review_revisions", 0)
                + state.get("implement_continuations", 0),
            )
            + 1,
            "attempt_base_commit": base_commit
            if base_commit is not None
            else state.get("attempt_base_commit"),
            "gate_receipt": None,
            "fix_receipt": None,
            "review_receipt": None,
            "test_results": None,
            "implementation": {},
            "pending_implementation": None,
            "review_clean": False,
            "review_current_findings": [],
            "implement_error_kind": "",
            "resume_partial_implement": kind == "continuation",
            "continuation_feedback": "",
            "status": Status.IMPLEMENTING,
        }
    )
    if kind in {"fix", "escalate"}:
        result["last_gate_output"] = (
            feedback
            if feedback is not None
            else (state.get("test_results") or {}).get("output", "")
        )
    if kind == "fix":
        result["fix_attempts"] += 1
    elif kind == "escalate":
        result["escalated"] = True
    elif kind == "review":
        result["review_revisions"] += 1
        result["last_gate_output"] = ""
        result["review_feedback"] = feedback or ""
    else:
        result["implement_continuations"] += 1
        result["continuation_feedback"] = CONTINUATION_FEEDBACK
    if base_commit is not None and state.get("pre_implement_ref"):
        result["pre_implement_ref"] = base_commit
    result["model_tier"] = "escalated" if result["escalated"] else "initial"
    result["attempt_id"] = _identity(result)
    return result
