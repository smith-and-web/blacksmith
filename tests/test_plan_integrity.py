"""Checkpointed planning and exact-input approvals through Git/SQLite/real SDK options."""

import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from test_run_context import create, saved_values, write_prd
from test_run_context import project as project

from blacksmith import cli
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.hitl import approve_plan
from blacksmith.nodes.implement import implement
from blacksmith.nodes.plan import plan
from blacksmith.nodes.review import review
from blacksmith.plan_artifacts import PlanIntegrityError, verify_approved_plan
from blacksmith.run_context import canonical_json, context_from_state, sha256, unit_hash
from blacksmith.state import Status
from blacksmith.worktree import RunCloneManager


def install_transport(log_path):
    """Replace only paid transport; retain Executor journal/options/role validation."""
    async def collect(self, prompt, options, *, emit_findings=False):
        unit = re.search(r"^Unit ([^:]+):", prompt, re.M).group(1)
        role = "critic" if prompt.startswith("You are reviewing a proposed") else "plan"
        assert prompt.startswith(("Produce a concise", "You are reviewing a proposed"))
        assert options.setting_sources == [] and options.strict_mcp_config
        assert "EXACT_PROSE_SENTINEL" in options.system_prompt
        assert "IMMUTABLE REQUIREMENTS" in options.system_prompt
        assert not Path(options.cwd).stat().st_mode & 0o222
        previous = [json.loads(line) for line in log_path.read_text().splitlines()] \
            if log_path.exists() else []
        number = 1 + sum(row["unit"] == unit and row["role"] == role for row in previous)
        row = {"unit": unit, "role": role, "cwd": options.cwd,
               "system_prompt": options.system_prompt, "number": number}
        with log_path.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        if role == "plan":
            text = f"APPROVED_PLAN_{unit}_revision_{number}"
        else:
            text = ('```json\n' + json.dumps(
                {"approved": number > 1, "critique": "COVER IT"}) + '\n```')
        return ExecutorResult(text, options.model, False, 1, 0.01, {}, "offline")
    Executor._collect = collect


CRASH_SCRIPT = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_plan_integrity import install_transport
from blacksmith import cli
import blacksmith.graph as gm
import blacksmith.nodes.plan as pm
import blacksmith.run_context as rc
rc.shutil.which = lambda tool: '/offline/' + tool
install_transport(Path(sys.argv[4]))
original = gm.plan
if sys.argv[5] == 'next_unit':
    def crash(state, **kwargs):
        if state.get('plan_cursor') == 1:
            os._exit(79)
        return original(state, **kwargs)
    gm.plan = crash
else:
    def crash_critic(*args, **kwargs):
        os._exit(78)
    pm._run_critique = crash_critic
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'plans', '--auto-approve'])
'''


@pytest.mark.parametrize("indexed,critic,stage", [
    (False, False, "next_unit"), (True, False, "next_unit"),
    (False, True, "next_unit"), (True, True, "next_unit"),
    (False, True, "before_critique"),
])
def test_process_exit_between_plans_or_before_critic_never_replays_paid_calls(
    project, monkeypatch, tmp_path, indexed, critic, stage,
):
    root, config, prd, contract = project
    contract["work_units"].append({**contract["work_units"][0], "id": "U2", "depends_on": ["U1"]})
    write_prd(prd, contract)
    config.write_text(config.read_text() + f"[index]\nenabled={str(indexed).lower()}\n"
                      + f"[critic]\nenabled={str(critic).lower()}\nmax_plan_revisions=1\n")
    log = tmp_path / "transport.jsonl"
    env = {**os.environ, "BLACKSMITH_ANTHROPIC_API_KEY": "unused-offline"}
    child = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, str(Path(__file__).parent),
                            str(prd), str(config), str(log), stage],
                           capture_output=True, text=True, env=env, timeout=45)
    assert child.returncode == (79 if stage == "next_unit" else 78), child.stderr
    before = saved_values(config, "plans")
    context = context_from_state(before)
    if stage == "next_unit":
        assert before["plan_cursor"] == 1 and set(before["plans_by_unit_id"]) == {"U1"}
        assert len(before["cost_events"]) == (4 if critic else 1)
    else:
        assert not before.get("plans_by_unit_id")
    monkeypatch.setattr(Executor, "_collect", Executor._collect)
    install_transport(log)
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")
    if indexed and stage == "next_unit":
        def forbidden_map(*args, **kwargs):
            pytest.fail("completed plan checkpoint must reuse its static repo map")
        monkeypatch.setattr("blacksmith.nodes.plan.build_repo_map", forbidden_map)

    def stop(payload, values):
        assert payload["gate"] == "plan"
        assert len(payload["plans"]) == 2
        assert payload["prd_sha256"] == context.prd_sha256
        assert payload["plans_sha256"] == sha256(canonical_json(values["plans_by_unit_id"]))
        raise KeyboardInterrupt("preserve single plan approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["resume", "--config", str(config), "--thread-id", "plans"])
    after = saved_values(config, "plans")
    assert after["plan_cursor"] == 2 and after["planning_complete"]
    assert list(after["plans_by_unit_id"]) == ["U1", "U2"]
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    for unit in ("U1", "U2"):
        relevant = [row for row in calls if row["unit"] == unit]
        assert [r["role"] for r in relevant] == (["plan", "critic", "plan", "critic"]
                                                if critic else ["plan"])
        assert all(Path(r["cwd"]) == context.planning_path for r in relevant)
    assert len({row["event_id"] for row in after["cost_events"]}) == len(calls)
    assert sum(row["cost_usd"] for row in after["cost_events"]) == pytest.approx(len(calls) * .01)
    assert len({r["system_prompt"] for r in calls}) == 1
    assert (root / "README.md").read_text() == "BASE_SENTINEL\n"
    assert not context.workspace_path.exists()
    assert not (context.directory / "plans/approved.json").exists()
    assert (context.directory / "plans/approval-proposal.json").exists()


@pytest.fixture
def approved(project, monkeypatch, tmp_path):
    context = create(project)
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", Executor._collect)
    log = tmp_path / "calls.jsonl"
    install_transport(log)
    state = context.initial_state()
    state.update(plan(state, executor=Executor(context.execution_config())))
    monkeypatch.setattr("blacksmith.nodes.hitl._decide", lambda payload: True)
    state.update(approve_plan(state))
    clone = RunCloneManager(context).create("U1")
    state["worktree_path"] = str(clone.path)
    verify_approved_plan(state, state["selected_unit"])
    return context, state, log


@pytest.mark.parametrize("tamper", [
    "missing_plan", "wrong_prd", "wrong_state_prd", "changed_plan", "rehash_plan",
    "missing_plan_file", "corrupt_plan_file", "missing_approval_file", "corrupt_approval_file",
    "missing_proposal", "missing_approval_state", "changed_approved_hash", "wrong_selected_plan",
    "foreign_plan", "duplicate_list", "no_approval", "wrong_snapshot", "wrong_selected_unit",
    "missing_run_context",
])
def test_missing_or_changed_approved_plan_blocks_implement_and_review_before_any_sdk(
    approved, tamper,
):
    context, state, log = approved
    plan_file = context.directory / "plans" / (unit_hash("U1") + ".json")
    if tamper == "missing_plan":
        state["plans_by_unit_id"] = {}
    elif tamper == "wrong_prd":
        state["planning_prd_sha256"] = "0" * 64
    elif tamper == "wrong_state_prd":
        prd = state["prd"]
        state["prd"] = type(prd)(prd.path, prd.contract, "WRONG PRD")
    elif tamper in {"changed_plan", "rehash_plan"}:
        row = state["plans_by_unit_id"]["U1"]
        row["steps"] = "UNAPPROVED NEW PLAN"
        if tamper == "rehash_plan":
            row["plan_sha256"] = sha256(canonical_json(
                {k: v for k, v in row.items() if k != "plan_sha256"}))
            value = json.loads(plan_file.read_text())
            value["plan"] = row
            plan_file.write_text(json.dumps(value))
    elif tamper == "missing_plan_file":
        plan_file.unlink()
    elif tamper == "corrupt_plan_file":
        plan_file.write_text("{")
    elif tamper == "missing_approval_file":
        (context.directory / "plans/approved.json").unlink()
    elif tamper == "corrupt_approval_file":
        (context.directory / "plans/approved.json").write_text("{}")
    elif tamper == "missing_proposal":
        (context.directory / "plans/approval-proposal.json").unlink()
    elif tamper == "missing_approval_state":
        state.pop("plan_approval")
    elif tamper == "changed_approved_hash":
        state["approved_plans_sha256"] = "0" * 64
    elif tamper == "wrong_selected_plan":
        state["selected_plan"] = {**state["plans"][0], "steps": "WRONG"}
    elif tamper == "foreign_plan":
        state["plans_by_unit_id"]["FOREIGN"] = state["plans"][0]
    elif tamper == "duplicate_list":
        state["plans"].append(state["plans"][0])
    elif tamper == "wrong_snapshot":
        state["planning_path"] = str(Path(context.repo_path))
    elif tamper == "wrong_selected_unit":
        state["selected_unit"] = state["selected_unit"].model_copy(update={"id": "FOREIGN"})
    elif tamper == "missing_run_context":
        state.pop("run_context")
    else:
        state["approvals"]["plan"] = False
    before = log.read_text()
    executor = Executor(context.execution_config())
    result = implement(state, executor=executor)
    assert result["status"] == Status.HALTED and result["implement_error_kind"] == "plan_integrity"
    result = review(state, executor=executor)
    assert result["status"] == Status.HALTED and result["review_clean"] is False
    assert log.read_text() == before
    assert not (Path(state["worktree_path"]) / "result.txt").exists()
    assert not state.get("implementation")


def test_approval_cannot_bind_a_plan_changed_during_the_interrupt(project, monkeypatch):
    from test_plan import FakeExecutor
    context = create(project)
    state = context.initial_state()
    state.update(plan(state, executor=FakeExecutor()))

    def substitute(payload):
        proposal = context.directory / "plans/approval-proposal.json"
        assert json.loads(proposal.read_text())["plans_sha256"] == payload["plans_sha256"]
        state["plans"][0]["steps"] = "CHANGED WHILE APPROVING"
        return True

    monkeypatch.setattr("blacksmith.nodes.hitl._decide", substitute)
    result = approve_plan(state)
    assert result["status"] == Status.HALTED and result["approvals"]["plan"] is False
    assert not (context.directory / "plans/approved.json").exists()


def test_plan_artifact_failure_reuses_completed_sdk_receipt_without_spend(
    project, monkeypatch, tmp_path,
):
    import blacksmith.plan_artifacts as artifacts
    context = create(project)
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", Executor._collect)
    log = tmp_path / "calls.jsonl"
    install_transport(log)
    original = artifacts.atomic_write

    def fail(*args):
        raise OSError("injected artifact disk failure")

    monkeypatch.setattr(artifacts, "atomic_write", fail)
    executor = Executor(context.execution_config())
    with pytest.raises(PlanIntegrityError, match="cannot persist"):
        plan(context.initial_state(), executor=executor)
    assert len(log.read_text().splitlines()) == 1
    monkeypatch.setattr(artifacts, "atomic_write", original)
    result = plan(context.initial_state(), executor=executor)
    assert result["planning_complete"] and result["plan_cursor"] == 1
    assert len(log.read_text().splitlines()) == 1
    assert len(result["cost_events"]) == 1 and result["cost_events"][0]["event_id"]


def test_pre_hash_approval_checkpoint_refuses_resume_without_executor_or_rewrite(
    project, monkeypatch, capsys,
):
    from test_plan import FakeExecutor
    context = create(project, "old-plans")
    config = context.execution_config()
    saver = build_checkpointer(config.checkpointer.db_path)
    cfg = {"configurable": {"thread_id": context.thread_id}}
    graph = compile_graph(saver, executor=FakeExecutor())
    graph.invoke(context.initial_state(), cfg, durability="sync")
    graph.update_state(cfg, {"planning_version": None})
    before = deepcopy(saver.get_tuple(cfg).checkpoint)
    saver.conn.close()

    def forbidden(*args):
        pytest.fail("legacy planning resume must not construct an executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(["resume", "--config", str(project[1]),
                     "--thread-id", context.thread_id]) == 1
    assert "legacy_planning_requires_manual_recovery" in capsys.readouterr().err
    saver = build_checkpointer(config.checkpointer.db_path)
    assert saver.get_tuple(cfg).checkpoint == before
    saver.conn.close()
    assert context.planning_path.exists() and not context.workspace_path.exists()


@pytest.mark.parametrize("parallel", [False, True])
def test_production_unit_graph_rejects_lost_approval_binding_in_both_shapes(
    project, monkeypatch, tmp_path, parallel,
):
    import blacksmith.unit_graph as ug
    root, config, prd, contract = project
    if parallel:
        contract["work_units"].append({**contract["work_units"][0], "id": "U2",
                                       "target_modules": ["other.txt"]})
        write_prd(prd, contract)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setattr(Executor, "_collect", Executor._collect)
    install_transport(log)  # This transport permits planning/critic only.
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")
    original = ug.unit_runner

    def runner(child):
        execute = original(child)

        def lost_approval(state):
            # Reproduce a missing fan-out/child seed field after the real approval.
            assert state["approvals"]["plan"] and state["plan_approval"]["approved"]
            assert state["approved_plans_sha256"]
            return execute({**state, "approved_plans_sha256": ""})
        return lost_approval

    monkeypatch.setattr(ug, "unit_runner", runner)
    assert cli.main([str(prd), "--config", str(config), "--thread-id", "lost",
                     "--auto-approve"]) == 1
    values = saved_values(config, "lost")
    assert values["status"] == Status.HALTED
    assert len(values["unit_outcomes"]) == (2 if parallel else 1)
    assert all(not row["ok"] for row in values["unit_outcomes"].values())
    assert all("approved plans binding" in row["error"]
               for row in values["unit_outcomes"].values())
    assert len(log.read_text().splitlines()) == (2 if parallel else 1)
    assert not values.get("pr_url")
    assert not (root / "result.txt").exists()


def test_corrupt_plan_on_resume_refuses_before_executor_construction(
    project, monkeypatch, tmp_path, capsys,
):
    from test_run_context import install_transport as cli_transport
    _, config, prd, _ = project
    calls = []
    cli_transport(monkeypatch, calls)

    def stop(payload, values):
        raise KeyboardInterrupt("saved plan approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "corrupt-plan"])
    values = saved_values(config, "corrupt-plan")
    context = context_from_state(values)
    plan_file = context.directory / "plans" / (unit_hash("U1") + ".json")
    plan_file.unlink()
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    cfg = {"configurable": {"thread_id": "corrupt-plan"}}
    before = deepcopy(saver.get_tuple(cfg).checkpoint)
    saver.conn.close()

    def forbidden(*args):
        pytest.fail("corrupt plan resume must not construct an executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(["resume", "--config", str(config), "--thread-id", "corrupt-plan"]) == 1
    assert "missing/corrupt plan artifact" in capsys.readouterr().err
    assert len(calls) == 1
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple(cfg).checkpoint == before
    saver.conn.close()
    assert context.planning_path.exists() and not context.workspace_path.exists()
