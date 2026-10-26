"""New-process publication recovery retains receipts, candidates and call identities."""

import subprocess
import sys
from pathlib import Path

import pytest
from test_publication_integrity import setup
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.run_context import context_from_state
from blacksmith.state import Status

CRASH = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pytest
from test_publication_integrity import setup, Remote
import blacksmith.publication as pub
from blacksmith import cli
project = tuple(Path(p) for p in sys.argv[2:5])
mode = sys.argv[5]
calls, remote, _ = setup(project, pytest.MonkeyPatch())
original = Remote.__call__
def effect(self, argv, cwd=None):
    result = original(self, argv, cwd)
    if mode == 'push' and list(argv[:2]) == ['git','push']:
        os._exit(106)
    if mode == 'create' and list(argv[:3]) == ['gh','pr','create']:
        os._exit(107)
    return result
Remote.__call__ = effect
persist = pub._persist
def before_file(path, data):
    if mode == 'content' and path.name.endswith('-content.json'):
        os._exit(109)
    return persist(path, data)
pub._persist = before_file
artifact = pub._artifact
def receipt(*args, **kwargs):
    result = artifact(*args, **kwargs)
    if mode == 'receipt' and args[2] == 'publication.result':
        os._exit(108)
    return result
pub._artifact = receipt
cli.main([str(project[2]), '--config', str(project[1]), '--thread-id', 'publication',
          '--approve', 'plan,pr'])
'''


@pytest.mark.parametrize("stage,code", [("push", 106), ("create", 107),
                                        ("receipt", 108), ("content", 109)])
def test_new_process_reuses_remote_effect_and_summary_without_duplicate_calls(
    project, monkeypatch, stage, code,
):
    child = subprocess.run([sys.executable, "-c", CRASH, str(Path(__file__).parent),
                            *(str(p) for p in project[:3]), stage],
                           capture_output=True, text=True, timeout=180)
    assert child.returncode == code, child.stdout + child.stderr
    before = saved_values(project[1], "publication")
    context = context_from_state(before)
    original = [o for o in context.journal().operations()
                if o.key.kind.startswith("publication.")]
    costs = [e for e in context.journal().events() if e["kind"] == "model_cost"]
    assert len(costs) == 4  # One unit: plan, implement, review, summary.
    calls, remote, payloads = setup(project, monkeypatch)
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "publication"]) == 0
    after = saved_values(project[1], "publication")
    assert after["status"] == Status.DONE and after["publication_receipt"]["pr"]["number"] == 1
    assert calls == [] and payloads == []
    for row in original:
        if row.status != "intent" or row.key.kind in {"publication.push", "publication.create"}:
            assert context.journal().get(row.key) == row
    assert len([c for c in remote.effects() if c[:2] == ["git", "push"]]) == 1
    assert len([c for c in remote.effects() if c[:3] == ["gh", "pr", "create"]]) == 1
    assert git(remote.repo, "rev-parse", "refs/heads/" + after["branch"]) == after[
        "pr_approval"]["proposal"]["candidate_commit"]
    assert {e["event_id"] for e in after["cost_events"]} == {e["event_id"] for e in costs}
    assert len(after["cost_events"]) == 4


@pytest.mark.parametrize("case", ["content", "result", "state", "legacy", "url", "status", "qa",
                                  "approval"])
def test_missing_or_foreign_publication_evidence_refuses_before_executor_without_checkpoint_change(
    project, monkeypatch, case,
):
    from test_publication_approval import run
    setup(project, monkeypatch)
    assert run(project, "publication") == 0
    state = saved_values(project[1], "publication")
    context = context_from_state(state)
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    graph = compile_graph(saver)
    if case in {"content", "result"}:
        next((context.directory / "publication").glob("*-" + case + ".json")).unlink()
    elif case == "state":
        graph.update_state({"configurable": {"thread_id": "publication"}},
                           {"publication_receipt": {"pr": "foreign"}})
    elif case in {"url", "status", "qa", "approval"}:
        patch = {"url": {"pr_url": "https://github.com/foreign/repo/pull/2"},
                 "status": {"status": Status.AWAITING_QA},
                 "qa": {"pending_qa_unit": {"id": "foreign", "test_contract": "unknown"}},
                 "approval": {"approvals": {"plan": True, "pr": False}, "pr_approval": None}}
        graph.update_state({"configurable": {"thread_id": "publication"}}, patch[case])
    else:
        graph.update_state({"configurable": {"thread_id": "publication"}},
                           {"publication_version": 0})
    before = saver.get_tuple({"configurable": {"thread_id": "publication"}}).checkpoint
    saver.conn.close()
    monkeypatch.setattr(cli, "Executor", lambda *a, **k: pytest.fail("executor before integrity"))
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "publication"]) == 1
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple({"configurable": {"thread_id": "publication"}}).checkpoint == before
    saver.conn.close()
