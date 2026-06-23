"""WU-RESUME: ``blacksmith resume --thread-id X`` continues an interrupted run.

These tests drive a fake-but-real compiled graph (a tiny ``StateGraph`` with a real
``interrupt()`` gate, compiled with a real temp ``SqliteSaver``) through the real
``cli.drive`` / ``cli.resume`` loop. The fake nodes count their own invocations into a
shared recorder, so a graph *rebuilt on the same DB file* (a simulated process restart)
still tracks total calls across the run's lifecycle. That lets us prove resume
re-attaches to the persisted checkpoint and drives to END WITHOUT re-running the
already-completed ingest/plan nodes — it must not re-spend on a unit already past a
gate. Separate cases cover an unknown thread-id exiting non-zero with a clear message
and confirm the fresh ``blacksmith <prd>`` run path is unchanged.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from blacksmith.cli import ResumeError, drive, main, resume
from blacksmith.graph import build_checkpointer


class FakeState(TypedDict, total=False):
    prd_path: str
    log: Annotated[list[str], operator.add]
    approved: bool


def _build_graph(saver, calls, order):
    """A 4-node graph (ingest -> plan -> gate[interrupt] -> finish) on a real saver.

    ``calls`` / ``order`` are shared mutable recorders held outside the graph, so a graph
    rebuilt on the same DB file (a simulated restart) keeps accumulating into them — that
    is how we observe which nodes a *resume* re-runs versus replays from the checkpoint.
    """

    def ingest(state: FakeState) -> dict:
        calls["ingest"] += 1
        order.append("ingest")
        return {"log": ["ingest"]}

    def plan(state: FakeState) -> dict:
        calls["plan"] += 1
        order.append("plan")
        return {"log": ["plan"]}

    def gate(state: FakeState) -> dict:
        # Halts here until a decision is injected; on resume the node re-runs from the
        # top, interrupt() returns the injected value, and the lines below execute.
        decision = interrupt({"gate": "plan"})
        calls["gate"] += 1
        order.append("gate")
        return {"approved": bool(decision), "log": ["gate"]}

    def finish(state: FakeState) -> dict:
        calls["finish"] += 1
        order.append("finish")
        return {"log": ["finish"]}

    g = StateGraph(FakeState)
    g.add_node("ingest", ingest)
    g.add_node("plan", plan)
    g.add_node("gate", gate)
    g.add_node("finish", finish)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "plan")
    g.add_edge("plan", "gate")
    g.add_edge("gate", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=saver)


def _approver(decision=True):
    seen: list[object] = []

    def approve(payload, values):
        seen.append(payload.get("gate") if isinstance(payload, dict) else None)
        return decision

    approve.seen = seen
    return approve


def _fresh_calls():
    return {"ingest": 0, "plan": 0, "gate": 0, "finish": 0}


def test_resume_continues_to_end_without_rerunning_planned_nodes(tmp_path):
    db = tmp_path / "ckpt.sqlite"
    calls = _fresh_calls()
    order: list[str] = []
    config = {"configurable": {"thread_id": "wu"}}

    # --- first "process": run until the gate interrupt, then exit ---
    saver1 = build_checkpointer(db)
    g1 = _build_graph(saver1, calls, order)
    g1.invoke({"prd_path": "x"}, config)  # halts at the gate interrupt
    paused = g1.get_state(config)
    assert paused.next == ("gate",)  # paused at the gate, state persisted under "wu"
    assert calls["ingest"] == 1 and calls["plan"] == 1  # ingest + plan already done
    assert calls["gate"] == 0 and calls["finish"] == 0  # not yet past the gate
    saver1.conn.close()  # simulate process exit

    # --- second "process": fresh saver on the SAME DB, resume by thread-id ---
    saver2 = build_checkpointer(db)
    g2 = _build_graph(saver2, calls, order)
    approver = _approver(decision=True)
    final = resume(g2, "wu", approver=approver)
    saver2.conn.close()

    assert final.next == ()  # re-attached and drove to END
    assert final.values["approved"] is True  # the injected approval was applied
    # The already-planned nodes were NOT re-invoked on resume (replayed from disk).
    assert calls["ingest"] == 1
    assert calls["plan"] == 1
    # The gate resolved and finish ran, post-resume.
    assert calls["gate"] == 1
    assert calls["finish"] == 1
    assert order == ["ingest", "plan", "gate", "finish"]  # continuous node order
    assert approver.seen == ["plan"]  # consulted exactly the one pending gate


def test_resume_unknown_thread_id_raises_clear_error(tmp_path):
    saver = build_checkpointer(tmp_path / "empty.sqlite")
    g = _build_graph(saver, _fresh_calls(), [])
    with pytest.raises(ResumeError) as excinfo:
        resume(g, "ghost", approver=_approver())
    saver.conn.close()

    message = str(excinfo.value)
    assert "ghost" in message
    assert "no checkpoint" in message.lower()


def test_resume_cli_unknown_thread_id_exits_nonzero_with_message(tmp_path, capsys):
    cfg = tmp_path / "blacksmith.config.toml"
    db = tmp_path / "cli-ckpt.sqlite"
    cfg.write_text(
        f"[target]\nrepo_path = {str(tmp_path)!r}\n"
        f"[checkpointer]\ndb_path = {str(db)!r}\n"
    )

    code = main(["resume", "--thread-id", "ghost", "--config", str(cfg)])
    captured = capsys.readouterr()

    assert code != 0  # unknown thread-id is a non-zero exit
    combined = captured.out + captured.err
    assert "resume" in combined  # the message names the failed action
    assert "ghost" in combined  # ...and the offending thread-id


def test_fresh_run_path_is_unchanged(tmp_path):
    """A fresh ``drive`` still drives the whole graph in one pass (no resume seam)."""
    calls = _fresh_calls()
    order: list[str] = []
    saver = build_checkpointer(tmp_path / "fresh.sqlite")
    g = _build_graph(saver, calls, order)
    approver = _approver(decision=True)

    final = drive(g, "x", approver=approver, thread_id="fresh")
    saver.conn.close()

    assert final.next == ()
    assert calls == {"ingest": 1, "plan": 1, "gate": 1, "finish": 1}
    assert order == ["ingest", "plan", "gate", "finish"]
    assert approver.seen == ["plan"]
