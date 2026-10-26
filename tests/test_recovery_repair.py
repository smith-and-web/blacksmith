"""Interrupted artifact publication repair uses critical seals, never guessed hashes."""

import json
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph
from test_recovery_artifacts import seeded
from test_run_context import create
from test_run_context import project as project

from blacksmith import cli, recovery, recovery_control
from blacksmith.graph import build_checkpointer
from blacksmith.receipts import OperationJournal, ReceiptError
from blacksmith.recovery_repair import repair
from blacksmith.run_context import RunContext, RunContextError
from blacksmith.state import BlacksmithState, Status


def operation(context, kind):
    return next(o for o in context.journal().operations() if o.key.kind == kind)


def forbid_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("recovery inspection/repair cannot load credentials or build executor")

    monkeypatch.setattr(cli, "load_dotenv", forbidden)
    monkeypatch.setattr(cli, "build_graph_for", forbidden)


@pytest.mark.parametrize("boundary", ["seal_outcome", "manifest", "capture_outcome"])
def test_sealed_capture_repair_preserves_original_pending_receipt_and_exact_bytes(
    project,
    monkeypatch,
    boundary,
    capsys,
):
    context, workspace, saver = seeded(project)
    (workspace / "paid-partial.bin").write_bytes(b"original\0partial\xff")
    finish, write = OperationJournal.finish, recovery.atomic_write

    def fail_finish(self, key, *args, **kwargs):
        if key.kind == (
            "recovery.capture.seal"
            if boundary == "seal_outcome"
            else "recovery.capture"
            if boundary == "capture_outcome"
            else "none"
        ):
            raise ReceiptError("injected lost outcome")
        return finish(self, key, *args, **kwargs)

    def fail_manifest(path, text):
        if (
            boundary == "manifest"
            and path.name == "manifest.json"
            and path.parent.parent.name == "recovery"
        ):
            raise OSError("injected manifest crash")
        return write(path, text)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(OperationJournal, "finish", fail_finish)
            patch.setattr(recovery, "atomic_write", fail_manifest)
            with pytest.raises((ReceiptError, OSError), match="injected"):
                recovery.capture(context, saver, reason="capture crash")
        original = operation(context, "recovery.capture")
        assert original.status == "intent"
        forbid_runtime(monkeypatch)
        assert cli.main(["recovery", "inspect", "--run-dir", str(context.directory)]) == 1
        report = json.loads(capsys.readouterr().out)
        assert (
            not report["valid"]
            and report["pending_artifacts"][0]["operation_id"] == original.key.id
        )
        assert (
            cli.main(
                [
                    "recovery",
                    "repair",
                    "--run-dir",
                    str(context.directory),
                    "--operation-id",
                    original.key.id,
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert result["repair"]["disposition"] == "reconciled" and result["valid"]
        index = recovery.validate(context)
        manifest = context.directory / "recovery" / index["records"][0]["manifest"]
        assert (
            manifest.parent / "workspace/untracked/paid-partial.bin"
        ).read_bytes() == b"original\0partial\xff"
        assert (workspace / "paid-partial.bin").read_bytes() == b"original\0partial\xff"
        assert context.journal().get(original.key) == original
        assert repair(context, original.key.id) == result["repair"]
    finally:
        saver.conn.close()


def test_unsealed_capture_retains_partial_and_allows_new_capture_without_fabricating_success(
    project,
    monkeypatch,
):
    context, workspace, saver = seeded(project)

    def crash(context, source, destination, *, readonly=False):
        destination.mkdir()
        (destination / "incomplete.bin").write_bytes(b"failed capture bytes")
        raise OSError("injected before seal")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(recovery, "_workspace", crash)
            with pytest.raises(OSError, match="before seal"):
                recovery.capture(context, saver, reason="unsealed")
        original = operation(context, "recovery.capture")
        result = repair(context, original.key.id)
        assert result["disposition"] == "retained_partial"
        assert recovery.validate(context)["records"] == []
        assert context.journal().get(original.key) == original
        partial = Path(original.intent["path"]) / "workspace/incomplete.bin"
        assert partial.read_bytes() == b"failed capture bytes" and workspace.is_dir()
        recovery.capture(context, saver, reason="new complete capture")
        assert len(recovery.validate(context)["records"]) == 1
        partial.write_bytes(b"tampered")
        with pytest.raises(ReceiptError, match="changed retained partial"):
            recovery.validate(context)
    finally:
        saver.conn.close()


def test_sealed_capture_corruption_is_not_repaired_or_admitted(project, monkeypatch):
    context, _, saver = seeded(project)
    finish = OperationJournal.finish

    def crash(self, key, *args, **kwargs):
        if key.kind == "recovery.capture":
            raise ReceiptError("lost capture outcome")
        return finish(self, key, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(OperationJournal, "finish", crash)
            with pytest.raises(ReceiptError):
                recovery.capture(context, saver, reason="sealed")
        original = operation(context, "recovery.capture")
        root = Path(original.intent["path"])
        before = recovery.file_hash(root / "manifest.json")
        (root / "workspace/refs.bundle").write_bytes(b"corrupt")
        with pytest.raises(ReceiptError, match="changed sealed capture file"):
            repair(context, original.key.id)
        assert recovery.file_hash(root / "manifest.json") == before
        assert not any(o.key.kind == "recovery.repair" for o in context.journal().operations())
        assert context.journal().get(original.key) == original
    finally:
        saver.conn.close()


@pytest.mark.parametrize("missing", [False, True])
def test_interrupted_amendment_repair_never_duplicates_budget_or_rewrites_original(
    project,
    monkeypatch,
    missing,
):
    from test_run_context import install_transport, saved_values

    install_transport(monkeypatch, [])
    project[1].write_text(project[1].read_text() + "[limits]\nmax_run_cost_usd=1\n")
    assert (
        cli.main(
            [str(project[2]), "--config", str(project[1]), "--thread-id", "amend", "--approve", ""]
        )
        == 1
    )
    context = RunContext.model_validate(saved_values(project[1], "amend")["run_context"])
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    try:
        saved = saver.get_tuple({"configurable": {"thread_id": "amend"}})
        finish, dump = OperationJournal.finish, recovery_control._dump_state

        def crash_finish(self, key, *args, **kwargs):
            if key.kind == "recovery.amend":
                raise ReceiptError("lost amendment outcome")
            return finish(self, key, *args, **kwargs)

        def crash_dump(path, values, serde):
            if missing:
                raise OSError("lost amendment bytes")
            return dump(path, values, serde)

        with monkeypatch.context() as patch:
            patch.setattr(OperationJournal, "finish", crash_finish)
            patch.setattr(recovery_control, "_dump_state", crash_dump)
            with pytest.raises((ReceiptError, OSError), match="lost amendment"):
                recovery_control.prepare(context, saver, saved, add_budget_usd=2)
        original = operation(context, "recovery.amend")
        result = repair(context, original.key.id)
        assert result["disposition"] == ("retained_partial" if missing else "reconciled")
        recovered = recovery_control.prepare(context, saver, saved, add_budget_usd=2)
        assert recovered["limits"]["max_run_cost_usd"] == 3
        assert recovered["budget_amendment_usd"] == 2
        assert context.journal().get(original.key) == original
        assert len(
            [o for o in context.journal().operations() if o.key.kind == "recovery.amend"]
        ) == (2 if missing else 1)
    finally:
        saver.conn.close()


def test_run_directory_inspection_works_without_checkpoint_or_credentials(
    project, monkeypatch, capsys
):
    context = create(project)
    database = context.execution_config().checkpointer.db_path
    assert not database.exists()
    before = {str(p): recovery.file_hash(p) for p in context.directory.rglob("*") if p.is_file()}
    forbid_runtime(monkeypatch)
    assert cli.main(["recovery", "inspect", "--run-dir", str(context.directory)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] and result["run_id"] == context.run_id
    assert not database.exists()
    assert before == {
        str(p): recovery.file_hash(p) for p in context.directory.rglob("*") if p.is_file()
    }


def test_legacy_completed_checkpoint_inspection_is_read_only(project, monkeypatch, capsys):
    from blacksmith.config import BlacksmithConfig

    database = BlacksmithConfig.load(project[1]).checkpointer.db_path
    saver = build_checkpointer(database)
    graph = StateGraph(BlacksmithState)
    graph.add_node("done", lambda state: {"status": Status.DONE})
    graph.add_edge(START, "done")
    graph.add_edge("done", END)
    graph.compile(checkpointer=saver).invoke({}, {"configurable": {"thread_id": "legacy"}})
    saver.conn.close()
    before = recovery.file_hash(database)
    forbid_runtime(monkeypatch)
    assert (
        cli.main(["recovery", "inspect", "--thread-id", "legacy", "--config", str(project[1])]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["legacy"] and not result["executable"] and result["status"] == "done"
    assert recovery.file_hash(database) == before


def test_bootstrap_failure_captures_original_inputs_without_a_checkpoint(
    project, monkeypatch, capsys
):
    import blacksmith.run_context as rc

    original = rc.git_read

    def fail(path, *args):
        if args[0] == "clone":
            raise RunContextError("injected bootstrap clone failure")
        return original(path, *args)

    monkeypatch.setattr(rc, "git_read", fail)
    with pytest.raises(RunContextError, match="retained artifacts"):
        create(project)
    manifest = next(project[1].parent.glob(".blacksmith/runs/*/manifest.json"))
    context = RunContext.model_validate(json.loads(manifest.read_text())["context"])
    context.verify_inputs()
    index = recovery.validate(context)
    assert len(index["records"]) == 1
    record = json.loads(
        (context.directory / "recovery" / index["records"][0]["manifest"]).read_text()
    )
    assert record["stage"] == "bootstrap" and record["checkpoints"] == []
    assert "injected bootstrap clone failure" in record["reason"]
    forbid_runtime(monkeypatch)
    assert cli.main(["recovery", "inspect", "--run-dir", str(context.directory)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
