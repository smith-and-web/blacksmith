"""Tests for the end-of-run cost summary (WU-COST-SUMMARY).

The plan node records ``cost_usd`` the same way the implement node does, and the
run-end report prints a "total cost: $X.XX" line summing the plan + implement spend
present in the final state — degrading gracefully when a node reports ``None``.
"""

from pathlib import Path
from types import SimpleNamespace

from blacksmith.cli import _report
from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.nodes.plan import plan
from blacksmith.state import Status

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"


class FakeExecutor:
    def __init__(self, cost=0.01):
        self.cost = cost

    def run_plan(self, prompt, **kwargs):
        return ExecutorResult(
            text="1. step",
            model="claude-sonnet-4-6",
            is_error=False,
            num_turns=1,
            cost_usd=self.cost,
            usage={},
            session_id="s1",
        )


def _snapshot(values):
    return SimpleNamespace(values=values)


def test_plan_node_records_cost_usd():
    out = plan({"prd": parse_prd(VENDORED_PRD)}, executor=FakeExecutor(cost=0.07))
    # The plan node surfaces each call's result.cost_usd, just as the implement node does.
    assert out["plans"][0]["cost_usd"] == 0.07


def test_report_prints_summed_total_cost(capsys):
    values = {
        "status": Status.DONE,
        "plan": {"cost_usd": 0.12},
        "implementation": {"cost_usd": 0.30},
    }
    _report(_snapshot(values))
    out = capsys.readouterr().out
    assert "total cost: $0.42" in out


def test_report_degrades_when_a_node_reports_none(capsys):
    # implement reports None -> excluded from the sum, only the plan cost counts.
    values = {
        "status": Status.HALTED,
        "plan": {"cost_usd": 0.25},
        "implementation": {"cost_usd": None},
    }
    _report(_snapshot(values))
    out = capsys.readouterr().out
    assert "total cost: $0.25" in out


def test_report_cost_unavailable_when_all_none(capsys):
    values = {
        "status": Status.HALTED,
        "plan": {"cost_usd": None},
    }
    _report(_snapshot(values))
    out = capsys.readouterr().out
    assert "cost unavailable" in out
