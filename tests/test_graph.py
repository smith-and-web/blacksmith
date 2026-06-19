"""Tests for the v0 graph skeleton + checkpointer (WU-03).

Test contract (PRD §6, WU-03): graph compiles; checkpointer persists + resumes a
dummy state. The persist/resume test simulates a full process restart (a fresh
checkpointer on the same DB file), exercising AC-2.
"""

import logging
from pathlib import Path

from langgraph.types import Command

from blacksmith.contract import parse_prd
from blacksmith.graph import (
    blacksmith_serde,
    build_checkpointer,
    build_graph,
    compile_graph,
    route_after_implement,
    route_after_test_gate,
)
from blacksmith.state import BlacksmithState, Status

SERDE_LOGGER = "langgraph.checkpoint.serde.jsonplus"


def _serde_warnings(caplog):
    return [
        r for r in caplog.records if "unregistered" in r.message or "will be blocked" in r.message
    ]

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_PRD = REPO_ROOT / "blacksmith-v0-prd.md"

EXPECTED_NODES = {
    "ingest_prd",
    "plan",
    "approve_plan",
    "prepare_worktree",
    "implement",
    "test_gate",
    "approve_pr",
    "open_pr",
    "human_halt",
}


def test_state_schema_has_expected_fields():
    assert "status" in BlacksmithState.__annotations__
    assert "errors" in BlacksmithState.__annotations__
    assert Status("halted") is Status.HALTED


def test_graph_compiles(tmp_path):
    saver = build_checkpointer(tmp_path / "c.sqlite")
    compiled = compile_graph(saver)
    node_ids = set(compiled.get_graph().nodes)
    assert EXPECTED_NODES <= node_ids
    saver.conn.close()


def _with_test_results(passed: bool) -> BlacksmithState:
    return {"test_results": {"passed": passed, "output": "", "command": "cargo test"}}


def test_route_after_test_gate():
    assert route_after_test_gate(_with_test_results(True)) == "approve_pr"
    assert route_after_test_gate(_with_test_results(False)) == "human_halt"
    assert route_after_test_gate({}) == "human_halt"  # no results yet -> halt, never auto-proceed


def test_route_after_implement_uses_layer_gate():
    prd = parse_prd(VENDORED_PRD)
    auto_unit = prd.contract.work_unit_by_id("WU-01")  # py-logic -> auto
    human_unit = prd.contract.work_unit_by_id("WU-06")  # py-logic + integration -> human
    assert route_after_implement({"prd": prd, "selected_unit": auto_unit}) == "test_gate"
    assert route_after_implement({"prd": prd, "selected_unit": human_unit}) == "human_halt"
    assert route_after_implement({}) == "test_gate"  # nothing selected -> default auto path


def test_checkpointer_persists_and_resumes_across_restart(tmp_path):
    db = tmp_path / "ckpt.sqlite"
    cfg = {"configurable": {"thread_id": "wu03"}}

    # --- first "process": run until the approve_plan HITL gate, then exit ---
    saver1 = build_checkpointer(db)
    g1 = compile_graph(saver1)
    g1.invoke({"status": Status.PENDING}, cfg)  # halts at the approve_plan interrupt
    paused = g1.get_state(cfg)
    assert paused.next == ("approve_plan",)
    assert paused.values["status"] == Status.AWAITING_PLAN_APPROVAL
    saver1.conn.close()  # simulate process exit

    # --- second "process": fresh checkpointer on the same file, resume ---
    saver2 = build_checkpointer(db)
    g2 = compile_graph(saver2)
    resumed = g2.get_state(cfg)
    assert resumed.next == ("approve_plan",)  # the pause point survived the restart
    assert resumed.values["status"] == Status.AWAITING_PLAN_APPROVAL  # so did the state

    g2.invoke(Command(resume=True), cfg)  # inject approval; resume to completion
    final = g2.get_state(cfg)
    assert final.next == ()  # reached END
    assert final.values["approvals"]["plan"] is True  # the injected approval was recorded
    saver2.conn.close()


def test_build_graph_is_uncompiled():
    # Topology object can be produced independently of any checkpointer.
    assert build_graph() is not None


def test_serde_round_trips_rich_state_without_warning(caplog):
    prd = parse_prd(VENDORED_PRD)
    state = {
        "prd": prd,
        "selected_unit": prd.contract.work_units[0],
        "work_units": list(prd.contract.work_units),
        "status": Status.AWAITING_PLAN_APPROVAL,
    }
    serde = blacksmith_serde()
    with caplog.at_level(logging.WARNING, logger=SERDE_LOGGER):
        restored = serde.loads_typed(serde.dumps_typed(state))

    assert restored["status"] == Status.AWAITING_PLAN_APPROVAL
    assert restored["selected_unit"].id == "WU-01"
    assert restored["prd"].contract.component == "blacksmith"
    assert _serde_warnings(caplog) == []


def test_checkpointer_persists_rich_state_across_restart(tmp_path, caplog):
    prd = parse_prd(VENDORED_PRD)
    db = tmp_path / "rich.sqlite"
    cfg = {"configurable": {"thread_id": "rich"}}
    # plan no-ops without an executor, so the seeded rich objects are what gets persisted.
    seed = {
        "status": Status.PENDING,
        "prd": prd,
        "selected_unit": prd.contract.work_unit_by_id("WU-06"),
    }

    with caplog.at_level(logging.WARNING, logger=SERDE_LOGGER):
        saver1 = build_checkpointer(db)
        compile_graph(saver1).invoke(seed, cfg)  # pauses at approve_plan
        saver1.conn.close()  # simulate restart

        saver2 = build_checkpointer(db)
        restored = compile_graph(saver2).get_state(cfg)
        saver2.conn.close()

    assert restored.values["prd"].contract.component == "blacksmith"
    assert restored.values["selected_unit"].id == "WU-06"  # rich state survived the restart
    assert _serde_warnings(caplog) == []
