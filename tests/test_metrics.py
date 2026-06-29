"""Local metrics SQLite sink (WU-METRICS-RECORD).

A purely additive, write-only OUTPUT channel: at the end of a run the CLI sink derives a
run row (one per thread, UPSERT) plus N unit rows from the FINAL graph state's
``cost_events`` / ``unit_results`` / ``status`` / ``errors`` / ``pr_url`` and records them
to a SEPARATE local SQLite file. It is never read back into the graph, so a run reaches the
same terminal state with or without it, and a metrics write that fails never fails the run.

The run cases are driven through the real CLI ``drive`` loop and the real graph (executor
and ``gh`` mocked, REAL worktree manager), exactly like the multi-unit suite, then the
final snapshot is recorded and the DB rows asserted.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from blacksmith.cli import _record_metrics, drive
from blacksmith.config import BlacksmithConfig, MetricsConfig
from blacksmith.executor import ExecutorResult
from blacksmith.gate import GateResult, run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.metrics import build_metrics_store, record_run
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

_PRD_TEMPLATE = """\
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
{units}
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

_TWO_UNITS = """\
  - id: WU-A
    title: "first unit"
    layers: [py-logic]
    target_modules: ["wu-a.txt"]
    test_contract: "the gate command passes"
    depends_on: []
  - id: WU-B
    title: "second unit"
    layers: [py-logic]
    target_modules: ["wu-b.txt"]
    test_contract: "the gate command passes"
    depends_on: [WU-A]"""

# Per-unit cost / turns so unit rows are individually verifiable.
_COSTS = {"WU-A": (0.30, 7), "WU-B": (0.50, 9)}
_PLAN_COST = 0.05
USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_input_tokens": 300,
    "cache_creation_input_tokens": 0,
}


def _result(cost, *, num_turns=1, model="claude-sonnet-4-6", text="done"):
    return ExecutorResult(
        text=text, model=model, is_error=False, num_turns=num_turns,
        cost_usd=cost, usage=USAGE, session_id="s",
    )


class FakeExecutor:
    """Writes each unit's file (committed by the real implement node) and reports a
    per-unit cost/turns so the recorded unit rows are individually checkable."""

    def run_plan(self, prompt, **kwargs):
        return _result(_PLAN_COST)

    def run_implement(self, prompt, **kwargs):
        cwd = Path(kwargs["cwd"])
        unit_id = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
        (cwd / f"{unit_id.lower()}.txt").write_text(f"impl {unit_id}\n")
        cost, turns = _COSTS[unit_id]
        return _result(cost, num_turns=turns)


class FakeGate:
    """Passes every unit except the ``fail_on``-th call (1-indexed)."""

    def __init__(self, fail_on: int | None = None):
        self.fail_on = fail_on
        self.calls = 0

    def __call__(self, worktree_path, layer):
        self.calls += 1
        passed = self.calls != self.fail_on
        return GateResult(passed=passed, output="ok" if passed else "boom", command="pytest")


def _recording_gh(url):
    def run(argv, cwd=None):
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")

    return run


def _approver(decision=True):
    def approve(payload, values):
        return decision

    return approve


def _target_repo(tmp_path):
    repo = tmp_path / "target"
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


def _wire(tmp_path, repo, *, executor, gh, gate=run_gate):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver, executor=executor,
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=gate, pr_runner=gh,
    )
    return graph, saver


def _write_prd(tmp_path, units):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(units=units))
    return path


def _run_to(tmp_path, units, *, gate=run_gate):
    """Drive a real run to its terminal state and return the final snapshot + prd path."""
    repo = _target_repo(tmp_path)
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    graph, saver = _wire(tmp_path, repo, executor=FakeExecutor(), gh=gh, gate=gate)
    prd = _write_prd(tmp_path, units)
    final = drive(graph, prd, approver=_approver(True), thread_id="wu")
    saver.conn.close()
    return final, prd


def _run_rows(store):
    cur = store.execute("SELECT * FROM run_metrics")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _unit_rows(store):
    cur = store.execute("SELECT * FROM unit_metrics ORDER BY unit_id")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


# --- schema migration -------------------------------------------------------


def test_build_store_migrates_old_schema_db(tmp_path):
    # A metrics DB created BEFORE the `transcripts` column existed must be forward-migrated
    # on open — otherwise record_run raises "no such column" and the best-effort sink drops
    # every row SILENTLY (the exact bug found reviewing the transcript-capture PR).
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(str(db))  # simulate a pre-transcripts run_metrics table
    conn.execute(
        "CREATE TABLE run_metrics (thread_id TEXT PRIMARY KEY, status TEXT, total_cost REAL)"
    )
    conn.commit()
    conn.close()

    store = build_metrics_store(db)  # opens + forward-migrates
    cols = {r[1] for r in store.execute("PRAGMA table_info(run_metrics)")}
    assert "transcripts" in cols  # the column that was missing
    assert "duration_s" in cols  # ...and every other absent column is added too

    # record_run now succeeds instead of raising OperationalError on the missing column.
    snap = SimpleNamespace(
        values={"status": Status.DONE, "cost_events": [], "errors": [], "unit_results": []}
    )
    record_run(store, snap, thread_id="t1", prd_path="x.md", started_at=0.0, ended_at=1.0)
    rows = _run_rows(store)
    assert [r["thread_id"] for r in rows] == ["t1"]
    store.close()


def test_build_store_is_idempotent_on_current_schema(tmp_path):
    # Re-opening an already-current DB adds nothing and still records fine (no double-ALTER).
    db = tmp_path / "cur.sqlite"
    build_metrics_store(db).close()
    store = build_metrics_store(db)
    snap = SimpleNamespace(
        values={"status": Status.DONE, "cost_events": [], "errors": [], "unit_results": []}
    )
    record_run(store, snap, thread_id="t1", prd_path="x.md", started_at=0.0, ended_at=1.0)
    assert len(_run_rows(store)) == 1
    store.close()


# --- config ------------------------------------------------------------------


def test_metrics_config_defaults_to_its_own_file():
    cfg = BlacksmithConfig()
    assert cfg.metrics == MetricsConfig()
    assert cfg.metrics.db_path == Path(".blacksmith/metrics.sqlite")
    # Its own file — never shared with the checkpointer or the long-term Store.
    assert cfg.metrics.db_path != cfg.checkpointer.db_path
    assert cfg.metrics.db_path != cfg.store.db_path


# --- DONE run records one run row + per-unit rows ----------------------------


def test_done_run_records_run_row_and_two_unit_rows(tmp_path):
    final, prd = _run_to(tmp_path, _TWO_UNITS)
    assert final.values["status"] == Status.DONE

    store = build_metrics_store(tmp_path / "metrics.sqlite")
    record_run(store, final, thread_id="wu", prd_path=prd, started_at=100.0, ended_at=142.5)

    runs = _run_rows(store)
    assert len(runs) == 1
    run = runs[0]
    # total_cost equals the summed cost_events: one plan call PER auto unit now
    # (WU-PLAN-ALL-UNITS), plus both implements.
    summed = sum(e["cost_usd"] for e in final.values["cost_events"])
    assert run["total_cost"] == summed == 2 * _PLAN_COST + 0.30 + 0.50
    assert run["status"] == "done"
    assert run["success"] == 1
    assert run["failure_reason"] == "none"
    assert run["duration_s"] == 42.5
    assert run["units_count"] == 2
    assert run["pr_url"].endswith("/pull/7")
    assert run["repo"] == "owner/demo"
    assert run["prd"] == str(prd)

    units = _unit_rows(store)
    assert [u["unit_id"] for u in units] == ["WU-A", "WU-B"]
    by_id = {u["unit_id"]: u for u in units}
    assert by_id["WU-A"]["cost"] == 0.30 and by_id["WU-A"]["turns"] == 7
    assert by_id["WU-B"]["cost"] == 0.50 and by_id["WU-B"]["turns"] == 9
    assert by_id["WU-A"]["gate_result"] == "passed"
    assert by_id["WU-A"]["files_count"] == 1
    store.close()


# --- HALTED run records status + failure_reason ------------------------------


def test_halted_run_records_status_and_failure_reason(tmp_path):
    # WU-A passes, WU-B's gate fails -> the run halts.
    final, prd = _run_to(tmp_path, _TWO_UNITS, gate=FakeGate(fail_on=2))
    assert final.values["status"] == Status.HALTED

    store = build_metrics_store(tmp_path / "metrics.sqlite")
    record_run(store, final, thread_id="wu", prd_path=prd, started_at=0.0, ended_at=10.0)

    run = _run_rows(store)[0]
    assert run["status"] == "halted"
    assert run["success"] == 0
    assert run["failure_reason"] == "gate_fail"
    assert run["pr_url"] is None
    store.close()


# --- a metrics-store error never changes the run's outcome -------------------


def test_metrics_store_error_leaves_run_outcome_unchanged(tmp_path):
    final, prd = _run_to(tmp_path, _TWO_UNITS)
    assert final.values["status"] == Status.DONE

    # A config whose metrics db_path can never be opened (a path under a regular FILE),
    # so build_metrics_store raises inside the best-effort sink.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x\n")
    cfg = BlacksmithConfig(metrics=MetricsConfig(db_path=blocker / "nested" / "metrics.sqlite"))

    # Must NOT raise — the failure is swallowed, leaving the run's outcome untouched.
    _record_metrics(cfg, final, thread_id="wu", prd_path=prd, started_at=0.0, ended_at=1.0)
    assert final.values["status"] == Status.DONE  # outcome unchanged


# --- a resumed thread UPSERTs the same row -----------------------------------


def test_resumed_thread_upserts_the_same_row(tmp_path):
    final, prd = _run_to(tmp_path, _TWO_UNITS)
    store = build_metrics_store(tmp_path / "metrics.sqlite")

    # First recording (the original run), then a second recording for the SAME thread-id
    # (as a resume of that thread would do) UPSERTs in place rather than duplicating.
    record_run(store, final, thread_id="wu", prd_path=prd, started_at=0.0, ended_at=5.0)
    record_run(store, final, thread_id="wu", prd_path=prd, started_at=0.0, ended_at=9.0)

    runs = _run_rows(store)
    assert len(runs) == 1  # one run row keyed by thread_id, updated in place
    assert runs[0]["duration_s"] == 9.0  # the latest recording won
    assert len(_unit_rows(store)) == 2  # unit rows replaced, not duplicated
    store.close()


# --- per-unit gate_result distinguishes escalated-passed from escalated-failed ----------


def _implement_event(unit_id, *, model, cost, turns):
    return {
        "node": "implement", "unit_id": unit_id, "model": model,
        "cost_usd": cost, "usage": USAGE, "num_turns": turns,
    }


def test_escalated_then_halted_unit_is_not_labelled_escalated(tmp_path):
    # WU-X escalated (TWO implement attempts) but never passed -> absent from unit_results.
    # Its row must read as failing, NOT a plain `passed`/`escalated` success.
    snap = SimpleNamespace(values={
        "status": Status.HALTED,
        "errors": [],
        "cost_events": [
            _implement_event("WU-X", model="m1", cost=0.10, turns=3),
            _implement_event("WU-X", model="m2", cost=0.20, turns=4),
        ],
        "unit_results": [],  # WU-X never passed (the gate halted the run)
    })
    store = build_metrics_store(tmp_path / "halt.sqlite")
    record_run(store, snap, thread_id="t", prd_path="x.md", started_at=0.0, ended_at=1.0)
    row = _unit_rows(store)[0]
    assert row["gate_result"] == "failed"
    assert row["gate_result"] not in ("escalated", "passed")
    store.close()


def test_escalated_then_passed_unit_is_labelled_escalated(tmp_path):
    # WU-X escalated (TWO attempts) and then passed (present in unit_results) -> `escalated`.
    snap = SimpleNamespace(values={
        "status": Status.DONE,
        "errors": [],
        "cost_events": [
            _implement_event("WU-X", model="m1", cost=0.10, turns=3),
            _implement_event("WU-X", model="m2", cost=0.20, turns=4),
        ],
        "unit_results": [{
            "unit_id": "WU-X", "title": "x", "files_touched": ["a"],
            "diff_summary": "1 file changed, 2 insertions(+)",
        }],
    })
    store = build_metrics_store(tmp_path / "esc.sqlite")
    record_run(store, snap, thread_id="t", prd_path="x.md", started_at=0.0, ended_at=1.0)
    row = _unit_rows(store)[0]
    assert row["gate_result"] == "escalated"
    store.close()


# --- diff_size is the parsed insertions+deletions, not the stat string length ------------


def test_diff_size_is_parsed_insertions_plus_deletions(tmp_path):
    diff = (
        " blacksmith/render.py | 90 ++++++\n"
        " blacksmith/cli.py | 12 +-\n"
        " 2 files changed, 95 insertions(+), 7 deletions(-)"
    )
    snap = SimpleNamespace(values={
        "status": Status.DONE,
        "errors": [],
        "cost_events": [_implement_event("WU-X", model="m", cost=0.10, turns=1)],
        "unit_results": [{
            "unit_id": "WU-X", "title": "x", "files_touched": ["a", "b"],
            "diff_summary": diff,
        }],
    })
    store = build_metrics_store(tmp_path / "diff.sqlite")
    record_run(store, snap, thread_id="t", prd_path="x.md", started_at=0.0, ended_at=1.0)
    row = _unit_rows(store)[0]
    assert row["diff_size"] == 95 + 7  # parsed magnitude of the change...
    assert row["diff_size"] != len(diff)  # ...NOT the length of the stat STRING
    store.close()


# --- duration excludes time blocked awaiting human approval ------------------------------


def test_duration_excludes_approval_wait(tmp_path):
    # An interactive run blocks at each gate waiting for a human. That idle wait must be
    # subtracted from the recorded pipeline duration_s (measured around the approver calls).
    repo = _target_repo(tmp_path)
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    graph, saver = _wire(tmp_path, repo, executor=FakeExecutor(), gh=gh)
    prd = _write_prd(tmp_path, _TWO_UNITS)

    wait_per_gate = 0.2

    def slow_approver(payload, values):
        time.sleep(wait_per_gate)
        return True

    waited = []
    start = time.monotonic()
    final = drive(
        graph, prd, approver=slow_approver, thread_id="wu",
        on_wait=lambda seconds: waited.append(seconds),
    )
    elapsed = time.monotonic() - start
    saver.conn.close()
    assert final.values["status"] == Status.DONE

    approval_wait = sum(waited)
    assert approval_wait >= wait_per_gate  # at least one gate's wait was measured

    store = build_metrics_store(tmp_path / "dur.sqlite")
    record_run(
        store, final, thread_id="wu", prd_path=prd,
        started_at=0.0, ended_at=elapsed, approval_wait_s=approval_wait,
    )
    run = _run_rows(store)[0]
    assert run["duration_s"] == elapsed - approval_wait  # approval wait subtracted
    assert run["duration_s"] < elapsed  # strictly less than wall-clock
    store.close()
