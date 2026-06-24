"""Tests for per-call token instrumentation (WU-COST-INSTRUMENT).

Every model-calling node (plan, implement) records — alongside the ``cost_usd`` it
already stores — a usage breakdown taken from ``ExecutorResult.usage``: uncached
``input_tokens``, ``output_tokens``, and the two cache counters. The run-end report
prints a token line summing those across the nodes, plus a cache-hit rate of
``cache_read / (input + cache_read + cache_creation)`` — degrading to
"tokens: unavailable" (not crashing) when a node reports ``usage=None``.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from blacksmith.cli import _report, _token_line
from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.nodes.implement import implement
from blacksmith.nodes.plan import plan
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"

PLAN_USAGE = {
    "input_tokens": 100,
    "output_tokens": 10,
    "cache_read_input_tokens": 400,
    "cache_creation_input_tokens": 50,
}
IMPLEMENT_USAGE = {
    "input_tokens": 100,
    "output_tokens": 25,
    "cache_read_input_tokens": 500,
    "cache_creation_input_tokens": 50,
}


def _result(usage, text="done") -> ExecutorResult:
    return ExecutorResult(
        text=text,
        model="claude-sonnet-4-6",
        is_error=False,
        num_turns=1,
        cost_usd=0.05,
        usage=usage,
        session_id="s",
    )


def _snapshot(values):
    return SimpleNamespace(values=values)


class PlanFakeExecutor:
    def __init__(self, usage):
        self.usage = usage

    def run_plan(self, prompt, **kwargs):
        return _result(self.usage, text="1. step")


class ImplementFakeExecutor:
    """Writes a file in the worktree and reports a known usage dict."""

    def __init__(self, usage):
        self.usage = usage

    def run_implement(self, prompt, **kwargs):
        Path(kwargs["cwd"], "feature.txt").write_text("hello\n")
        return _result(self.usage)


def _scratch_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    g("add", "-A")
    g("commit", "-m", "init")
    return WorktreeManager(repo, base_dir=tmp_path / "wt").create("WU-01")


# --- node recording ----------------------------------------------------------


def test_plan_node_records_usage_breakdown():
    out = plan({"prd": parse_prd(VENDORED_PRD)}, executor=PlanFakeExecutor(PLAN_USAGE))
    assert out["plan"]["cost_usd"] == 0.05  # the cost it already stored is untouched
    assert out["plan"]["usage"] == {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_input_tokens": 400,
        "cache_creation_input_tokens": 50,
    }


def test_implement_node_records_usage_breakdown(tmp_path):
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    state = {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)}

    out = implement(state, executor=ImplementFakeExecutor(IMPLEMENT_USAGE))

    assert out["status"] == Status.TESTING
    assert out["implementation"]["cost_usd"] == 0.05  # cost still recorded as before
    assert out["implementation"]["usage"] == {
        "input_tokens": 100,
        "output_tokens": 25,
        "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 50,
    }


def test_node_usage_is_none_when_executor_reports_no_usage():
    out = plan({"prd": parse_prd(VENDORED_PRD)}, executor=PlanFakeExecutor(None))
    assert out["plan"]["usage"] is None


# --- run-end report ----------------------------------------------------------


def test_report_prints_token_totals_and_hit_rate(capsys):
    values = {
        "status": Status.DONE,
        "plan": {"cost_usd": 0.05, "usage": PLAN_USAGE},
        "implementation": {"cost_usd": 0.05, "usage": IMPLEMENT_USAGE},
    }
    _report(_snapshot(values))
    out = capsys.readouterr().out
    # totals summed across the model-calling nodes
    assert "input 200" in out  # 100 + 100
    assert "output 35" in out  # 10 + 25
    # cache-hit = cache_read / (input + cache_read + cache_creation)
    #           = 900 / (200 + 900 + 100) = 900/1200 = 0.75
    assert "75.0%" in out
    assert "total cost: $0.10" in out  # the existing cost total is not regressed


def test_token_line_value_directly():
    values = {
        "plan": {"usage": PLAN_USAGE},
        "implementation": {"usage": IMPLEMENT_USAGE},
    }
    assert _token_line(values) == "tokens: input 200, output 35, cache-hit 75.0%"


def test_report_degrades_when_usage_unavailable(capsys):
    # A node reporting usage=None must not crash the report — it degrades plainly.
    values = {
        "status": Status.HALTED,
        "plan": {"cost_usd": 0.05, "usage": None},
        "implementation": {"cost_usd": None, "usage": None},
    }
    _report(_snapshot(values))
    out = capsys.readouterr().out
    assert "tokens: unavailable" in out


def test_token_line_unavailable_when_no_usage_keys():
    assert _token_line({"status": Status.HALTED}) == "tokens: unavailable"
