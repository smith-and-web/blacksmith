"""Explicit recovery through production CLI, real checkpoints, Git and gates."""

import subprocess
import sys
from pathlib import Path

import pytest
from test_integration_integrity import configure, stop_at_pr, transport
from test_recovery_artifacts import CRASH_MODEL
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.graph import build_checkpointer
from blacksmith.recovery import RecoveryError
from blacksmith.recovery_control import prepare
from blacksmith.run_context import context_from_state
from blacksmith.state import Status


def resume_args(project, thread, *args):
    return ["resume", "--config", str(project[1]), "--thread-id", thread, *args]


def test_failed_parallel_stage_retry_retains_successful_sibling_and_counters(
    project, monkeypatch, tmp_path,
):
    marker = tmp_path / "gate-fixed"
    configure(project, f"[ ! -f b.txt ] || test -f '{marker}'")
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    calls = transport(monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "retry", "--approve", "plan"]) == 1
    before = saved_values(project[1], "retry")
    context = context_from_state(before)
    sibling = before["unit_outcomes"]["A"]
    failed = before["unit_outcomes"]["B"]
    assert sibling["ok"] and not failed["ok"]
    assert failed["failed_stage"] == "test_gate" and failed["fix_attempts"] == 1
    assert failed["model_tier"] == "escalated"
    original_calls = list(calls)
    marker.touch()
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(resume_args(project, "retry", "--retry-stage"))
    after = saved_values(project[1], "retry")
    assert calls == original_calls
    assert after["unit_outcomes"]["A"] == sibling
    repaired = after["unit_outcomes"]["B"]
    assert repaired["ok"] and repaired["recovery_history"] == [failed]
    assert repaired["fix_attempts"] == failed["fix_attempts"]
    assert repaired["implement_continuations"] == failed["implement_continuations"]
    assert repaired["attempt_number"] == failed["attempt_number"]
    assert after["final_gate_receipt"].outcome == "passed"
    assert {e["event_id"] for e in after["cost_events"]} == {
        e["event_id"] for e in before["cost_events"]}
    assert git(context.unit_path("A"), "rev-parse", "HEAD") == sibling["source_commit"]
    assert not after.get("pr_url")


def test_rejected_plan_resume_creates_fresh_interrupt_without_replanning(project, monkeypatch):
    calls = transport(monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "reapproval", "--approve", ""]) == 1
    before = saved_values(project[1], "reapproval")
    assert before["approvals"]["plan"] is False and len(calls) == 1
    seen = []

    def stop(payload, values):
        seen.append(payload)
        raise KeyboardInterrupt("fresh approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt, match="fresh approval"):
        cli.main(resume_args(project, "reapproval"))
    assert len(calls) == 1 and len(seen) == 1 and seen[0]["gate"] == "plan"
    after = saved_values(project[1], "reapproval")
    assert after["plans"] == before["plans"] and after["approvals"]["plan"] is False
    assert not after.get("worktree_path")


@pytest.mark.parametrize("amount", [0, -1, float("nan"), float("inf")])
def test_invalid_budget_amendment_precedes_effect_and_preserves_checkpoint(
    project, monkeypatch, amount,
):
    transport(monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "budget", "--approve", ""]) == 1
    state = saved_values(project[1], "budget")
    context = context_from_state(state)
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    try:
        saved = saver.get_tuple({"configurable": {"thread_id": "budget"}})
        operations = context.journal().operations()
        with pytest.raises(RecoveryError, match="positive finite"):
            prepare(context, saver, saved, add_budget_usd=amount)
        assert context.journal().operations() == operations
        assert saver.get_tuple(saved.config).checkpoint == saved.checkpoint
    finally:
        saver.conn.close()


def test_explicit_killed_sdk_continuation_preserves_partial_bytes_and_old_receipt(
    project, monkeypatch,
):
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    child = subprocess.run([sys.executable, "-c", CRASH_MODEL, str(project[2]), str(project[1])],
                           capture_output=True, text=True, timeout=40)
    assert child.returncode == 93, child.stderr
    before = saved_values(project[1], "sdk-kill")
    context = context_from_state(before)
    old = [o for o in context.journal().operations() if o.key.kind == "model.implement"][0]
    assert old.status == "intent"
    calls = []
    from blacksmith.executor import Executor, ExecutorResult

    async def finish(self, prompt, options, *, emit_findings=False):
        target = Path(options.cwd) / "result.txt"
        assert target.read_bytes() == b"PARTIAL_BINARY\0" * 200
        assert "explicitly acknowledged" in prompt and "CONTINUATION FEEDBACK" in prompt
        calls.append(prompt)
        target.write_text("finished")
        return ExecutorResult("finished", options.model, False, 1, .02, {}, "offline")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", finish)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(resume_args(project, "sdk-kill", "--continue-attempt"))
    after = saved_values(project[1], "sdk-kill")
    assert len(calls) == 1 and after["unit_outcomes"]["U1"]["ok"]
    assert after["implement_continuations"] == 1 and after["fix_attempts"] == 0
    assert context.journal().get(old.key) == old
    assert (context.workspace_path / "untracked-partial.bin").read_bytes() == b"RETAIN_THIS\xff"
    assert len([o for o in context.journal().operations() if o.key.kind == "model.implement"]) == 2
    assert after["status"] != Status.DONE and not after.get("pr_url")


def test_budget_addition_is_durable_uses_saved_limit_and_never_resets_spend(project, monkeypatch):
    from test_run_context import install_transport
    calls = []
    project[1].write_text(project[1].read_text()
        + "[limits]\nmax_run_cost_usd=0.01\n[review]\nenabled=false\n")
    install_transport(monkeypatch, calls)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "cap", "--approve", ""]) == 1
    before = saved_values(project[1], "cap")
    assert sum(e["cost_usd"] for e in before["cost_events"]) == .01
    context = context_from_state(before)
    project[1].write_text(project[1].read_text().replace("max_run_cost_usd=0.01",
                                                       "max_run_cost_usd=999"))
    assert cli.main(resume_args(project, "cap", "--approve", "plan")) == 1
    assert len(calls) == 1
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(resume_args(project, "cap", "--add-budget-usd", "0.02"))
    after = saved_values(project[1], "cap")
    assert len(calls) == 2 and after["limits"]["max_run_cost_usd"] == pytest.approx(.03)
    assert after["budget_amendment_usd"] == .02
    assert sum(e["cost_usd"] for e in after["cost_events"]) == .02
    assert after["cost_events"][0] == before["cost_events"][0]
    records = [o for o in context.journal().operations() if o.key.kind == "recovery.amend"]
    assert len(records) == 1 and records[0].intent["request"]["add_budget_usd"] == .02
    assert context.execution_config().limits.max_run_cost_usd == .01


def test_changed_amendment_stops_before_executor(project, monkeypatch):
    from blacksmith.recovery import owned_file
    transport(monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "sealed", "--approve", ""]) == 1
    state = saved_values(project[1], "sealed")
    context = context_from_state(state)
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    try:
        saved = saver.get_tuple({"configurable": {"thread_id": "sealed"}})
        patch = prepare(context, saver, saved)
        assert prepare(context, saver, saved) == patch
    finally:
        saver.conn.close()
    operation = [o for o in context.journal().operations() if o.key.kind == "recovery.amend"][0]
    owned_file(context.directory / "recovery", operation.outcome["patch"]).write_text("corrupt")

    def forbidden(*args):
        pytest.fail("invalid amendment must stop before constructing executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(resume_args(project, "sealed")) == 1
    assert saved_values(project[1], "sealed") == state


CRASH_REVIEW = '''
import os, sys
sys.path.insert(0, sys.argv[1])
from pytest import MonkeyPatch
from test_run_context import install_transport
from blacksmith import cli
from blacksmith.executor import Executor
mp=MonkeyPatch()
install_transport(mp, [])
original=Executor._collect
seats=0
async def collect(self, prompt, options, *, emit_findings=False):
    global seats
    if emit_findings:
        seats += 1
        if seats == 2:
            os._exit(95)
    return await original(self, prompt, options, emit_findings=emit_findings)
Executor._collect=collect
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'review-kill', '--auto-approve'])
'''


def test_interrupted_review_reuses_completed_panel_seat_and_original_candidate(
    project, monkeypatch,
):
    from blacksmith.executor import Executor, ExecutorResult
    project[1].write_text(project[1].read_text() + "[review]\npanel_size=3\n")
    child = subprocess.run([sys.executable, "-c", CRASH_REVIEW, str(Path(__file__).parent),
                            str(project[2]), str(project[1])], capture_output=True,
                           text=True, timeout=40)
    assert child.returncode == 95, child.stderr
    state = saved_values(project[1], "review-kill")
    context = context_from_state(state)
    head = git(context.workspace_path, "rev-parse", "HEAD")
    original = [o for o in context.journal().operations() if o.key.kind == "model.review"]
    assert len(original) == 2 and {o.status for o in original} == {"completed", "intent"}
    calls = []

    async def review(self, prompt, options, *, emit_findings=False):
        assert emit_findings and git(context.workspace_path, "rev-parse", "HEAD") == head
        calls.append(prompt)
        return ExecutorResult('{"verdict":"clean","findings":[]}', options.model,
                              False, 1, .01, {}, "offline")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", review)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(resume_args(project, "review-kill", "--continue-attempt"))
    after = saved_values(project[1], "review-kill")
    assert len(calls) == 2 and after["implement_continuations"] == 1
    assert git(context.workspace_path, "rev-parse", "HEAD") == head
    assert after["final_gate_receipt"].outcome == "passed"
    assert all(context.journal().get(o.key) == o for o in original)
    assert len(after["cost_events"]) == 5
    from blacksmith.recovery import latest_checkpoints
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    try:
        unit = next(value for config, value in latest_checkpoints(saver, context)
                    if config["configurable"]["checkpoint_ns"])
        assert unit["run_cost_base"] + sum(e["cost_usd"] for e in unit["cost_events"]) \
            == pytest.approx(sum(e["cost_usd"] for e in after["cost_events"]))
    finally:
        saver.conn.close()


CRASH_CRITIC = '''
import os, sys
from pathlib import Path
from blacksmith import cli
from blacksmith.executor import Executor, ExecutorResult
import blacksmith.run_context as rc
rc.shutil.which=lambda tool: '/offline/' + tool
os.environ['BLACKSMITH_ANTHROPIC_API_KEY']='unused-offline'
async def collect(self, prompt, options, *, emit_findings=False):
    if prompt.startswith('You are reviewing a proposed'):
        os._exit(96)
    assert prompt.startswith('Produce a concise')
    return ExecutorResult('RETAINED_PLAN', options.model, False, 1, .01, {}, 'offline')
Executor._collect=collect
cli.main([sys.argv[1], '--config', sys.argv[2], '--thread-id', 'critic-kill', '--auto-approve'])
'''


def test_uncertain_critic_reuses_completed_plan_and_consumes_continuation_allowance(
    project, monkeypatch,
):
    from blacksmith.executor import Executor, ExecutorResult
    project[1].write_text(project[1].read_text() + "[critic]\nenabled=true\n")
    child = subprocess.run([sys.executable, "-c", CRASH_CRITIC, str(project[2]), str(project[1])],
                           capture_output=True, text=True, timeout=40)
    assert child.returncode == 96, child.stderr
    state = saved_values(project[1], "critic-kill")
    context = context_from_state(state)
    original = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]
    assert len(original) == 2 and {o.status for o in original} == {"completed", "intent"}
    calls = []

    async def approve(self, prompt, options, *, emit_findings=False):
        assert prompt.startswith("You are reviewing a proposed") and "RETAINED_PLAN" in prompt
        calls.append(prompt)
        return ExecutorResult('```json\n{"approved":true,"critique":""}\n```', options.model,
                              False, 1, .02, {}, "offline")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", approve)

    def stop(payload, values):
        assert payload["gate"] == "plan"
        raise KeyboardInterrupt("original plan approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt, match="original plan approval"):
        cli.main(resume_args(project, "critic-kill", "--continue-attempt"))
    after = saved_values(project[1], "critic-kill")
    assert len(calls) == 1 and after["plans"][0]["steps"] == "RETAINED_PLAN"
    assert sum(after["recovery_plan_continuations"].values()) == 1
    assert all(context.journal().get(o.key) == o for o in original)
    assert len(after["cost_events"]) == 2


def test_budget_blocked_worker_uses_original_remaining_retry_policy(project, monkeypatch, tmp_path):
    marker = tmp_path / "repaired-gate"
    configure(project, f"[ ! -f b.txt ] || test -f '{marker}'", sequential=True)
    project[1].write_text(project[1].read_text()
                         + "[limits]\nmax_run_cost_usd=0.04\n[review]\nenabled=false\n")
    calls = transport(monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", "worker-cap", "--approve", "plan"]) == 1
    before = saved_values(project[1], "worker-cap")
    previous = before["unit_outcomes"]["B"]
    assert previous["attempt_number"] == 1 and previous["fix_attempts"] == 0
    assert previous["model_tier"] == "initial" and not previous["ok"]
    marker.touch()
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(resume_args(project, "worker-cap", "--add-budget-usd", "0.10"))
    after = saved_values(project[1], "worker-cap")
    assert calls.count(("implement", "A")) == 1 and calls.count(("implement", "B")) == 2
    assert after["unit_outcomes"]["A"] == before["unit_outcomes"]["A"]
    result = after["unit_outcomes"]["B"]
    assert result["ok"] and result["fix_attempts"] == 1 and result["attempt_number"] == 2
    assert result["model_tier"] == "initial" and result["recovery_history"] == [previous]
    assert after["limits"]["max_run_cost_usd"] == pytest.approx(.14)
    assert sum(e["cost_usd"] for e in after["cost_events"]) == .05
    assert after["cost_events"][:len(before["cost_events"])] == before["cost_events"]
