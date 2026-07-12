"""Tests for the plan node (WU-09).

Test contract (PRD §6, WU-09): mocked decomposition; selects exactly one unit. A
fake executor stands in for the live model call (the manual smoke is separate).
"""

import subprocess
from pathlib import Path

from blacksmith.config import IndexConfig
from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.index import QUALIFIED_INDEX_TOOL_NAMES
from blacksmith.nodes.plan import (
    _PLAN_BLOCKED,
    _PLAN_READ_ONLY,
    plan,
    select_unit,
)
from blacksmith.state import Status

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"


class FakeExecutor:
    def __init__(self, text="1. scaffold\n2. test"):
        self.text = text
        self.calls: list[dict] = []

    def run_plan(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return ExecutorResult(
            text=self.text,
            model="claude-sonnet-4-6",
            is_error=False,
            num_turns=1,
            cost_usd=0.01,
            usage={},
            session_id="s1",
        )


def _contract():
    return parse_prd(VENDORED_PRD).contract


# --- selection ---------------------------------------------------------------


def test_select_unit_picks_first_ready():
    contract = _contract()
    assert select_unit(contract).id == "WU-01"  # only root with no deps


def test_select_unit_advances_as_units_complete():
    contract = _contract()
    assert select_unit(contract, completed=["WU-01"]).id == "WU-02"
    assert select_unit(contract, completed=["WU-01", "WU-02"]).id == "WU-03"


def test_select_unit_none_when_all_done():
    contract = _contract()
    all_ids = [u.id for u in contract.work_units]
    assert select_unit(contract, completed=all_ids) is None


# --- plan node ---------------------------------------------------------------


def test_plan_node_plans_all_auto_units():
    prd = parse_prd(VENDORED_PRD)
    fake = FakeExecutor(text="1. write config\n2. write tests")
    out = plan({"prd": prd}, executor=fake)

    auto = [u for u in prd.contract.work_units if prd.contract.gate_for(u) != "human"]
    human = [u for u in prd.contract.work_units if prd.contract.gate_for(u) == "human"]
    assert human  # the vendored PRD has human-gated unit(s), so the skip below is meaningful

    # A plan per AUTO unit, in declaration order; human-gated units are skipped (they get
    # manual QA via a draft PR). One plan model call per auto unit — not just the first.
    assert [p["unit_id"] for p in out["plans"]] == [u.id for u in auto]
    human_ids = {u.id for u in human}
    assert all(p["unit_id"] not in human_ids for p in out["plans"])
    assert len(fake.calls) == len(auto)

    first = out["plans"][0]
    assert first["unit_id"] == "WU-01"
    assert first["target_modules"] == list(prd.contract.work_unit_by_id("WU-01").target_modules)
    assert first["steps"] == "1. write config\n2. write tests"
    assert out["selected_unit"].id == "WU-01"
    assert out["status"] == Status.AWAITING_PLAN_APPROVAL
    assert len(out["work_units"]) == 11


def test_plan_node_passes_untouchables_as_constitution():
    fake = FakeExecutor()
    plan({"prd": parse_prd(VENDORED_PRD)}, executor=fake)
    system_prompt = fake.calls[0]["system_prompt"]
    assert "CONSTITUTION" in system_prompt
    assert "AI" in system_prompt  # the no-AI-in-Kindling untouchable is present


def test_plan_node_noop_without_executor():
    out = plan({})  # skeleton pass-through
    assert out == {"status": Status.AWAITING_PLAN_APPROVAL}
    assert "selected_unit" not in out


def test_plan_node_missing_prd_halts():
    out = plan({}, executor=FakeExecutor())
    assert out["status"] == Status.HALTED
    assert out["errors"][0]["node"] == "plan"


class ErroringExecutor:
    """A plan executor whose call fails (e.g. max-turns) — surfaced as an is_error result
    by the executor wrapper rather than a raised exception."""

    def run_plan(self, prompt, **kwargs):
        return ExecutorResult(
            text="Reached maximum number of turns (20)",
            model="claude-sonnet-4-6",
            is_error=True,
            num_turns=20,
            cost_usd=None,
            usage=None,
            session_id="s1",
        )


def test_plan_node_halts_on_executor_error():
    out = plan({"prd": parse_prd(VENDORED_PRD)}, executor=ErroringExecutor())
    assert out["status"] == Status.HALTED
    assert out["errors"][0]["node"] == "plan"
    assert "max" in out["errors"][0]["message"].lower()
    assert "selected_unit" not in out  # halted before producing a plan


# --- graph integration -------------------------------------------------------


def test_plan_node_wired_into_graph(tmp_path):
    saver = build_checkpointer(tmp_path / "c.sqlite")
    g = compile_graph(saver, executor=FakeExecutor())
    cfg = {"configurable": {"thread_id": "plan-wired"}}

    g.invoke({"prd": parse_prd(VENDORED_PRD)}, cfg)
    snapshot = g.get_state(cfg)
    assert snapshot.next == ("approve_plan",)  # planned, now paused at the HITL gate
    assert snapshot.values["selected_unit"].id == "WU-01"
    assert snapshot.values["plans"][0]["unit_id"] == "WU-01"
    saver.conn.close()


# --- repo map injection (WU-PLAN-REPO-MAP) -----------------------------------


def _init_target_repo(tmp_path):
    """A plain (non-worktree) git repo standing in for the target repo, with one
    tracked python file carrying a known top-level symbol for build_repo_map to pick up.
    No worktree is created — at plan time none exists yet."""
    repo = tmp_path / "target"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "helper.py").write_text("def known_symbol():\n    pass\n")
    g("add", "-A")
    g("commit", "-m", "init")
    return repo


def test_plan_node_injects_repo_map_when_index_enabled(tmp_path):
    repo = _init_target_repo(tmp_path)
    fake = FakeExecutor()

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=fake,
        index_config=IndexConfig(enabled=True),
        repo_path=str(repo),
    )

    system_prompt = fake.calls[0]["system_prompt"]
    assert "REPO MAP" in system_prompt  # clearly-labelled section
    assert "known_symbol" in system_prompt  # a known symbol from the target repo
    assert "CONSTITUTION" in system_prompt  # existing section unaffected


def test_plan_node_index_prompt_names_both_tools_and_query_semantics(tmp_path):
    repo = _init_target_repo(tmp_path)
    fake = FakeExecutor()

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=fake,
        index_config=IndexConfig(enabled=True),
        repo_path=str(repo),
    )

    system_prompt = fake.calls[0]["system_prompt"]
    # both tools are named, not just search_code
    assert "search_code" in system_prompt
    assert "read_symbol" in system_prompt
    # search_code's query semantics are spelled out
    assert "space-separated" in system_prompt
    assert "OR" in system_prompt
    assert "case-insensitive" in system_prompt
    assert "literal" in system_prompt.lower()
    # Read is scoped to questions the index can't answer (the planner does not edit, so
    # the shared advertisement's implementer-flavored "about to edit" tail is not used here)
    assert "Reach for Read only when the index cannot answer" in system_prompt


def test_plan_node_system_prompt_unchanged_when_index_disabled(tmp_path):
    repo = _init_target_repo(tmp_path)

    baseline = FakeExecutor()
    plan({"prd": parse_prd(VENDORED_PRD)}, executor=baseline)  # today's call, no index kwargs

    disabled = FakeExecutor()
    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=disabled,
        index_config=IndexConfig(enabled=False),
        repo_path=str(repo),
    )

    baseline_prompt = baseline.calls[0]["system_prompt"]
    disabled_prompt = disabled.calls[0]["system_prompt"]
    # Byte-for-byte identical: an explicitly disabled index config (with a real repo_path)
    # produces the exact same system prompt as no index config at all.
    assert baseline_prompt == disabled_prompt
    assert "REPO MAP" not in baseline_prompt
    assert "REPO MAP" not in disabled_prompt


def test_plan_node_builds_repo_map_once_not_per_unit(tmp_path, monkeypatch):
    import blacksmith.nodes.plan as plan_module

    repo = _init_target_repo(tmp_path)
    real_build_repo_map = plan_module.build_repo_map
    calls: list[None] = []

    def counting_build_repo_map(*args, **kwargs):
        calls.append(None)
        return real_build_repo_map(*args, **kwargs)

    monkeypatch.setattr(plan_module, "build_repo_map", counting_build_repo_map)

    prd = parse_prd(VENDORED_PRD)
    auto = [u for u in prd.contract.work_units if prd.contract.gate_for(u) != "human"]
    assert len(auto) > 1  # multiple auto units, so "once per unit" would be observable

    fake = FakeExecutor()
    out = plan(
        {"prd": prd},
        executor=fake,
        index_config=IndexConfig(enabled=True),
        repo_path=str(repo),
    )

    assert len(out["plans"]) == len(auto)  # every auto unit still gets its own plan call
    assert len(calls) == 1  # the repo map itself is built exactly once, not once per unit


# --- graph_rank forwarding (WU-RANK-WIRE) -------------------------------------


def test_plan_node_forwards_graph_rank_true_to_build_repo_map(tmp_path, monkeypatch):
    import blacksmith.nodes.plan as plan_module

    repo = _init_target_repo(tmp_path)
    calls: list[dict] = []
    real_build_repo_map = plan_module.build_repo_map

    def spy_build_repo_map(*args, **kwargs):
        calls.append(kwargs)
        return real_build_repo_map(*args, **kwargs)

    monkeypatch.setattr(plan_module, "build_repo_map", spy_build_repo_map)

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=FakeExecutor(),
        index_config=IndexConfig(enabled=True, graph_rank=True),
        repo_path=str(repo),
    )

    assert len(calls) == 1
    assert calls[0]["rank_by_graph"] is True


def test_plan_node_forwards_graph_rank_false_by_default(tmp_path, monkeypatch):
    import blacksmith.nodes.plan as plan_module

    repo = _init_target_repo(tmp_path)
    calls: list[dict] = []
    real_build_repo_map = plan_module.build_repo_map

    def spy_build_repo_map(*args, **kwargs):
        calls.append(kwargs)
        return real_build_repo_map(*args, **kwargs)

    monkeypatch.setattr(plan_module, "build_repo_map", spy_build_repo_map)

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=FakeExecutor(),
        index_config=IndexConfig(enabled=True),
        repo_path=str(repo),
    )

    assert len(calls) == 1
    assert calls[0]["rank_by_graph"] is False


def test_plan_node_no_map_built_when_index_disabled_regardless_of_graph_rank(
    tmp_path, monkeypatch
):
    import blacksmith.nodes.plan as plan_module

    repo = _init_target_repo(tmp_path)
    calls: list[dict] = []

    def spy_build_repo_map(*args, **kwargs):
        calls.append(kwargs)
        raise AssertionError("build_repo_map must not be called when index is disabled")

    monkeypatch.setattr(plan_module, "build_repo_map", spy_build_repo_map)

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=FakeExecutor(),
        index_config=IndexConfig(enabled=False, graph_rank=True),
        repo_path=str(repo),
    )

    assert calls == []


# --- search_code tool grant (WU-PLAN-SEARCH-TOOL) -----------------------------


def test_plan_node_grants_search_code_tool_when_index_enabled(tmp_path):
    repo = _init_target_repo(tmp_path)
    fake = FakeExecutor()

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=fake,
        index_config=IndexConfig(enabled=True),
        repo_path=str(repo),
    )

    call = fake.calls[0]
    assert set(QUALIFIED_INDEX_TOOL_NAMES) <= set(call["allowed_tools"])
    assert "blacksmith-index" in call["mcp_servers"]
    # the plan tier stays read-only: raw Read/Glob/Grep stay available...
    for raw_tool in _PLAN_READ_ONLY:
        assert raw_tool in call["allowed_tools"]
    # ...and no write/shell/sub-agent tool is ever added alongside search_code. Agent/Task are
    # blocked so the planner can't delegate blind exploration that bypasses the index.
    assert call["disallowed_tools"] == _PLAN_BLOCKED
    for forbidden in ("Write", "Edit", "Bash", "Agent", "Task"):
        assert forbidden in call["disallowed_tools"]
        assert forbidden not in call["allowed_tools"]


def test_plan_node_tool_surface_unchanged_when_index_disabled(tmp_path):
    repo = _init_target_repo(tmp_path)

    baseline = FakeExecutor()
    plan({"prd": parse_prd(VENDORED_PRD)}, executor=baseline)  # today's call, no index kwargs

    disabled = FakeExecutor()
    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=disabled,
        index_config=IndexConfig(enabled=False),
        repo_path=str(repo),
    )

    baseline_call = baseline.calls[0]
    disabled_call = disabled.calls[0]
    # Byte-for-byte identical tool surface: an explicitly disabled index config (with a
    # real repo_path) produces the exact same allowed_tools as no index config at all.
    assert baseline_call["allowed_tools"] == disabled_call["allowed_tools"] == _PLAN_READ_ONLY
    assert "mcp_servers" not in baseline_call
    assert "mcp_servers" not in disabled_call
    for name in QUALIFIED_INDEX_TOOL_NAMES:
        assert name not in baseline_call["allowed_tools"]
        assert name not in disabled_call["allowed_tools"]


# --- structural tool grants (WU-STRUCT-WIRE) -----------------------------------


def test_plan_node_grants_structural_tools_when_index_enabled(tmp_path):
    # search_class/search_method/search_method_in_class are exposed on the same in-process
    # MCP server as search_code/read_symbol (create_index_mcp_server) and granted together as
    # the single QUALIFIED_INDEX_TOOL_NAMES set — the index enable switch grants the WHOLE set.
    repo = _init_target_repo(tmp_path)
    fake = FakeExecutor()

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=fake,
        index_config=IndexConfig(enabled=True),
        repo_path=str(repo),
    )

    allowed = fake.calls[0]["allowed_tools"]
    assert set(QUALIFIED_INDEX_TOOL_NAMES) <= set(allowed)


def test_plan_node_prompt_names_structural_tools_when_index_enabled(tmp_path):
    repo = _init_target_repo(tmp_path)
    fake = FakeExecutor()

    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=fake,
        index_config=IndexConfig(enabled=True),
        repo_path=str(repo),
    )

    system_prompt = fake.calls[0]["system_prompt"]
    assert "search_class" in system_prompt
    assert "search_method" in system_prompt
    assert "search_method_in_class" in system_prompt


def test_plan_system_prompt_advertises_index_tools_when_enabled_even_without_repo_map():
    # Regression (the wiring-cleanup fix): mirror the implement tier — the plan system
    # prompt advertises the index tools whenever the index is WIRED (index_enabled), not
    # only when a repo map built. An empty/failed map must not silently un-advertise them.
    from blacksmith.nodes.plan import _system_prompt

    contract = parse_prd(VENDORED_PRD).contract
    prompt = _system_prompt(contract, None, None, index_enabled=True)
    assert "USE THE INDEX FIRST" in prompt
    assert "search_code" in prompt
    assert "search_class" in prompt
    assert "REPO MAP" not in prompt


def test_plan_node_structural_tools_absent_when_index_disabled(tmp_path):
    repo = _init_target_repo(tmp_path)

    baseline = FakeExecutor()
    plan({"prd": parse_prd(VENDORED_PRD)}, executor=baseline)  # today's call, no index kwargs

    disabled = FakeExecutor()
    plan(
        {"prd": parse_prd(VENDORED_PRD)},
        executor=disabled,
        index_config=IndexConfig(enabled=False),
        repo_path=str(repo),
    )

    for call in (baseline.calls[0], disabled.calls[0]):
        for name in QUALIFIED_INDEX_TOOL_NAMES:
            assert name not in call["allowed_tools"]
        assert "search_class" not in call["system_prompt"]
        assert "search_method" not in call["system_prompt"]
