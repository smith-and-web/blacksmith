"""Explicit, journaled recovery amendments through the existing compiled graph.

This controller selects a saved stage. It never runs an independent attempt loop,
recreates a worker, replenishes counters, or rewrites an uncertain effect receipt.
"""

from __future__ import annotations

import base64
import json
import math
import os
from copy import deepcopy
from pathlib import Path

from blacksmith.receipts import OperationKey, attempt_identity, digest, encoded, merge_events
from blacksmith.recovery import (
    RecoveryError,
    _directory,
    _dump_state,
    _git,
    _read,
    capture,
    file_hash,
    latest_checkpoints,
    owned_file,
    validate,
)
from blacksmith.run_context import context_from_state
from blacksmith.state import Status
from blacksmith.unit_state import next_attempt

UNIT_STAGES = frozenset(
    {
        "implement",
        "audit_commit",
        "auto_fix",
        "normalize_candidate",
        "test_gate",
        "review",
        "recovery_decision",
        "continue_implement",
        "fix_retry",
        "escalate",
        "prepare_review_revision",
    }
)


def fingerprint(path):
    """Bind a scheduled amendment to bytes, index and HEAD, including partial edits."""
    path = Path(path)
    if path.is_symlink() or path.resolve() != path or not (path / ".git").exists():
        raise RecoveryError("missing/foreign recovery workspace")
    names = _git(path, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
    rows = []
    for name in sorted(set(filter(None, names.split("\0")))):
        item = path / name
        if item.is_symlink():
            value = ["symlink", os.readlink(item)]
        elif item.exists():
            if item.resolve() != item or not item.is_file():
                raise RecoveryError("unsafe path in recovery workspace")
            value = ["file", file_hash(item)]
        else:
            value = ["deleted"]
        rows.append([name, value])
    return {
        "head": _git(path, "rev-parse", "HEAD").stdout.strip(),
        "index": digest(_git(path, "ls-files", "--stage", "-z").stdout),
        "files": digest(encoded(rows)),
    }


def dispatch(state):
    """One parent transition selects one saved child, or one parent stage."""
    queue = state.get("recovery_queue") or []
    context = context_from_state(state)
    operation = next((o for o in context.journal().operations()
                      if o.key.id == state.get("recovery_amendment_id")), None)
    if operation is None or operation.key.kind != "recovery.amend":
        raise RecoveryError("recovery transition lacks its critical amendment")
    from blacksmith.graph import blacksmith_serde
    sealed = _load_amendment(context, operation, blacksmith_serde())
    if (sealed["limits"] != state["limits"]
            or sealed["recovery_units"] != state["recovery_units"]
            or sealed["recovery_target"] != state["recovery_target"]
            or sealed["recovery_fingerprints"] != state["recovery_fingerprints"]):
        raise RecoveryError("recovery transition differs from critical amendment")
    key = queue[0] if queue else "__run__"
    fingerprints = state.get("recovery_fingerprints") or {}
    if key in fingerprints:
        bound = fingerprints[key]
        if fingerprint(bound["path"]) != bound["value"]:
            raise RecoveryError("workspace changed after recovery amendment; inspect before retry")
    if queue:
        unit = state["recovery_units"][key]["selected_unit"]
        return {"selected_unit": unit, "status": Status.IMPLEMENTING}
    return {}


def route_dispatch(state):
    return "execute_unit" if state.get("recovery_queue") else state["recovery_target"]


def _costs(context, checkpoints, root):
    # Prefer the original graph payload over journal spelling of node/usage fields.
    result = list(root.get("cost_events") or [])
    seen = {e.get("event_id") for e in result}
    for _, values in checkpoints:
        for event in values.get("cost_events") or []:
            if event.get("event_id") not in seen:
                result = merge_events(result, [event])
                seen.add(event.get("event_id"))
    from blacksmith.nodes.plan import usage_breakdown

    for event in context.journal().events():
        if event["kind"] == "model_cost" and event["event_id"] not in seen:
            payload = event["payload"]
            result = merge_events(
                result,
                [
                    {
                        **{
                            k: payload[k]
                            for k in (
                                "call_id",
                                "unit_id",
                                "model",
                                "cost_usd",
                                "num_turns",
                                "session_id",
                            )
                        },
                        "node": payload["node"].removeprefix("model."),
                        "usage": usage_breakdown(payload.get("usage")),
                        "event_id": event["event_id"],
                    }
                ],
            )
            seen.add(event["event_id"])
    return result


def _latest_units(checkpoints):
    result = {}
    # Saver returns newest UUIDv7 checkpoint first across namespaces. Never trust index order.
    for config, values in checkpoints:
        unit = values.get("selected_unit")
        if config["configurable"].get("checkpoint_ns") and unit:
            result.setdefault(unit.id, values)
    return result


def _validate_workspaces(context, root):
    try:
        for unit, outcome in (root.get("unit_outcomes") or {}).items():
            path = Path(outcome["clone_path"])
            if (path not in {context.workspace_path, context.unit_path(unit)}
                    or path.resolve() != path or not path.is_dir()):
                raise RecoveryError("missing/foreign recorded unit workspace")
            if outcome.get("ok") and path == context.unit_path(unit):
                head = _git(path, "rev-parse", "HEAD").stdout.strip()
                dirty = _git(path, "status", "--porcelain", "--untracked-files=all").stdout
                if head != outcome["source_commit"] or dirty:
                    raise RecoveryError("successful sibling changed since its recorded outcome")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise RecoveryError("invalid recorded unit workspace metadata") from exc


def _limits(context, addition):
    amended = sum(
        float(o.intent["request"].get("add_budget_usd") or 0)
        for o in context.journal().operations()
        if o.key.kind == "recovery.amend" and o.status == "completed"
    )
    config = context.execution_config().limits
    limits = {
        k: getattr(config, k)
        for k in (
            "max_fix_attempts",
            "max_run_cost_usd",
            "max_review_revisions",
            "max_implement_turns",
            "max_implement_continuations",
        )
    }
    cap = limits["max_run_cost_usd"]
    if addition and cap is None:
        raise RecoveryError("cannot add budget to an already uncapped run")
    amended += addition or 0
    if cap is not None:
        limits["max_run_cost_usd"] = cap + amended
        if not math.isfinite(limits["max_run_cost_usd"]):
            raise RecoveryError("amended run budget must remain finite")
    return limits, amended


def _load_amendment(context, operation, serde):
    if operation.status != "completed":
        raise RecoveryError("incomplete recovery amendment; inspect retained amendment first")
    path = owned_file(_directory(context), operation.outcome["patch"])
    if file_hash(path) != operation.outcome["sha256"]:
        raise RecoveryError("changed recovery amendment")
    data = _read(path)
    if data["kind"] not in {"msgpack", "json"}:
        raise RecoveryError("unsupported recovery amendment encoding")
    return serde.loads_typed((data["kind"], base64.b64decode(data["data"], validate=True)))


def prepare(
    context,
    saver,
    saved,
    *,
    retry_stage=False,
    continue_attempt=False,
    unit_id=None,
    add_budget_usd=None,
):
    """Return a sealed scheduling patch, or None for ordinary pending-node resume."""
    validate(context)
    if add_budget_usd is not None and (not math.isfinite(add_budget_usd) or add_budget_usd <= 0):
        raise RecoveryError("--add-budget-usd must be a positive finite amount")
    root = saved.checkpoint["channel_values"]
    _validate_workspaces(context, root)
    pending_dispatch = "branch:to:recovery_dispatch" in root
    explicit = retry_stage or continue_attempt or add_budget_usd is not None
    if unit_id and not (retry_stage or continue_attempt):
        raise RecoveryError("--unit-id requires --retry-stage or --continue-attempt")
    if pending_dispatch and explicit:
        raise RecoveryError("recovery amendment already scheduled; resume without amendment flags")
    approvals = root.get("approvals") or {}
    rejection = (
        root.get("status") == Status.HALTED
        and (approvals.get("plan") is False or approvals.get("pr") is False)
    )
    if not explicit and not rejection:
        if root.get("status") == Status.HALTED:
            raise RecoveryError(
                "failed stage requires explicit resume --retry-stage or "
                "--continue-attempt; budgets and artifacts retained"
            )
        return None
    request = {
        "retry_stage": retry_stage,
        "continue_attempt": continue_attempt,
        "unit_id": unit_id,
        "add_budget_usd": add_budget_usd,
        "reapproval": rejection,
    }
    checkpoint = saved.config["configurable"]["checkpoint_id"]
    key = OperationKey(
        context.run_id,
        "__run__",
        "recovery:" + digest(encoded([checkpoint, request])),
        "recovery.amend",
    )
    if existing := context.journal().get(key):
        return _load_amendment(context, existing, saver.serde)
    checkpoints = latest_checkpoints(saver, context)
    costs = _costs(context, checkpoints, root)
    limits, amended = _limits(context, add_budget_usd)
    patch = {
        "status": Status.IMPLEMENTING,
        "limits": limits,
        "cost_events": costs,
        "budget_amendment_usd": amended,
        "recovery_queue": [],
        "recovery_units": {},
        "recovery_fingerprints": {},
        "attempt_id": attempt_identity(root) + ":recovery:" + key.id[:16],
        "recovery_amendment_id": key.id,
    }
    if rejection and not (retry_stage or continue_attempt):
        gate = "plan" if approvals.get("plan") is False else "pr"
        patch.update(recovery_target="approve_" + gate, approvals={**approvals, gate: False})
    elif not root.get("worktree_path"):
        if not continue_attempt and not add_budget_usd:
            raise RecoveryError("planning recovery requires --continue-attempt")
        overrides = dict(root.get("recovery_call_overrides") or {})
        counts = dict(root.get("recovery_plan_continuations") or {})
        uncertain = [
            o
            for o in context.journal().operations()
            if o.key.kind.startswith("model.")
            and o.status not in {"completed", "failed"}
            and o.key.id not in set(root.get("recovery_acknowledged_calls") or [])
        ]
        if uncertain and not continue_attempt:
            raise RecoveryError("uncertain planner/critic requires --continue-attempt")
        for operation in uncertain:
            identity = encoded(
                [operation.key.unit_id, operation.key.attempt_id, operation.key.kind]
            )
            identity = next((base for base, target in overrides.items()
                             if target == operation.key.attempt_id
                             and json.loads(base)[::2]
                             == [operation.key.unit_id, operation.key.kind]), identity)
            count = counts.get(identity, 0)
            if count >= limits["max_implement_continuations"]:
                raise RecoveryError("planning continuation allowance exhausted")
            counts[identity] = count + 1
            overrides[identity] = operation.key.attempt_id + f":continuation:{count + 1}"
        patch.update(
            recovery_target="plan",
            recovery_call_overrides=overrides,
            recovery_plan_continuations=counts,
            recovery_acknowledged_calls=[
                *(root.get("recovery_acknowledged_calls") or []),
                *[o.key.id for o in uncertain],
            ],
        )
    else:
        units = _latest_units(checkpoints)
        outcomes = root.get("unit_outcomes") or {}
        selected = [
            u.id
            for u in root.get("work_units") or []
            if u.id in units and not outcomes.get(u.id, {}).get("ok")
        ]
        if unit_id:
            if unit_id not in selected:
                raise RecoveryError("selected unit is not a recorded failed/interrupted unit")
            selected = [unit_id]
        if not selected:
            # A final or combined gate has no arbitrary worker attribution.
            final = root.get("final_gate_receipt")
            integration = (root.get("integration_gate_receipts") or {}).get(
                str(root.get("level_cursor", 0))
            )
            if final and final.outcome != "passed":
                patch["recovery_target"] = "final_gate"
            elif integration and integration.outcome != "passed":
                patch["recovery_target"] = "integration_gate"
            else:
                raise RecoveryError("no recoverable failed unit/gate; inspect retained stage")
        else:
            for identity in selected:
                state = deepcopy(units[identity])
                state["cost_events"] = merge_events(state.get("cost_events") or [],
                    [event for event in costs if event.get("unit_id") == identity])
                path = Path(state["worktree_path"])
                expected = (
                    context.unit_path(identity)
                    if state.get("unit_mode") == "parallel"
                    else context.workspace_path
                )
                if path != expected or path.resolve() != expected:
                    raise RecoveryError("saved worker path differs from run ownership")
                actual = fingerprint(path)
                previous = outcomes.get(identity)
                pending = [name.removeprefix("branch:to:") for name in state
                           if name.startswith("branch:to:")]
                stage = state.get("unit_failed_stage") or (
                    pending[0] if len(pending) == 1 else state.get("unit_stage"))
                if stage == "finalize_unit":
                    stage = previous.get("failed_stage") if previous else None
                if stage not in UNIT_STAGES:
                    raise RecoveryError("missing recorded failed stage; manual inspection required")
                review_resume = stage == "review"
                overrides = dict(state.get("recovery_call_overrides") or {})
                if review_resume and not continue_attempt:
                    raise RecoveryError("review requires --continue-attempt "
                                        "under remaining allowance")
                if continue_attempt:
                    if stage not in {"implement", "review"}:
                        raise RecoveryError("--continue-attempt requires a model stage")
                    if (
                        state.get("implement_continuations", 0)
                        >= limits["max_implement_continuations"]
                    ):
                        raise RecoveryError("implementation continuation allowance exhausted")
                    expected_head = ((state.get("implementation") or {}).get("candidate_commit")
                                     if review_resume else state.get("pre_implement_ref")
                                     or state.get("attempt_base_commit"))
                    if actual["head"] != expected_head:
                        raise RecoveryError(
                            "unknown commit after interrupted model; inspect before continuing"
                        )
                    if review_resume:
                        identity_attempt = attempt_identity(state)
                        for operation in context.journal().operations():
                            if (operation.key.unit_id == identity
                                    and operation.key.kind.startswith("model.review")
                                    and operation.status != "completed"):
                                lookup = encoded([identity, operation.key.attempt_id,
                                                  operation.key.kind])
                                lookup = next((base for base, target in overrides.items()
                                               if target == operation.key.attempt_id), lookup)
                                overrides[lookup] = identity_attempt + ":review:" + key.id[:16]
                        state["implement_continuations"] = state.get(
                            "implement_continuations", 0) + 1
                        state["attempt_number"] = state.get("attempt_number", 1) + 1
                    else:
                        state.update(next_attempt(state, "continuation"))
                    state["continuation_feedback"] = (
                        "The operator explicitly acknowledged an interrupted attempt. Preserve its "
                        "partial files and finish the original approved unit. "
                        "Prior usage may be unknown."
                    )
                    stage = "review" if review_resume else "implement"
                elif stage == "implement":
                    raise RecoveryError("model stage requires explicit --continue-attempt")
                else:
                    expected_head = (state.get("implementation") or {}).get("candidate_commit")
                    if (
                        expected_head
                        and actual["head"] != expected_head
                        and stage != "normalize_candidate"
                    ):
                        raise RecoveryError(
                            "candidate HEAD changed since failure; inspect before retry"
                        )
                generation = (previous or state).get("recovery_generation", 0) + 1
                old_attempt = attempt_identity(state)
                state.update(
                    recovery_generation=generation,
                    recovery_entry=stage,
                    recovery_previous_outcome=previous,
                    recovery_amendment_id=key.id,
                    unit_initialized=True,
                    unit_failed_stage="",
                    status=Status.IMPLEMENTING,
                    limits=limits,
                    recovery_call_overrides=overrides,
                )
                state["implementation_attempt_id"] = (
                    state.get("implementation_attempt_id") or old_attempt
                )
                state["attempt_id"] = old_attempt if review_resume else (
                    old_attempt + f":recovery:{generation}")
                if add_budget_usd and stage == "test_gate":
                    state["recovery_entry"] = "recovery_decision"
                    state["unit_stage"] = "test_gate"
                    state["status"] = Status.TESTING
                patch["recovery_units"][identity] = state
                patch["recovery_fingerprints"][identity] = {"path": str(path), "value": actual}
            patch["recovery_queue"] = selected
            patch["recovery_target"] = "execute_unit"
        if not patch["recovery_queue"]:
            path = context.workspace_path
            patch["recovery_fingerprints"]["__run__"] = {
                "path": str(path),
                "value": fingerprint(path),
            }
    cap = limits.get("max_run_cost_usd")
    if cap is not None and sum(float(e.get("cost_usd") or 0) for e in costs) >= cap:
        raise RecoveryError("run budget exhausted; explicit resume --add-budget-usd N required")
    # Retain a fresh immutable capture before any attempt transition or replacement.
    manifest = capture(context, saver, reason="explicit recovery amendment", stage="recovery")
    relative = "amendments/" + key.id + ".json"
    intent = {
        "checkpoint_id": checkpoint,
        "request": request,
        "capture": str(manifest.relative_to(_directory(context))),
        "patch": relative,
        "acknowledged_operations": [o.key.id for o in context.journal().operations()
                                    if o.key.kind.startswith("model.")
                                    and o.status not in {"completed", "failed"}
                                    and (not unit_id or o.key.unit_id == unit_id)]
                                   if continue_attempt else [],
    }
    context.journal().intent(key, intent)
    _dump_state(owned_file(_directory(context), relative), patch, saver.serde)
    context.journal().finish(
        key,
        "completed",
        {"patch": relative, "sha256": file_hash(owned_file(_directory(context), relative))},
    )
    return patch
