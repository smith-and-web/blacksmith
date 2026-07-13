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

import subprocess
from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt

from blacksmith.state import BlacksmithState, Status

if TYPE_CHECKING:
    from blacksmith.config import HitlConfig


def approve_plan(state: BlacksmithState) -> dict:
    # Surface a plan for EVERY auto-gated unit (WU-PLAN-ALL-UNITS), not just the first, so the
    # human approves the whole multi-unit PRD at this one gate.
    decision = _decide(
        {
            "gate": "plan",
            "plans": state.get("plans") or [],
        }
    )
    approvals = {**state.get("approvals", {}), "plan": decision}
    if not decision:
        return {"approvals": approvals, "status": Status.HALTED}
    return {"approvals": approvals}


def approve_pr(state: BlacksmithState, *, hitl: HitlConfig | None = None) -> dict:
    payload = {
        "gate": "pr",
        "unit": _unit_summary(state.get("selected_unit")),
        "implementation": state.get("implementation"),
        "test_results": state.get("test_results"),
    }
    # Additive, read-only combined-diff display (WU-PR-DIFF-CAPTURE): only when a HitlConfig
    # is injected (production via build_graph_for) AND its byte ceiling is nonzero (the
    # off switch), AND the run actually has a shared worktree + a captured base ref to diff
    # against. Never gates the approver's bool contract or routing -- purely for display.
    if hitl is not None and hitl.pr_diff_max_bytes > 0:
        worktree_path = state.get("worktree_path")
        base_ref = state.get("pr_base_ref")
        if worktree_path and base_ref:
            diff_text = combined_diff(worktree_path, base_ref, max_bytes=hitl.pr_diff_max_bytes)
            if diff_text:
                payload["diff_text"] = diff_text
    decision = _decide(payload)
    approvals = {**state.get("approvals", {}), "pr": decision}
    if not decision:
        return {"approvals": approvals, "status": Status.HALTED}
    return {"approvals": approvals}


def combined_diff(worktree_path: str, base_ref: str, *, max_bytes: int) -> str:
    """The bounded combined diff (every unit's commits, not just the last) of the shared
    branch, for display at the ``approve_pr`` gate (WU-PR-DIFF-CAPTURE).

    Read-only and best-effort: runs ``git diff <base_ref>..HEAD`` in ``worktree_path`` via a
    subprocess (mirroring the existing ``_git``/``_git_run`` helpers -- no new dependency);
    any git failure or an empty diff yields ``""`` without raising. When the diff exceeds
    ``max_bytes`` it is truncated to that byte ceiling with an explicit
    ``"…(diff truncated at N bytes)…"`` marker appended."""
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "diff", f"{base_ref}..HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    diff_text = result.stdout
    if not diff_text:
        return ""
    encoded = diff_text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return diff_text
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{truncated}\n…(diff truncated at {max_bytes} bytes)…\n"


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
