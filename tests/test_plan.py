"""Tests for the plan node (WU-09).

Test contract (PRD §6, WU-09): mocked decomposition; selects exactly one unit. A
fake executor stands in for the live model call (the manual smoke is separate).
"""

from pathlib import Path

from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.plan import plan, select_unit
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


def test_plan_node_selects_one_and_builds_plan():
    prd = parse_prd(VENDORED_PRD)
    fake = FakeExecutor(text="1. write config\n2. write tests")
    out = plan({"prd": prd}, executor=fake)

    assert out["selected_unit"].id == "WU-01"  # exactly one unit selected
    assert out["plan"]["unit_id"] == "WU-01"
    expected_modules = list(prd.contract.work_unit_by_id("WU-01").target_modules)
    assert out["plan"]["target_modules"] == expected_modules
    assert out["plan"]["steps"] == "1. write config\n2. write tests"
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
    assert snapshot.values["plan"]["unit_id"] == "WU-01"
    saver.conn.close()
