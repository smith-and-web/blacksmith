"""Exact-candidate normal/draft approval, with immutable request and decision evidence."""

from __future__ import annotations

import json
from pathlib import Path

from blacksmith.contract import validate_execution_contract
from blacksmith.integration import levels_for, verify_final_gate
from blacksmith.receipts import OperationKey, ReceiptError, digest, encoded
from blacksmith.review_patch import capture_patch, verify_patch
from blacksmith.review_receipts import ReviewPatch
from blacksmith.review_receipts import summary as review_summary
from blacksmith.run_context import atomic_write, context_from_state, origin_identity, run_branch
from blacksmith.state import Status
from blacksmith.verification import _git, candidate_identity


class ApprovalError(ValueError):
    """Unapproved, stale, mismatched or incomplete publication; retain work."""


def _context(state):
    if state.get("run_context") or state.get("state_version") is not None:
        context = context_from_state(state)
        if state.get("approval_version") != 1:
            raise ApprovalError("legacy_approval_requires_manual_recovery")
        return context
    return None


def _receipt_reference(record):
    if record is None:
        return None
    data = record.model_dump(mode="json")
    return {"receipt_id": record.receipt_id, "sha256": digest(encoded(data)),
            "artifact_path": record.artifact_path,
            "candidate_commit": record.candidate_commit,
            "layers": list(getattr(record, "layers", ())),
            "status": getattr(record, "status", getattr(record, "outcome", None))}


def _round(state):
    value = state.get("pr_approval_round", 0)
    if type(value) is not int or value < 0:
        raise ApprovalError("invalid PR approval request round")
    return value


def proposal(state):
    """Recompute immutable acceptance data; observations never stand in for approval."""
    context = _context(state)
    if state.get("status") == Status.HALTED:
        raise ApprovalError("halted run requires explicit recovery before PR approval")
    verify_final_gate(state)
    path = state.get("worktree_path")
    if context is None and (not path or not (Path(path) / ".git").exists()):
        return None  # Dependency-free historical graph seam, never the production CLI.
    candidate, tree, dirty = candidate_identity(path)
    if dirty:
        raise ApprovalError("PR approval requires a clean committed candidate")
    prd = context.prd() if context else state.get("prd")
    qa = []
    if prd:
        validate_execution_contract(prd.contract)
        qa = [u for u in prd.contract.work_units if prd.contract.gate_for(u) == "human"]
    records = []
    levels = levels_for(state) if state.get("execution_levels") else []
    if context:
        if not levels:
            raise ApprovalError("missing completed execution levels")
        last = levels[-1][-1]
        final = (state.get("unit_outcomes") or {}).get(last.id)
        if final is None:
            raise ApprovalError("missing final unit outcome")
        expected = final.get("source_commit")
        if len(levels[-1]) > 1 and state.get("fanout"):
            expected = (state.get("join_destinations") or {}).get(last.id, {}).get(
                "destination_commit")
        if candidate != expected:
            raise ApprovalError("candidate contains unknown changes after completed units")
        if (Path(path).resolve() != context.workspace_path
                or state.get("branch") != run_branch(context.run_id, levels[0][0].id)
                or _git(path, "branch", "--show-current").strip() != state.get("branch")
                or state.get("default_branch") != context.default_branch
                or origin_identity(_git(path, "remote", "get-url", "origin").strip())
                != context.origin):
            raise ApprovalError("publication workspace/branch/base/origin differs from saved run")
    for level in levels:
        for unit in level:
            if prd and prd.contract.gate_for(unit) == "human":
                continue
            row = (state.get("unit_outcomes") or {}).get(unit.id)
            if row:
                records.append({"unit_id": unit.id, "base_commit": row.get("base_commit"),
                    "candidate_commit": row.get("source_commit"),
                    "gate_receipt": _receipt_reference(row.get("gate_receipt")),
                    "review_receipt": _receipt_reference(row.get("review_receipt"))})
    return {"version": 1, "run_id": context.run_id if context else None,
            "request_round": _round(state),
            "candidate_commit": candidate, "candidate_tree": tree,
            "branch": state.get("branch"), "base_branch": state.get("default_branch"),
            "origin": context.origin if context else None,
            "draft": bool(qa), "pending_qa_unit": qa[0].model_dump(mode="json") if qa else None,
            "auto_unit_receipts": records,
            "final_gate_receipt": _receipt_reference(state.get("final_gate_receipt")),
            "review_status": review_summary(state)}


def _key(context, state, kind):
    return OperationKey(context.run_id, "__run__", "pr-approval:" + str(
        _round(state)), kind)


def _path(context, state, name):
    path = context.directory / "publication" / ("approval-" + str(
        _round(state)) + "-" + name + ".json")
    if path.resolve() != path or path.is_symlink():
        raise ReceiptError("foreign publication approval artifact")
    return path


def _persist(path, data):
    text = encoded(data) + "\n"
    if path.exists():
        if path.read_text() != text:
            raise ApprovalError("publication approval evidence changed while awaiting a decision")
    else:
        atomic_write(path, text)


def _require_proposal(context, state, prepared):
    row = context.journal().get(_key(context, state, "approval.pr.proposal"))
    path = _path(context, state, "proposal")
    try:
        valid = (row and row.status == "completed" and row.intent == prepared
                 and row.outcome == {"sha256": digest(encoded(prepared))}
                 and path.is_file() and json.loads(path.read_text()) == prepared)
    except (OSError, ValueError) as exc:
        raise ReceiptError("missing/corrupt pending PR approval artifact") from exc
    if not valid:
        raise ReceiptError("missing/corrupt pending PR approval evidence")


def prepare(state, *, hitl=None):
    core = proposal(state)
    if core is None:
        return None, ""
    context = _context(state)
    preview = ""
    if hitl and hitl.pr_diff_max_bytes > 0:
        patch = capture_patch(state, context=context, scope="publication",
            base_commit=context.base_commit if context else state.get("pr_base_ref"))
        core = {**core, "diff_manifest": patch.model_dump(mode="json")}
        preview = display_patch(patch, hitl.pr_diff_max_bytes)
    if context:
        key = _key(context, state, "approval.pr.proposal")
        old = context.journal().get(key)
        if old and old.intent != core:
            raise ApprovalError("candidate/evidence changed while awaiting PR approval")
        context.journal().intent(key, core)
        _persist(_path(context, state, "proposal"), core)
        context.journal().finish(key, "completed", {"sha256": digest(encoded(core))})
    elif state.get("pr_approval_proposal") and state["pr_approval_proposal"] != core:
        raise ApprovalError("candidate changed while awaiting PR approval")
    return core, preview


def display_patch(patch, maximum):
    path = verify_patch(patch)
    with path.open("rb") as stream:
        data = stream.read(maximum)
    text = data.decode("utf-8", errors="ignore")
    if patch.bytes > maximum:
        text += f"\n…(diff truncated at {maximum} bytes)…\n"
    return text


def cached_decision(state, prepared):
    context = _context(state)
    if not context:
        return None
    _require_proposal(context, state, prepared)
    row = context.journal().get(_key(context, state, "approval.pr.decision"))
    if row is None:
        return None
    if row.status != "completed":
        raise ApprovalError("interrupted PR approval requires a fresh confirmation")
    if type(row.intent.get("approved")) is not bool:
        raise ReceiptError("invalid critical PR approval decision type")
    expected = {"proposal_sha256": digest(encoded(prepared)), "approved": row.intent["approved"]}
    if row.intent != expected or row.outcome != {"approval": {**expected,
            "proposal": prepared, "decision_id": row.key.id}}:
        raise ReceiptError("corrupt critical PR approval decision")
    return row.outcome["approval"]


def record_decision(state, prepared, approved):
    if type(approved) is not bool:
        raise ApprovalError("PR approval requires a boolean decision")
    if prepared is None:
        return None
    current = proposal(state)
    if current != {k: v for k, v in prepared.items() if k != "diff_manifest"}:
        raise ApprovalError("candidate changed after the PR approval prompt")
    if prepared.get("diff_manifest"):
        verify_patch(ReviewPatch.model_validate(prepared["diff_manifest"]))
    context = _context(state)
    data = {"proposal_sha256": digest(encoded(prepared)), "approved": approved}
    if context:
        _require_proposal(context, state, prepared)
        key = _key(context, state, "approval.pr.decision")
        context.journal().intent(key, data)
        data = {**data, "proposal": prepared, "decision_id": key.id}
        _persist(_path(context, state, "decision"), data)
        context.journal().finish(key, "completed", {"approval": data})
    else:
        data = {**data, "proposal": prepared, "decision_id": None}
    return data


def _verify(state, *, draft=None):
    if state.get("status") == Status.HALTED or state.get("approvals", {}).get("pr") is not True:
        raise ApprovalError("publication requires explicit PR approval")
    context = _context(state)
    record = state.get("pr_approval")
    if context is None and state.get("approval_version") is None and record is None:
        return None  # Explicitly approved pre-versioned direct-node/library caller only.
    if not isinstance(record, dict) or record.get("approved") is not True:
        raise ApprovalError("missing exact-candidate PR approval")
    prepared = record["proposal"]
    current = proposal(state)
    if (current != {k: v for k, v in prepared.items() if k != "diff_manifest"}
            or record.get("proposal_sha256") != digest(encoded(prepared))
            or (draft is not None and prepared["draft"] != draft)):
        raise ApprovalError("PR approval does not match the current candidate or draft intent")
    if prepared.get("diff_manifest"):
        verify_patch(ReviewPatch.model_validate(prepared["diff_manifest"]),
                     root=context.directory / "publication" / "diffs" if context else None)
    if context:
        for name, expected in (("proposal", prepared), ("decision", record)):
            path = _path(context, state, name)
            if not path.is_file() or json.loads(path.read_text()) != expected:
                raise ReceiptError("missing/corrupt publication approval artifact")
        key = _key(context, state, "approval.pr.proposal")
        row = context.journal().get(key)
        if (not row or row.status != "completed" or row.intent != prepared
                or row.outcome != {"sha256": digest(encoded(prepared))}
                or cached_decision(state, prepared) != record):
            raise ReceiptError("PR approval differs from critical decision evidence")
    return prepared


def verify(state, *, draft=None):
    try:
        return _verify(state, draft=draft)
    except (ReceiptError, ApprovalError):
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        raise ApprovalError("missing/corrupt exact-candidate PR approval evidence") from exc


def hold(state, error, *, node="approve_pr"):
    return {"status": Status.HALTED, "approvals": {**state.get("approvals", {}), "pr": False},
            "errors": [{"node": node, "message": str(error)}]}


def _validate_state(state):
    """Read-only approval artifact validation before CLI executor construction."""
    if not state.get("worktree_path") and not state.get("pr_approval_proposal"):
        return
    context = _context(state)
    if context is None:
        return
    prepared, decision = state.get("pr_approval_proposal"), state.get("pr_approval")
    if prepared:
        _require_proposal(context, state, prepared)
        if prepared.get("diff_manifest"):
            verify_patch(ReviewPatch.model_validate(prepared["diff_manifest"]),
                         root=context.directory / "publication" / "diffs")
        if decision:
            path = _path(context, state, "decision")
            if (decision != cached_decision(state, prepared) or not path.is_file()
                    or json.loads(path.read_text()) != decision):
                raise ReceiptError("missing/corrupt retained PR approval decision")
    if state.get("approvals", {}).get("pr") is True and (not prepared or not decision):
        raise ReceiptError("PR approval bool lacks original candidate-bound evidence")


def validate_state(state):
    try:
        return _validate_state(state)
    except ReceiptError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        raise ReceiptError("missing/corrupt retained publication approval metadata") from exc
