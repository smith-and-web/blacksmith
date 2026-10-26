"""Exact PRD/plan sentinels reach each role through the real graph and SDK options."""

import json
import re
import subprocess
from pathlib import Path

import pytest
from langgraph.types import Command

from blacksmith.config import (
    BlacksmithConfig,
    CriticConfig,
    HitlConfig,
    IndexConfig,
    LimitsConfig,
    ReviewConfig,
)
from blacksmith.context import render_context
from blacksmith.contract import parse_prd
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.gate import run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.implement import implement
from blacksmith.nodes.plan import plan
from blacksmith.state import Status
from blacksmith.worktree import CloneManager

PROSE = "PROSE_ONLY_SENTINEL: preserve café exactly, including this prose-only decision."
PLAN = "APPROVED_PLAN_SENTINEL"


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def prd_file(tmp_path, *, parallel):
    units = []
    for i in range(2):
        units.append({
            "id": f"U{i}", "title": f"unit {i}", "layers": ["python"],
            "target_modules": [f"unit_{i}.txt"], "test_contract": f"contract sentinel {i}",
            "depends_on": [] if parallel or i == 0 else ["U0"],
        })
    # JSON is valid YAML and keeps the contract fixture explicit and deterministic.
    contract = {
        "contract_version": 1, "component": "context-demo", "version": "v1",
        "primary_target_repo": "example/context-demo", "layers": {"python": "auto"},
        "untouchables": ["Do not change the brand"], "work_units": units,
    }
    path = tmp_path / "requirements.md"
    path.write_text(
        "---\n" + json.dumps(contract) + "\n---\n"
        "# Purpose\n" + PROSE + "\n# Scope\nall units\n# Untouchables\nbrand\n"
        "# Acceptance\n" + "requirements at the end must survive too: FINAL_PROSE_SENTINEL\n",
    )
    return path


class RequirementsTransport(Executor):
    def __init__(self, monkeypatch):
        monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "test-only-unused-key")
        async def forbidden_query(*, prompt, options):
            pytest.fail("overridden offline transport must never reach an SDK query")
            yield  # Keep the actual query async-iterator protocol if reached.
        super().__init__(BlacksmithConfig(), query_fn=forbidden_query)
        self.calls = []

    async def _collect(self, prompt, options, *, emit_findings=False):
        assert Path(options.cwd).is_dir()  # inspect the read cwd when the call actually occurs
        expected_tools = {"Read", "Glob", "Grep"}
        if prompt.startswith("Implement this work unit"):
            expected_tools |= {"Write", "Edit", "NotebookEdit"}
        assert set(options.tools) == expected_tools
        if prompt.startswith("Produce a concise"):
            role = "plan"
            uid = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
            text = f"{PLAN}_{uid}\n1. implement the exact requirements"
        elif prompt.startswith("You are reviewing a proposed"):
            role = "critic"
            text = '```json\n{"approved": true, "critique": ""}\n```'
        elif prompt.startswith("Implement this work unit"):
            role = "implement"
            uid = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
            target = re.search(r"^Target modules: (.+)$", prompt, re.M).group(1)
            (Path(options.cwd) / target).write_text(f"{uid}\n")
            text = "implemented"
        elif prompt.startswith("This work unit's implementation"):
            role = "review"
            text = '{"verdict": "clean", "findings": []}'
        else:
            pytest.fail(f"unexpected paid call class: {prompt[:80]}")
        self.calls.append((role, prompt, options))
        return ExecutorResult(text, options.model, False, 1, 0.01, {}, f"offline-{role}")


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "fanout"])
@pytest.mark.parametrize("indexed", [False, True], ids=["raw-reads", "index"])
def test_every_role_gets_exact_prose_contract_and_selected_plan(
    tmp_path, monkeypatch, parallel, indexed,
):
    source = tmp_path / "explicit-other-target"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Test")
    git(source, "config", "user.email", "test@example.invalid")
    (source / "source-sentinel.txt").write_text("source must stay unchanged")
    (source / "blacksmith.toml").write_text('test_cmd = "true"\n')
    git(source, "add", "-A")
    git(source, "commit", "-m", "initial")
    base = git(source, "rev-parse", "HEAD")
    prd_path = prd_file(tmp_path, parallel=parallel)
    prd = parse_prd(prd_path)
    executor = RequirementsTransport(monkeypatch)
    saver = build_checkpointer(tmp_path / "checkpoints.sqlite")
    try:
        graph = compile_graph(
            saver, executor=executor,
            worktree_manager=CloneManager(source, base_dir=tmp_path / "clones"),
            gate=run_gate, limits=LimitsConfig(), review=ReviewConfig(),
            critic=CriticConfig(enabled=True), index=IndexConfig(enabled=indexed),
            hitl=HitlConfig(),
        )
        config = {"configurable": {"thread_id": "requirements"}, "recursion_limit": 200}
        graph.invoke({"prd_path": str(prd_path)}, config)
        before = graph.get_state(config)
        assert before.interrupts[0].value["gate"] == "plan"
        graph.invoke(Command(resume=True), config)
        final = graph.get_state(config)
        assert final.interrupts[0].value["gate"] == "pr"
        assert not final.values.get("pr_url")  # no publication transport was permitted
        assert [role for role, _, _ in executor.calls].count("review") == 2
        for role, prompt, options in executor.calls:
            context = prompt + (options.system_prompt or "")
            assert PROSE in context
            assert prd.body in context
            assert prd.contract.model_dump_json(indent=2) in context
            assert "FINAL_PROSE_SENTINEL" in context
            assert options.cwd  # applies to critic too, with index enabled or disabled
            assert options.setting_sources == [] and options.strict_mcp_config is True
            if role in {"plan", "critic"}:
                assert Path(options.cwd) == source.resolve()
            if role in {"implement", "review"}:
                uid = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
                assert f"{PLAN}_{uid}\n1. implement the exact requirements" in prompt
                assert "DEPENDENCY COMPLETION RECORDS:" in prompt
                assert Path(options.cwd) != source
        assert git(source, "rev-parse", "HEAD") == base
        assert git(source, "status", "--porcelain") == ""
        assert (source / "source-sentinel.txt").read_text() == "source must stay unchanged"
    finally:
        saver.conn.close()


def test_input_limit_rejects_before_first_model_call(tmp_path, monkeypatch):
    path = prd_file(tmp_path, parallel=False)
    executor = RequirementsTransport(monkeypatch)
    prd = parse_prd(path)
    limits = LimitsConfig(max_prd_bytes=100)
    out = plan({"prd": prd}, executor=executor, max_prd_bytes=limits.max_prd_bytes)
    assert out["status"] == Status.HALTED
    assert "split the PRD" in out["errors"][0]["message"]
    assert not executor.calls


def test_configured_input_limit_reaches_production_graph_node(tmp_path, monkeypatch):
    path = prd_file(tmp_path, parallel=False)
    config_path = tmp_path / "runtime.toml"
    config_path.write_text("[limits]\nmax_prd_bytes = 100\n")
    runtime = BlacksmithConfig.load(config_path)
    assert runtime.limits.max_prd_bytes == 100
    assert LimitsConfig().max_prd_bytes == 262144
    executor = RequirementsTransport(monkeypatch)
    saver = build_checkpointer(tmp_path / "limit.sqlite")
    try:
        graph = compile_graph(saver, executor=executor, limits=runtime.limits)
        result = graph.invoke(
            {"prd_path": str(path)}, {"configurable": {"thread_id": "limit"}},
        )
        assert result["status"] == Status.HALTED
        assert not executor.calls
    finally:
        saver.conn.close()


def test_context_retains_full_plan_and_only_relevant_dependency_records(tmp_path):
    prd = parse_prd(prd_file(tmp_path, parallel=False))
    unit = prd.contract.work_units[1]
    steps = "PLAN_START\n" + ("never truncate requirements\n" * 1000) + "PLAN_END"
    context = render_context({
        "prd": prd, "plans": [{"unit_id": "U1", "steps": steps}],
        "unit_results": [{"unit_id": "U0", "candidate": "DEPENDENCY_SENTINEL"},
                         {"unit_id": "foreign", "candidate": "UNRELATED_SENTINEL"}],
    }, unit, include_plan=True)
    assert prd.body in context and steps in context
    assert "DEPENDENCY_SENTINEL" in context
    assert "UNRELATED_SENTINEL" not in context


@pytest.mark.parametrize("resuming,escalated", [(False, False), (True, False), (False, True)])
def test_retries_keep_complete_requirements_plan_and_separate_review_feedback(
    tmp_path, monkeypatch, resuming, escalated,
):
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "README").write_text("base")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    prd = parse_prd(prd_file(tmp_path, parallel=False))
    unit = prd.contract.work_units[0]
    request = "ORIGINAL_REVIEW_START\n" + ("review requirement\n" * 1000) + "ORIGINAL_REVIEW_END"
    executor = RequirementsTransport(monkeypatch)
    result = implement({
        "prd": prd, "selected_unit": unit, "worktree_path": str(root),
        "plans": [{"unit_id": unit.id, "steps": PLAN}], "review_feedback": request,
        "last_gate_output": "GATE_ERROR_SENTINEL", "resume_partial_implement": resuming,
        "escalated": escalated,
    }, executor=executor)
    assert result["status"] == Status.TESTING
    _, prompt, options = executor.calls[0]
    assert prd.body in prompt and PLAN in prompt
    assert "Gate output (tail):\nGATE_ERROR_SENTINEL" in prompt
    assert "REVIEW FEEDBACK (full reviewer request):\n" + request in prompt
    assert ("CONTINUATION:" in prompt) is resuming
    models = executor._config.models
    assert options.model == (models.implement_escalate if escalated else models.implement)
