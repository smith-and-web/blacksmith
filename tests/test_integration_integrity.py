"""Exact-source joins, layer-union gates and retained failures with real Git/SQLite."""

import json
import re
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow
from test_run_context import git, saved_values, write_prd
from test_run_context import project as project

from blacksmith import cli, integration, joins, verification
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.gate import GateError
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.run_context import context_from_state
from blacksmith.state import Status


def configure(project, command="true", *, sequential=False, qa=False):
    root, config, prd, contract = project
    contract["work_units"] = [
        {"id": name, "title": name, "layers": ["python"],
         "target_modules": [name.lower() + ".txt"], "test_contract": "integration contract",
         "depends_on": ["A"] if sequential and name == "B" else []}
        for name in ("A", "B")
    ]
    if qa:
        contract["layers"]["qa"] = "human"
        contract["work_units"].append({"id": "QA", "title": "manual", "layers": ["qa"],
                                       "target_modules": ["manual"], "test_contract": "inspect",
                                       "depends_on": ["A", "B"]})
    write_prd(prd, contract)
    (root / "blacksmith.toml").write_text("test_cmd=" + json.dumps(command) + "\n")
    (root / ".gitignore").write_text(".checks/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "integration verifier")
    return project


def transport(monkeypatch, *, on_write=None):
    calls = []
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")

    async def collect(self, prompt, options, *, emit_findings=False):
        name = re.search(r"^Unit ([^:]+):", prompt, re.M).group(1)
        role = "plan" if prompt.startswith("Produce a concise") else (
            "review" if emit_findings else "implement")
        calls.append((role, name))
        if role == "plan":
            text = f"APPROVED_{name}"
        elif role == "review":
            text = '{"verdict":"clean","findings":[]}'
        else:
            assert prompt.startswith("Implement this work unit")
            root = Path(options.cwd)
            target = re.search(r"^Target modules: (.+)$", prompt, re.M).group(1)
            assert isinstance(await options.can_use_tool("Write", {"file_path": target}, None),
                              PermissionResultAllow)
            (root / target).write_text(name + "\n")
            if on_write:
                on_write(root, name)
            text = "implemented"
        return ExecutorResult(text, options.model, False, 1, .01, {}, "offline")

    monkeypatch.setattr(Executor, "_collect", collect)
    return calls


def stop_at_pr(monkeypatch):
    seen = []

    def approve(payload, values):
        seen.append(payload["gate"])
        if payload["gate"] == "pr":
            raise KeyboardInterrupt("no publication")
        return True
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    return seen


def run(project, name):
    return cli.main([str(project[2]), "--config", str(project[1]), "--thread-id", name])


def paused(project, monkeypatch):
    configure(project)
    transport(monkeypatch)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        run(project, "joined")
    values = saved_values(project[1], "joined")
    return context_from_state(values), values


def paused_before_join(project, monkeypatch):
    configure(project)
    calls = transport(monkeypatch)
    stop_at_pr(monkeypatch)

    def stop(*args):
        raise KeyboardInterrupt("before first join effect")

    with monkeypatch.context() as patch:
        patch.setattr(joins, "_place", stop)
        with pytest.raises(KeyboardInterrupt):
            run(project, "before-join")
    state = saved_values(project[1], "before-join")
    context = context_from_state(state)
    assert state["join_cursor"] == 0
    assert git(context.workspace_path, "rev-parse", "HEAD") == state["level_bases"]["0"]
    return context, state, calls


def test_individually_green_siblings_combined_red_halt_before_approval_and_preserve_all(
    project, monkeypatch,
):
    configure(project, 'test ! -f a.txt || test ! -f b.txt')
    calls = transport(monkeypatch)
    approvals = stop_at_pr(monkeypatch)
    assert run(project, "combined-red") == 1
    values = saved_values(project[1], "combined-red")
    context = context_from_state(values)
    assert all(r["ok"] and r["gate_receipt"].outcome == "passed"
               for r in values["unit_outcomes"].values())
    proof = values["integration_gate_receipts"]["0"]
    assert proof.outcome == "failed" and proof.scope == "level"
    assert proof.candidate_commit == git(context.workspace_path, "rev-parse", "HEAD")
    assert values["status"] == Status.HALTED and not values.get("final_gate_receipt")
    assert approvals == ["plan"] and not values.get("pr_url")
    assert sorted(calls) == sorted([(role, unit) for role in ("plan", "implement", "review")
                                    for unit in ("A", "B")])
    assert context.workspace_path.is_dir()
    assert all(context.unit_path(u).is_dir() for u in ("A", "B"))
    assert (context.workspace_path / "a.txt").exists()
    assert (context.workspace_path / "b.txt").exists()
    assert Path(proof.artifact_path).is_file()


@pytest.mark.parametrize("parallel,qa", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_final_union_gate_covers_candidate_after_all_auto_work_including_terminal_qa(
    project, monkeypatch, parallel, qa,
):
    configure(project, sequential=not parallel, qa=qa)
    transport(monkeypatch)
    stop_at_pr(monkeypatch)
    # S13 will route QA through approval too. Stop at its existing publication seam here.
    if qa:
        def forbid_publication(*args, **kwargs):
            raise KeyboardInterrupt("final gate reached QA publication seam")
        monkeypatch.setattr("blacksmith.graph.open_draft_pr", forbid_publication)
    with pytest.raises(KeyboardInterrupt):
        run(project, "final-union")
    state = saved_values(project[1], "final-union")
    proof = state["final_gate_receipt"]
    assert proof.outcome == "passed" and proof.scope == "final" and proof.layers == ("python",)
    assert proof.candidate_commit == git(state["worktree_path"], "rev-parse", "HEAD")
    integration.verify_final_gate(state)
    assert all(state["unit_outcomes"][u]["ok"] for u in ("A", "B"))
    assert not state.get("pr_url")
    if parallel:
        assert state["integration_gate_receipts"]["0"].candidate_commit == proof.candidate_commit
        assert len(state["join_destinations"]) == 2
    if qa:
        assert state["unit_outcomes"]["QA"]["human"]


def test_second_pick_conflict_restores_entire_saved_base_and_keeps_both_workers(
    project, monkeypatch,
):
    configure(project)
    root, _, prd, contract = project
    for unit in contract["work_units"]:
        unit["target_modules"] = ["same.txt"]
    write_prd(prd, contract)
    calls = transport(monkeypatch)
    approvals = stop_at_pr(monkeypatch)
    assert run(project, "conflict") == 1
    state = saved_values(project[1], "conflict")
    context = context_from_state(state)
    assert state["join_cursor"] == 1
    assert git(context.workspace_path, "rev-parse", "HEAD") == state["level_bases"]["0"]
    assert not git(context.workspace_path, "status", "--porcelain")
    assert not (context.workspace_path / "same.txt").exists()
    assert not (root / "same.txt").exists()
    assert all(context.unit_path(u).is_dir() for u in ("A", "B"))
    assert (context.unit_path("A") / "same.txt").read_text() == "A\n"
    assert (context.unit_path("B") / "same.txt").read_text() == "B\n"
    failure = (context.directory / "joins/0/failure.txt").read_text()
    assert "A and B" in failure and "same.txt" in failure
    assert (context.directory / "joins/0/conflict.patch").is_file()
    assert approvals == ["plan"] and not state.get("final_gate_receipt")
    assert len([c for c in calls if c[0] == "implement"]) == 2
    assert not state.get("pr_url")


@pytest.mark.parametrize("tamper", ["missing", "foreign", "wrong_level", "duplicate", "base",
                                    "source", "dirty", "gate", "branch"])
def test_join_rejects_missing_foreign_duplicate_changed_or_unverified_outcomes_before_pick(
    project, monkeypatch, tamper,
):
    context, before, _ = paused_before_join(project, monkeypatch)
    before = deepcopy(before)
    if tamper == "missing":
        before["unit_outcomes"].pop("B")
    elif tamper == "foreign":
        before["unit_outcomes"]["FOREIGN"] = {"unit_id": "FOREIGN", "level": 0, "ok": True}
    elif tamper == "wrong_level":
        before["unit_outcomes"]["B"]["level"] = 3
    elif tamper == "duplicate":
        before["level_builds"] = [before["unit_outcomes"]["B"]]
    elif tamper == "base":
        before["unit_outcomes"]["B"]["base_commit"] = "0" * 40
    elif tamper == "source":
        before["unit_outcomes"]["B"]["source_commit"] = "0" * 40
    elif tamper == "dirty":
        (context.unit_path("B") / "unexpected").write_text("extra")
    elif tamper == "gate":
        before["unit_outcomes"]["B"]["gate_receipt"] = None
    else:
        before["unit_outcomes"]["B"]["branch"] = "wrong-branch"
    result = joins.join_level(before)
    assert result["status"] == Status.HALTED
    assert git(context.workspace_path, "rev-parse", "HEAD") == before["level_bases"]["0"]
    assert not (context.workspace_path / "a.txt").exists()
    assert all(context.unit_path(u).exists() for u in ("A", "B"))


def test_gate_exception_or_changed_candidate_replaces_prior_green_and_never_publishes(
    project, monkeypatch,
):
    context, state = paused(project, monkeypatch)
    green = state["final_gate_receipt"]
    (context.workspace_path / "unexpected.txt").write_text("dirty after verification")
    with pytest.raises(GateError):
        integration.verify_final_gate(state)
    result = integration.final_gate(state)
    assert result["status"] == Status.HALTED
    assert result["final_gate_receipt"].outcome == "dirty"
    assert result["final_gate_receipt"].receipt_id != green.receipt_id
    assert (context.workspace_path / "unexpected.txt").read_text() == "dirty after verification"
    assert not state.get("pr_url")


CRASH_JOIN = '''
import os, sys
sys.path.insert(0, sys.argv[1])
from pytest import MonkeyPatch
from test_integration_integrity import transport
from blacksmith import cli, joins
from blacksmith.receipts import OperationJournal
mp = MonkeyPatch()
transport(mp)
stage = sys.argv[4]
original = joins._place
if stage in ('after_pick', 'after_checkpoint'):
    def crash(state, unit, row, base, parent):
        if stage == 'after_checkpoint' and state['join_cursor'] == 1:
            os._exit(87)
        result = original(state, unit, row, base, parent)
        if stage == 'after_pick' and unit.id == 'A':
            os._exit(88)
        return result
    joins._place = crash
else:
    finish = OperationJournal.finish
    def crash_finish(self, key, status, outcome, *, events=()):
        if key.kind == 'join.commit' and key.unit_id == 'A':
            os._exit(89)
        return finish(self, key, status, outcome, events=events)
    OperationJournal.finish = crash_finish
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'join-crash', '--auto-approve'])
'''


@pytest.mark.parametrize("stage,exit_code", [("after_checkpoint", 87), ("after_pick", 88),
                                            ("before_receipt", 89)])
def test_real_process_exit_reconciles_exact_join_commit_and_skips_completed_model_calls(
    project, monkeypatch, stage, exit_code,
):
    configure(project)
    child = subprocess.run([sys.executable, "-c", CRASH_JOIN, str(Path(__file__).parent),
                            str(project[2]), str(project[1]), stage],
                           capture_output=True, text=True, timeout=75)
    assert child.returncode == exit_code, child.stdout + child.stderr
    before = saved_values(project[1], "join-crash")
    context = context_from_state(before)
    assert before["join_cursor"] == (1 if stage == "after_checkpoint" else 0)
    first_head = git(context.workspace_path, "rev-parse", "HEAD")
    assert git(context.workspace_path, "rev-list", "--count", context.base_commit + "..HEAD") == "1"
    original_cost_ids = {row["event_id"] for row in before["cost_events"]}

    async def forbidden(*args, **kwargs):
        pytest.fail("resume after completed workers must not reach paid SDK transport")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", forbidden)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "join-crash"])
    after = saved_values(project[1], "join-crash")
    assert after["join_destinations"]["A"]["destination_commit"] == first_head
    assert git(context.workspace_path, "rev-list", "--count", context.base_commit + "..HEAD") == "2"
    assert after["join_cursor"] == 2 and after["final_gate_receipt"].outcome == "passed"
    assert {row["event_id"] for row in after["cost_events"]} == original_cost_ids
    assert len(after["cost_events"]) == 6
    with sqlite3.connect(context.journal().path) as conn:
        assert conn.execute("SELECT count(*) FROM operations WHERE kind='join.pick' "
                            "AND status='completed'").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM operations WHERE kind='join.commit' "
                            "AND status='completed'").fetchone()[0] == 2
    integration.verify_final_gate(after)
    assert all(context.unit_path(u).is_dir() for u in ("A", "B"))
    assert not after.get("pr_url")


def test_join_integration_rechecks_union_of_layers_from_earlier_completed_levels(
    project, monkeypatch,
):
    configure(project)
    root, _, prd, contract = project
    contract["layers"]["second"] = "auto"
    contract["work_units"] = [
        {"id": "A", "title": "root", "layers": ["python"], "target_modules": ["a.txt"],
         "test_contract": "A remains good"},
        {"id": "B", "title": "left", "layers": ["second"], "target_modules": ["b.txt"],
         "test_contract": "second passes", "depends_on": ["A"]},
        {"id": "C", "title": "right", "layers": ["second"], "target_modules": ["c.txt"],
         "test_contract": "second passes", "depends_on": ["A"]},
    ]
    write_prd(prd, contract)
    (root / "blacksmith.toml").write_text(
        'test_cmd="grep -qx A a.txt"\n[layers.second]\ntest_cmd="true"\n')
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "distinct layer checks")

    def break_prior_layer(path, unit):
        if unit == "B":
            (path / "a.txt").write_text("broken by B\n")

    calls = transport(monkeypatch, on_write=break_prior_layer)
    approvals = stop_at_pr(monkeypatch)
    assert run(project, "earlier-layer") == 1
    state = saved_values(project[1], "earlier-layer")
    proof = state["integration_gate_receipts"]["1"]
    assert proof.layers == ("python", "second") and proof.outcome == "failed"
    assert proof.layer_results[0].outcome == "failed"
    assert proof.layer_results[1].outcome == "not_run"
    assert all(row["ok"] for row in state["unit_outcomes"].values())
    assert approvals == ["plan"] and not state.get("pr_url")
    assert len([row for row in calls if row[0] == "implement"]) == 3


def test_final_gate_runs_prior_layer_after_last_sequential_unit_changes_its_output(
    project, monkeypatch,
):
    configure(project, sequential=True)
    root, _, prd, contract = project
    contract["layers"]["second"] = "auto"
    contract["work_units"][1]["layers"] = ["second"]
    write_prd(prd, contract)
    (root / "blacksmith.toml").write_text(
        'test_cmd="grep -qx A a.txt"\n[layers.second]\ntest_cmd="true"\n')
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "distinct final checks")
    transport(monkeypatch, on_write=lambda path, unit:
              (path / "a.txt").write_text("bad\n") if unit == "B" else None)
    approvals = stop_at_pr(monkeypatch)
    assert run(project, "final-red") == 1
    state = saved_values(project[1], "final-red")
    assert state["final_gate_receipt"].layers == ("python", "second")
    assert state["final_gate_receipt"].outcome == "failed"
    assert all(row["ok"] for row in state["unit_outcomes"].values())
    assert state["status"] == Status.HALTED and approvals == ["plan"]
    assert Path(state["worktree_path"]).is_dir() and not state.get("pr_url")


def test_new_gate_command_exception_replaces_green_receipt_with_error(project, monkeypatch):
    context, state = paused(project, monkeypatch)
    green = state["final_gate_receipt"]
    (context.workspace_path / "extra.txt").write_text("new candidate\n")
    git(context.workspace_path, "add", "extra.txt")
    git(context.workspace_path, "commit", "-m", "new candidate requires fresh gate")

    def broken_command(*args):
        raise GateError("injected native gate infrastructure failure")

    monkeypatch.setattr(verification, "_execute", broken_command)
    result = integration.final_gate(state)
    assert result["status"] == Status.HALTED
    assert result["final_gate_receipt"].outcome == "error"
    assert result["final_gate_receipt"].candidate_commit != green.candidate_commit
    assert result["final_gate_receipt"].receipt_id != green.receipt_id
    assert "injected native gate" in result["final_gate_receipt"].message
    assert not state.get("pr_url")


def test_corrupt_historical_unit_proof_cannot_reuse_green_final_receipt(project, monkeypatch):
    context, state = paused(project, monkeypatch)
    unit_proof = state["unit_outcomes"]["A"]["gate_receipt"]
    Path(unit_proof.artifact_path).write_text("corrupt")
    result = integration.final_gate(state)
    assert result["status"] == Status.HALTED
    assert result["final_gate_receipt"].outcome == "error"
    assert result["final_gate_receipt"].receipt_id != state["final_gate_receipt"].receipt_id
    assert all(row.outcome == "not_run" for row in result["final_gate_receipt"].commands)
    assert context.workspace_path.is_dir() and not state.get("pr_url")


@pytest.mark.parametrize("failure_kind", ["exit", "timeout"])
def test_fetch_failure_after_first_pick_rolls_back_all_picks_with_workers_intact(
    project, monkeypatch, failure_kind,
):
    context, state, calls = paused_before_join(project, monkeypatch)
    state.update(joins.join_level(state))
    assert state["join_cursor"] == 1
    first_head = git(context.workspace_path, "rev-parse", "HEAD")
    assert first_head != state["level_bases"]["0"]
    original = joins.git

    def fail_fetch(path, *args, check=True):
        if args[0] == "fetch":
            assert args[-1] == state["unit_outcomes"]["B"]["source_commit"]
            if failure_kind == "timeout":
                from blacksmith.commands import CommandError
                raise CommandError("injected bounded fetch timeout")
            args = ("fetch", "--no-tags", str(context.unit_path("B") / "missing"), args[-1])
        return original(path, *args, check=check)

    monkeypatch.setattr(joins, "git", fail_fetch)
    result = joins.join_level(state)
    assert result["status"] == Status.HALTED
    expected = "could not fetch exact source" if failure_kind == "exit" else "bounded fetch timeout"
    assert expected in result["errors"][0]["message"]
    assert git(context.workspace_path, "rev-parse", "HEAD") == state["level_bases"]["0"]
    assert not git(context.workspace_path, "status", "--porcelain")
    assert all(context.unit_path(u).is_dir() for u in ("A", "B"))
    assert (context.directory / "joins/0/failure.txt").is_file()
    assert len(calls) == 6 and not state.get("pr_url")


@pytest.mark.parametrize("point", ["intent", "outcome"])
def test_critical_join_write_failure_stops_effects_or_reconciles_completed_commit(
    project, monkeypatch, point,
):
    from blacksmith.receipts import OperationJournal, ReceiptError
    context, state, calls = paused_before_join(project, monkeypatch)
    original = getattr(OperationJournal, "intent" if point == "intent" else "finish")

    def fail(self, key, *args, **kwargs):
        if key.kind == "join.pick":
            raise ReceiptError("injected critical join SQL failure")
        return original(self, key, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(OperationJournal, "intent" if point == "intent" else "finish", fail)
        with pytest.raises(ReceiptError, match="injected critical join SQL"):
            joins.join_level(state)
    head = git(context.workspace_path, "rev-parse", "HEAD")
    if point == "intent":
        assert head == state["level_bases"]["0"]
        assert not (context.workspace_path / "a.txt").exists()
    else:
        assert git(context.workspace_path, "rev-list", "--count",
                   context.base_commit + "..HEAD") == "1"
        result = joins.join_level(state)
        assert result["join_destinations"]["A"]["destination_commit"] == head
        assert git(context.workspace_path, "rev-parse", "HEAD") == head
        assert result["join_cursor"] == 1
    assert all(context.unit_path(u).is_dir() for u in ("A", "B"))
    assert len(calls) == 6


def test_unknown_extra_shared_changes_stop_without_discarding_them(project, monkeypatch):
    context, state, calls = paused_before_join(project, monkeypatch)
    state.update(joins.join_level(state))
    head = git(context.workspace_path, "rev-parse", "HEAD")
    (context.workspace_path / "operator-partial.txt").write_text("do not discard")
    result = joins.join_level(state)
    assert result["status"] == Status.HALTED
    assert "unknown extra shared changes" in result["errors"][0]["message"]
    assert git(context.workspace_path, "rev-parse", "HEAD") == head
    assert (context.workspace_path / "operator-partial.txt").read_text() == "do not discard"
    assert len(calls) == 6 and not state.get("pr_url")


def test_active_pre_join_topology_checkpoint_refuses_without_executor_or_rewrite(
    project, monkeypatch, capsys,
):
    context, state, calls = paused_before_join(project, monkeypatch)
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    cfg = {"configurable": {"thread_id": "before-join"}}
    graph = compile_graph(saver)
    graph.update_state(cfg, {"join_graph_version": None})
    before = deepcopy(saver.get_tuple(cfg).checkpoint)
    saver.conn.close()

    def forbidden(*args):
        pytest.fail("old join topology must refuse before executor construction")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "before-join"]) == 1
    assert "legacy_join_graph_requires_manual_recovery" in capsys.readouterr().err
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple(cfg).checkpoint == before
    saver.conn.close()
    assert context.workspace_path.is_dir()
    assert all(context.unit_path(u).is_dir() for u in ("A", "B"))
    assert len(calls) == 6


def test_parent_topology_and_routes_halt_before_evidence_or_prior_approval():
    from blacksmith.graph import (
        build_graph,
        route_after_approve_pr,
        route_after_final_gate,
        route_after_join,
        route_after_next_unit,
        route_after_unit,
    )
    graph = build_graph()
    assert {"prepare_level", "join_level", "integration_gate", "advance_joined_level",
            "final_gate"} <= graph.nodes.keys()
    assert graph.branches["join_level"]["route_after_join"].ends == {
        "join_level": "join_level", "integration_gate": "integration_gate",
        "human_halt": "human_halt",
    }
    halted = {"status": Status.HALTED, "approvals": {"pr": True}, "join_complete": True}
    for route in (route_after_approve_pr, route_after_final_gate, route_after_join,
                  route_after_next_unit, route_after_unit):
        assert route(halted) == "human_halt"


@pytest.mark.parametrize("tamper", ["missing_context", "halted", "wrong_final_scope"])
def test_final_guard_refuses_publication_or_summary_on_lost_context_halt_or_wrong_scope(
    project, monkeypatch, tamper,
):
    from blacksmith.graph import _open_pr_node
    context, state = paused(project, monkeypatch)
    if tamper == "missing_context":
        state.pop("run_context")
    elif tamper == "halted":
        state["status"] = Status.HALTED
    else:
        state["final_gate_receipt"] = state["unit_outcomes"]["A"]["gate_receipt"]

    def forbidden(state):
        pytest.fail("invalid final evidence must stop before publication or summary")

    result = _open_pr_node(forbidden, None)(state)
    assert result["status"] == Status.HALTED
    assert (result.get("final_gate_receipt") is None
            or result["final_gate_receipt"].outcome != "passed")
    assert context.workspace_path.is_dir() and not state.get("pr_url")


def test_commit_hook_cannot_strip_join_operation_identity_and_reach_a_gate(project, monkeypatch):
    context, state, calls = paused_before_join(project, monkeypatch)
    hooks = context.workspace_path / ".git/hooks"
    hook = hooks / "commit-msg"
    hook.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\n"
                    "p=Path(sys.argv[1])\np.write_text('\\n'.join(\n"
                    "line for line in p.read_text().splitlines()\n"
                    "if not line.startswith('Blacksmith-Operation-Id:'))+'\\n')\n")
    hook.chmod(0o755)
    git(context.workspace_path, "config", "core.hooksPath", str(hooks))
    result = joins.join_level(state)
    assert result["status"] == Status.HALTED
    assert "operation identity" in result["errors"][0]["message"]
    assert git(context.workspace_path, "rev-parse", "HEAD") == state["level_bases"]["0"]
    assert not state.get("integration_gate_receipts") and not state.get("pr_url")
    assert all(context.unit_path(u).is_dir() for u in ("A", "B"))
    assert len(calls) == 6


def test_rollback_failure_preserves_original_cause_and_never_retries_reset(project, monkeypatch):
    from blacksmith.commands import CommandError
    context, state, calls = paused_before_join(project, monkeypatch)
    state.update(joins.join_level(state))
    first_head = git(context.workspace_path, "rev-parse", "HEAD")
    original = joins.git
    reset_calls = []

    def broken(path, *args, check=True):
        if args[0] == "fetch":
            raise CommandError("FETCH_CAUSE_SENTINEL")
        if args[0] == "reset":
            reset_calls.append(args)
            raise CommandError("RESET_CAUSE_SENTINEL")
        return original(path, *args, check=check)

    monkeypatch.setattr(joins, "git", broken)
    result = joins.join_level(state)
    assert result["status"] == Status.HALTED
    message = result["errors"][0]["message"]
    assert "FETCH_CAUSE_SENTINEL" in message and "RESET_CAUSE_SENTINEL" in message
    assert len(reset_calls) == 1
    assert "FETCH_CAUSE_SENTINEL" in (context.directory / "joins/0/failure.txt").read_text()
    assert git(context.workspace_path, "rev-parse", "HEAD") == first_head
    assert all(context.unit_path(u).is_dir() for u in ("A", "B")) and len(calls) == 6
