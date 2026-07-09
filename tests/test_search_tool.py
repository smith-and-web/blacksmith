"""Tests for the search_code tool granted to the implementer (WU-SEARCH-TOOL).

Test contract:
  (a) the tool handler returns ranked matches for a query against a temp repo -- routed
      straight into ``search_code`` (WU-CODE-INDEX), via an in-process MCP tool built with
      the SAME ``create_sdk_mcp_server``/``tool`` pattern ``blacksmith.sandbox`` uses for
      ``run_command``;
  (b) ``[index].enabled=true`` -> the implement node grants the implementer this tool
      (raw Read/Glob/Grep stay available, unchanged);
  (c) ``[index].enabled=false`` (or no ``index_config`` at all) -> the tool is absent and
      the call is byte-for-byte unchanged from before this unit.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from blacksmith.config import IndexConfig
from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.index import (
    SEARCH_CODE_TOOL_NAME,
    create_index_mcp_server,
    format_search_results,
    make_search_code_tool,
)
from blacksmith.nodes.implement import _ALLOWED_TOOLS, _SEARCH_TOOL_NAME, implement
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _sample_repo(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path / "repo")
    (repo / "greet.py").write_text("def hello(name):\n    return f'hi {name}'\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    return repo


def _invoke(tool_def, query: str) -> dict:
    return asyncio.run(tool_def.handler({"query": query}))


# --- (a) tool handler over a real temp repo ----------------------------------


def test_tool_is_named_search_code():
    tool_def = make_search_code_tool(Path("."))
    assert tool_def.name == SEARCH_CODE_TOOL_NAME == "search_code"


def test_search_code_tool_returns_ranked_matches_for_query(tmp_path):
    repo = _sample_repo(tmp_path)
    tool_def = make_search_code_tool(repo)

    result = _invoke(tool_def, "hello")

    text = result["content"][0]["text"]
    assert "greet.py:1" in text
    assert "def hello" in text


def test_search_code_tool_reports_no_matches_for_absent_query(tmp_path):
    repo = _sample_repo(tmp_path)
    tool_def = make_search_code_tool(repo)

    result = _invoke(tool_def, "totally_absent_symbol_xyz")

    assert result["content"][0]["text"] == "no matches"


def test_search_code_tool_respects_configured_limit(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "many.py").write_text("".join(f"def func_{i}():\n    pass\n" for i in range(30)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "many")
    tool_def = make_search_code_tool(repo, limit=3)

    result = _invoke(tool_def, "func")

    lines = result["content"][0]["text"].splitlines()
    assert len(lines) == 3


def test_search_code_tool_respects_exclude(tmp_path):
    repo = _sample_repo(tmp_path)
    tool_def = make_search_code_tool(repo, exclude=("greet.py",))

    result = _invoke(tool_def, "hello")

    assert result["content"][0]["text"] == "no matches"


def test_format_search_results_no_matches():
    assert format_search_results([]) == "no matches"


def test_format_search_results_renders_file_line_snippet():
    results = [{"file": "a.py", "line": 3, "kind": "function", "snippet": "def foo():"}]
    assert format_search_results(results) == "a.py:3: def foo():"


def test_create_index_mcp_server_exposes_search_code_in_process(tmp_path):
    """VERIFY-AT-BUILD: mirrors test_sandbox_tool's server-build check -- builds a live
    McpSdkServerConfig via create_sdk_mcp_server/tool (no mocked SDK internals)."""
    repo = _sample_repo(tmp_path)

    server_config = create_index_mcp_server(repo)

    assert server_config["type"] == "sdk"
    assert server_config["name"] == "blacksmith-index"
    assert server_config["instance"] is not None


# --- (b)/(c) implement node wiring --------------------------------------------


class _EditingFakeExecutor:
    """Simulates the agent editing a safe file in the worktree; records call kwargs."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append({**kwargs, "prompt": prompt})
        Path(kwargs["cwd"], "feature.txt").write_text("hello\n")
        return ExecutorResult(
            text="done",
            model="claude-opus-4-8",
            is_error=False,
            num_turns=3,
            cost_usd=0.5,
            usage={},
            session_id="s",
        )


def _scratch_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return WorktreeManager(repo, base_dir=tmp_path / "wt").create("WU-01")


def test_implement_grants_search_code_tool_when_index_enabled(tmp_path):
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    fake = _EditingFakeExecutor()

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=fake,
        index_config=IndexConfig(enabled=True),
    )

    assert out["status"] == Status.TESTING
    call = fake.calls[0]
    assert SEARCH_CODE_TOOL_NAME in _SEARCH_TOOL_NAME
    assert _SEARCH_TOOL_NAME in call["allowed_tools"]
    assert "blacksmith-index" in call["mcp_servers"]
    # raw Read/Glob/Grep stay available alongside the new tool
    for raw_tool in ("Read", "Glob", "Grep"):
        assert raw_tool in call["allowed_tools"]


def test_implement_search_code_tool_absent_when_index_disabled(tmp_path):
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    wt_baseline = _scratch_worktree(baseline_dir)
    fake_baseline = _EditingFakeExecutor()
    implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt_baseline.path)},
        executor=fake_baseline,
    )

    disabled_dir = tmp_path / "disabled"
    disabled_dir.mkdir()
    wt_disabled = _scratch_worktree(disabled_dir)
    fake_disabled = _EditingFakeExecutor()
    implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt_disabled.path)},
        executor=fake_disabled,
        index_config=IndexConfig(enabled=False),
    )

    baseline_call = fake_baseline.calls[0]
    disabled_call = fake_disabled.calls[0]
    # No index_config at all vs. an explicitly disabled one produce the identical
    # (byte-for-byte) tool surface and prompt/system_prompt -- no search_code tool, no
    # mcp_servers, no other change.
    assert baseline_call["allowed_tools"] == disabled_call["allowed_tools"] == _ALLOWED_TOOLS
    assert "mcp_servers" not in baseline_call
    assert "mcp_servers" not in disabled_call
    assert _SEARCH_TOOL_NAME not in baseline_call["allowed_tools"]
    assert _SEARCH_TOOL_NAME not in disabled_call["allowed_tools"]
    assert baseline_call["system_prompt"] == disabled_call["system_prompt"]
    assert baseline_call["prompt"] == disabled_call["prompt"]
