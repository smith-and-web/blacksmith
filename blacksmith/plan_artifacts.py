"""Immutable per-unit plans and the single, exact-input approval binding."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from blacksmith.context import requirements_prd
from blacksmith.receipts import ReceiptError
from blacksmith.run_context import (
    atomic_write,
    canonical_json,
    context_from_state,
    sha256,
    unit_hash,
)


class PlanIntegrityError(ReceiptError):
    """Missing or changed planning evidence; do not admit implementation/review."""


def prd_hash(state: Mapping[str, Any]) -> str:
    if (state.get("state_version") is not None or state.get("run_id")) and not state.get(
        "run_context"
    ):
        raise PlanIntegrityError("missing run context for versioned plans")
    prd = requirements_prd(state)
    if prd is None:
        raise PlanIntegrityError("missing planning PRD")
    if state.get("run_context"):
        context = context_from_state(dict(state))
        if state.get("planning_path") != str(context.planning_path):
            raise PlanIntegrityError("planning snapshot path differs from immutable run")
        visible = state.get("prd")
        if visible and (visible.text != prd.text or visible.body != prd.body
                        or visible.contract != prd.contract):
            raise PlanIntegrityError("wrong PRD in planning state")
        return context.prd_sha256
    return sha256(prd.text or canonical_json(
        {"contract": prd.contract.model_dump(), "body": prd.body},
    ))


def auto_units(state):
    prd = requirements_prd(state)
    if prd is None:
        raise PlanIntegrityError("missing planning PRD")
    return [u for u in prd.contract.work_units if prd.contract.gate_for(u) != "human"]


def _artifact(state, name: str) -> Path | None:
    if not state.get("run_context"):
        return None  # Legacy in-memory callers have no durable run directory.
    root = context_from_state(dict(state)).directory
    directory = root / "plans"
    if directory.is_symlink() or directory.resolve() != directory:
        raise PlanIntegrityError("foreign planning artifact directory")
    path = directory / name
    if path.is_symlink():
        raise PlanIntegrityError("symlink planning artifact")
    return path


def _persist(state, name: str, value: dict) -> None:
    path = _artifact(state, name)
    if path is None:
        return
    try:
        if path.exists():
            if json.loads(path.read_text()) != value:
                raise PlanIntegrityError(f"changed immutable plan artifact: {name}")
        else:
            atomic_write(path, canonical_json(value) + "\n")
    except (OSError, ValueError) as exc:
        raise PlanIntegrityError(f"cannot persist plan artifact: {name}") from exc


def _require(state, name: str, value: dict) -> None:
    path = _artifact(state, name)
    if path is None:
        return
    try:
        if json.loads(path.read_text()) != value:
            raise PlanIntegrityError(f"changed immutable plan artifact: {name}")
    except (OSError, ValueError) as exc:
        raise PlanIntegrityError(f"missing/corrupt plan artifact: {name}") from exc


def _plan_envelope(state, row):
    return {"version": 1, "run_id": state.get("run_id"), "plan": row}


def record_plan(state, row: dict) -> dict:
    row = {**row, "prd_sha256": prd_hash(state)}
    if not isinstance(row.get("steps"), str) or not row["steps"].strip():
        raise PlanIntegrityError("empty implementation plan")
    row["plan_sha256"] = sha256(canonical_json(row))
    _persist(state, unit_hash(row["unit_id"]) + ".json", _plan_envelope(state, row))
    return row


def validate_plans(state, *, complete: bool = True) -> dict[str, dict]:
    """Validate a declaration-order prefix, or the complete single-approval bundle."""
    digest = prd_hash(state)
    if state.get("planning_version") != 1 or state.get("planning_prd_sha256") != digest:
        raise PlanIntegrityError("missing/wrong PRD hash for plans")
    units = auto_units(state)
    keyed = state.get("plans_by_unit_id") or {}
    ids = [u.id for u in units]
    if not isinstance(keyed, dict) or set(keyed) != set(ids[:len(keyed)]):
        raise PlanIntegrityError("missing/foreign/non-prefix unit plan")
    if complete and set(keyed) != set(ids):
        raise PlanIntegrityError("missing unit plan")
    if state.get("plan_cursor") != len(keyed):
        raise PlanIntegrityError("inconsistent plan cursor")
    if state.get("plans") != [keyed[i] for i in ids if i in keyed]:
        raise PlanIntegrityError("changed/duplicate plan list")
    for unit in units[:len(keyed)]:
        row = keyed[unit.id]
        expected_fields = {"unit_id": unit.id, "title": unit.title, "layers": list(unit.layers),
                           "target_modules": list(unit.target_modules),
                           "test_contract": unit.test_contract, "prd_sha256": digest}
        if not isinstance(row, dict) or any(row.get(k) != v for k, v in expected_fields.items()):
            raise PlanIntegrityError("wrong unit/PRD plan binding")
        if not isinstance(row.get("steps"), str) or not row["steps"].strip():
            raise PlanIntegrityError("empty implementation plan")
        actual = sha256(canonical_json({k: v for k, v in row.items() if k != "plan_sha256"}))
        if row.get("plan_sha256") != actual:
            raise PlanIntegrityError("changed plan hash")
        _require(state, unit_hash(unit.id) + ".json", _plan_envelope(state, row))
    return keyed


def approval_proposal(state) -> dict:
    keyed = validate_plans(state)
    return {"version": 1, "run_id": state.get("run_id"), "prd_sha256": prd_hash(state),
            "plans_sha256": sha256(canonical_json(keyed)),
            "unit_plan_hashes": {i: row["plan_sha256"] for i, row in keyed.items()}}


def prepare_approval(state) -> dict:
    proposal = approval_proposal(state)
    # Persist BEFORE interrupt; re-entry cannot approve a substituted payload with an old bool.
    _persist(state, "approval-proposal.json", proposal)
    return proposal


def record_approval(state, proposal) -> dict:
    if approval_proposal(state) != proposal:
        raise PlanIntegrityError("plans changed while awaiting approval")
    _require(state, "approval-proposal.json", proposal)
    approved = {**proposal, "approved": True}
    _persist(state, "approved.json", approved)
    return {"plan_approval": approved, "approved_plans_sha256": proposal["plans_sha256"]}


def verify_approved_plan(state, unit) -> None:
    if (not state.get("run_context") and state.get("planning_version") is None
            and state.get("state_version") is None and not state.get("run_id")):
        return  # Pre-snapshot direct-node compatibility only; production always has context.
    if unit is None:
        raise PlanIntegrityError("missing selected unit for approved plan")
    proposal = approval_proposal(state)
    expected = {**proposal, "approved": True}
    if (not state.get("approvals", {}).get("plan") or state.get("plan_approval") != expected
            or state.get("approved_plans_sha256") != proposal["plans_sha256"]):
        raise PlanIntegrityError("missing/changed approved plans binding")
    _require(state, "approval-proposal.json", proposal)
    _require(state, "approved.json", expected)
    selected = state.get("selected_plan")
    if selected is not None and selected != state["plans_by_unit_id"].get(unit.id):
        raise PlanIntegrityError("selected plan differs from approved plan")
    prd = requirements_prd(state)
    expected_unit = next((u for u in prd.contract.work_units if u.id == unit.id), None)
    if unit != expected_unit:
        raise PlanIntegrityError("selected unit differs from approved contract")
    if prd.contract.gate_for(unit) != "human" and unit.id not in state["plans_by_unit_id"]:
        raise PlanIntegrityError("missing approved unit plan")
