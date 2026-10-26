"""Explicit repair of interrupted artifact publication, preserving original receipts."""

from __future__ import annotations

from blacksmith.receipts import digest, encoded
from blacksmith.recovery import (
    RecoveryError,
    _directory,
    _identity,
    _key,
    _read,
    file_hash,
    owned_file,
)
from blacksmith.run_context import atomic_write


def resolution(context, operation):
    proof = context.journal().get(_key(context, operation.key.id, "recovery.repair"))
    if not proof or proof.status != "completed":
        return None
    if proof.intent.get("operation_id") != operation.key.id:
        raise RecoveryError("repair proof belongs to a different operation")
    result = proof.outcome
    if result["disposition"] == "retained_partial":
        root = _directory(context)
        for row in result["inventory"]:
            if file_hash(owned_file(root, row["path"])) != row["sha256"]:
                raise RecoveryError("changed retained partial recovery artifact")
    elif result["disposition"] != "reconciled":
        raise RecoveryError("invalid recovery repair disposition")
    return result


def observed_outcome(context, operation):
    if operation.status == "completed":
        return operation.outcome
    resolved = resolution(context, operation)
    return resolved.get("observed") if resolved else None


def _inventory(root, path):
    paths = sorted(path.rglob("*")) if path.is_dir() else [path] if path.exists() else []
    result = []
    for item in paths:
        if item.is_symlink() or item.resolve() != item:
            raise RecoveryError("link in partial recovery artifacts")
        if item.is_file():
            result.append({"path": str(item.relative_to(root)), "sha256": file_hash(item)})
    return result


def _verify_capture(context, operation, seal):
    root = _directory(context)
    intent = seal.intent
    entry, record = intent["entry"], intent["record"]
    if (seal.status not in {"intent", "completed"}
            or intent["operation_id"] != operation.key.id
            or entry["id"] != operation.key.attempt_id
            or entry["manifest"] != operation.key.attempt_id + "/manifest.json"
            or (seal.status == "completed" and seal.outcome != {"entry": entry})
            or digest(encoded(record) + "\n") != entry["sha256"]
            or any(record.get(k) != v for k, v in _identity(context).items())):
        raise RecoveryError("invalid critical capture seal")
    manifest = owned_file(root, entry["manifest"])
    for row in record["files"]:
        if file_hash(owned_file(manifest.parent, row["path"])) != row["sha256"]:
            raise RecoveryError("changed sealed capture file; repair refused")
    if manifest.exists() and file_hash(manifest) != entry["sha256"]:
        raise RecoveryError("changed sealed recovery manifest; repair refused")
    return entry, record


def _repair(context, operation_id):
    """Reconcile only sealed bytes; retain an unsealed partial as failed evidence."""
    journal, root = context.journal(), _directory(context)
    operation = next((o for o in journal.operations() if o.key.id == operation_id), None)
    if operation is None or operation.key.kind not in {"recovery.capture", "recovery.amend"}:
        raise RecoveryError("select a recorded recovery.capture or recovery.amend operation")
    if operation.status == "completed":
        raise RecoveryError("operation already complete; no repair performed")
    if result := resolution(context, operation):
        return result
    index = _read(root / "index.json")
    if (not isinstance(index, dict) or not isinstance(index.get("records"), list)
            or any(index.get(k) != v for k, v in _identity(context).items())):
        raise RecoveryError("corrupt recovery index; retain for manual inspection")
    entry = record = None
    if operation.key.kind == "recovery.capture":
        partial = owned_file(root, operation.key.attempt_id)
        if str(partial) != operation.intent["path"]:
            raise RecoveryError("capture intent path differs from run ownership")
        seal = journal.get(_key(context, operation.key.attempt_id, "recovery.capture.seal"))
        if seal:
            entry, record = _verify_capture(context, operation, seal)
            duplicates = [e for e in index["records"] if e["id"] == entry["id"]]
            if duplicates and duplicates != [entry]:
                raise RecoveryError("conflicting recovery index entry; repair refused")
            result = {"disposition": "reconciled", "observed": entry}
        else:
            if any(e["id"] == operation.key.attempt_id for e in index["records"]):
                raise RecoveryError("unsealed capture already indexed; retain for inspection")
            result = {"disposition": "retained_partial", "inventory": _inventory(root, partial)}
    else:
        partial = owned_file(root, operation.intent["patch"])
        expected = operation.intent.get("patch_sha256")
        if expected and partial.exists():
            if file_hash(partial) != expected:
                raise RecoveryError("changed sealed recovery amendment; repair refused")
            result = {"disposition": "reconciled", "observed": {
                "patch": operation.intent["patch"], "sha256": expected}}
        else:
            result = {"disposition": "retained_partial", "inventory": _inventory(root, partial)}
    key = _key(context, operation.key.id, "recovery.repair")
    journal.intent(key, {"operation_id": operation.key.id, "resolution": result})
    if entry:
        manifest = owned_file(root, entry["manifest"])
        if not manifest.exists():
            atomic_write(manifest, encoded(record) + "\n")
        if not any(e["id"] == entry["id"] for e in index["records"]):
            atomic_write(root / "index.json", encoded(
                {**index, "records": [*index["records"], entry]}) + "\n")
    journal.finish(key, "completed", result)
    return result


def repair(context, operation_id):
    try:
        return _repair(context, operation_id)
    except (KeyError, TypeError, ValueError, AttributeError, RuntimeError) as exc:
        raise RecoveryError("missing/corrupt critical repair metadata; "
                            "retained for inspection") from exc
