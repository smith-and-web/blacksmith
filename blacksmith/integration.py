"""Parent-only completeness checks and exact-candidate layer-union gates."""

from __future__ import annotations

from pathlib import Path

from blacksmith import verification
from blacksmith.commands import run_command
from blacksmith.gate import GateError, GateResult
from blacksmith.path_policy import policy_for_state
from blacksmith.planner import execution_levels
from blacksmith.receipts import OperationKey, ReceiptError
from blacksmith.run_context import context_from_state, run_branch
from blacksmith.state import Status


def git(path, *args, check=True):
    return run_command(["git", "-C", str(path), *args], check=check).require_complete_output()


def join_key(context, unit_id, level, kind="join.pick", *, generation=0):
    attempt = f"join:{level}" + (f":recovery:{generation}" if generation else "")
    return OperationKey(context.run_id, unit_id, attempt, kind)


def join_key_for(state, context, unit_id, level, kind="join.pick"):
    generation = (state.get("join_generations") or {}).get(f"{level}:{unit_id}", 0)
    return join_key(context, unit_id, level, kind, generation=generation)


def join_observed(context, key):
    original = context.journal().get(key)
    if original and original.status == "completed":
        return original
    proof = context.journal().get(OperationKey(
        key.run_id, key.unit_id, key.attempt_id, key.kind + ".reconciliation"))
    if (proof and proof.status == "completed"
            and proof.intent.get("original_operation") == key.id):
        return proof
    return original


def levels_for(state):
    if state.get("run_context"):
        levels = execution_levels(context_from_state(state).prd().contract)
        if state.get("execution_levels") != levels:
            raise GateError("execution levels differ from immutable contract")
        return levels
    return state.get("execution_levels") or []


def outcomes_for(state, *, through=None):
    """Exactly one keyed outcome per expected auto unit; no foreign/future identities."""
    levels = levels_for(state)
    contract = (context_from_state(state).prd().contract if state.get("run_context")
                else state["prd"].contract)
    expected = {u.id: (u, level) for level, units in enumerate(levels) for u in units}
    outcomes = state.get("unit_outcomes") or {}
    if state.get("level_builds"):
        raise GateError("duplicate/legacy unit outcome records require reconciliation")
    for key, row in outcomes.items():
        if key not in expected or row.get("unit_id") != key:
            raise GateError("foreign unit outcome or key/identity mismatch")
        if row.get("level") != expected[key][1]:
            raise GateError("unit outcome belongs to incorrect level")
        if through is not None and row["level"] > through:
            raise GateError("future unit outcome before integration")
    required = [(u, level) for u, level in expected.values()
                if contract.gate_for(u) != "human" and (through is None or level <= through)]
    for unit, _ in required:
        row = outcomes.get(unit.id)
        if not row or row.get("ok") is not True or row.get("human") is not False:
            detail = row.get("error", "") if row else "missing outcome"
            raise GateError(f"unit {unit.id} lacks exactly one successful auto outcome: {detail}")
    return [(u, outcomes[u.id]) for u, _ in required]


def layer_union(state, *, through=None):
    contract = (context_from_state(state).prd().contract if state.get("run_context")
                else state["prd"].contract)
    levels = execution_levels(contract) if state.get("run_context") else levels_for(state)
    return tuple(dict.fromkeys(layer for level, units in enumerate(levels)
                              if through is None or level <= through
                              for u in units for layer in u.layers
                              if contract.layers[layer] == "auto"))


def validate_saved_base(state, level):
    base = (state.get("level_bases") or {}).get(str(level))
    if not base:
        raise GateError("missing saved level base")
    if state.get("run_context"):
        context = context_from_state(state)
        receipt = context.journal().get(join_key(context, "__level__", level, "join.prepare"))
        expected = {"base_commit": base,
                    "unit_ids": [u.id for u in levels_for(state)[level]]}
        if (not receipt or receipt.status != "completed" or receipt.intent != expected
                or receipt.outcome != {"base_commit": base}):
            raise ReceiptError("saved level base differs from critical preparation receipt")
    return base


def validate_worker(state, unit, row, base):
    path = row.get("clone_path")
    source = row.get("source_commit")
    if not path or not source or row.get("base_commit") != base:
        raise GateError(f"unit {unit.id} has missing/wrong source/base commit")
    if state.get("run_context"):
        context = context_from_state(state)
        if (Path(path).resolve() != context.unit_path(unit.id)
                or row.get("branch") != run_branch(context.run_id, unit.id)):
            raise GateError("foreign worker clone/branch")
    head, _, dirty = verification.candidate_identity(path)
    if dirty or head != source:
        raise GateError(f"unit {unit.id} source changed or worker is dirty")
    if git(path, "rev-parse", source + "^").stdout.strip() != base:
        raise GateError("worker must contain exactly one normalized commit on saved level base")
    if git(path, "branch", "--show-current").stdout.strip() != row.get("branch"):
        raise GateError("worker branch differs from recorded identity")
    if state.get("run_context"):
        proof = row.get("gate_receipt")
        if not proof or proof.attempt_id != row.get("attempt_id"):
            raise GateError("worker attempt differs from gate receipt")
        worker = {**state, "selected_unit": unit, "worktree_path": path}
        verification.verify_gate_receipt(proof, worker, layers=verification.required_layers(worker))
        policy_for_state(worker, path).audit(base)


def verify_completed_units(state, *, through=None):
    records = outcomes_for(state, through=through)
    shared = state["worktree_path"]
    for unit, row in records:
        level = row["level"]
        parallel = state.get("fanout") and len(levels_for(state)[level]) > 1
        if parallel:
            base = validate_saved_base(state, level)
            validate_worker(state, unit, row, base)
            placed = (state.get("join_destinations") or {}).get(unit.id)
            if not placed or placed.get("source_commit") != row["source_commit"]:
                raise GateError(f"unit {unit.id} has no reconciled destination commit")
            destination = placed["destination_commit"]
            if (placed.get("level") != level or placed.get("level_base") != base
                    or git(shared, "rev-parse", destination + "^").stdout.strip()
                    != placed.get("parent_commit")
                    or git(shared, "rev-parse", destination + "^{tree}").stdout.strip()
                    != placed.get("destination_tree")):
                raise GateError("join destination parent/tree/base mapping differs from actual Git")
            if state.get("run_context"):
                context = context_from_state(state)
                receipt = join_observed(context, join_key_for(state, context, unit.id, level))
                commit = join_observed(context, join_key_for(
                    state, context, unit.id, level, "join.commit"))
                if (not receipt or receipt.status != "completed" or receipt.outcome != placed
                        or not commit or commit.status != "completed"
                        or commit.outcome != {"destination_commit": destination,
                                              "destination_tree": placed["destination_tree"]}):
                    raise ReceiptError("join destination differs from critical receipt")
        else:
            destination = row.get("source_commit")
            if (not destination
                    or Path(row.get("clone_path") or "").resolve() != Path(shared).resolve()):
                raise GateError("sequential outcome lacks owned source commit")
            if state.get("run_context"):
                worker = {**state, "selected_unit": unit, "worktree_path": shared}
                proof = row.get("gate_receipt")
                if not proof or proof.attempt_id != row.get("attempt_id"):
                    raise GateError("sequential attempt differs from gate receipt")
                verification.verify_historical_unit_gate(
                    proof, worker, candidate_commit=destination,
                    layers=verification.required_layers(worker),
                )
        if git(shared, "merge-base", "--is-ancestor", destination, "HEAD", check=False).returncode:
            raise GateError(f"unit {unit.id} destination is absent from current candidate")
    return records


def _gate(state, *, scope, gate=None, failure=None):
    level = state.get("level_cursor", 0)
    layers = layer_union(state, through=level if scope == "level" else None)
    try:
        if state.get("state_version") is not None:
            context_from_state(state)  # Lost context cannot enter direct-node compatibility.
        if failure:
            raise GateError(str(failure))
        if state.get("status") == Status.HALTED:
            raise GateError("halted run requires explicit recovery before verification")
        verify_completed_units(state, through=level if scope == "level" else None)
        if state.get("run_context"):
            context = context_from_state(state)
            policy_for_state(state, state["worktree_path"]).audit(context.base_commit)
            result = verification.run_candidate_gate(
                state, scope=scope, scope_id=str(level) if scope == "level" else "__run__",
                layers=layers,
            )
        elif gate:
            results = []
            for layer in layers:
                result = gate(state["worktree_path"], layer)
                results.append(result)
                if not result.passed:
                    break
            result = GateResult(all(r.passed for r in results),
                                "\n".join(r.output for r in results),
                                " && ".join(r.command for r in results))
        else:
            return {}  # Legacy graphs with no command gate cannot create typed evidence.
    except Exception as exc:
        if state.get("run_context"):
            # Run the primitive only to persist fresh nonpassing evidence, never commands.
            result = verification.run_candidate_gate(
                state, scope=scope, scope_id=str(level) if scope == "level" else "__run__",
                layers=layers, failure=str(exc),
            )
        else:
            result = GateResult(False, str(exc), "")
    field = "final_gate_receipt" if scope == "final" else "integration_gate_receipts"
    update = {"status": Status.TESTING if result.passed else Status.HALTED,
              "final_test_results" if scope == "final" else "integration_test_results":
                  result.as_test_results()}
    update[field] = (result.receipt if scope == "final"
                     else {**state.get(field, {}), str(level): result.receipt})
    if not result.passed:
        update["errors"] = [{"node": scope + "_gate", "message":
                             f"{scope} integration failed: {result.output}"}]
    return update


def integration_gate(state, *, gate=None):
    return _gate(state, scope="level", gate=gate)


def final_gate(state, *, gate=None, failure=None):
    if not state.get("run_context") and (not state.get("prd") or not state.get("execution_levels")):
        return {}  # Dependency-free historical skeleton only.
    return _gate(state, scope="final", gate=gate, failure=failure)


def verify_final_gate(state):
    if not state.get("run_context") and state.get("state_version") is None:
        return
    context_from_state(state)
    if state.get("status") == Status.HALTED:
        raise GateError("halted run requires explicit recovery before publication")
    verify_completed_units(state)
    verification.verify_gate_receipt(
        state.get("final_gate_receipt"), state, layers=layer_union(state),
    )
    receipt = state["final_gate_receipt"]
    if receipt.scope != "final" or receipt.scope_id != "__run__":
        raise GateError("final candidate lacks a complete final gate")
