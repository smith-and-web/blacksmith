"""Shared, unabridged requirements and approved-plan context for every model role.

Log tails and retry instructions are separate from this renderer: neither may
truncate or replace the PRD or original plan. RunContext will supply the persisted
PRD on restart; this module does not reload mutable source files for prompts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from blacksmith.contract import WorkUnit


def selected_plan(state: Mapping[str, Any], unit: WorkUnit) -> dict | None:
    explicit = state.get("selected_plan")
    if explicit and explicit.get("unit_id") == unit.id:
        return explicit
    keyed = state.get("plans_by_unit_id") or {}
    if unit.id in keyed:
        return keyed[unit.id]
    matches = [p for p in state.get("plans") or [] if p.get("unit_id") == unit.id]
    if len(matches) > 1:
        raise ValueError(f"duplicate plan for {unit.id}")
    return matches[0] if matches else None


def render_context(
    state: Mapping[str, Any], unit: WorkUnit | None = None, *, include_plan: bool = False,
) -> str:
    """Render exact prose and full contract; add this unit's plan and completed deps."""
    prd = state.get("prd")
    if prd is None:
        raise ValueError("missing PRD requirements context")
    prompt = (
        "\n\nIMMUTABLE REQUIREMENTS — FULL PRD PROSE (unabridged):\n"
        + prd.body
        + "\n\nFULL PRD CONTRACT (all fields):\n"
        + prd.contract.model_dump_json(indent=2)
    )
    if include_plan:
        if unit is None:
            raise ValueError("approved-plan context requires a selected unit")
        approved = selected_plan(state, unit)
        prompt += "\n\nAPPROVED IMPLEMENTATION PLAN:\n"
        if approved is None:
            # Legacy in-memory node callers have no approval record. The production
            # snapshot/approval verifier must reject this before calling a model.
            prompt += "(no approved plan recorded)"
        else:
            prompt += str(approved.get("steps") or "")
        completed = [
            record for record in state.get("unit_results") or []
            if record.get("unit_id") in unit.depends_on
        ]
        prompt += "\n\nDEPENDENCY COMPLETION RECORDS:\n" + json.dumps(
            completed, sort_keys=True, ensure_ascii=False, default=str,
        )
    return prompt
