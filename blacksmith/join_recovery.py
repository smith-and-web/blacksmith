"""Explicit join rollback recovery reuses existing destination objects in order."""

from __future__ import annotations

from blacksmith.integration import (
    git,
    join_key_for,
    join_observed,
    levels_for,
    outcomes_for,
    validate_saved_base,
    validate_worker,
)
from blacksmith.recovery import RecoveryError
from blacksmith.verification import candidate_identity


def _owned_destination(context, state, unit, level, base, parent):
    from blacksmith.joins import _trailer_matches

    key = join_key_for(state, context, unit.id, level)
    pick = context.journal().get(key)
    observed = join_observed(context, key)
    commit = context.journal().get(join_key_for(state, context, unit.id, level, "join.commit"))
    source = state["unit_outcomes"][unit.id]["source_commit"]
    expected = {
        "level": level,
        "level_base": base,
        "parent_commit": parent,
        "source_commit": source,
        "clone_path": state["unit_outcomes"][unit.id]["clone_path"],
    }
    if pick and pick.intent != expected:
        raise RecoveryError("join recovery intent differs from source/base/parent")
    if observed and observed.status == "completed":
        row = observed.outcome
        if (
            row.get("operation_id") != key.id
            or row.get("source_commit") != source
            or row.get("parent_commit") != parent
            or row.get("level_base") != base
            or not _trailer_matches(
                state["worktree_path"],
                row["destination_commit"],
                parent,
                row["destination_tree"],
                key,
                source,
            )
        ):
            raise RecoveryError("recorded join destination no longer matches Git")
        return row
    if not commit:
        return None
    if commit.intent != {**expected, "destination_tree": commit.intent.get("destination_tree")}:
        raise RecoveryError("join commit intent belongs to a different source/base")
    tree = commit.intent["destination_tree"]
    candidates = git(state["worktree_path"], "reflog", "--all", "--format=%H").stdout.splitlines()
    if commit.status == "completed":
        candidates.append(commit.outcome["destination_commit"])
    matches = []
    for candidate in dict.fromkeys(candidates):
        if _trailer_matches(state["worktree_path"], candidate, parent, tree, key, source):
            matches.append(candidate)
    if len(matches) > 1:
        raise RecoveryError("multiple matching join effects; retain for inspection")
    if not matches:
        return None
    return {
        "unit_id": unit.id,
        "level": level,
        "source_commit": source,
        "destination_commit": matches[0],
        "destination_tree": tree,
        "parent_commit": parent,
        "level_base": base,
        "operation_id": key.id,
    }


def prepare_join(context, state):
    level = state.get("level_cursor", 0)
    levels = levels_for(state)
    if (
        level >= len(levels)
        or len(levels[level]) < 2
        or not state.get("fanout")
        or state.get("prepared_level") != level
        or state.get("join_complete")
    ):
        return None
    outcomes_for(state, through=level)
    units, base = levels[level], validate_saved_base(state, level)
    for unit in units:
        validate_worker(state, unit, state["unit_outcomes"][unit.id], base)
        from blacksmith.recovery_control import _unknown_commands
        if _unknown_commands(context, unit.id,
                             join_key_for(state, context, unit.id, level).attempt_id):
            raise RecoveryError("unconfirmed native join command; inspect before recovery")
    head, _, dirty = candidate_identity(state["worktree_path"])
    cursor = state.get("join_cursor", 0)
    if not 0 <= cursor < len(units):
        raise RecoveryError("invalid retained join cursor")
    destinations = state.get("join_destinations") or {}
    prefix = {u.id for u in units[:cursor]}
    current = {uid for uid, row in destinations.items() if row.get("level") == level}
    if current != prefix:
        raise RecoveryError("retained join mapping differs from its cursor prefix")
    parent = base
    archive = {}
    generations = dict(state.get("join_generations") or {})
    resetting = head == base and not dirty
    start = 0 if resetting else cursor
    for index, unit in enumerate(units):
        if index < start:
            row = _owned_destination(context, state, unit, level, base, parent)
            if row is None or row != destinations[unit.id]:
                raise RecoveryError("missing recorded prefix destination")
            parent = row["destination_commit"]
            continue
        key = join_key_for(state, context, unit.id, level)
        original = context.journal().get(key)
        if not resetting and index == start:
            if head != parent:
                raise RecoveryError("unknown shared HEAD before join recovery")
            if dirty:
                commit = context.journal().get(
                    join_key_for(state, context, unit.id, level, "join.commit")
                )
                if not original or original.status != "intent" or not commit:
                    raise RecoveryError("unsealed staged join; retain for inspection")
                if (
                    git(state["worktree_path"], "write-tree").stdout.strip()
                    != commit.intent["destination_tree"]
                    or git(
                        state["worktree_path"], "ls-files", "--others", "--exclude-standard"
                    ).stdout
                ):
                    raise RecoveryError("unknown staged join changes")
                git(state["worktree_path"], "diff", "--quiet")
                break  # The original staged pick finishes under its original effect key.
        row = _owned_destination(context, state, unit, level, base, parent)
        if row:
            archive[unit.id] = row
            parent = row["destination_commit"]
        else:
            if original:
                slot = f"{level}:{unit.id}"
                generations[slot] = generations.get(slot, 0) + 1
            # Later units cannot have been placed after a missing ordered pick.
            for later in units[index + 1 :]:
                if context.journal().get(join_key_for(state, context, later.id, level)):
                    raise RecoveryError("join effects exist after a missing ordered destination")
            break
    return {
        "recovery_target": "join_level",
        "join_generations": generations,
        "join_recovery": {"level": level, "destinations": archive},
        "join_cursor": start,
        "join_complete": False,
        "join_destinations": (
            {uid: row for uid, row in destinations.items() if row.get("level") != level}
            if resetting
            else destinations
        ),
    }
