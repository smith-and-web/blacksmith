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

from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt

from blacksmith.path_policy import PolicyViolation, compile_path_rules, policy_for_state
from blacksmith.plan_artifacts import PlanIntegrityError, prepare_approval, record_approval
from blacksmith.state import BlacksmithState, Status

if TYPE_CHECKING:
    from blacksmith.config import HitlConfig


def approve_plan(state: BlacksmithState) -> dict:
    # Surface a plan for EVERY auto-gated unit (WU-PLAN-ALL-UNITS), not just the first, so the
    # human approves the whole multi-unit PRD at this one gate.
    try:
        prd = state.get("prd")
        hard_rules = compile_path_rules(prd.contract.untouchables if prd else ())
        if state.get("run_context"):
            hard_rules = policy_for_state(state, root=state["planning_path"]).rules
    except PolicyViolation as exc:
        return {"status": Status.HALTED,
                "errors": [{"node": "approve_plan", "message": str(exc)}]}
    try:
        proposal = prepare_approval(state) if (
            state.get("run_context") or state.get("planning_version") is not None
        ) else None
    except PlanIntegrityError as exc:
        return {"status": Status.HALTED,
                "errors": [{"node": "approve_plan", "message": str(exc)}]}
    decision = _decide(
        {
            "gate": "plan",
            **(proposal or {}),
            "plans": state.get("plans") or [],
            "hard_path_rules": list(hard_rules),
            "always_protected": [
                ".git and descendants", ".gitmodules", ".mcp.json", ".claude/settings*.json",
                "blacksmith.toml", "blacksmith.config.toml",
            ],
        }
    )
    approvals = {**state.get("approvals", {}), "plan": decision}
    if not decision:
        return {"approvals": approvals, "status": Status.HALTED}
    try:
        evidence = record_approval(state, proposal) if proposal is not None else {}
    except PlanIntegrityError as exc:
        return {"approvals": {**approvals, "plan": False}, "status": Status.HALTED,
                "errors": [{"node": "approve_plan", "message": str(exc)}]}
    return {"approvals": approvals, **evidence}


def prepare_pr_approval(state: BlacksmithState, *, hitl: HitlConfig | None = None) -> dict:
    from blacksmith.contract import ContractError
    from blacksmith.gate import GateError
    from blacksmith.publication_approval import ApprovalError, hold, prepare
    try:
        prepared, _ = prepare(state, hitl=hitl)
    except (ApprovalError, ContractError, GateError) as exc:
        return hold(state, exc)
    return {"pr_approval_proposal": prepared, "status": Status.AWAITING_PR_APPROVAL,
            "publication_draft": bool(prepared and prepared["draft"]),
            "pending_qa_unit": prepared["pending_qa_unit"] if prepared else None}


def approve_pr(state: BlacksmithState, *, hitl: HitlConfig | None = None) -> dict:
    from blacksmith.contract import ContractError
    from blacksmith.gate import GateError
    from blacksmith.observability import review_revision_count
    from blacksmith.publication_approval import (
        ApprovalError,
        cached_decision,
        display_patch,
        hold,
        prepare,
        proposal,
        record_decision,
    )
    from blacksmith.review_receipts import ReviewPatch, current_blockers
    from blacksmith.review_receipts import summary as review_summary
    try:
        prepared = state.get("pr_approval_proposal")
        if prepared is None:
            prepared, _ = prepare(state, hitl=hitl)
        elif proposal(state) != {k: v for k, v in prepared.items() if k != "diff_manifest"}:
            raise ApprovalError("candidate/evidence changed after the PR approval prompt")
        payload = {"gate": "pr", **(prepared or {}),
                   "unit": _unit_summary(state.get("selected_unit")),
                   "implementation": state.get("implementation"),
                   "test_results": state.get("test_results"),
                   "review_status": review_summary(state),
                   "unresolved_review_findings": current_blockers(state),
                   "review_revisions_total": review_revision_count(state)}
        if prepared and prepared.get("diff_manifest") and hitl and hitl.pr_diff_max_bytes > 0:
            diff = display_patch(ReviewPatch.model_validate(prepared["diff_manifest"]),
                                 hitl.pr_diff_max_bytes)
            if diff:
                payload["diff_text"] = diff
        cached = cached_decision(state, prepared) if prepared else None
        if cached is None:
            decision = _decide(payload)
            cached = record_decision(state, prepared, decision)
        else:
            decision = cached["approved"]
        if not decision:
            return {"approvals": {**state.get("approvals", {}), "pr": False},
                    "pr_approval": cached, "status": Status.HALTED}
        return {"approvals": {**state.get("approvals", {}), "pr": True},
                "pr_approval": cached, "status": Status.TESTING}
    except (ApprovalError, ContractError, GateError) as exc:
        return hold(state, exc)


def combined_diff(worktree_path: str, base_ref: str, *, max_bytes: int) -> str:
    """Optional bounded preview; full Git output never travels through capped stdout."""
    from blacksmith.commands import CommandError
    from blacksmith.publication_approval import display_patch
    from blacksmith.review_patch import capture_patch
    if max_bytes <= 0:
        return ""
    try:
        patch = capture_patch({"worktree_path": worktree_path}, base_commit=base_ref,
                              scope="publication")
        return display_patch(patch, max_bytes)
    except (OSError, ValueError, CommandError):
        return ""


def _decide(payload: dict[str, Any]) -> bool:
    """Halt at the gate, surfacing ``payload``; return the injected approval as a bool."""
    answer = interrupt(payload)
    if isinstance(answer, dict):
        if type(answer.get("approved")) is not bool:
            return False
        if payload.get("gate") == "pr" and any(
                field in answer and answer[field] != payload.get(field)
                for field in ("run_id", "candidate_commit", "draft", "request_round")):
            return False
        return answer["approved"]
    return answer if type(answer) is bool else False


def _unit_summary(unit: Any) -> dict[str, Any] | None:
    if unit is None:
        return None
    return {"id": unit.id, "title": unit.title, "layers": list(unit.layers)}
