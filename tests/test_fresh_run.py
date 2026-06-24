"""WU-FRESH-RUN-GUARD: a fresh ``blacksmith <prd>`` run must not inherit a prior run's
state on the same thread-id.

These tests drive a fake-but-real compiled graph (a tiny ``StateGraph`` with a real
``interrupt()`` gate, compiled with a real temp ``SqliteSaver``) through the real
``cli.drive`` loop. The graph mirrors the production state shape that matters here: an
``errors`` channel with the same ``operator.add`` accumulator as ``BlacksmithState`` and
a last-write-wins ``pr_url``. The ``seed`` node stamps a stale error + pr_url ONLY when
driven with ``prd_path == "stale"``, so a clean second run produces neither itself — any
error/pr_url it ends with could only have been *carried* from the prior run's checkpoint.

That lets us prove the guard:
1. a terminal thread (prior run reached END) is reset to a clean slate before the fresh
   run, so the second run's outcome carries neither the stale ``errors`` nor ``pr_url``;
2. a thread paused at a gate (a pending interrupt) is left untouched — the fresh run
   refuses and points at ``blacksmith resume --thread-id X`` instead of clobbering it;
3. an unused thread-id proceeds normally;
4. ``resume`` on an existing thread is unaffected.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from blacksmith.cli import FreshRunError, drive, main, resume
from blacksmith.graph import build_checkpointer
from blacksmith.state import Status

STALE_PR = "https://github.com/smith-and-web/old/pull/1"


class FakeState(TypedDict, total=False):
    prd_path: str
    errors: Annotated[list, operator.add]  # same accumulator as BlacksmithState
    pr_url: str | None
    status: Status
    approved: bool


def _build_graph(saver):
    """A 3-node graph (seed -> gate[interrupt] -> finish) on a real saver.

    ``seed`` stamps a stale error + pr_url only for ``prd_path == "stale"``, so a fresh
    clean run produces neither — proving any it ends with were carried from the prior
    checkpoint. ``finish`` lands the run at the terminal ``DONE`` status.
    """

    def seed(state: FakeState) -> dict:
        if state.get("prd_path") == "stale":
            return {
                "errors": [{"node": "seed", "message": "stale"}],
                "pr_url": STALE_PR,
            }
        return {}

    def gate(state: FakeState) -> dict:
        decision = interrupt({"gate": "plan"})
        return {"approved": bool(decision)}

    def finish(state: FakeState) -> dict:
        return {"status": Status.DONE}

    g = StateGraph(FakeState)
    g.add_node("seed", seed)
    g.add_node("gate", gate)
    g.add_node("finish", finish)
    g.add_edge(START, "seed")
    g.add_edge("seed", "gate")
    g.add_edge("gate", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=saver)


def _approver(decision=True):
    def approve(payload, values):
        return decision

    return approve


def test_fresh_run_on_terminal_thread_starts_clean(tmp_path):
    """A prior run that reached a terminal state leaves no errors/pr_url to the next."""
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    g = _build_graph(saver)

    # First run on thread T: drive to DONE carrying a stale error + pr_url.
    first = drive(g, "stale", approver=_approver(True), thread_id="T")
    assert first.next == ()  # terminal
    assert first.values["status"] == Status.DONE
    assert first.values["errors"] == [{"node": "seed", "message": "stale"}]
    assert first.values["pr_url"] == STALE_PR

    # Second run on the SAME thread T: must start from a clean slate.
    second = drive(g, "clean", approver=_approver(True), thread_id="T")
    saver.conn.close()

    assert second.next == ()
    assert second.values["status"] == Status.DONE
    assert second.values.get("errors", []) == []  # did NOT carry the prior run's errors
    assert second.values.get("pr_url") is None  # ...nor its pr_url


def test_fresh_run_on_paused_thread_refuses_and_points_to_resume(tmp_path):
    """A thread paused at a gate is left intact; the fresh run refuses with guidance."""
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    g = _build_graph(saver)
    config = {"configurable": {"thread_id": "P"}}

    # Leave a run paused at the gate interrupt (non-terminal, pending interrupt).
    g.invoke({"prd_path": "x"}, config)
    assert g.get_state(config).next == ("gate",)

    with pytest.raises(FreshRunError) as excinfo:
        drive(g, "y", approver=_approver(True), thread_id="P")

    message = str(excinfo.value)
    assert "P" in message  # names the offending thread-id
    assert "resume --thread-id P" in message  # points the human at resume

    # The paused run was NOT clobbered — its checkpoint still sits at the gate.
    assert g.get_state(config).next == ("gate",)
    saver.conn.close()


def test_fresh_run_on_unused_thread_proceeds_normally(tmp_path):
    """An unused thread-id is untouched by the guard — the run drives straight through."""
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    g = _build_graph(saver)

    final = drive(g, "clean", approver=_approver(True), thread_id="unused")
    saver.conn.close()

    assert final.next == ()
    assert final.values["status"] == Status.DONE
    assert final.values.get("errors", []) == []
    assert final.values["approved"] is True


def test_resume_is_unaffected_by_the_guard(tmp_path):
    """The guard touches only the fresh-run path: resume still continues a paused run."""
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    g = _build_graph(saver)
    config = {"configurable": {"thread_id": "R"}}

    g.invoke({"prd_path": "stale"}, config)  # pause at the gate
    assert g.get_state(config).next == ("gate",)

    final = resume(g, "R", approver=_approver(True))
    saver.conn.close()

    assert final.next == ()  # resume drove the existing run to END
    assert final.values["status"] == Status.DONE
    assert final.values["approved"] is True
    # Resume continued the SAME run, so its own stale error/pr_url are preserved.
    assert final.values["pr_url"] == STALE_PR


def test_main_translates_fresh_run_refusal_to_nonzero_exit(tmp_path, monkeypatch, capsys):
    """``main`` surfaces a paused-thread refusal as a non-zero exit + a clear message."""
    import blacksmith.cli as cli

    cfg = tmp_path / "blacksmith.config.toml"
    db = tmp_path / "cli-ckpt.sqlite"
    cfg.write_text(
        f"[target]\nrepo_path = {str(tmp_path)!r}\n"
        f"[checkpointer]\ndb_path = {str(db)!r}\n"
    )

    def _raise(*args, **kwargs):
        raise FreshRunError(
            "thread-id 'P' has a run paused at a gate; starting a fresh run would "
            "clobber it. Continue it with `blacksmith resume --thread-id P`."
        )

    monkeypatch.setattr(cli, "build_graph_for", lambda *a, **k: None)
    monkeypatch.setattr(cli, "drive", _raise)

    # A non-PRD path parses to a ContractError, so the repo-consistency preflight is
    # skipped and the run reaches drive (where the guard refuses).
    not_a_prd = tmp_path / "missing.md"
    code = main([str(not_a_prd), "--config", str(cfg), "--thread-id", "P", "--auto-approve"])
    captured = capsys.readouterr()

    assert code != 0
    combined = captured.out + captured.err
    assert "resume --thread-id P" in combined
