"""Observed formatter commit recovery, retaining each completed formatter command."""

from __future__ import annotations

from blacksmith.gate_receipts import GateCommandReceipt
from blacksmith.receipts import OperationKey, ReceiptError


def step_key(main, index, kind="command"):
    return OperationKey(main.run_id, main.unit_id, main.attempt_id,
                        f"git.fix.{kind}:{main.id}:{index}")


def resume_formatter(state, context, main, previous, planned):
    from blacksmith.verification import _git, _seal_command, candidate_identity
    if previous.intent.get("steps_version") != 1:
        raise ReceiptError("legacy_interrupted_formatter_requires_inspection")
    journal, path = context.journal(), state["worktree_path"]
    head = previous.intent["before_commit"]
    commands = []
    for index, (command, covered) in enumerate(planned.items()):
        key, amend = step_key(main, index), journal.get(step_key(main, index, "amend"))
        step = journal.get(key)
        if step and step.status == "completed":
            row = GateCommandReceipt.model_validate(step.outcome["command"])
            if (step.intent["formatter"] != main.id or step.intent["command"] != command
                    or tuple(step.intent["layers"]) != tuple(covered)
                    or step.intent["candidate_commit"] != head
                    or _seal_command(row).output_artifact_sha256 != row.output_artifact_sha256):
                raise ReceiptError("conflicting completed formatter step")
            commands.append(row)
            head = step.outcome["candidate_commit"]
            continue
        if amend:
            actual, _, dirty = candidate_identity(path)
            intended = amend.intent
            row = GateCommandReceipt.model_validate(intended["command"])
            if (intended["formatter"] != main.id or intended["before_commit"] != head
                    or not step or step.status != "intent"
                    or step.intent["formatter"] != main.id or step.intent["command"] != command
                    or tuple(row.layers) != tuple(covered) or row.command != command
                    or _seal_command(row).output_artifact_sha256 != row.output_artifact_sha256
                    or intended["parent"] != _git(path, "rev-parse",
                                                 intended["before_commit"] + "^").strip()):
                raise ReceiptError("formatter amend intent ownership mismatch")
            if actual == intended["before_commit"]:
                if (_git(path, "write-tree").strip() != intended["tree"]
                        or _git(path, "ls-files", "--others", "--exclude-standard").strip()):
                    raise ReceiptError("unknown staged changes during formatter recovery")
                _git(path, "diff", "--quiet")
                body = _git(path, "show", "-s", "--format=%B", head).rstrip()
                _git(path, "commit", "--amend", "-m", body + "\n\n" + amend.key.trailer)
                actual, _, dirty = candidate_identity(path)
            head = actual
            if (dirty or _git(path, "rev-parse", head + "^{tree}").strip() != intended["tree"]
                    or _git(path, "rev-parse", head + "^").strip() != intended["parent"]
                    or amend.key.trailer not in _git(
                        path, "log", "-1", "--format=%B").splitlines()):
                raise ReceiptError("unknown formatter amend effect; retain for inspection")
            outcome = {"candidate_commit": head, "candidate_tree": intended["tree"]}
            if amend.status == "intent":
                journal.finish(amend.key, "completed", outcome)
            elif amend.status != "completed" or amend.outcome != outcome:
                raise ReceiptError("conflicting formatter amend outcome")
            result = {"command": row.model_dump(mode="json"), "candidate_commit": head}
            if step and step.status == "intent":
                journal.finish(key, "completed", result)
                step = journal.get(key)
        if step is None:
            break
        if (step.status != "completed" or step.intent["formatter"] != main.id
                or step.intent["command"] != command
                or tuple(step.intent["layers"]) != tuple(covered)):
            raise ReceiptError("unknown formatter command effect; retain for inspection")
        row = GateCommandReceipt.model_validate(step.outcome["command"])
        if _seal_command(row).output_artifact_sha256 != row.output_artifact_sha256:
            raise ReceiptError("changed retained formatter command evidence")
        commands.append(row)
        head = step.outcome["candidate_commit"]
    actual, _, dirty = candidate_identity(path)
    if actual != head or dirty:
        raise ReceiptError("formatter recovery candidate has unknown extra changes")
    return commands, head, len(commands)
