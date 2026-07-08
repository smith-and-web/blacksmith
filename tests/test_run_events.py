"""Durable, thread-keyed run-event stream to an additive live sink (WU-RUN-EVENTS).

A purely ADDITIVE OBSERVATION channel, mirroring the metrics sink: the drive loop emits a
structured event at each node boundary (node_start / node_end, node_end carrying the
node's duration) and, at run end, one summary event per unit (unit_result) plus a final
run_status event — all derived from the EXISTING graph reducers (cost_events /
unit_results), with no new graph state. Events are written append-only to a SEPARATE live
SQLite DB, keyed by thread_id with a per-thread monotonic seq, so a fleet of runs shares
one sink while each thread keeps its own ordered stream.

Like metrics it is best-effort: with ``[live] enabled=false`` or on any sink write error
the run is byte-for-byte unaffected. The run cases are driven through the real CLI
``drive`` loop and the real graph (executor and ``gh`` mocked, REAL worktree manager),
exactly like the metrics suite, then the recorded events are read back and asserted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blacksmith.cli import _live_emitter, _safe_event_emitter, drive
from blacksmith.config import BlacksmithConfig, LiveConfig
from blacksmith.events import (
    NODE_END,
    NODE_START,
    RUN_STATUS,
    UNIT_RESULT,
    LiveSink,
    build_live_store,
    read_events,
)
from blacksmith.executor import ExecutorResult
from blacksmith.gate import run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

PRD = """\
---
contract_version: 1
component: demo
version: v0
primary_target_repo: owner/demo
layers:
  py-logic: auto
untouchables:
  - "do not touch the brand files"
work_units:
  - id: WU-E1
    title: "trivial unit"
    layers: [py-logic]
    target_modules: ["out.txt"]
    test_contract: "the gate command passes"
    depends_on: []
---
# Demo PRD

## 1. Purpose
demo.

## 2. Scope fences
demo.

## 7. Untouchables
none.

## 10. Acceptance criteria
done.
"""


class FakeExecutor:
    def run_plan(self, prompt, **kwargs):
        return _result("1. write out.txt")

    def run_implement(self, prompt, **kwargs):
        Path(kwargs["cwd"], "out.txt").write_text("implemented\n")
        return _result("done")


def _result(text):
    return ExecutorResult(
        text=text, model="claude-opus-4-8", is_error=False, num_turns=1,
        cost_usd=0.01, usage={}, session_id="s",
    )


def _fake_gh(url):
    def run(argv, cwd=None):
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")

    return run


def _approver(decision=True):
    def approve(payload, values):
        return decision

    return approve


def _target_repo(tmp_path: Path, name: str = "target") -> Path:
    """A fresh git repo with a passing gate command (each run gets its own repo, so two
    fleet runs never collide on the shared per-unit branch name)."""
    repo = tmp_path / name
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    (repo / "blacksmith.toml").write_text('test_cmd = "true"\n')
    g("add", "-A")
    g("commit", "-m", "init")
    return repo


def _wire(tmp_path: Path, repo: Path, *, wt_name: str = "wt", ckpt: str = "ckpt.sqlite"):
    saver = build_checkpointer(tmp_path / ckpt)
    graph = compile_graph(
        saver,
        executor=FakeExecutor(),
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / wt_name),
        gate=run_gate,
        pr_runner=_fake_gh("https://github.com/owner/demo/pull/1"),
    )
    return graph, saver


def _write_prd(tmp_path: Path, name: str = "prd.md") -> Path:
    path = tmp_path / name
    path.write_text(PRD)
    return path


# --- config ------------------------------------------------------------------


def test_live_config_defaults_to_its_own_enabled_file():
    cfg = BlacksmithConfig()
    assert cfg.live == LiveConfig()
    assert cfg.live.enabled is True
    assert cfg.live.db_path == Path(".blacksmith/live.sqlite")
    # Its own file — never shared with the checkpointer / Store / metrics sinks.
    assert cfg.live.db_path != cfg.checkpointer.db_path
    assert cfg.live.db_path != cfg.store.db_path
    assert cfg.live.db_path != cfg.metrics.db_path


# --- (a) happy run: node_start/node_end in order, monotonic seq --------------


def test_happy_run_appends_node_events_in_order_with_monotonic_seq(tmp_path):
    repo = _target_repo(tmp_path)
    graph, saver = _wire(tmp_path, repo)
    store = build_live_store(tmp_path / "live.sqlite")
    sink = LiveSink(store)

    final = drive(
        graph, _write_prd(tmp_path), approver=_approver(True), thread_id="t",
        on_event=_safe_event_emitter(sink, "t"),
    )
    saver.conn.close()
    assert final.values["status"] == Status.DONE

    events = read_events(store, "t")
    # Per-thread seq is monotonic and contiguous from 0 (append-only stream).
    assert [e.seq for e in events] == list(range(len(events)))

    kinds = [e.kind for e in events]
    assert NODE_START in kinds and NODE_END in kinds

    # The first worker node starts before it ends, and node_end carries a duration.
    start_idx = next(
        i for i, e in enumerate(events)
        if e.kind == NODE_START and e.payload.get("node") == "ingest_prd"
    )
    end_idx = next(
        i for i, e in enumerate(events)
        if e.kind == NODE_END and e.payload.get("node") == "ingest_prd"
    )
    assert start_idx < end_idx
    assert all("duration" in e.payload for e in events if e.kind == NODE_END)

    # End-of-unit + end-of-run summaries derived from the existing reducers, at the tail.
    unit_results = [e for e in events if e.kind == UNIT_RESULT]
    assert [e.payload["unit_id"] for e in unit_results] == ["WU-E1"]
    assert unit_results[0].payload["gate_result"] == "passed"
    assert events[-1].kind == RUN_STATUS
    assert events[-1].payload["status"] == "done"
    assert events[-1].payload["pr_url"].endswith("/pull/1")
    store.close()


# --- (b) two thread_ids write independently to the same sink (fleet) ---------


def test_two_thread_ids_write_independently_to_the_same_sink(tmp_path):
    # A shared live sink, but two INDEPENDENT runs on their OWN repos/checkpointers — a
    # fleet. Separate repos are essential: a successful PR run KEEPS its per-unit branch,
    # so reusing one repo would collide on `blacksmith/wu-e1` in the second run.
    store = build_live_store(tmp_path / "live.sqlite")
    sink = LiveSink(store)

    repo1 = _target_repo(tmp_path, "target1")
    graph1, saver1 = _wire(tmp_path, repo1, wt_name="wt1", ckpt="ckpt1.sqlite")
    final1 = drive(
        graph1, _write_prd(tmp_path, "prd1.md"), approver=_approver(True),
        thread_id="alpha", on_event=_safe_event_emitter(sink, "alpha"),
    )
    saver1.conn.close()

    repo2 = _target_repo(tmp_path, "target2")
    graph2, saver2 = _wire(tmp_path, repo2, wt_name="wt2", ckpt="ckpt2.sqlite")
    final2 = drive(
        graph2, _write_prd(tmp_path, "prd2.md"), approver=_approver(True),
        thread_id="beta", on_event=_safe_event_emitter(sink, "beta"),
    )
    saver2.conn.close()

    assert final1.values["status"] == Status.DONE
    assert final2.values["status"] == Status.DONE

    alpha = read_events(store, "alpha")
    beta = read_events(store, "beta")

    # Each thread keeps its OWN contiguous, monotonic seq starting at 0.
    assert [e.seq for e in alpha] == list(range(len(alpha)))
    assert [e.seq for e in beta] == list(range(len(beta)))
    assert alpha and beta

    # The streams are independent: every row is tagged with only its own thread_id.
    assert all(e.thread_id == "alpha" for e in alpha)
    assert all(e.thread_id == "beta" for e in beta)
    for stream in (alpha, beta):
        assert any(e.kind == NODE_START for e in stream)
        assert any(e.kind == NODE_END for e in stream)
        assert stream[-1].kind == RUN_STATUS
    store.close()


# --- (c) enabled=false emits nothing and the run still ends DONE -------------


def test_disabled_sink_emits_nothing_and_run_still_done(tmp_path):
    repo = _target_repo(tmp_path)
    graph, saver = _wire(tmp_path, repo)
    db_path = tmp_path / "live.sqlite"
    cfg = BlacksmithConfig(live=LiveConfig(enabled=False, db_path=db_path))

    emitter = _live_emitter(cfg, "t")
    assert emitter is None  # disabled -> no emitter, so drive emits nothing

    final = drive(
        graph, _write_prd(tmp_path), approver=_approver(True), thread_id="t",
        on_event=emitter,
    )
    saver.conn.close()

    assert final.values["status"] == Status.DONE  # run unaffected
    assert not db_path.exists()  # nothing written — the sink DB was never even created


# --- (d) a sink write raising is swallowed and does not halt the run ---------


class _BoomSink:
    """A live sink whose every write raises — models a sink error mid-run."""

    def emit(self, *args, **kwargs):
        raise RuntimeError("live sink is on fire")


def test_sink_write_error_is_swallowed_and_run_still_done(tmp_path):
    repo = _target_repo(tmp_path)
    graph, saver = _wire(tmp_path, repo)

    # The real best-effort wrapper over a sink that always raises: emitting must NOT
    # propagate the error into the drive loop.
    emitter = _safe_event_emitter(_BoomSink(), "t")

    final = drive(
        graph, _write_prd(tmp_path), approver=_approver(True), thread_id="t",
        on_event=emitter,
    )
    saver.conn.close()

    assert final.values["status"] == Status.DONE  # byte-for-byte unaffected outcome
