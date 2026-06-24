"""Tests for the localhost read-only metrics JSON API (WU-DASHBOARD-SERVER).

The dashboard server is a stdlib ``http.server`` that reads the local metrics SQLite sink
in READ-ONLY mode and exposes it as JSON. These tests start the server on an ephemeral port
in a background thread and make REAL localhost requests:

- ``GET /api/runs`` returns recorded run rows as JSON (most-recent first).
- ``GET /api/runs/<thread_id>`` returns ``{run, units}`` with the unit rows.
- a POST returns 405 (the API only reads).
- the bind host is ``127.0.0.1`` (never network-exposed).
- the metrics DB is never written.

The sink is seeded directly via ``record_run`` (snapshots are plain dicts, which
``record_run`` accepts), exactly like the ``runs`` CLI suite.
"""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from blacksmith import dashboard
from blacksmith.metrics import build_metrics_store, record_run
from blacksmith.state import Status

USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_input_tokens": 300,
    "cache_creation_input_tokens": 0,
}


class _Snap:
    """A minimal snapshot stand-in exposing ``.values`` like the LangGraph snapshot."""

    def __init__(self, values: dict):
        self.values = values


def _snapshot(unit_id: str, *, pr_url: str) -> _Snap:
    return _Snap({
        "status": Status.DONE,
        "cost_events": [
            {
                "node": "implement",
                "unit_id": unit_id,
                "model": "claude-sonnet-4-6",
                "cost_usd": 0.30,
                "num_turns": 7,
                "usage": USAGE,
            }
        ],
        "unit_results": [
            {
                "unit_id": unit_id,
                "title": f"title {unit_id}",
                "files_touched": [f"{unit_id.lower()}.txt"],
                "diff_summary": "x",
            }
        ],
        "pr_url": pr_url,
    })


def _seed_two_runs(db_path: Path) -> None:
    """Record two runs (newest = run-b) into a metrics store."""
    store = build_metrics_store(db_path)
    record_run(
        store,
        _snapshot("WU-A", pr_url="https://github.com/owner/demo/pull/1"),
        thread_id="run-a",
        prd_path="a.md",
        started_at=100.0,
        ended_at=110.0,
    )
    record_run(
        store,
        _snapshot("WU-B", pr_url="https://github.com/owner/demo/pull/2"),
        thread_id="run-b",
        prd_path="b.md",
        started_at=200.0,
        ended_at=242.5,
    )
    store.close()


@contextlib.contextmanager
def _running_server(db_path: Path):
    """Start the dashboard server on an ephemeral port in a thread; yield (host, port)."""
    server = dashboard.build_server(db_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get_json(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


# --- bind host ---------------------------------------------------------------


def test_server_binds_localhost_only(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    server = dashboard.build_server(db_path, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


# --- GET /api/runs -----------------------------------------------------------


def test_api_runs_returns_recorded_rows_newest_first(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    with _running_server(db_path) as (_host, port):
        status, runs = _get_json(port, "/api/runs")

    assert status == 200
    assert isinstance(runs, list)
    assert [r["thread_id"] for r in runs] == ["run-b", "run-a"]
    assert runs[0]["pr_url"].endswith("/pull/2")


def test_api_runs_honours_limit(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    with _running_server(db_path) as (_host, port):
        status, runs = _get_json(port, "/api/runs?limit=1")

    assert status == 200
    assert [r["thread_id"] for r in runs] == ["run-b"]


def test_api_runs_empty_store_returns_empty_list(tmp_path):
    # An absent store is not an error — the API reports an empty history.
    db_path = tmp_path / "absent" / "metrics.sqlite"
    with _running_server(db_path) as (_host, port):
        status, runs = _get_json(port, "/api/runs")

    assert status == 200
    assert runs == []
    assert not db_path.exists()


# --- GET /api/runs/<thread_id> ----------------------------------------------


def test_api_run_detail_returns_units(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    with _running_server(db_path) as (_host, port):
        status, payload = _get_json(port, "/api/runs/run-a")

    assert status == 200
    assert payload["run"]["thread_id"] == "run-a"
    assert [u["unit_id"] for u in payload["units"]] == ["WU-A"]
    assert payload["units"][0]["title"] == "title WU-A"


def test_api_run_detail_unknown_thread_returns_null_run(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    with _running_server(db_path) as (_host, port):
        status, payload = _get_json(port, "/api/runs/nope")

    assert status == 200
    assert payload["run"] is None
    assert payload["units"] == []


# --- non-GET methods ---------------------------------------------------------


def test_post_returns_405(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    with _running_server(db_path) as (_host, port):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/runs", data=b"{}", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 405


# --- read-only over the metrics DB -------------------------------------------


def test_metrics_db_is_never_written(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    before = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns

    with _running_server(db_path) as (_host, port):
        _get_json(port, "/api/runs")
        _get_json(port, "/api/runs/run-a")
        _get_json(port, "/api/runs/run-b")

    assert db_path.read_bytes() == before
    assert db_path.stat().st_mtime_ns == before_mtime
