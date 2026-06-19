"""Tests for the HITL interrupt nodes (WU-07).

Test contract (PRD §6, WU-07): graph halts at interrupt, resumes on injected
approval. We also cover the rejection path (routes to human_halt) and that the
interrupt surfaces a payload for the human to review.
"""

from pathlib import Path

from langgraph.types import Command

from blacksmith.contract import parse_prd
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"

# A passing gate result lets the auto path reach the PR gate. The test_gate node is
# still a placeholder until WU-06, so seeding test_results stands in for a pass.
PASSING = {"passed": True, "output": "ok", "command": "pytest"}


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _fake_gh_runner(url: str):
    """Succeed for git, return a canned URL for gh — no real GitHub or repo needed."""

    def run(argv, cwd=None):
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")

    return run


def _graph(tmp_path, **compile_kwargs):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    return compile_graph(saver, **compile_kwargs), saver


def test_plan_gate_halts_then_resumes_on_approval(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("plan-approve")

    result = g.invoke({"status": Status.PENDING}, cfg)
    assert "__interrupt__" in result  # halted, surfacing a payload
    assert g.get_state(cfg).next == ("approve_plan",)

    g.invoke(Command(resume=True), cfg)  # inject approval
    state = g.get_state(cfg)
    assert state.values["approvals"]["plan"] is True
    assert state.next == ()  # ran on past the gate
    saver.conn.close()


def test_plan_gate_rejection_routes_to_human_halt(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("plan-reject")

    g.invoke({"status": Status.PENDING}, cfg)
    g.invoke(Command(resume=False), cfg)  # reject
    state = g.get_state(cfg)
    assert state.values["approvals"]["plan"] is False
    assert state.values["status"] == Status.HALTED
    assert state.next == ()
    saver.conn.close()


def test_interrupt_surfaces_plan_payload(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("payload")

    result = g.invoke({"status": Status.PENDING, "plan": {"steps": ["do x"]}}, cfg)
    payload = result["__interrupt__"][0].value
    assert payload["gate"] == "plan"
    assert payload["plan"] == {"steps": ["do x"]}
    saver.conn.close()


def test_pr_gate_halts_and_resumes_on_approval(tmp_path):
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    pr_url = "https://github.com/smith-and-web/kindling/pull/7"
    g, saver = _graph(tmp_path, pr_runner=_fake_gh_runner(pr_url))
    cfg = _cfg("pr-approve")
    seed = {
        "status": Status.PENDING,
        "selected_unit": unit,
        "worktree_path": "/tmp/wt",
        "test_results": PASSING,
    }

    g.invoke(seed, cfg)
    g.invoke(Command(resume=True), cfg)  # approve plan -> runs through to the PR gate
    assert g.get_state(cfg).next == ("approve_pr",)

    g.invoke(Command(resume=True), cfg)  # approve PR -> open_pr (mocked gh)
    state = g.get_state(cfg)
    assert state.values["approvals"] == {"plan": True, "pr": True}
    assert state.values["pr_url"] == pr_url
    assert state.values["status"] == Status.DONE
    assert state.next == ()
    saver.conn.close()


def test_pr_gate_rejection_halts(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("pr-reject")

    g.invoke({"status": Status.PENDING, "test_results": PASSING}, cfg)
    g.invoke(Command(resume=True), cfg)  # approve plan
    assert g.get_state(cfg).next == ("approve_pr",)

    g.invoke(Command(resume=False), cfg)  # reject PR
    state = g.get_state(cfg)
    assert state.values["approvals"]["pr"] is False
    assert state.values["status"] == Status.HALTED
    assert state.next == ()
    saver.conn.close()
