"""Recovery generations, rejection, remaining allowance and lost parent barriers."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_integration_integrity import configure, stop_at_pr, transport
from test_recovery_artifacts import CRASH_MODEL
from test_run_context import git, install_transport, saved_values
from test_run_context import project as project

from blacksmith import cli, recovery_control
from blacksmith.graph import build_checkpointer
from blacksmith.receipts import OperationKey
from blacksmith.recovery import latest_checkpoints
from blacksmith.run_context import context_from_state


def test_pr_rejection_requires_fresh_interrupt_for_same_candidate_without_paid_replay(
    project,
    monkeypatch,
):
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    calls = []
    install_transport(monkeypatch, calls)
    assert (
        cli.main(
            [
                str(project[2]),
                "--config",
                str(project[1]),
                "--thread-id",
                "reject-pr",
                "--approve",
                "plan",
            ]
        )
        == 1
    )
    before = saved_values(project[1], "reject-pr")
    assert before["approvals"]["pr"] is False
    head = git(before["worktree_path"], "rev-parse", "HEAD")
    seen = stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "reject-pr"])
    after = saved_values(project[1], "reject-pr")
    assert seen == ["pr"] and len(calls) == 2 and not after["approvals"]["pr"]
    assert git(before["worktree_path"], "rev-parse", "HEAD") == head
    assert after["cost_events"] == before["cost_events"]
    assert after["unit_outcomes"] == before["unit_outcomes"] and not after.get("pr_url")


def test_depleted_continuation_refuses_before_executor_without_resetting_any_state(
    project,
    monkeypatch,
):
    # Even if the expected exit misses, the plan-only approval cannot publish.
    code = CRASH_MODEL.replace("'--auto-approve'", "'--approve', 'plan'")
    project[1].write_text(project[1].read_text() + "[limits]\nmax_implement_continuations=0\n")
    child = subprocess.run(
        [sys.executable, "-c", code, str(project[2]), str(project[1])],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert child.returncode == 93, child.stderr
    before = saved_values(project[1], "sdk-kill")
    context = context_from_state(before)
    model = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]
    partial = (context.workspace_path / "result.txt").read_bytes()

    def forbidden(*args):
        pytest.fail("depleted continuation must stop before constructing executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert (
        cli.main(
            ["resume", "--config", str(project[1]), "--thread-id", "sdk-kill", "--continue-attempt"]
        )
        == 1
    )
    assert saved_values(project[1], "sdk-kill") == before
    assert (context.workspace_path / "result.txt").read_bytes() == partial
    assert [o for o in context.journal().operations() if o.key.kind.startswith("model.")] == model


def test_budget_amendment_lost_before_checkpoint_is_applied_once_and_scheduled_flags_refuse(
    project,
    monkeypatch,
):
    calls = []
    install_transport(monkeypatch, calls)
    project[1].write_text(project[1].read_text() + "[limits]\nmax_run_cost_usd=1\n")
    assert (
        cli.main(
            [str(project[2]), "--config", str(project[1]), "--thread-id", "once", "--approve", ""]
        )
        == 1
    )
    before = saved_values(project[1], "once")
    context = context_from_state(before)
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    try:
        saved = saver.get_tuple({"configurable": {"thread_id": "once"}})
        patch = recovery_control.prepare(context, saver, saved, add_budget_usd=2)
        # Repairing an unrelated partial capture must not create another budget amendment.
        from blacksmith import recovery
        from blacksmith.recovery_repair import repair

        def failed_capture(*args, **kwargs):
            raise OSError("unrelated partial capture")

        with monkeypatch.context() as local:
            local.setattr(recovery, "_workspace", failed_capture)
            with pytest.raises(OSError, match="unrelated partial capture"):
                recovery.capture(context, saver, reason="independent capture failure")
        pending = next(
            o
            for o in context.journal().operations()
            if o.key.kind == "recovery.capture" and o.status == "intent"
        )
        assert repair(context, pending.key.id)["disposition"] == "retained_partial"
        assert recovery_control.prepare(context, saver, saved, add_budget_usd=2) == patch
        graph = cli.build_graph_for(context.execution_config(), saver)
        graph.update_state(
            {"configurable": {"thread_id": "once"}}, patch, as_node="recovery_prepare"
        )
    finally:
        saver.conn.close()
    scheduled = saved_values(project[1], "once")
    assert scheduled["limits"]["max_run_cost_usd"] == 3
    with monkeypatch.context() as local:
        local.setattr(
            cli, "build_graph_for", lambda *args: pytest.fail("must refuse duplicate flags")
        )
        assert (
            cli.main(
                [
                    "resume",
                    "--config",
                    str(project[1]),
                    "--thread-id",
                    "once",
                    "--add-budget-usd",
                    "2",
                ]
            )
            == 1
        )
    assert saved_values(project[1], "once") == scheduled

    def stop(payload, state):
        assert payload["gate"] == "plan"
        raise KeyboardInterrupt("fresh plan interrupt")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt, match="fresh plan interrupt"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "once"])
    after = saved_values(project[1], "once")
    assert len(calls) == 1 and after["limits"]["max_run_cost_usd"] == 3
    assert after["budget_amendment_usd"] == 2 and after["cost_events"] == before["cost_events"]
    assert len([o for o in context.journal().operations() if o.key.kind == "recovery.amend"]) == 1


def test_failed_recovery_generations_keep_counters_and_flat_complete_history(
    project,
    monkeypatch,
    tmp_path,
):
    marker = tmp_path / "fixed"
    root = project[0]
    (root / "blacksmith.toml").write_text("test_cmd=" + json.dumps(f"test -f '{marker}'") + "\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "external recovery gate")
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    calls = []
    install_transport(monkeypatch, calls)
    assert (
        cli.main(
            [
                str(project[2]),
                "--config",
                str(project[1]),
                "--thread-id",
                "generations",
                "--approve",
                "plan",
            ]
        )
        == 1
    )
    original = saved_values(project[1], "generations")
    row = original["unit_outcomes"]["U1"]
    paid = len(calls)
    assert row["attempt_number"] == 3 and row["fix_attempts"] == 1
    args = ["resume", "--config", str(project[1]), "--thread-id", "generations", "--retry-stage"]
    for generation in (1, 2):
        assert cli.main(args) == 1
        current = saved_values(project[1], "generations")["unit_outcomes"]["U1"]
        assert not current["ok"] and current["recovery_generation"] == generation
        assert len(current["recovery_history"]) == generation
        assert all("recovery_history" not in prior for prior in current["recovery_history"])
        assert current["attempt_number"] == 3 and current["fix_attempts"] == 1
        assert current["implement_continuations"] == row["implement_continuations"]
        assert len(calls) == paid
    marker.touch()
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(args)
    after = saved_values(project[1], "generations")
    current = after["unit_outcomes"]["U1"]
    assert current["ok"] and current["recovery_generation"] == 3
    assert len(current["recovery_history"]) == 3 and current["recovery_history"][0] == row
    assert len(calls) == paid and after["cost_events"] == original["cost_events"]


def test_unconfirmed_native_command_refuses_before_executor_and_preserves_old_intent(
    project,
    monkeypatch,
):
    root = project[0]
    (root / "blacksmith.toml").write_text('test_cmd="false"\n')
    git(root, "add", "-A")
    git(root, "commit", "-m", "failed gate")
    calls = []
    install_transport(monkeypatch, calls)
    assert (
        cli.main(
            [
                str(project[2]),
                "--config",
                str(project[1]),
                "--thread-id",
                "native-unknown",
                "--approve",
                "plan",
            ]
        )
        == 1
    )
    before = saved_values(project[1], "native-unknown")
    context = context_from_state(before)
    attempt = before["unit_outcomes"]["U1"]["attempt_id"]
    key = OperationKey(context.run_id, "U1", attempt, "command:999:gate_unit:bash")
    context.journal().intent(key, {"argv": "unknown native command", "stage": "gate_unit"})
    original = context.journal().get(key)
    head = git(context.workspace_path, "rev-parse", "HEAD")

    def forbidden(*args):
        pytest.fail("unconfirmed native command must stop before executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert (
        cli.main(
            [
                "resume",
                "--config",
                str(project[1]),
                "--thread-id",
                "native-unknown",
                "--retry-stage",
            ]
        )
        == 1
    )
    assert context.journal().get(key) == original
    assert saved_values(project[1], "native-unknown") == before
    assert git(context.workspace_path, "rev-parse", "HEAD") == head


CRASH_BARRIER = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from pytest import MonkeyPatch
from test_integration_integrity import transport
from blacksmith import cli, unit_graph
import blacksmith.nodes.pr as pr
mp=MonkeyPatch()
transport(mp)
def forbidden(*args, **kwargs):
    raise AssertionError('publication forbidden in barrier drill')
pr.open_pull_request=forbidden
original=unit_graph.unit_runner
marker=Path(sys.argv[3]).parent/'a-completed'
def factory(graph):
    execute=original(graph)
    def run(state):
        if state['selected_unit'].id=='B':
            deadline=time.monotonic()+30
            while not marker.exists() and time.monotonic()<deadline:
                time.sleep(.01)
            assert marker.exists()
            os._exit(105)
        result=execute(state)
        marker.write_text('completed child, parent barrier pending')
        return result
    return run
unit_graph.unit_runner=factory
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'barrier', '--approve', 'plan'])
"""


def test_completed_child_and_never_started_sibling_survive_lost_parent_barrier(
    project,
    monkeypatch,
):
    configure(project)
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH_BARRIER,
            str(Path(__file__).parent),
            str(project[2]),
            str(project[1]),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 105, child.stderr
    before = saved_values(project[1], "barrier")
    context = context_from_state(before)
    assert not context.unit_path("B").exists()
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    try:
        a = next(
            state
            for cfg, state in latest_checkpoints(saver, context)
            if cfg["configurable"]["checkpoint_ns"]
        )
        assert a["unit_outcome"]["ok"] and a["selected_unit"].id == "A"
        original_a = a["unit_outcome"]
    finally:
        saver.conn.close()
    calls = transport(monkeypatch)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "barrier", "--retry-stage"])
    after = saved_values(project[1], "barrier")
    assert calls == [("implement", "B")]
    assert after["unit_outcomes"]["A"] == original_a
    b = after["unit_outcomes"]["B"]
    assert b["ok"] and b["model_tier"] == "initial" and b["attempt_number"] == 1
    assert b["fix_attempts"] == b["implement_continuations"] == b["review_revisions"] == 0
    assert after["final_gate_receipt"].outcome == "passed" and len(after["cost_events"]) == 4
    assert git(context.unit_path("A"), "rev-parse", "HEAD") == original_a["source_commit"]


@pytest.mark.parametrize("evidence", ["workspace", "model_intent"])
def test_missing_child_checkpoint_with_existing_attempt_evidence_is_never_started_fresh(
    project,
    monkeypatch,
    evidence,
):
    configure(project)
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH_BARRIER,
            str(Path(__file__).parent),
            str(project[2]),
            str(project[1]),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 105, child.stderr
    before = saved_values(project[1], "barrier")
    context = context_from_state(before)
    if evidence == "workspace":
        context.unit_path("B").mkdir(parents=True)
    else:
        context.journal().intent(
            OperationKey(context.run_id, "B", "prior-unknown", "model.implement"),
            {"scenario": "paid attempt without checkpoint"},
        )
    models = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]

    def forbidden(*args):
        pytest.fail("incomplete attempt evidence must stop before constructing executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert (
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "barrier", "--retry-stage"])
        == 1
    )
    assert saved_values(project[1], "barrier") == before
    assert [o for o in context.journal().operations() if o.key.kind.startswith("model.")] == models


def test_missing_successful_worker_stops_before_another_failed_worker_can_admit_work(
    project,
    monkeypatch,
    tmp_path,
):
    marker = tmp_path / "repair-gate"
    configure(project, f"[ ! -f b.txt ] || test -f '{marker}'")
    calls = transport(monkeypatch)
    assert (
        cli.main(
            [
                str(project[2]),
                "--config",
                str(project[1]),
                "--thread-id",
                "missing-worker",
                "--approve",
                "plan",
            ]
        )
        == 1
    )
    before = saved_values(project[1], "missing-worker")
    context = context_from_state(before)
    assert before["unit_outcomes"]["A"]["ok"] and not before["unit_outcomes"]["B"]["ok"]
    original_calls = list(calls)
    retained = tmp_path / "retained-a"
    context.unit_path("A").rename(retained)
    marker.touch()

    def forbidden(*args):
        pytest.fail("a missing successful worker must stop before executor/admission")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert (
        cli.main(
            [
                "resume",
                "--config",
                str(project[1]),
                "--thread-id",
                "missing-worker",
                "--retry-stage",
            ]
        )
        == 1
    )
    assert saved_values(project[1], "missing-worker") == before and calls == original_calls
    assert git(retained, "rev-parse", "HEAD") == before["unit_outcomes"]["A"]["source_commit"]
