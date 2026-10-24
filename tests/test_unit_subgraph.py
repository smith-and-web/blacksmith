"""Shared recovery policy, durable child checkpoints and keyed replay reduction."""

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow
from test_run_context import git, saved_values, write_prd
from test_run_context import project as project

from blacksmith import cli, unit_graph
from blacksmith.config import DEFAULT_IMPLEMENT_ESCALATE_MODEL, DEFAULT_IMPLEMENT_MODEL
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.graph import build_checkpointer
from blacksmith.receipts import ReceiptError, merge_events, merge_unit_outcomes, merge_unit_results


def test_outcomes_results_and_call_findings_are_keyed_and_replay_safe():
    outcome = {"unit_id": "A", "ok": True, "source_commit": "sha"}
    assert merge_unit_outcomes({"A": outcome}, {"A": outcome}) == {"A": outcome}
    with pytest.raises(ReceiptError, match="conflicting terminal"):
        merge_unit_outcomes({"A": outcome}, {"A": {**outcome, "source_commit": "other"}})
    with pytest.raises(ReceiptError, match="key/identity"):
        merge_unit_outcomes({}, {"B": outcome})
    assert merge_unit_results([outcome], [outcome]) == [outcome]
    with pytest.raises(ReceiptError, match="conflicting completed"):
        merge_unit_results([outcome], [{**outcome, "source_commit": "other"}])
    finding = {"event_id": "A:attempt:review:one", "severity": "blocking", "detail": "bug"}
    assert merge_events([finding], [finding]) == [finding]
    with pytest.raises(ReceiptError, match="conflicting reduced"):
        merge_events([finding], [{**finding, "detail": "different"}])


@pytest.mark.parametrize("parallel", [False, True])
def test_both_shapes_use_identical_bounded_attempt_policy_and_namespaces(
    project, monkeypatch, parallel
):
    root, config, prd, contract = project
    contract["work_units"] = [
        {
            "id": u,
            "title": u,
            "layers": ["python"],
            "target_modules": [u.lower() + ".txt"],
            "test_contract": "starts with good",
            "depends_on": [] if parallel or u == "A" else ["A"],
        }
        for u in ("A", "B")
    ]
    write_prd(prd, contract)
    command = (
        'for f in a.txt b.txt; do if [ -f "$f" ]; then '
        'grep -q "^good" "$f" || { echo BAD_FILE:$f; exit 1; }; fi; done'
    )
    (root / "blacksmith.toml").write_text("test_cmd=" + json.dumps(command) + "\n")
    (root / ".gitignore").write_text(".checks/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "policy gate")
    config.write_text(
        config.read_text()
        + "[limits]\nmax_fix_attempts=1\nmax_review_revisions=1\nmax_implement_continuations=1\n"
    )
    coverage_cmd = "mkdir -p .checks; printf x >> .checks/coverage-count"
    config.write_text(
        config.read_text() + "[sbfl]\nenabled=true\ncoverage_cmd=" + json.dumps(coverage_cmd) + "\n"
    )
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")
    models, reviews = {}, {}

    async def collect(self, prompt, options, *, emit_findings=False):
        assert options.setting_sources == [] and options.strict_mcp_config
        if prompt.startswith("Produce a concise"):
            return ExecutorResult("APPROVED", options.model, False, 1, 0.01, {}, "offline")
        name = re.search(r"^Unit ([^:]+):", prompt, re.M).group(1)
        if emit_findings:
            reviews[name] = reviews.get(name, 0) + 1
            findings = (
                [{"severity": "blocking", "file": name.lower() + ".txt", "detail": "REVISE"}]
                if reviews[name] == 1
                else []
            )
            return ExecutorResult(
                json.dumps(
                    {"verdict": "clean" if not findings else "blocked", "findings": findings}
                ),
                options.model,
                False,
                1,
                0.01,
                {},
                "offline",
            )
        models.setdefault(name, []).append(options.model)
        number = len(models[name])
        target = Path(options.cwd) / (name.lower() + ".txt")
        permission = await options.can_use_tool("Write", {"file_path": str(target)}, None)
        assert isinstance(permission, PermissionResultAllow)
        if number == 1:
            target.write_text("partial")
            return ExecutorResult(
                "cap", options.model, True, 40, 0.01, {}, "offline", error_kind="max_turns"
            )
        if number == 2:
            assert target.read_text() == "partial"
            assert "CONTINUATION FEEDBACK" in prompt
        if number in {3, 4}:
            assert "BAD_FILE:" + name.lower() + ".txt" in prompt
        if number == 5:
            assert "REVISE" in prompt
        target.write_text(("bad" if number < 4 else "good") + f" attempt {number}\n")
        return ExecutorResult("implemented", options.model, False, 1, 0.01, {}, "offline")

    monkeypatch.setattr(Executor, "_collect", collect)

    def approve(payload, values):
        if payload["gate"] == "plan":
            return True
        raise KeyboardInterrupt("no publication")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "policy"])
    assert models == {
        u: [DEFAULT_IMPLEMENT_MODEL] * 3 + [DEFAULT_IMPLEMENT_ESCALATE_MODEL] * 2
        for u in ("A", "B")
    }
    assert reviews == {"A": 2, "B": 2}
    values = saved_values(config, "policy")
    assert set(values["unit_outcomes"]) == {"A", "B"}
    assert sorted(row["unit_id"] for row in values["unit_results"]) == ["A", "B"]
    assert len(values["review_findings"]) == 2
    assert len({e["event_id"] for e in values["cost_events"]}) == len(values["cost_events"]) == 16
    for outcome in values["unit_outcomes"].values():
        assert outcome["ok"] and outcome["review_clean"]
        assert outcome["attempt_number"] == 5 and outcome["attempt_id"] == "[1,true,1,1]"
        assert outcome["model_tier"] == "escalated"
        assert (
            outcome["fix_attempts"]
            == outcome["review_revisions"]
            == outcome["implement_continuations"]
            == 1
        )
        assert outcome["gate_receipt"].candidate_commit == outcome["source_commit"]
        assert outcome["unit_namespace"].startswith(values["run_id"] + "/")
    operations = next(config.parent.glob(".blacksmith/runs/*/operations.sqlite"))
    with sqlite3.connect(operations) as conn:
        commands = [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT intent_json FROM operations WHERE kind LIKE 'command:%'"
            )
        ]
    coverage_runs = [row for row in commands if row["argv"] == coverage_cmd]
    assert len(coverage_runs) == 2 and all(row["stage"] == "fix_retry" for row in coverage_runs)
    saver = build_checkpointer(config.parent / ".blacksmith/checkpoints.sqlite")
    try:
        namespaces = [
            row[0]
            for row in saver.conn.execute(
                "SELECT DISTINCT checkpoint_ns FROM checkpoints "
                "WHERE thread_id='policy' AND checkpoint_ns!=''"
            )
        ]
        assert len(namespaces) == 2
        for namespace in namespaces:
            checkpoint = saver.get_tuple(
                {"configurable": {"thread_id": "policy", "checkpoint_ns": namespace}}
            )
            unit = checkpoint.checkpoint["channel_values"]
            assert unit["unit_outcome"] == values["unit_outcomes"][unit["selected_unit"].id]
            assert unit["unit_stage"] == "finalize_unit"
    finally:
        saver.conn.close()


CRASH_SCRIPT = r"""
import os,sys
from pathlib import Path
from claude_agent_sdk import PermissionResultAllow
from blacksmith import cli,unit_graph
from blacksmith.executor import Executor,ExecutorResult
os.environ['BLACKSMITH_ANTHROPIC_API_KEY']='unused-offline'
cli.load_dotenv=lambda path: None
import blacksmith.run_context
blacksmith.run_context.shutil.which=lambda tool: '/offline/'+tool
async def collect(self,prompt,options,*,emit_findings=False):
    if prompt.startswith('Produce a concise'):
        return ExecutorResult('APPROVED',options.model,False,1,.01,{},'offline')
    assert prompt.startswith('Implement this work unit') and not emit_findings
    target=Path(options.cwd)/'result.txt'
    allowed=await options.can_use_tool('Write',{'file_path':str(target)},None)
    assert isinstance(allowed,PermissionResultAllow)
    target.write_text('completed\n')
    return ExecutorResult('done',options.model,False,1,.01,{},'offline')
Executor._collect=collect
unit_graph.audit_and_commit=lambda state: os._exit(77)
cli._resolve_approver=lambda args,renderer: lambda payload,values: True
cli.main([sys.argv[1],'--config',sys.argv[2],'--thread-id','crash'])
"""


def test_process_exit_after_model_checkpoint_resumes_commit_without_sdk_replay(
    project, monkeypatch
):
    root, config, prd, _ = project
    config.write_text(config.read_text() + "[review]\nenabled=false\n")
    command = [sys.executable, "-c", CRASH_SCRIPT, str(prd), str(config)]
    child = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60
    )
    assert child.returncode == 77, child.stdout + child.stderr
    values = saved_values(config, "crash")
    path = Path(values["worktree_path"])
    assert (path / "result.txt").read_text() == "completed\n"
    assert git(path, "rev-parse", "HEAD") == values["run_context"]["base_commit"]
    saver = build_checkpointer(config.parent / ".blacksmith/checkpoints.sqlite")
    try:
        namespace = saver.conn.execute(
            "SELECT DISTINCT checkpoint_ns FROM checkpoints "
            "WHERE thread_id='crash' AND checkpoint_ns!=''"
        ).fetchone()[0]
        checkpoint = saver.get_tuple(
            {"configurable": {"thread_id": "crash", "checkpoint_ns": namespace}}
        )
        channels = checkpoint.checkpoint["channel_values"]
        assert channels["unit_stage"] == "implement"
        assert (
            channels["pending_implementation"]["base_commit"]
            == values["run_context"]["base_commit"]
        )
        assert len(channels["cost_events"]) == 1 and channels["cost_events"][0]["call_id"]
    finally:
        saver.conn.close()
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")

    async def forbidden(self, prompt, options, *, emit_findings=False):
        pytest.fail("completed model checkpoint must not reach any SDK transport again")

    monkeypatch.setattr(Executor, "_collect", forbidden)

    def approve(payload, values):
        assert payload["gate"] == "pr"
        raise KeyboardInterrupt("retain before publication")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["resume", "--config", str(config), "--thread-id", "crash"])
    final = saved_values(config, "crash")
    assert final["unit_outcomes"]["U1"]["ok"]
    assert len(final["cost_events"]) == 2
    assert len({e["event_id"] for e in final["cost_events"]}) == 2
    assert git(path, "rev-list", "--count", f"{final['run_context']['base_commit']}..HEAD") == "1"
    assert git(path, "status", "--porcelain") == ""
    assert not final.get("pr_url")
    with sqlite3.connect(Path(final["run_context"]["run_dir"]) / "operations.sqlite") as conn:
        assert (
            conn.execute("SELECT count(*) FROM operations WHERE kind='model.implement'").fetchone()[
                0
            ]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM operations WHERE kind='git.commit' AND status='completed'"
            ).fetchone()[0]
            == 1
        )


def test_completed_squash_reuses_exact_sha_after_lost_node_checkpoint(project):
    from test_gate_integrity import owned_commit, prepare

    context, state = prepare(project)
    state["unit_base_commit"] = context.base_commit
    state["review_revisions"] = 1
    owned_commit(state, "revision.txt", "revision")
    state["unit_mode"] = "parallel"
    before = git(state["worktree_path"], "rev-parse", "HEAD")
    result = unit_graph.normalize_parallel_candidate(state)
    after = result["implementation"]["candidate_commit"]
    assert after != before
    assert git(state["worktree_path"], "rev-parse", before + "^{tree}") == git(
        state["worktree_path"], "rev-parse", after + "^{tree}"
    )
    # Re-enter with the old pre-node state: HEAD is already squashed to one commit.
    replay = unit_graph.normalize_parallel_candidate(state)
    assert replay == result
    assert git(state["worktree_path"], "rev-parse", "HEAD") == after
    with sqlite3.connect(context.journal().path) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM operations WHERE kind='git.squash' AND status='completed'"
            ).fetchone()[0]
            == 1
        )


def test_old_active_unit_topology_refused_before_executor_and_checkpoint_unchanged(
    project, monkeypatch, capsys
):
    from test_run_context import install_transport

    from blacksmith.run_context import context_from_state

    root, config, prd, _ = project
    config.write_text(config.read_text() + "[review]\nenabled=false\n")
    calls = []
    install_transport(monkeypatch, calls)

    def approve(payload, values):
        if payload["gate"] == "plan":
            return True
        raise KeyboardInterrupt("retain")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "old-topology"])
    values = saved_values(config, "old-topology")
    context = context_from_state(values)
    saver = build_checkpointer(config.parent / ".blacksmith/checkpoints.sqlite")
    try:
        g = cli.build_graph_for(context.execution_config(), saver)
        g.update_state(
            {"configurable": {"thread_id": "old-topology"}},
            {"unit_graph_version": 0},
            as_node="prepare_worktree",
        )
    finally:
        saver.conn.close()
    before = saved_values(config, "old-topology")
    previous_calls = len(calls)

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported topology must stop before constructing an executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(["resume", "--config", str(config), "--thread-id", "old-topology"]) == 1
    assert "legacy_unit_graph_requires_manual_recovery" in capsys.readouterr().err
    assert len(calls) == previous_calls
    assert saved_values(config, "old-topology") == before
    assert context.workspace_path.is_dir() and (context.directory / "operations.sqlite").is_file()


def test_unit_prepare_rejects_foreign_workspace_before_model(project, monkeypatch):
    from test_gate_integrity import prepare

    from blacksmith import graph as gm
    from blacksmith.run_context import RunContextError

    context, state = prepare(project)
    state["worktree_path"] = context.repo_path

    def forbidden(*args, **kwargs):
        pytest.fail("foreign workspace reached implementation")

    monkeypatch.setattr(gm, "implement", forbidden)
    child = unit_graph.build_unit_graph(gm.UnitDeps())
    with pytest.raises(RunContextError, match="immutable run ownership"):
        child.invoke(state)
    assert not (Path(context.repo_path) / "result.txt").exists()


def test_standalone_legacy_runner_and_failed_optional_writer_do_not_block():
    from blacksmith.contract import WorkUnit
    from blacksmith.graph import UnitDeps

    unit = WorkUnit(
        id="legacy",
        title="legacy",
        layers=["python"],
        target_modules=["x"],
        test_contract="preseeded compatibility state",
    )
    seed = {
        "selected_unit": unit,
        "test_results": {"passed": True, "output": "ok", "command": "injected"},
    }
    child = unit_graph.build_unit_graph(UnitDeps())

    def broken_writer(payload):
        raise OSError("observation sink unavailable")

    token = unit_graph._parent_writer.set(broken_writer)
    try:
        assert child.invoke(seed)["unit_outcome"]["ok"]
    finally:
        unit_graph._parent_writer.reset(token)
    result = unit_graph.unit_runner(child)(seed)
    assert result["unit_outcomes"]["legacy"]["ok"]
    assert result["cost_events"] == []
