"""Append-only per-call cost/usage ledger (WU-COST-EVENTS).

The multi-unit run used to undercount cost: ``implementation`` is last-write-wins, so the
run-end report summed plan + ONLY the final unit's spend. This unit adds an append-only
``cost_events`` ledger — the plan node and EVERY implement attempt (including the
escalation retry) append exactly one event {node, unit_id, model, cost_usd, num_turns,
usage} — and points the report's cost/token lines at that ledger, so a multi-unit run sums
ALL units' (and all escalation attempts') spend.
"""

import operator
import subprocess
from functools import reduce
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, get_type_hints

from blacksmith.cli import _token_line, _total_cost_line
from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.nodes.implement import implement
from blacksmith.nodes.plan import plan
from blacksmith.state import BlacksmithState, Status
from blacksmith.worktree import WorktreeManager

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"

USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_input_tokens": 300,
    "cache_creation_input_tokens": 0,
}


def _result(cost, *, usage=USAGE, num_turns=3, model="claude-sonnet-4-6", text="done"):
    return ExecutorResult(
        text=text,
        model=model,
        is_error=False,
        num_turns=num_turns,
        cost_usd=cost,
        usage=usage,
        session_id="s",
    )


def _snapshot(values):
    return SimpleNamespace(values=values)


def _accumulate(*updates):
    """Apply the ``cost_events`` reducer (operator.add) across node updates, the way the
    graph does, so a multi-node run's ledger can be assembled in a unit test."""
    return reduce(operator.add, (u.get("cost_events", []) for u in updates), [])


class PlanFakeExecutor:
    def __init__(self, result):
        self._result = result

    def run_plan(self, prompt, **kwargs):
        return self._result


class ImplementFakeExecutor:
    """Writes a file in the worktree and reports a fixed result for both tiers."""

    def __init__(self, result, *, escalate_result=None):
        self._result = result
        self._escalate_result = escalate_result or result

    def run_implement(self, prompt, **kwargs):
        Path(kwargs["cwd"], "feature.txt").write_text("first\n")
        return self._result

    def run_implement_escalate(self, prompt, **kwargs):
        Path(kwargs["cwd"], "feature.txt").write_text("escalated\n")
        return self._escalate_result


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


# --- state shape -------------------------------------------------------------


def test_state_has_appendonly_cost_events_field():
    hints = get_type_hints(BlacksmithState, include_extras=True)
    assert hints["cost_events"] == Annotated[list[dict], operator.add]


# --- node recording ----------------------------------------------------------


def test_plan_node_appends_one_cost_event():
    out = plan({"prd": parse_prd(VENDORED_PRD)}, executor=PlanFakeExecutor(_result(0.05)))
    events = out["cost_events"]
    assert len(events) == 1
    event = events[0]
    assert event["node"] == "plan"
    assert event["unit_id"] == out["selected_unit"].id
    assert event["model"] == "claude-sonnet-4-6"
    assert event["cost_usd"] == 0.05
    assert event["num_turns"] == 3  # persisted from ExecutorResult.num_turns
    assert event["usage"] == USAGE


def test_implement_node_appends_one_cost_event(tmp_path):
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    state = {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)}

    out = implement(state, executor=ImplementFakeExecutor(_result(0.30, num_turns=7)))

    assert out["status"] == Status.TESTING
    events = out["cost_events"]
    assert len(events) == 1
    assert events[0]["node"] == "implement"
    assert events[0]["unit_id"] == "WU-01"
    assert events[0]["cost_usd"] == 0.30
    assert events[0]["num_turns"] == 7


def test_implement_escalation_attempt_appends_its_own_event(tmp_path):
    # The escalation retry is a separate implement invocation (state["escalated"] set), so it
    # contributes its OWN event — both attempts' spend lands in the ledger.
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    executor = ImplementFakeExecutor(
        _result(0.30, num_turns=7),
        escalate_result=_result(0.90, num_turns=9, model="claude-opus-4-8"),
    )
    base = {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)}

    first = implement(dict(base), executor=executor)
    escalated = implement(dict(base, escalated=True), executor=executor)

    ledger = _accumulate(first, escalated)
    assert [e["cost_usd"] for e in ledger] == [0.30, 0.90]
    assert ledger[1]["model"] == "claude-opus-4-8"  # the escalation attempt's stronger tier
    assert ledger[1]["num_turns"] == 9


def test_cost_event_handles_usage_none(tmp_path):
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    state = {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)}

    out = implement(state, executor=ImplementFakeExecutor(_result(0.10, usage=None)))
    assert out["cost_events"][0]["usage"] is None
    # ...and the report does not crash on a None-usage event (treats it as zeros).
    assert _token_line({"cost_events": out["cost_events"]}) == "tokens: unavailable"


# --- run-end report sums the ledger -----------------------------------------


def test_multi_unit_total_sums_all_events_not_just_last_unit():
    # plan ($0.05) + unit1 ($0.30) + unit2 ($0.50) = $0.85 — the bug summed only plan + the
    # last unit ($0.55), since ``implementation`` is last-write-wins.
    values = {
        "cost_events": [
            {"node": "plan", "unit_id": "WU-01", "cost_usd": 0.05, "usage": USAGE},
            {"node": "implement", "unit_id": "WU-01", "cost_usd": 0.30, "usage": USAGE},
            {"node": "implement", "unit_id": "WU-02", "cost_usd": 0.50, "usage": USAGE},
        ],
        # The last-write-wins slice holds only the final unit — present but NOT what is summed.
        "implementation": {"cost_usd": 0.50, "usage": USAGE},
    }
    assert _total_cost_line(values) == "total cost: $0.85"


def test_escalated_unit_contributes_both_attempts_to_total():
    # plan + first attempt + escalation attempt are all counted (3 events).
    values = {
        "cost_events": [
            {"node": "plan", "unit_id": "WU-01", "cost_usd": 0.05, "usage": None},
            {"node": "implement", "unit_id": "WU-01", "cost_usd": 0.30, "usage": None},
            {"node": "implement", "unit_id": "WU-01", "cost_usd": 0.90, "usage": None},
        ],
    }
    assert _total_cost_line(values) == "total cost: $1.25"


def test_single_unit_total_is_unchanged():
    # One plan + one implement event -> same total a single-unit run reported before.
    values = {
        "cost_events": [
            {"node": "plan", "unit_id": "WU-01", "cost_usd": 0.12, "usage": None},
            {"node": "implement", "unit_id": "WU-01", "cost_usd": 0.30, "usage": None},
        ],
    }
    assert _total_cost_line(values) == "total cost: $0.42"


def test_token_line_sums_ledger_usage():
    values = {
        "cost_events": [
            {"node": "plan", "unit_id": "WU-01", "cost_usd": 0.05, "usage": USAGE},
            {"node": "implement", "unit_id": "WU-01", "cost_usd": 0.30, "usage": USAGE},
            {"node": "implement", "unit_id": "WU-02", "cost_usd": 0.50, "usage": USAGE},
        ],
    }
    # input 300, output 60, cache_read 900; hit = 900 / (300 + 900 + 0) = 0.75
    assert _token_line(values) == "tokens: input 300, output 60, cache-hit 75.0%"


def test_report_uses_ledger(capsys):
    from blacksmith.cli import _report

    values = {
        "status": Status.DONE,
        "cost_events": [
            {"node": "plan", "unit_id": "WU-01", "cost_usd": 0.05, "usage": USAGE},
            {"node": "implement", "unit_id": "WU-01", "cost_usd": 0.30, "usage": USAGE},
            {"node": "implement", "unit_id": "WU-02", "cost_usd": 0.50, "usage": USAGE},
        ],
    }
    _report(_snapshot(values))
    out = capsys.readouterr().out
    assert "total cost: $0.85" in out
    assert "input 300" in out
