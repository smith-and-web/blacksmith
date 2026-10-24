"""Critical storage failures at real CLI/Git/graph boundaries cannot fall through."""

import sqlite3

import pytest
from test_run_context import git, install_transport, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.nodes import plan as plan_module
from blacksmith.receipts import OperationJournal
from blacksmith.run_context import context_from_state
from blacksmith.state import Status


def inject_trigger(monkeypatch, statement):
    original = OperationJournal.create

    def create_with_trigger(cls, context):
        journal = original(context)
        with sqlite3.connect(journal.path) as connection:
            connection.execute(statement)
        return journal

    monkeypatch.setattr(OperationJournal, "create", classmethod(create_with_trigger))


def database(project):
    paths = list(project[1].parent.glob(".blacksmith/runs/*/operations.sqlite"))
    assert len(paths) == 1
    return paths[0]


def operations(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT kind,status FROM operations ORDER BY rowid").fetchall()


def test_bootstrap_intent_failure_prevents_planning_clone_and_sdk(project, monkeypatch, capsys):
    _, config, prd, _ = project
    calls = []
    install_transport(monkeypatch, calls)
    inject_trigger(
        monkeypatch,
        "CREATE TRIGGER fail_bootstrap BEFORE INSERT ON operations "
        "WHEN NEW.kind='git.planning_snapshot' BEGIN "
        "SELECT RAISE(ABORT,'critical write unavailable'); END",
    )
    assert cli.main([str(prd), "--config", str(config)]) == 1
    path = database(project)
    assert operations(path) == []
    assert not (path.parent / "planning").exists()
    assert (path.parent / "manifest.json").is_file()
    assert calls == []
    assert "retained artifacts" in capsys.readouterr().err


def test_sdk_intent_failure_prevents_first_provider_call(project, monkeypatch, capsys):
    _, config, prd, _ = project
    calls = []
    install_transport(monkeypatch, calls)
    inject_trigger(
        monkeypatch,
        "CREATE TRIGGER fail_sdk BEFORE INSERT ON operations "
        "WHEN NEW.kind LIKE 'model.%' BEGIN SELECT RAISE(ABORT,'no receipt'); END",
    )
    assert cli.main([str(prd), "--config", str(config), "--auto-approve"]) == 1
    assert calls == []
    path = database(project)
    assert operations(path) == [("git.planning_snapshot", "completed")] + [
        (f"command:{i}:bootstrap:git", "completed") for i in range(1, 9)
    ]
    assert (path.parent / "planning").is_dir()
    assert not (path.parent / "workspace").exists()
    assert "critical journal read/write failed" in capsys.readouterr().err


def test_sdk_outcome_failure_rolls_back_cost_and_resume_cannot_rebill(project, monkeypatch, capsys):
    _, config, prd, _ = project
    calls = []
    install_transport(monkeypatch, calls)
    inject_trigger(
        monkeypatch,
        "CREATE TRIGGER fail_cost BEFORE INSERT ON events BEGIN "
        "SELECT RAISE(ABORT,'cost storage unavailable'); END",
    )
    assert cli.main([str(prd), "--config", str(config), "--thread-id", "cost"]) == 1
    path = database(project)
    assert len(calls) == 1
    assert operations(path)[-1] == ("model.plan", "intent")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert cli.main(["resume", "--config", str(config), "--thread-id", "cost"]) == 1
    assert len(calls) == 1  # provider response lost at journal boundary; never guess/repeat
    assert "interrupted_attempt_requires_resume" in capsys.readouterr().err
    assert (path.parent / "planning").is_dir()


def test_checkpoint_replay_reuses_completed_plan_response_and_cost_event(project, monkeypatch):
    _, config, prd, _ = project
    calls = []
    install_transport(monkeypatch, calls)
    original = plan_module.cost_event

    def crash_after_receipt(node, unit_id, result):
        raise KeyboardInterrupt("injected exit after SDK receipt, before graph checkpoint")

    monkeypatch.setattr(plan_module, "cost_event", crash_after_receipt)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "replay"])
    assert len(calls) == 1
    values = saved_values(config, "replay")
    context = context_from_state(values)
    assert operations(context.journal().path)[-1] == ("model.plan", "completed")
    monkeypatch.setattr(plan_module, "cost_event", original)
    monkeypatch.setattr(
        cli, "_resolve_approver", lambda args, renderer: lambda payload, values: False
    )
    assert cli.main(["resume", "--config", str(config), "--thread-id", "replay"]) == 1
    assert len(calls) == 1
    final = saved_values(config, "replay")
    assert final["plans"][0]["steps"] == "APPROVED_SNAPSHOT_PLAN"
    assert len(final["cost_events"]) == 1
    assert len(context.journal().events()) == 1
    assert final["status"] == Status.HALTED and not final.get("pr_url")


def test_git_intent_failure_preserves_sdk_work_and_resume_reuses_paid_result(project, monkeypatch):
    root, config, prd, _ = project
    calls = []
    install_transport(monkeypatch, calls)
    inject_trigger(
        monkeypatch,
        "CREATE TRIGGER fail_commit BEFORE INSERT ON operations "
        "WHEN NEW.kind='git.commit' BEGIN SELECT RAISE(ABORT,'no Git receipt'); END",
    )
    monkeypatch.setattr(
        cli,
        "_resolve_approver",
        lambda args, renderer: lambda payload, values: payload["gate"] == "plan",
    )
    assert cli.main([str(prd), "--config", str(config), "--thread-id", "commit"]) == 1
    context = context_from_state(saved_values(config, "commit"))
    workspace = context.workspace_path
    assert (workspace / "result.txt").read_text() == "implemented"
    assert git(workspace, "rev-parse", "HEAD") == context.base_commit
    assert git(workspace, "diff", "--cached", "--name-only") == ""
    assert len(calls) == 2
    with sqlite3.connect(context.journal().path) as connection:
        connection.execute("DROP TRIGGER fail_commit")
    assert cli.main(["resume", "--config", str(config), "--thread-id", "commit"]) == 1
    assert len(calls) == 3  # one new review, no repeated implementation call
    final = saved_values(config, "commit")
    assert len(final["cost_events"]) == 3
    assert sum(e["cost_usd"] for e in final["cost_events"]) == pytest.approx(0.03)
    assert len(context.journal().events()) == 3
    assert not final.get("pr_url")
    assert git(root, "rev-parse", "HEAD") == context.base_commit
    assert not (root / "result.txt").exists()


@pytest.mark.parametrize("stage,expected_calls", [("model.critic", 1), ("model.summary", 3)])
def test_optional_model_handlers_cannot_swallow_critical_receipt_failure(
    project,
    monkeypatch,
    capsys,
    stage,
    expected_calls,
):
    _, config, prd, _ = project
    if stage == "model.critic":
        config.write_text(config.read_text() + "[critic]\nenabled = true\n")
    calls = []
    install_transport(monkeypatch, calls)
    inject_trigger(
        monkeypatch,
        "CREATE TRIGGER fail_optional BEFORE INSERT ON operations "
        f"WHEN NEW.kind='{stage}' BEGIN SELECT RAISE(ABORT,'critical failure'); END",
    )

    def forbidden_publication(
        *, worktree_path, branch, title, body, base=None, remote="origin", draft=False, runner=None
    ):
        pytest.fail("critical receipt failure must prevent push/PR publication")

    monkeypatch.setattr("blacksmith.nodes.pr.open_pull_request", forbidden_publication)
    assert cli.main([str(prd), "--config", str(config), "--auto-approve"]) == 1
    assert len(calls) == expected_calls
    assert "critical journal read/write failed" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_resume_missing_or_corrupt_journal_stops_before_executor(
    project, monkeypatch, capsys, failure
):
    _, config, prd, _ = project
    calls = []
    install_transport(monkeypatch, calls)

    def exit_at_approval(payload, values):
        raise KeyboardInterrupt("process exited at approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: exit_at_approval)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "journal"])
    context = context_from_state(saved_values(config, "journal"))
    path = context.journal().path
    if failure == "missing":
        path.unlink()
    else:
        path.write_bytes(b"preserved corrupt journal")

    def forbidden(config, saver):
        pytest.fail("journal corruption must be diagnosed before executor construction")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(["resume", "--config", str(config), "--thread-id", "journal"]) == 1
    assert len(calls) == 1
    assert "critical journal" in capsys.readouterr().err
    if failure == "missing":
        assert not path.exists()
    else:
        assert path.read_bytes() == b"preserved corrupt journal"
    assert context.planning_path.is_dir()
