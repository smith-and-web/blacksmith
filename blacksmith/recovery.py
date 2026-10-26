"""Critical, immutable recovery snapshots; never a source of invented acceptance."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from blacksmith.commands import command_scope, run_command
from blacksmith.receipts import OperationKey, ReceiptError, digest, encoded
from blacksmith.run_context import atomic_write
from blacksmith.state import Status


class RecoveryError(ReceiptError):
    """Recovery evidence is incomplete or inconsistent; admit no new paid work."""


def _key(context, attempt, kind):
    return OperationKey(context.run_id, "__run__", attempt, kind)


def _identity(context):
    return {"version": 1, "run_id": context.run_id,
            "context_sha256": digest(encoded(context.model_dump()))}


def _directory(context):
    path = context.directory / "recovery"
    if path.is_symlink() or path.resolve() != path:
        raise RecoveryError("foreign recovery directory")
    return path


def initialize(context):
    root = _directory(context)
    root.mkdir(mode=0o700)
    identity = _identity(context)
    key = _key(context, "recovery", "recovery.initialize")
    journal = context.journal()
    journal.intent(key, identity)
    atomic_write(root / "index.json", encoded({**identity, "records": []}) + "\n")
    journal.finish(key, "completed", identity)


def file_hash(path):
    result = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _read(path):
    try:
        if path.is_symlink():
            raise ValueError("symlink artifact")
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RecoveryError(f"missing/corrupt recovery artifact: {path}") from exc


def owned_file(root, relative):
    path = Path(relative)
    if path.is_absolute() or any(part in {"..", "."} for part in path.parts):
        raise RecoveryError("invalid recovery artifact path")
    result = root / path
    if result.resolve() != result or result.is_symlink():
        raise RecoveryError("foreign/symlink recovery artifact")
    return result


def _validate(context, *, allow_pending=False):
    from blacksmith.recovery_repair import observed_outcome, resolution
    root = _directory(context)
    index = _read(root / "index.json")
    if (not isinstance(index, dict) or not isinstance(index.get("records"), list)
            or type(index.get("version")) is not int):
        raise RecoveryError("missing/corrupt recovery index schema")
    identity = _identity(context)
    if any(index.get(k) != v for k, v in identity.items()):
        raise RecoveryError("recovery index belongs to a different run/context")
    journal = context.journal()
    initial = journal.get(_key(context, "recovery", "recovery.initialize"))
    if not initial or initial.status != "completed" or initial.outcome != identity:
        raise RecoveryError("missing critical recovery initialization receipt")
    seen = set()
    for entry in index.get("records", []):
        name = entry["id"]
        UUID(name)
        if name in seen:
            raise RecoveryError("duplicate recovery record")
        seen.add(name)
        path = owned_file(root, entry["manifest"])
        record = _read(path)
        if file_hash(path) != entry["sha256"]:
            raise RecoveryError("changed recovery manifest")
        if any(record.get(k) != v for k, v in identity.items()):
            raise RecoveryError("recovery snapshot belongs to a different run")
        owner = journal.get(_key(context, name, "recovery.capture"))
        if not owner or observed_outcome(context, owner) != entry:
            raise RecoveryError("recovery manifest lacks its matching critical receipt")
        for row in record["files"]:
            artifact = owned_file(path.parent, row["path"])
            try:
                if not artifact.is_file() or file_hash(artifact) != row["sha256"]:
                    raise RecoveryError(f"changed/missing recovery file: {artifact}")
            except OSError as exc:
                raise RecoveryError(f"unreadable recovery file: {artifact}") from exc
    for operation in journal.operations():
        if operation.key.kind == "recovery.amend":
            observed = observed_outcome(context, operation)
            if observed is None:
                if resolution(context, operation) or allow_pending:
                    continue
                raise RecoveryError("incomplete recovery amendment; retained for inspection")
            patch = owned_file(root, observed["patch"])
            if file_hash(patch) != observed["sha256"]:
                raise RecoveryError("changed recovery amendment")
        if operation.key.kind == "recovery.capture":
            observed = observed_outcome(context, operation)
            if observed and operation.key.attempt_id not in seen:
                raise RecoveryError("recovery index is missing a completed capture")
            if (not observed and not resolution(context, operation) and not allow_pending):
                raise RecoveryError("incomplete recovery capture; use recovery inspect first")
    return index


def validate(context, *, allow_pending=False):
    try:
        return _validate(context, allow_pending=allow_pending)
    except (RecoveryError, ReceiptError):
        raise
    except (KeyError, TypeError, ValueError, OSError, AttributeError, RuntimeError) as exc:
        raise RecoveryError(f"missing/corrupt recovery metadata: {_directory(context)}") from exc


def _git(path, *args):
    return run_command(["git", "-C", str(path), *args], check=True).require_complete_output()


def _dump_state(path, values, serde):
    atomic_write(path, serialize_state(values, serde))


def serialize_state(values, serde):
    kind, data = serde.dumps_typed(values)
    return encoded({"kind": kind, "data": base64.b64encode(data).decode()}) + "\n"


def load_state(context, entry_id, snapshot):
    index = validate(context)
    entry = next((e for e in index["records"] if e["id"] == entry_id), None)
    if not entry:
        raise RecoveryError("missing recorded recovery point")
    manifest = owned_file(_directory(context), entry["manifest"])
    record = _read(manifest)
    if snapshot not in [s["state_path"] for s in record["checkpoints"]]:
        raise RecoveryError("unknown recovery state snapshot")
    row = _read(owned_file(manifest.parent, snapshot))
    from blacksmith.graph import blacksmith_serde
    if row["kind"] not in {"msgpack", "json"}:
        raise RecoveryError("unsupported recovery state encoding")
    return blacksmith_serde().loads_typed(
        (row["kind"], base64.b64decode(row["data"], validate=True)),
    )


def latest_checkpoints(checkpointer, context):
    rows, seen = [], set()
    for saved in checkpointer.list({"configurable": {"thread_id": context.thread_id}}):
        namespace = saved.config["configurable"]["checkpoint_ns"]
        if namespace in seen:
            continue
        seen.add(namespace)
        values = saved.checkpoint["channel_values"]
        if values.get("run_id") != context.run_id:
            raise RecoveryError("checkpoint namespace belongs to a different run")
        rows.append((saved.config, values))
    return rows


def _copy(source, destination):
    if source.is_symlink() or source.resolve() != source:
        raise RecoveryError(f"symlink in critical run artifacts: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    destination.chmod(0o600)


def _workspace(context, source, destination, *, readonly=False):
    if source.is_symlink() or source.resolve() != source:
        raise RecoveryError("recovery workspace has foreign ownership")
    destination.mkdir(parents=True, mode=0o700)
    head = _git(source, "rev-parse", "HEAD").stdout.strip()
    before = _git(source, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    # Explicit output artifacts preserve full binary patches without unbounded stdout.
    _git(source, "diff", "--binary", "--output=" + str(destination / "unstaged.patch"))
    _git(source, "diff", "--cached", "--binary", "--output=" + str(destination / "staged.patch"))
    names = _git(source, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
    links = {}
    for name in filter(None, names):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RecoveryError("unsafe untracked artifact path")
        original = source / relative
        if original.is_symlink():
            links[name] = os.readlink(original)
        else:
            if original.resolve() != original:
                raise RecoveryError("untracked path traverses a symlink")
            _copy(original, destination / "untracked" / relative)
    atomic_write(destination / "symlinks.json", encoded(links) + "\n")
    if not readonly:
        # Preserve orphaned operation commits (e.g. a failed join rolled back to its base).
        refs = _git(source, "reflog", "--all", "--format=%H").stdout.splitlines()
        for number, commit in enumerate(dict.fromkeys([head, *refs])):
            ref = f"refs/blacksmith/recovery/{destination.parent.name}/{number}"
            _git(source, "update-ref", ref, commit)
    _git(source, "bundle", "create", str(destination / "refs.bundle"), "--all")
    if (head != _git(source, "rev-parse", "HEAD").stdout.strip()
            or before != _git(source, "status", "--porcelain=v1", "-z",
                              "--untracked-files=all").stdout):
        raise RecoveryError("workspace changed during recovery capture; retain partial snapshot")
    return {"path": str(source), "head": head, "status": before,
            "snapshot_path": str(destination.name)}


def capture(context, checkpointer, *, reason, stage="cli"):
    context.execution_config().require_supported_execution()
    index = validate(context)
    name = str(uuid4())
    root = _directory(context)
    directory = root / name
    directory.mkdir(mode=0o700)
    journal = context.journal()
    key = _key(context, name, "recovery.capture")
    journal.intent(key, {"stage": stage, "reason": str(reason), "path": str(directory)})
    record = {**_identity(context), "id": name, "stage": stage, "reason": str(reason),
              "checkpoints": [], "workspaces": []}
    with command_scope(context, unit_id="__run__", attempt_id=name, stage="recovery_capture"):
        for config, values in latest_checkpoints(checkpointer, context) if checkpointer else []:
            namespace = config["configurable"]["checkpoint_ns"]
            state_path = f"state/{digest(namespace)}.json"
            _dump_state(directory / state_path, values, checkpointer.serde)
            unit = values.get("selected_unit")
            record["checkpoints"].append({"config": config, "state_path": state_path,
                                          "unit_id": unit.id if namespace and unit else None,
                                          "stage": values.get("unit_failed_stage")
                                                   or values.get("unit_stage"),
                                          "generation": values.get("recovery_generation", 0)})
        paths = [("workspace", context.workspace_path)]
        paths += [("unit-" + digest(u.id), context.unit_path(u.id))
                  for u in context.prd().contract.work_units]
        existing = [(label, path) for label, path in paths if path.exists() or path.is_symlink()]
        if not existing and context.planning_path.exists():
            existing = [("planning", context.planning_path)]
        for label, path in existing:
            record["workspaces"].append(_workspace(context, path, directory / label,
                                                     readonly=label == "planning"))
        for folder in ("plans", "gates", "fixes", "joins", "commands", "reviews", "publication"):
            source = context.directory / folder
            if source.exists() or source.is_symlink():
                if source.is_symlink() or source.resolve() != source:
                    raise RecoveryError("symlink in critical artifact directory")
                for path in source.rglob("*"):
                    if path.is_symlink():
                        raise RecoveryError("symlink in critical artifacts")
                    if path.is_file():
                        _copy(path, directory / "artifacts" / path.relative_to(context.directory))
        for filename in ("manifest.json", "input.prd.md", "runtime-config.json",
                         "trusted-blacksmith.toml"):
            _copy(context.directory / filename, directory / "artifacts" / filename)
        with journal._connection() as connection:
            backup = sqlite3.connect(directory / "operations.sqlite")
            try:
                connection.backup(backup)
            finally:
                backup.close()
    for path in directory.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in sorted([directory, *[p for p in directory.rglob("*") if p.is_dir()]],
                       key=lambda p: len(p.parts), reverse=True):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    record["files"] = [{"path": str(p.relative_to(directory)), "sha256": file_hash(p),
                         "bytes": p.stat().st_size}
                        for p in sorted(directory.rglob("*")) if p.is_file()]
    record["bytes"] = sum(row["bytes"] for row in record["files"])
    manifest_text = encoded(record) + "\n"
    entry = {"id": name, "manifest": f"{name}/manifest.json",
             "sha256": hashlib.sha256(manifest_text.encode()).hexdigest()}
    seal = _key(context, name, "recovery.capture.seal")
    journal.intent(seal, {"operation_id": key.id, "entry": entry, "record": record})
    journal.finish(seal, "completed", {"entry": entry})
    atomic_write(directory / "manifest.json", manifest_text)
    # Both writes are critical; a crash between them is diagnosed before any paid call.
    atomic_write(root / "index.json", encoded(
        {**index, "records": [*index["records"], entry]},
    ) + "\n")
    journal.finish(key, "completed", entry)
    return directory / "manifest.json"


def inspect(context):
    index = validate(context)
    settings = context.execution_config()
    from blacksmith.receipts import (
        MODEL_TERMINAL_POLICY_VERSION,
        LegacyModelTerminalPolicyError,
        validate_model_terminal_policy,
    )
    policy = {"version": MODEL_TERMINAL_POLICY_VERSION, "supported": True}
    try:
        validate_model_terminal_policy(context.journal())
    except LegacyModelTerminalPolicyError as exc:
        policy.update(supported=False, diagnostic=str(exc))
    records = []
    for entry in index["records"]:
        record = _read(owned_file(_directory(context), entry["manifest"]))
        records.append({**entry, "bytes": record["bytes"], "stage": record["stage"],
                        "reason": record["reason"], "workspaces": record["workspaces"]})
    return {**_identity(context), "recovery_path": str(_directory(context)),
            "records": records, "total_snapshot_bytes": sum(r["bytes"] for r in records),
            "model_terminal_policy": policy,
            # Historical settings remain readable, but neither deferred feature can
            # execute. Report the saved values, never the caller's changed config.
            "disabled_features": {
                "respond": {"supported": False, "status": "unsupported_until_hardened",
                            "reserved_max_attempts": settings.respond.max_attempts},
                "sandbox": {"supported": False, "status": "unsupported_until_hardened",
                            "enabled_in_saved_config": settings.sandbox.enabled}},
            "uncertain_model_calls": [op.key.id for op in context.journal().operations()
                                      if op.key.kind.startswith("model.")
                                      and op.status not in {"completed", "failed"}]}


def diagnose(context):
    """Read-only metadata and repair guidance remain available for incomplete captures."""
    from blacksmith.recovery_repair import resolution
    try:
        result = {**inspect(context), "valid": True}
    except (ReceiptError, OSError) as exc:
        result = {**_identity(context), "recovery_path": str(_directory(context)),
                  "valid": False, "integrity_error": str(exc)}
    result["pending_artifacts"] = []
    for operation in context.journal().operations():
        if (operation.key.kind in {"recovery.capture", "recovery.amend"}
                and operation.status != "completed" and not resolution(context, operation)):
            command = ["blacksmith", "recovery", "repair", "--run-dir", str(context.directory),
                       "--operation-id", operation.key.id]
            result["pending_artifacts"].append({"operation_id": operation.key.id,
                "kind": operation.key.kind, "status": operation.status,
                "next_action": shlex.join(command)})
    return result


@contextmanager
def protect_run(context, checkpointer):
    """Capture before releasing the run lock, on terminal failure or raised exception."""
    command = ["blacksmith", "recovery", "inspect", "--thread-id", context.thread_id]
    if context.runtime_config_path:
        command += ["--config", context.runtime_config_path]
    action = shlex.join(command)
    try:
        yield
    except BaseException as exc:
        from blacksmith.accounting import print_failure
        print_failure(context, out=sys.stderr)
        try:
            path = capture(context, checkpointer, reason=f"{type(exc).__name__}: {exc}")
            print(f"Recovery manifest: {path}\nNext action: {action}", file=sys.stderr)
        except BaseException as capture_error:
            fallback = shlex.join(["blacksmith", "recovery", "inspect", "--run-dir",
                                   str(context.directory)])
            print(f"Critical recovery capture failed: {capture_error}; retained: "
                  f"{_directory(context)}\nNext action: {fallback}", file=sys.stderr)
            if isinstance(exc, Exception):
                raise RecoveryError("critical recovery capture failed; no further effects") from exc
        raise
    else:
        saved = checkpointer.get_tuple({"configurable": {"thread_id": context.thread_id}})
        values = saved.checkpoint["channel_values"] if saved else {}
        if values.get("status") == Status.HALTED:
            path = capture(context, checkpointer,
                           reason=values.get("errors") or "approval rejected")
            print(f"Recovery manifest: {path}\nNext action: {action}", file=sys.stderr)
