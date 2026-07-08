"""Tests for the live SSE + active-runs endpoints on the dashboard (WU-LIVE-SERVER).

The dashboard gains two READ-ONLY additions over the existing metrics-only API, both
sourced from the additive live-events sink (WU-RUN-EVENTS, ``[live] db_path``):

- ``GET /api/runs/active`` -> JSON list of thread_ids with recent activity in the live
  sink, each with its latest status and last-event ts.
- ``GET /live/<thread_id>`` -> a ``text/event-stream`` (SSE) response that replays that
  thread's events in seq order as ``data:`` frames.

Neither route ever writes the live sink or the metrics store, and an absent/unconfigured
live sink degrades to an empty result rather than an error — exactly like the existing
metrics routes. The sink is seeded directly with ``LiveSink``, exactly like the
``test_run_events`` suite, and a real server is started on an ephemeral port so these
tests make real localhost requests. The SSE endpoint is bounded (a short, fixed number of
empty polls before closing), so reading its response to completion never hangs a test.
"""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.request
from pathlib import Path

from blacksmith import dashboard
from blacksmith.events import NODE_END, NODE_START, RUN_STATUS, LiveSink, build_live_store


@contextlib.contextmanager
def _running_server(metrics_db_path: Path, live_db_path: Path | None = None):
    """Start the dashboard server on an ephemeral port in a thread; yield (host, port)."""
    server = dashboard.build_server(metrics_db_path, port=0, live_db_path=live_db_path)
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


def _get_sse(port: int, path: str):
    """GET an SSE endpoint to completion; return (status, content_type, [data frames])."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        status = resp.status
        content_type = resp.headers.get("Content-Type")
        body = resp.read().decode("utf-8")
    frames = [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    return status, content_type, frames


def _seed(live_db_path: Path) -> None:
    """Seed two threads: ``alpha`` has concluded (run_status), ``beta`` is still running."""
    store = build_live_store(live_db_path)
    sink = LiveSink(store)
    sink.emit("alpha", NODE_START, {"node": "ingest_prd"}, ts=100.0)
    sink.emit("alpha", NODE_END, {"node": "ingest_prd", "duration": 0.5}, ts=100.5)
    sink.emit(
        "alpha", RUN_STATUS,
        {"status": "done", "pr_url": "https://github.com/owner/demo/pull/1"}, ts=101.0,
    )
    sink.emit("beta", NODE_START, {"node": "plan"}, ts=200.0)
    store.close()


# --- GET /api/runs/active -----------------------------------------------------


def test_active_runs_lists_seeded_threads_with_latest_status(tmp_path):
    live_db = tmp_path / "live.sqlite"
    _seed(live_db)
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        status, active = _get_json(port, "/api/runs/active")

    assert status == 200
    by_thread = {row["thread_id"]: row for row in active}
    assert set(by_thread) == {"alpha", "beta"}

    # alpha concluded: latest status comes from its run_status event.
    assert by_thread["alpha"]["status"] == "done"
    assert by_thread["alpha"]["last_ts"] == 101.0

    # beta has activity but no run_status yet: reported as still running.
    assert by_thread["beta"]["status"] == "running"
    assert by_thread["beta"]["last_ts"] == 200.0

    # Most-recently-active thread first.
    assert [row["thread_id"] for row in active] == ["beta", "alpha"]


def test_active_runs_absent_sink_returns_empty_list(tmp_path):
    live_db = tmp_path / "absent" / "live.sqlite"
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        status, active = _get_json(port, "/api/runs/active")

    assert status == 200
    assert active == []
    assert not live_db.exists()  # a read-only GET never creates the sink file


def test_active_runs_unconfigured_live_sink_returns_empty_list(tmp_path):
    # live_db_path omitted entirely -> the route still exists but reports empty.
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db) as (_host, port):
        status, active = _get_json(port, "/api/runs/active")

    assert status == 200
    assert active == []


# --- GET /live/<thread_id> -----------------------------------------------------


def test_live_stream_replays_seeded_events_in_seq_order(tmp_path):
    live_db = tmp_path / "live.sqlite"
    _seed(live_db)
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        status, content_type, frames = _get_sse(port, "/live/alpha")

    assert status == 200
    assert content_type == "text/event-stream"
    assert [f["seq"] for f in frames] == [0, 1, 2]
    assert [f["kind"] for f in frames] == [NODE_START, NODE_END, RUN_STATUS]
    assert all(f["thread_id"] == "alpha" for f in frames)
    assert frames[0]["payload"]["node"] == "ingest_prd"
    assert frames[-1]["payload"]["status"] == "done"


def test_live_stream_unknown_thread_returns_empty_stream(tmp_path):
    live_db = tmp_path / "live.sqlite"
    _seed(live_db)
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        status, content_type, frames = _get_sse(port, "/live/does-not-exist")

    assert status == 200
    assert content_type == "text/event-stream"
    assert frames == []


def test_live_stream_absent_sink_returns_empty_stream(tmp_path):
    live_db = tmp_path / "absent" / "live.sqlite"
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        status, content_type, frames = _get_sse(port, "/live/whatever")

    assert status == 200
    assert content_type == "text/event-stream"
    assert frames == []
    assert not live_db.exists()


# --- existing metrics routes are unaffected ------------------------------------


def test_existing_api_runs_route_still_works_with_live_db_path_set(tmp_path):
    live_db = tmp_path / "live.sqlite"
    _seed(live_db)
    metrics_db = tmp_path / "metrics.sqlite"  # never seeded -> empty metrics history

    with _running_server(metrics_db, live_db) as (_host, port):
        status, runs = _get_json(port, "/api/runs")

    assert status == 200
    assert runs == []
