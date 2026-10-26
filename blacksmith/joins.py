"""One exact-source join effect per checkpoint, with critical Git reconciliation."""

from __future__ import annotations

from pathlib import Path

from blacksmith.commands import CommandError, command_scope
from blacksmith.gate import GateError
from blacksmith.integration import (
    git,
    join_key,
    join_key_for,
    join_observed,
    levels_for,
    outcomes_for,
    validate_saved_base,
    validate_worker,
)
from blacksmith.receipts import OperationKey, ReceiptError
from blacksmith.run_context import atomic_write, context_from_state
from blacksmith.state import Status
from blacksmith.verification import candidate_identity


def prepare_level(state):
    level = state.get("level_cursor", 0)
    path = state.get("worktree_path")
    if not path:
        return {"prepared_level": level}
    head, _, dirty = candidate_identity(path)
    if dirty:
        raise GateError("cannot enter level with dirty shared candidate")
    bases = state.get("level_bases") or {}
    if str(level) in bases and bases[str(level)] != head:
        raise GateError("level base changed before preparation")
    if state.get("run_context"):
        context = context_from_state(state)
        if Path(path).resolve() != context.workspace_path:
            raise GateError("shared join workspace differs from immutable run")
        key = join_key(context, "__level__", level, "join.prepare")
        journal = context.journal()
        journal.intent(key, {"base_commit": head,
                             "unit_ids": [u.id for u in levels_for(state)[level]]})
        journal.finish(key, "completed", {"base_commit": head})
    return {"prepared_level": level, "level_bases": {**bases, str(level): head},
            "join_cursor": 0, "join_complete": False}


def _trailer_matches(path, head, parent, tree, operation, source):
    body = git(path, "show", "-s", "--format=%B", head).stdout.splitlines()
    return (operation.trailer in body and "Blacksmith-Source-Commit: " + source in body
            and git(path, "rev-parse", head + "^").stdout.strip() == parent
            and git(path, "rev-parse", head + "^{tree}").stdout.strip() == tree
            )


def _place(state, unit, row, base, parent):
    path, source = state["worktree_path"], row["source_commit"]
    level = state["level_cursor"]
    context = context_from_state(state) if state.get("run_context") else None
    journal = context.journal() if context else None
    key = join_key_for(state, context, unit.id, level) if context else None
    commit_key = join_key_for(state, context, unit.id, level, "join.commit") if context else None
    intent = {"level": level, "level_base": base, "parent_commit": parent,
              "source_commit": source, "clone_path": row["clone_path"]}
    previous = journal.intent(key, intent) if journal else None
    head, tree, dirty = candidate_identity(path)
    recovery = state.get("join_recovery") or {}
    saved = recovery.get("destinations", {}).get(unit.id)
    observed = join_observed(context, key) if context else None
    if previous and ((observed and observed.status == "completed") or saved):
        placed = observed.outcome if observed and observed.status == "completed" else saved
        if recovery.get("destinations", {}).get(unit.id) == placed:
            from blacksmith.recovery_control import amendment_for_state
            sealed = amendment_for_state(state)
            if sealed.get("join_recovery") != recovery:
                raise ReceiptError("join restore differs from sealed recovery amendment")
            pending = journal.get(commit_key)
            if (not pending or pending.intent != {**intent,
                                                  "destination_tree": placed["destination_tree"]}
                    or placed["operation_id"] != key.id):
                raise ReceiptError("saved join effect lacks original tree intent")
            restore = OperationKey(context.run_id, unit.id,
                key.attempt_id + ":restore:" + state["recovery_amendment_id"], "join.restore")
            restore_intent = {"original_operation": key.id, "parent": parent, **placed}
            if (not _trailer_matches(path, placed["destination_commit"], parent,
                                     placed["destination_tree"], key, source)):
                raise GateError("saved destination changed before join restore")
            if head == parent and not dirty:
                journal.intent(restore, restore_intent)
                git(path, "reset", "--hard", placed["destination_commit"])
                head, tree, dirty = candidate_identity(path)
            if head == placed["destination_commit"] and not dirty and journal.get(restore):
                journal.finish(restore, "completed", placed)
        if (dirty or head != placed["destination_commit"]
                or not _trailer_matches(
                    path, head, parent, placed["destination_tree"], key, source)):
            raise GateError("completed join differs from actual candidate; retain for inspection")
        if previous.status != "completed":
            _finish_observed(journal, key, previous, placed)
            commit_outcome = {"destination_commit": placed["destination_commit"],
                              "destination_tree": placed["destination_tree"]}
            _finish_observed(journal, commit_key, journal.get(commit_key), commit_outcome)
        return placed
    if previous and previous.status != "intent":
        raise GateError("failed/uncertain join requires explicit recovery")
    pending_commit = journal.get(commit_key) if journal else None
    if pending_commit:
        expected_tree = pending_commit.intent["destination_tree"]
        if head == parent:
            if (git(path, "write-tree").stdout.strip() != expected_tree
                    or git(path, "ls-files", "--others", "--exclude-standard").stdout):
                raise GateError("unknown staged changes during join recovery")
            git(path, "diff", "--quiet")
            body = git(path, "show", "-s", "--format=%B", source).stdout.rstrip()
            body += "\n\nBlacksmith-Source-Commit: " + source + "\n" + key.trailer
            git(path, "commit", "-m", body)
            head, tree, dirty = candidate_identity(path)
        if (dirty or not _trailer_matches(path, head, parent, expected_tree, key, source)):
            raise GateError("interrupted_join_requires_reconciliation: retain staged work")
        commit_result = {"destination_commit": head, "destination_tree": tree}
        journal.finish(commit_key, "completed", commit_result)
    else:
        if dirty or head != parent:
            raise GateError("unknown extra shared changes before join; retain for inspection")
        try:
            fetched = git(path, "fetch", "--no-tags", row["clone_path"], source, check=False)
            if fetched.returncode:
                raise GateError("could not fetch exact source: " + fetched.stderr)
            if git(path, "rev-parse", "FETCH_HEAD").stdout.strip() != source:
                raise GateError("fetch returned a different source commit")
            picked = git(path, "cherry-pick", "--no-commit", source, check=False)
            if picked.returncode:
                raise GateError("cherry-pick conflict/error: " + picked.stderr)
            tree = git(path, "write-tree").stdout.strip()
            if context:
                from blacksmith.path_policy import policy_for_state
                policy_for_state(state, path).audit(base)
                journal.intent(commit_key, {**intent, "destination_tree": tree})
            body = git(path, "show", "-s", "--format=%B", source).stdout.rstrip()
            body += "\n\nBlacksmith-Source-Commit: " + source
            if key:
                body += "\n" + key.trailer
            git(path, "commit", "-m", body)
            head, after_tree, dirty = candidate_identity(path)
            if (dirty or tree != after_tree
                    or (key and not _trailer_matches(path, head, parent, tree, key, source))):
                raise GateError("join commit/hook changed tree or operation identity")
            if context:
                from blacksmith.path_policy import policy_for_state
                policy_for_state(state, path).audit(base)
                journal.finish(commit_key, "completed", {
                    "destination_commit": head, "destination_tree": tree,
                })
        except ReceiptError:
            raise  # Preserve effect and pending intent; never roll back critical write failure.
        except Exception as exc:
            return _failed(state, unit, str(exc), key=key, journal=journal)
    placed = {"unit_id": unit.id, "level": level, "source_commit": source,
              "destination_commit": head, "destination_tree": tree, "parent_commit": parent,
              "level_base": base, "operation_id": key.id if key else None}
    if journal:
        journal.finish(key, "completed", placed)
    return placed


def _finish_observed(journal, key, previous, outcome):
    if previous.status in {"intent", "completed"}:
        journal.finish(key, "completed", outcome)
    else:
        proof = OperationKey(key.run_id, key.unit_id, key.attempt_id, key.kind + ".reconciliation")
        journal.intent(proof, {"original_operation": key.id})
        journal.finish(proof, "completed", outcome)


def _failed(state, unit, cause, *, key=None, journal=None):
    """Retain the conflict first, then restore the entire level with checked Git."""
    path = state["worktree_path"]
    level = state["level_cursor"]
    base = state["level_bases"][str(level)]
    conflicts = git(path, "diff", "--name-only", "--diff-filter=U").stdout.strip()
    peers = [u.id for u in levels_for(state)[level] if u.id != unit.id]
    message = f"level {level} join failed for {', '.join(peers)} and {unit.id}: {cause} {conflicts}"
    if state.get("run_context"):
        context = context_from_state(state)
        directory = context.directory / "joins" / str(level)
        atomic_write(directory / "failure.txt", message + "\n")
        atomic_write(directory / "conflict.patch", git(path, "diff", "--binary").stdout)
    try:
        active = git(path, "rev-parse", "--verify", "-q", "CHERRY_PICK_HEAD", check=False)
        if not active.returncode:
            git(path, "cherry-pick", "--abort")
        git(path, "reset", "--hard", base)
        head, _, dirty = candidate_identity(path)
        if head != base or dirty:
            raise GateError("shared tree differs from clean saved base")
    except (CommandError, GateError, OSError) as exc:
        raise GateError(f"join rollback failed: {exc}; original failure: {message}") from exc
    if journal:
        journal.finish(key, "failed", {"message": message, "restored_base": base})
    return {"failure": message}


def join_level(state):
    try:
        level = state.get("level_cursor", 0)
        units = levels_for(state)[level]
        outcomes_for(state, through=level)
        base = validate_saved_base(state, level)
        if not base or state.get("prepared_level") != level:
            raise GateError("missing saved level base")
        cursor = state.get("join_cursor", 0)
        if not 0 <= cursor < len(units):
            raise GateError("invalid join cursor")
        for unit in units if cursor == 0 else [units[cursor]]:
            validate_worker(state, unit, state["unit_outcomes"][unit.id], base)
        placed = state.get("join_destinations") or {}
        current = {uid for uid, row in placed.items() if row.get("level") == level}
        if current != {u.id for u in units[:cursor]}:
            raise GateError("join destinations differ from checkpointed cursor prefix")
        parent = base if cursor == 0 else placed[units[cursor - 1].id]["destination_commit"]
        unit = units[cursor]
        if state.get("run_context"):
            context = context_from_state(state)
            attempt = join_key_for(state, context, unit.id, level).attempt_id
            with command_scope(context, unit_id=unit.id, attempt_id=attempt, stage="join"):
                result = _place(state, unit, state["unit_outcomes"][unit.id], base, parent)
        else:
            result = _place(state, unit, state["unit_outcomes"][unit.id], base, parent)
        if result.get("failure"):
            raise GateError(result["failure"])
        if unit.id in placed and placed[unit.id] != result:
            raise ReceiptError("conflicting join destination")
        return {"join_destinations": {**placed, unit.id: result}, "join_cursor": cursor + 1,
                "join_complete": cursor + 1 == len(units), "status": Status.TESTING}
    except ReceiptError:
        raise
    except (GateError, CommandError, OSError, KeyError, IndexError) as exc:
        return {"status": Status.HALTED,
                "errors": [{"node": "join_level", "message": str(exc)}]}
