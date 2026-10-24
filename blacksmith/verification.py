"""Trusted, exact-candidate gates and owned formatter commits for version-2 runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from blacksmith.commands import CommandError, command_scope_for_state, run_command
from blacksmith.gate import GateError, GateResult, _amend_if_changed
from blacksmith.gate_receipts import (
    FixReceipt,
    GateCommandReceipt,
    GateLayerReceipt,
    GateReceipt,
)
from blacksmith.path_policy import PolicyViolation, policy_for_state
from blacksmith.receipts import OperationKey, ReceiptError, attempt_identity, digest, encoded
from blacksmith.run_context import atomic_write, context_from_state

GATE_OUTPUT_TAIL_BYTES = 32768


def _now():
    return datetime.now(UTC).isoformat()


def _git(path, *args):
    return run_command(["git", "-C", str(path), *args], check=True).require_complete_output().stdout


def candidate_identity(path):
    """Observe the candidate, index and nonignored source; errors never mean clean."""
    return (
        _git(path, "rev-parse", "--verify", "HEAD^{commit}").strip(),
        _git(path, "rev-parse", "--verify", "HEAD^{tree}").strip(),
        _git(path, "status", "--porcelain", "-z", "--untracked-files=all"),
    )


def required_layers(state):
    context = context_from_state(state)
    unit = state.get("selected_unit")
    saved = next((u for u in context.prd().contract.work_units if unit and u.id == unit.id), None)
    if saved is None or saved != unit:
        raise GateError("selected unit differs from immutable execution contract")
    layers = tuple(
        dict.fromkeys(
            layer for layer in saved.layers if context.prd().contract.layers[layer] == "auto"
        )
    )
    if not layers:
        raise GateError("no automatic layers to verify")
    return layers


def _workspace(state, context):
    path = Path(state["worktree_path"]).resolve()
    unit = state.get("selected_unit")
    allowed = {context.workspace_path}
    if unit:
        allowed.add(context.unit_path(unit.id))
    if path not in allowed:
        raise GateError("gate workspace is not owned by this run/unit")
    return path


def _planned(toolchain, layers):
    groups = {}
    for layer in layers:
        groups.setdefault(toolchain.commands_for(layer), []).append(layer)
    commands = []
    rows = {}
    for (setup, test, lint, _), covered in groups.items():
        indices = []
        for stage, command in (("setup", setup), ("test", test), ("lint", lint)):
            if command is not None:
                if not command.strip():
                    raise GateError(f"empty {stage} command")
                indices.append(len(commands))
                commands.append(
                    GateCommandReceipt(stage=stage, command=command, layers=tuple(covered))
                )
        for layer in covered:
            rows[layer] = GateLayerReceipt(
                layer=layer, covered_by=covered[0], command_indices=tuple(indices)
            )
    return commands, [rows[layer] for layer in layers]


def _execute(path, planned):
    started = _now()
    try:
        result = run_command(planned.command, cwd=path, shell=True, toolchain=True)
    except ReceiptError:
        raise
    except Exception as exc:
        result = exc.result if isinstance(exc, CommandError) else None
        if result is None:
            log_dir = getattr(exc, "command_log_dir", None)
            row = planned.model_copy(
                update={
                    "outcome": "error",
                    "started_at": started,
                    "finished_at": _now(),
                    "output_artifact_paths": tuple(
                        str(log_dir / name) for name in ("stdout.log", "stderr.log", "outcome.json")
                    )
                    if log_dir
                    else (),
                }
            )
            row = _seal_command(row)
            return row, f"$ {planned.command}\ncommand error: {exc}"
    outcome = "timeout" if result.timed_out else "passed" if result.returncode == 0 else "failed"
    row = planned.model_copy(
        update={
            "exit_code": result.returncode,
            "started_at": started,
            "finished_at": _now(),
            "output_artifact_paths": tuple(
                str(result.log_dir / name) for name in ("stdout.log", "stderr.log", "outcome.json")
            ),
            "outcome": outcome,
        }
    )
    row = _seal_command(row)
    return row, f"$ {planned.command}\n{result.stdout_tail}{result.stderr_tail}"


def _seal_command(row):
    try:
        hashes = tuple(
            hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in row.output_artifact_paths
        )
    except OSError as exc:
        raise ReceiptError(
            "critical command artifacts missing before verification receipt"
        ) from exc
    return row.model_copy(update={"output_artifact_sha256": hashes})


def _artifact(context, folder, operation):
    return context.directory / folder / f"{operation.id}.json"


def _save(context, operation, record, *, passing):
    data = record.model_dump(mode="json")
    path = _artifact(context, "gates" if isinstance(record, GateReceipt) else "fixes", operation)
    if record.artifact_path != str(path):
        raise ReceiptError("verification artifact identity mismatch")
    try:
        atomic_write(path, encoded(data) + "\n")
    except OSError as exc:
        raise ReceiptError(f"critical verification artifact write failed: {path}") from exc
    context.journal().finish(operation, "completed" if passing else "failed", {"receipt": data})


def _load(context, operation, previous, cls):
    try:
        record = cls.model_validate(previous.outcome["receipt"])
        path = _artifact(context, "gates" if cls is GateReceipt else "fixes", operation)
        if record.artifact_path != str(path) or json.loads(path.read_text()) != record.model_dump(
            mode="json"
        ):
            raise ValueError("verification artifact mismatch")
        if record.receipt_id != operation.id or record.run_id != context.run_id:
            raise ValueError("verification identity mismatch")
        for command in record.commands:
            if _seal_command(command).output_artifact_sha256 != command.output_artifact_sha256:
                raise ValueError("verification command artifact hash mismatch")
        return record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReceiptError("missing/corrupt verification receipt artifact") from exc


def result_for_receipt(receipt):
    ran = [row.command for row in receipt.commands if row.outcome != "not_run"]
    return GateResult(
        receipt.outcome == "passed", receipt.output or receipt.message, " && ".join(ran), receipt
    )


def verify_gate_receipt(receipt, state, *, layers=None):
    """Publication/join primitive: verify durable evidence against current truth."""
    context = context_from_state(state)
    layers = tuple(layers) if layers is not None else required_layers(state)
    if not isinstance(receipt, GateReceipt):
        raise GateError("missing typed gate receipt")
    if receipt.scope == "unit" and layers != required_layers(state):
        raise GateError("unit gate layers differ from immutable required layers")
    with command_scope_for_state(state, "verify_gate_receipt"):
        path = _workspace(state, context)
        head, tree, dirty = candidate_identity(path)
    if (
        receipt.outcome != "passed"
        or receipt.run_id != context.run_id
        or receipt.toolchain_hash != context.toolchain_sha256
        or receipt.layers != layers
        or (receipt.candidate_commit, receipt.candidate_tree) != (head, tree)
        or dirty
    ):
        raise GateError("gate receipt does not match the clean current candidate/layers/toolchain")
    if receipt.scope == "unit" and receipt.scope_id != state["selected_unit"].id:
        raise GateError("gate receipt belongs to a different unit")
    return _recorded_gate(receipt, context, head, tree, layers)


def verify_historical_unit_gate(receipt, state, *, candidate_commit, layers):
    """Check a prior unit's critical evidence against its immutable Git object.

    Sequential units share a workspace whose HEAD advances. This validates their
    original candidate without treating that old receipt as a gate for today's HEAD.
    """
    context = context_from_state(state)
    if (not isinstance(receipt, GateReceipt) or receipt.scope != "unit"
            or receipt.scope_id != state["selected_unit"].id
            or receipt.outcome != "passed" or receipt.run_id != context.run_id
            or receipt.candidate_commit != candidate_commit
            or receipt.toolchain_hash != context.toolchain_sha256
            or receipt.layers != tuple(layers)):
        raise GateError("unit outcome lacks matching recorded gate evidence")
    path = _workspace(state, context)
    tree = _git(path, "rev-parse", "--verify", candidate_commit + "^{tree}").strip()
    if tree != receipt.candidate_tree:
        raise GateError("historical unit tree differs from gate evidence")
    return _recorded_gate(receipt, context, candidate_commit, tree, tuple(layers))


def _recorded_gate(receipt, context, head, tree, layers):
    operation = OperationKey(
        context.run_id,
        receipt.scope_id,
        receipt.attempt_id,
        "gate."
        + receipt.scope
        + ":"
        + digest(
            encoded(
                [
                    receipt.scope_id,
                    head,
                    tree,
                    context.toolchain_sha256,
                    layers,
                    "",
                    "",
                ]
            )
        ),
    )
    previous = context.journal().get(operation)
    if previous is None or previous.status != "completed":
        raise ReceiptError("passing gate has no completed critical receipt")
    if _load(context, operation, previous, GateReceipt) != receipt:
        raise ReceiptError("gate evidence differs from critical receipt")
    return receipt


def run_candidate_gate(
    state, *, scope="unit", scope_id=None, layers=None, failure=None, failure_outcome="error"
):
    """Gate all required tuples, or persist a fresh nonpassing stage diagnostic."""
    context = context_from_state(state)
    unit = state.get("selected_unit")
    scope_id = scope_id or (unit.id if unit else "__run__")
    attempt = attempt_identity(state)
    started = _now()
    head = tree = None
    dirty = ""
    commands, rows = [], []
    output_tail = bytearray()
    output_truncated = False
    outcome = failure_outcome if failure else "passed"
    message = str(failure) if failure else ""
    with command_scope_for_state(state, f"gate_{scope}"):
        try:
            layers = tuple(layers) if layers is not None else required_layers(state)
            if scope == "unit" and layers != required_layers(state):
                raise GateError("unit gate layers differ from immutable required layers")
            commands, rows = _planned(context.toolchain(), layers)
            path = _workspace(state, context)
            head, tree, dirty = candidate_identity(path)
            expected = (state.get("implementation") or {}).get("candidate_commit")
            if scope == "unit" and not failure and expected != head:
                raise GateError("candidate differs from the owned implementation/fixer commit")
            if dirty:
                outcome = "dirty"
                message = message or "candidate has tracked/index/nonignored source changes"
        except ReceiptError:
            raise
        except Exception as exc:
            outcome, message = "error", str(exc)
            layers = tuple(layers or ())
        if tuple(row.layer for row in rows) != layers:
            rows = [
                GateLayerReceipt(layer=layer, covered_by=layer, command_indices=())
                for layer in layers
            ]
        operation = OperationKey(
            context.run_id,
            scope_id,
            attempt,
            "gate."
            + scope
            + ":"
            + digest(
                encoded([scope_id, head, tree, context.toolchain_sha256, layers, dirty, message])
            ),
        )
        previous = context.journal().intent(
            operation,
            {
                "candidate_commit": head,
                "candidate_tree": tree,
                "scope": scope,
                "scope_id": scope_id,
                "layers": layers,
                "toolchain_hash": context.toolchain_sha256,
                "workspace": str(state.get("worktree_path")),
                "diagnostic": message,
            },
        )
        if not previous.created:
            if previous.status in {"completed", "failed"}:
                record = _load(context, operation, previous, GateReceipt)
                if record.outcome == "passed":
                    verify_gate_receipt(record, state, layers=layers)
                return result_for_receipt(record)
            raise ReceiptError("interrupted_gate_requires_reconciliation")
        if outcome == "passed":
            try:
                for index, planned in enumerate(commands):
                    row, output = _execute(path, planned)
                    commands[index] = row
                    output_tail.extend((output + "\n").encode("utf-8"))
                    if len(output_tail) > GATE_OUTPUT_TAIL_BYTES:
                        del output_tail[:-GATE_OUTPUT_TAIL_BYTES]
                        output_truncated = True
                    current_head, current_tree, dirty = candidate_identity(path)
                    if dirty or (current_head, current_tree) != (head, tree):
                        outcome, message = "dirty", "gate command changed candidate/source/index"
                    elif row.outcome != "passed":
                        outcome, message = row.outcome, f"{row.stage} command did not pass"
                    for n, layer in enumerate(rows):
                        if index in layer.command_indices:
                            indices = layer.command_indices
                            status = (
                                outcome
                                if outcome != "passed"
                                else (
                                    "passed"
                                    if all(commands[i].outcome == "passed" for i in indices)
                                    else "not_run"
                                )
                            )
                            rows[n] = layer.model_copy(update={"outcome": status})
                    if outcome != "passed":
                        break
            except ReceiptError:
                raise
            except Exception as exc:
                outcome, message = "error", str(exc)
        artifact = _artifact(context, "gates", operation)
        fix = state.get("fix_receipt")
        record = GateReceipt(
            receipt_id=operation.id,
            run_id=context.run_id,
            scope=scope,
            scope_id=scope_id,
            attempt_id=attempt,
            candidate_commit=head,
            candidate_tree=tree,
            toolchain_hash=context.toolchain_sha256,
            layers=layers,
            layer_results=tuple(rows),
            commands=tuple(commands),
            started_at=started,
            finished_at=_now(),
            output_artifact_paths=tuple(p for row in commands for p in row.output_artifact_paths),
            artifact_path=str(artifact),
            outcome=outcome,
            message=message,
            output=(
                f"[gate output truncated; command artifacts: {artifact}]\n"
                if output_truncated
                else ""
            )
            + output_tail.decode("utf-8", errors="replace")
            + message,
            fix_receipt_id=fix.receipt_id if fix else None,
        )
        _save(context, operation, record, passing=outcome == "passed")
        return result_for_receipt(record)


def fix_candidate(state):
    """Run trusted formatters only on the attempt's journal-owned commit."""
    context = context_from_state(state)
    unit = state["selected_unit"]
    attempt = attempt_identity(state)
    commit_key = OperationKey(context.run_id, unit.id, attempt, "git.commit")
    owner = context.journal().get(commit_key)
    if owner and owner.status != "completed":
        owner = context.journal().get(OperationKey(
            context.run_id, unit.id, attempt, "git.commit.reconciliation",
        ))
    if owner is None or owner.status != "completed" or not owner.outcome.get("commit"):
        raise GateError("formatter requires an implementation commit owned by this attempt")
    before = owner.outcome["commit"]
    key = OperationKey(context.run_id, unit.id, attempt, "git.fix:" + before)
    started = _now()
    commands = []
    head = before
    outcome, message = "passed", ""
    with command_scope_for_state(state, "fix_candidate"):
        path = _workspace(state, context)
        layers = required_layers(state)
        planned = {}
        for layer in layers:
            command = context.toolchain().commands_for(layer)[3]
            if command is not None:
                if not command.strip():
                    raise GateError("empty formatter command")
                planned.setdefault(command, []).append(layer)
        previous = context.journal().intent(
            key,
            {
                "before_commit": before,
                "workspace": str(path),
                "layers": layers,
                "toolchain_hash": context.toolchain_sha256,
            },
        )
        if not previous.created:
            if previous.status in {"completed", "failed"}:
                record = _load(context, key, previous, FixReceipt)
                actual_head, _, dirty = candidate_identity(path)
                if record.eligible and (actual_head != record.candidate_commit or dirty):
                    raise GateError("formatter receipt does not match current clean candidate")
                return record
            raise ReceiptError("interrupted_formatter_requires_reconciliation")
        try:
            actual_head, _, dirty = candidate_identity(path)
            if actual_head != before or dirty:
                raise GateError("formatter requires the clean owned implementation commit")
            if commit_key.trailer not in _git(path, "log", "-1", "--format=%B").splitlines():
                raise GateError("formatter commit ownership trailer is missing")
            for command, covered in planned.items():
                row, _ = _execute(
                    path, GateCommandReceipt(stage="fix", command=command, layers=tuple(covered))
                )
                commands.append(row)
                actual_head, _, dirty = candidate_identity(path)
                if actual_head != head:
                    outcome, message = "dirty", "formatter changed Git HEAD"
                    break
                if row.outcome != "passed":
                    if dirty:
                        outcome, message = "dirty", "failed formatter left source changes"
                        break
                    if row.outcome == "timeout":
                        outcome, message = "timeout", "formatter timed out"
                        break
                    outcome, message = "clean_failure", "formatter failed without source changes"
                    continue
                policy_for_state(state, path).audit(state.get("pre_implement_ref") or before)
                _amend_if_changed(path)
                head, _, dirty = candidate_identity(path)
                policy_for_state(state, path).audit(state.get("pre_implement_ref") or before)
                if dirty:
                    outcome, message = "dirty", "formatter staging/amend left source changes"
                    break
                if commit_key.trailer not in _git(path, "log", "-1", "--format=%B").splitlines():
                    raise GateError("formatter amend lost commit ownership")
        except ReceiptError:
            raise
        except PolicyViolation as exc:
            outcome, message = "dirty", str(exc)
        except Exception as exc:
            outcome, message = "error", str(exc)
        record = FixReceipt(
            receipt_id=key.id,
            run_id=context.run_id,
            unit_id=unit.id,
            attempt_id=attempt,
            before_commit=before,
            candidate_commit=head,
            toolchain_hash=context.toolchain_sha256,
            commands=tuple(commands),
            outcome=outcome,
            started_at=started,
            finished_at=_now(),
            artifact_path=str(_artifact(context, "fixes", key)),
            message=message,
        )
        _save(context, key, record, passing=record.eligible)
        return record
