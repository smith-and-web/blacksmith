"""Critical recovery capture, immutable readback and read-only CLI inspection."""

import json
import os
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph
from test_run_context import create, git, saved_values
from test_run_context import project as project

from blacksmith import cli, recovery
from blacksmith.graph import build_checkpointer
from blacksmith.receipts import OperationJournal, ReceiptError
from blacksmith.run_context import context_from_state
from blacksmith.state import BlacksmithState, Status
from blacksmith.worktree import RunCloneManager


def seeded(project):
    context = create(project)
    clone = RunCloneManager(context).create("U1")
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    graph = StateGraph(BlacksmithState)
    graph.add_node("hold", lambda state: {"status": Status.HALTED})
    graph.add_edge(START, "hold")
    graph.add_edge("hold", END)
    graph.compile(checkpointer=saver).invoke(context.initial_state(),
                                           {"configurable": {"thread_id": context.thread_id}})
    return context, clone.path, saver


def test_full_staged_binary_unstaged_untracked_and_refs_restore_from_snapshot(
    project, tmp_path,
):
    context, workspace, saver = seeded(project)
    try:
        staged = os.urandom(2 * 1024 * 1024)
        unstaged = b"binary\0unstaged\xff" * 37
        (workspace / "large-staged.bin").write_bytes(staged)
        git(workspace, "add", "large-staged.bin")
        (workspace / "README.md").write_bytes(unstaged)
        (workspace / "untracked.bin").write_bytes(b"nonignored\0partial")
        manifest = recovery.capture(context, saver, reason="failed binary attempt",
                                    stage="implement")
        record = json.loads(manifest.read_text())
        snapshot = manifest.parent / "workspace"
        assert (snapshot / "staged.patch").stat().st_size > 1024 * 1024
        assert (snapshot / "untracked/untracked.bin").read_bytes() == b"nonignored\0partial"
        assert record["bytes"] > 2 * 1024 * 1024
        restored = tmp_path / "restored"
        git(tmp_path, "clone", str(snapshot / "refs.bundle"), str(restored))
        git(restored, "checkout", record["workspaces"][0]["head"])
        git(restored, "apply", "--index", str(snapshot / "staged.patch"))
        git(restored, "apply", str(snapshot / "unstaged.patch"))
        assert (restored / "large-staged.bin").read_bytes() == staged
        assert (restored / "README.md").read_bytes() == unstaged
        index = recovery.validate(context)
        assert index["records"][0]["id"] == record["id"]
        state = recovery.load_state(context, record["id"], record["checkpoints"][0]["state_path"])
        assert state["status"] == Status.HALTED and state["run_id"] == context.run_id
        with sqlite3.connect(manifest.parent / "operations.sqlite") as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not manifest.parent.stat().st_mode & 0o077
        assert (workspace / "large-staged.bin").read_bytes() == staged
    finally:
        saver.conn.close()


def test_capture_preserves_orphaned_commit_in_refs_bundle(project, tmp_path):
    context, workspace, saver = seeded(project)
    try:
        (workspace / "failed.txt").write_text("paid attempt\n")
        git(workspace, "add", "failed.txt")
        git(workspace, "commit", "-m", "failed implementation")
        orphan = git(workspace, "rev-parse", "HEAD")
        git(workspace, "reset", "--hard", context.base_commit)
        manifest = recovery.capture(context, saver, reason="rolled back join")
        restore = tmp_path / "restored"
        git(tmp_path, "clone", str(manifest.parent / "workspace/refs.bundle"), str(restore))
        assert git(restore, "show", orphan + ":failed.txt") == "paid attempt"
        assert git(workspace, "rev-parse", "HEAD") == context.base_commit
    finally:
        saver.conn.close()


def test_untracked_escape_symlink_is_recorded_without_reading_its_target(project, tmp_path):
    context, workspace, saver = seeded(project)
    try:
        outside = tmp_path / "outside"
        outside.write_bytes(b"DO_NOT_FOLLOW_SOURCE_SENTINEL")
        (workspace / "escape-link").symlink_to(outside)
        manifest = recovery.capture(context, saver, reason="symlink failure")
        links = json.loads((manifest.parent / "workspace/symlinks.json").read_text())
        assert links == {"escape-link": str(outside)}
        assert not (manifest.parent / "workspace/untracked/escape-link").exists()
        assert outside.read_bytes() == b"DO_NOT_FOLLOW_SOURCE_SENTINEL"
        assert (workspace / "escape-link").is_symlink()
        recovery.validate(context)
    finally:
        saver.conn.close()


@pytest.mark.parametrize("damage", ["missing_index", "corrupt_index", "manifest", "state",
                                    "bundle", "patch", "journal", "removed_entry", "duplicate"])
def test_missing_corrupt_recovery_evidence_refuses_before_executor_or_checkpoint_mutation(
    project, monkeypatch, damage, capsys,
):
    context, _, saver = seeded(project)
    manifest = recovery.capture(context, saver, reason="failure")
    index_path = context.directory / "recovery/index.json"
    index = json.loads(index_path.read_text())
    record = json.loads(manifest.read_text())
    cfg = {"configurable": {"thread_id": context.thread_id}}
    before = deepcopy(saver.get_tuple(cfg).checkpoint)
    saver.conn.close()
    if damage == "missing_index":
        index_path.unlink()
    elif damage == "corrupt_index":
        index_path.write_text("{")
    elif damage == "manifest":
        manifest.write_text("{}")
    elif damage == "removed_entry":
        index["records"] = []
        index_path.write_text(json.dumps(index))
    elif damage == "duplicate":
        index["records"].append(index["records"][0])
        index_path.write_text(json.dumps(index))
    else:
        file = {"state": record["checkpoints"][0]["state_path"],
                "bundle": "workspace/refs.bundle", "patch": "workspace/staged.patch",
                "journal": "operations.sqlite"}[damage]
        (manifest.parent / file).write_bytes(b"corrupt")

    def forbidden(*args):
        pytest.fail("invalid recovery files must not construct an executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(["resume", "--config", str(project[1]),
                     "--thread-id", context.thread_id]) == 1
    assert "recovery" in capsys.readouterr().err
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple(cfg).checkpoint == before
    saver.conn.close()


def test_read_only_inspection_never_loads_credentials_or_changes_artifact_or_checkpoint(
    project, monkeypatch, capsys,
):
    context, workspace, saver = seeded(project)
    manifest = recovery.capture(context, saver, reason="inspection")
    saver.conn.close()
    db = context.execution_config().checkpointer.db_path
    before = recovery.file_hash(db), recovery.file_hash(manifest)
    head = git(workspace, "rev-parse", "HEAD")

    def forbidden(*args):
        pytest.fail("inspection cannot load credentials or construct an executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    monkeypatch.setattr(cli, "load_dotenv", forbidden)
    assert cli.main(["recovery", "inspect", "--config", str(project[1]),
                     "--thread-id", context.thread_id]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["run_id"] == context.run_id and data["records"]
    assert before == (recovery.file_hash(db), recovery.file_hash(manifest))
    assert git(workspace, "rev-parse", "HEAD") == head


def test_critical_capture_intent_failure_precedes_git_ref_or_workspace_effects(
    project, monkeypatch,
):
    context, workspace, saver = seeded(project)
    original = OperationJournal.intent
    refs = git(workspace, "show-ref")

    def fail(self, key, payload):
        if key.kind == "recovery.capture":
            raise ReceiptError("injected capture intent failure")
        return original(self, key, payload)

    monkeypatch.setattr(OperationJournal, "intent", fail)
    try:
        with pytest.raises(ReceiptError, match="capture intent"):
            recovery.capture(context, saver, reason="failure")
        assert git(workspace, "show-ref") == refs
        assert git(workspace, "rev-parse", "HEAD") == context.base_commit
    finally:
        saver.conn.close()


def test_incomplete_capture_is_retained_and_refuses_automatic_resume(project, monkeypatch):
    context, workspace, saver = seeded(project)
    original = recovery.atomic_write

    def fail(path, text):
        if path.name == "manifest.json" and path.parent.parent.name == "recovery":
            raise OSError("injected full artifact persistence failure")
        return original(path, text)

    monkeypatch.setattr(recovery, "atomic_write", fail)
    try:
        with pytest.raises(OSError, match="artifact persistence"):
            recovery.capture(context, saver, reason="failed capture")
        pending = [op for op in context.journal().operations() if op.key.kind == "recovery.capture"]
        assert len(pending) == 1 and pending[0].status == "intent"
        assert Path(pending[0].intent["path"]).is_dir()
        assert workspace.is_dir()
        with pytest.raises(recovery.RecoveryError, match="incomplete recovery capture"):
            recovery.validate(context)
    finally:
        saver.conn.close()


def test_real_cli_failure_preserves_failed_stage_counters_and_all_worker_snapshots(
    project, monkeypatch, capsys,
):
    from test_integration_integrity import configure, transport
    configure(project, "false")
    transport(monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "failure", "--approve", "plan"]) == 1
    state = saved_values(project[1], "failure")
    context = context_from_state(state)
    index = recovery.validate(context)
    manifest = context.directory / "recovery" / index["records"][-1]["manifest"]
    record = json.loads(manifest.read_text())
    units = [r for r in record["checkpoints"] if r["unit_id"]]
    assert {r["unit_id"] for r in units} == {"A", "B"}
    for row in units:
        value = recovery.load_state(context, record["id"], row["state_path"])
        assert value["unit_failed_stage"] == "test_gate" and value["escalated"]
        assert value["attempt_number"] == 3 and value["fix_attempts"] == 1
    assert all(Path(row["path"]).is_dir() for row in record["workspaces"])
    assert "Recovery manifest:" in capsys.readouterr().err
    assert not state.get("pr_url")


CRASH_MODEL = '''
import os, sys
from pathlib import Path
from claude_agent_sdk import PermissionResultAllow
from blacksmith import cli
from blacksmith.executor import Executor, ExecutorResult
import blacksmith.run_context as rc
rc.shutil.which=lambda tool: '/offline/' + tool
os.environ['BLACKSMITH_ANTHROPIC_API_KEY']='unused-offline'
async def collect(self, prompt, options, *, emit_findings=False):
    if prompt.startswith('Produce a concise'):
        return ExecutorResult('APPROVED', options.model, False, 1, .01, {}, 'offline')
    target=Path(options.cwd)/'result.txt'
    result=await options.can_use_tool('Write', {'file_path': str(target)}, None)
    assert isinstance(result, PermissionResultAllow)
    target.write_bytes(b'PARTIAL_BINARY\\0' * 200)
    (Path(options.cwd)/'untracked-partial.bin').write_bytes(b'RETAIN_THIS\\xff')
    os._exit(93)
Executor._collect=collect
cli.main([sys.argv[1], '--config', sys.argv[2], '--thread-id', 'sdk-kill', '--auto-approve'])
'''


def test_killed_model_preserves_partial_bytes_and_ordinary_resume_never_repeats_sdk(
    project, monkeypatch, capsys,
):
    import subprocess
    import sys

    from blacksmith.executor import Executor
    child = subprocess.run([sys.executable, "-c", CRASH_MODEL, str(project[2]), str(project[1])],
                           capture_output=True, text=True, timeout=40)
    assert child.returncode == 93, child.stderr
    state = saved_values(project[1], "sdk-kill")
    context = context_from_state(state)
    partial = context.workspace_path / "result.txt"
    assert partial.read_bytes() == b"PARTIAL_BINARY\0" * 200
    assert recovery.validate(context)["records"] == []

    async def forbidden(*args, **kwargs):
        pytest.fail("an uncertain SDK call must never be repeated by ordinary resume")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", forbidden)
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "sdk-kill"]) == 1
    error = capsys.readouterr().err
    assert "interrupted_attempt_requires_resume" in error and "Recovery manifest:" in error
    inspection = recovery.inspect(context)
    assert len(inspection["uncertain_model_calls"]) == 1
    record = inspection["records"][-1]
    snapshot = context.directory / "recovery" / record["id"] / "workspace/untracked"
    assert (snapshot / "result.txt").read_bytes() == partial.read_bytes()
    assert (snapshot / "untracked-partial.bin").read_bytes() == b"RETAIN_THIS\xff"
    assert git(context.workspace_path, "rev-parse", "HEAD") == context.base_commit
    calls = [op for op in context.journal().operations() if op.key.kind.startswith("model.")]
    assert len(calls) == 2
    assert sorted(op.status for op in calls) == ["completed", "intent"]
    assert not state.get("pr_url")


CRASH_COMMIT = '''
import os, sys
sys.path.insert(0, sys.argv[1])
from pytest import MonkeyPatch
from test_run_context import install_transport
from blacksmith import cli
import blacksmith.nodes.implement as imp
mp=MonkeyPatch()
install_transport(mp, [])
original=imp._commit_changes
def crash(path, message, *, before_commit=None):
    result=original(path, message, before_commit=before_commit)
    if sys.argv[4] == 'exit':
        os._exit(94)
    raise KeyboardInterrupt('committed before receipt')
imp._commit_changes=crash
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'commit-kill', '--auto-approve'])
'''


@pytest.mark.parametrize("mode", ["exit", "interrupt"])
def test_committed_effect_reconciles_after_process_exit_or_interrupted_receipt_without_sdk(
    project, monkeypatch, mode,
):
    import subprocess
    import sys

    from blacksmith.executor import Executor
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    child = subprocess.run([sys.executable, "-c", CRASH_COMMIT, str(Path(__file__).parent),
                            str(project[2]), str(project[1]), mode],
                           capture_output=True, text=True, timeout=45)
    assert child.returncode != 0
    if mode == "exit":
        assert child.returncode == 94, child.stderr
    else:
        assert "KeyboardInterrupt" in child.stderr
    state = saved_values(project[1], "commit-kill")
    context = context_from_state(state)
    head = git(context.workspace_path, "rev-parse", "HEAD")
    commits = [op for op in context.journal().operations() if op.key.kind == "git.commit"]
    assert len(commits) == 1
    assert commits[0].status == ("intent" if mode == "exit" else "interrupted")

    async def forbidden(*args, **kwargs):
        pytest.fail("a committed effect cannot cause another model call on resume")

    def stop(payload, values):
        assert payload["gate"] == "pr"
        raise KeyboardInterrupt("preserve final verification")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", forbidden)
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "commit-kill"])
    after = saved_values(project[1], "commit-kill")
    assert git(context.workspace_path, "rev-parse", "HEAD") == head
    assert git(context.workspace_path, "rev-list", "--count", context.base_commit + "..HEAD") == "1"
    assert len(after["cost_events"]) == 2
    assert after["final_gate_receipt"].outcome == "passed"
    proofs = [op for op in context.journal().operations()
              if op.key.kind == "git.commit.reconciliation"]
    assert len(proofs) == 1 and proofs[0].status == "completed"
    original = context.journal().get(commits[0].key)
    assert original.status == ("completed" if mode == "exit" else "interrupted")
    recovery.validate(context)
    assert not after.get("pr_url")


def test_recovery_files_removed_during_plan_approval_block_before_workspace_or_new_model(
    project, monkeypatch, capsys,
):
    from test_run_context import install_transport
    calls = []
    install_transport(monkeypatch, calls)

    def remove_index(payload, values):
        assert payload["gate"] == "plan"
        context = context_from_state(values)
        (context.directory / "recovery/index.json").unlink()
        return True

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: remove_index)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "deleted-at-approval"]) == 1
    state = saved_values(project[1], "deleted-at-approval")
    context = context_from_state(state)
    assert len(calls) == 1  # only the already-completed plan
    assert not context.workspace_path.exists()
    assert not state.get("pr_url")
    assert "recovery" in capsys.readouterr().err


@pytest.mark.parametrize("value", [None, [], {}, {"records": {}},
                                   {"records": [{}]}, {"records": ["bad"]}])
def test_malformed_recovery_metadata_is_diagnostic_without_file_or_model_effects(project, value):
    context = create(project)
    identity = recovery._identity(context)
    index = {**identity, **value} if isinstance(value, dict) else value
    (context.directory / "recovery/index.json").write_text(json.dumps(index))
    with pytest.raises(recovery.RecoveryError, match="recovery"):
        recovery.validate(context)
    assert not context.workspace_path.exists()


def test_critical_artifact_directory_link_cannot_copy_unrelated_host_files(
    project, tmp_path,
):
    context, _, saver = seeded(project)
    outside = tmp_path / "unrelated-host-files"
    outside.mkdir()
    sentinel = outside / "host-sentinel.txt"
    sentinel.write_text("UNRELATED_HOST_SENTINEL")
    (context.directory / "plans").symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(recovery.RecoveryError, match="symlink in critical artifact directory"):
            recovery.capture(context, saver, reason="corrupted source directory")
        assert sentinel.read_text() == "UNRELATED_HOST_SENTINEL"
        assert not list((context.directory / "recovery").rglob("host-sentinel.txt"))
    finally:
        saver.conn.close()
