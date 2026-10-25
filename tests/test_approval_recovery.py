"""Approval migration, immutable decisions and actual new-process reconciliation."""

import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from test_integration_integrity import configure
from test_publication_approval import install, run
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.run_context import context_from_state
from blacksmith.state import Status


@pytest.mark.parametrize("qa", [False, True])
def test_rejected_approval_requires_fresh_round_preserving_original_decision_and_calls(
    project, monkeypatch, qa,
):
    configure(project, qa=qa)
    calls, remote, payloads = install(monkeypatch)
    assert run(project, "reapprove", "plan") == 1
    before = saved_values(project[1], "reapprove")
    context = context_from_state(before)
    old = context.journal().get_by_id(before["pr_approval"]["decision_id"])
    original = Path(context.directory / "publication/approval-0-decision.json").read_bytes()
    call_ids = {e["event_id"] for e in before["cost_events"]}
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "reapprove",
                     "--approve", "pr"]) == (2 if qa else 0)
    after = saved_values(project[1], "reapprove")
    assert [p["gate"] for p in payloads] == ["plan", "pr", "pr"]
    assert after["pr_approval_round"] == 1 and after["pr_approval"]["approved"] is True
    assert after["pr_approval"]["proposal"]["candidate_commit"] == before[
        "pr_approval_proposal"]["candidate_commit"]
    assert context.journal().get(old.key) == old
    assert (context.directory / "publication/approval-0-decision.json").read_bytes() == original
    assert len(calls) == 7 and len(remote) == 2  # Original six roles plus one summary.
    assert call_ids < {e["event_id"] for e in after["cost_events"]}


def pause(project, monkeypatch, thread):
    calls, remote, payloads = install(monkeypatch)

    def approve(payload, values):
        if payload["gate"] == "plan":
            return True
        payloads.append(payload)
        raise KeyboardInterrupt("retain exact approval prompt")
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt, match="exact approval"):
        run(project, thread)
    return calls, remote, payloads


@pytest.mark.parametrize("case", ["missing_proposal", "malformed_proposal", "patch", "state",
                                  "missing_decision", "malformed_decision", "legacy"])
def test_corrupt_or_legacy_approval_refuses_before_executor_and_preserves_checkpoint(
    project, monkeypatch, case,
):
    if "decision" in case:
        calls, remote, _ = install(monkeypatch)
        assert run(project, "integrity") == 0
    else:
        calls, remote, _ = pause(project, monkeypatch, "integrity")
    state = saved_values(project[1], "integrity")
    context = context_from_state(state)
    folder = context.directory / "publication"
    if case == "missing_proposal":
        (folder / "approval-0-proposal.json").unlink()
    elif case == "malformed_proposal":
        (folder / "approval-0-proposal.json").write_text("{")
    elif case == "patch":
        Path(state["pr_approval_proposal"]["diff_manifest"]["path"]).write_text("changed")
    elif case == "missing_decision":
        (folder / "approval-0-decision.json").unlink()
    elif case == "malformed_decision":
        (folder / "approval-0-decision.json").write_text("{")
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    graph = compile_graph(saver)
    if case == "legacy":
        graph.update_state({"configurable": {"thread_id": "integrity"}}, {"approval_version": 0})
    elif case == "state":
        changed = deepcopy(state["pr_approval_proposal"])
        changed["draft"] = not changed["draft"]
        graph.update_state({"configurable": {"thread_id": "integrity"}},
                           {"pr_approval_proposal": changed})
    original = saver.get_tuple({"configurable": {"thread_id": "integrity"}}).checkpoint
    saver.conn.close()
    previous_calls, previous_remote = list(calls), list(remote)
    monkeypatch.setattr(cli, "Executor", lambda *a, **k: pytest.fail("executor before integrity"))
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "integrity",
                     "--approve", "pr"]) == 1
    assert calls == previous_calls and remote == previous_remote
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple({"configurable": {"thread_id": "integrity"}}).checkpoint == original
    saver.conn.close()


def test_original_source_prd_and_models_may_change_but_approval_uses_frozen_candidate(
    project, monkeypatch,
):
    calls, remote, _ = pause(project, monkeypatch, "frozen")
    before = saved_values(project[1], "frozen")
    old = before["pr_approval_proposal"]["candidate_commit"]
    (project[0] / "README.md").write_text("new original source commit")
    git(project[0], "add", "README.md")
    git(project[0], "commit", "-m", "advance original source")
    project[2].unlink()
    project[1].write_text(project[1].read_text() + '[models]\nplan="changed-local-model"\n')
    # Restore only the approval chooser; the real transport/graph/remote seams remain installed.
    monkeypatch.setattr(cli, "_resolve_approver",
                        lambda args, renderer: lambda payload, values: True)
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "frozen"]) == 0
    after = saved_values(project[1], "frozen")
    assert after["pr_approval"]["proposal"]["candidate_commit"] == old
    assert remote[0][-1].startswith(old + ":refs/heads/")
    assert [c for c in calls if c[0] == "summary"][0][3].model != "changed-local-model"


CRASH = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pytest
from test_publication_approval import install
from blacksmith import cli
import blacksmith.publication_approval as approval
install(pytest.MonkeyPatch())
mode = sys.argv[4]
persist = approval._persist
def partial(path, data):
    if mode == 'intent' and path.name.endswith('-decision.json'):
        os._exit(94)
    return persist(path, data)
approval._persist = partial
record = approval.record_decision
def decision(state, prepared, approved):
    result = record(state, prepared, approved)
    if mode == 'completed':
        os._exit(95)
    return result
approval.record_decision = decision
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'approval-exit',
          '--approve', 'plan,pr'])
'''


@pytest.mark.parametrize("mode,exit_code", [("intent", 94), ("completed", 95)])
def test_new_process_distinguishes_completed_approval_from_unfinished_decision(
    project, monkeypatch, mode, exit_code,
):
    configure(project, qa=True)
    child = subprocess.run([sys.executable, "-c", CRASH, str(Path(__file__).parent),
                            str(project[2]), str(project[1]), mode],
                           capture_output=True, text=True, timeout=90)
    assert child.returncode == exit_code, child.stdout + child.stderr
    before = saved_values(project[1], "approval-exit")
    context = context_from_state(before)
    original = next(o for o in context.journal().operations()
                    if o.key.kind == "approval.pr.decision")
    assert original.status == ("intent" if mode == "intent" else "completed")
    calls, remote, payloads = install(monkeypatch)
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer:
                        lambda *a: pytest.fail("retained decision must reconcile or hold first"))
    result = cli.main(["resume", "--config", str(project[1]), "--thread-id", "approval-exit"])
    if mode == "intent":
        assert result == 1 and calls == [] and remote == []
        held = saved_values(project[1], "approval-exit")
        assert held["status"] == Status.HALTED and held["approvals"]["pr"] is False
        assert context.journal().get(original.key) == original
        monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer:
                            lambda payload, values: payloads.append(payload) or True)
        assert cli.main(["resume", "--config", str(project[1]),
                         "--thread-id", "approval-exit"]) == 2
        assert len(payloads) == 1 and payloads[0]["gate"] == "pr"
    else:
        assert result == 2 and payloads == []
    after = saved_values(project[1], "approval-exit")
    assert context.journal().get(original.key) == original
    assert after["status"] == Status.AWAITING_QA and after["pending_qa_unit"]["id"] == "QA"
    assert len(calls) == 1 and calls[0][0] == "summary"
    assert len(remote) == 2 and "--draft" in remote[1]
    assert len(after["cost_events"]) == len({e["event_id"] for e in after["cost_events"]}) == 7
