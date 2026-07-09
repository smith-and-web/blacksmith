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

This file also covers ``GET /live`` (WU-LIVE-UI): the self-contained fleet page built on
top of the two endpoints above. Since that view is visually QA'd on the PR, these tests
are purely STRUCTURAL — they assert the response is HTML, that the markup carries the
fleet + per-run mount points, that the inlined JS wires an ``EventSource`` to
``/live/<thread_id>``, and that no external http(s) asset URL appears anywhere in the page.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
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


def _get_html(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")


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


def test_active_runs_excludes_concluded_runs(tmp_path):
    # A concluded run (terminal run_status) is history, not in-flight, so it must NOT appear
    # in the live fleet — a still-running run does. (Recent timestamps, so neither is filtered
    # by the silence window, which the unit test below covers on its own.)
    live_db = tmp_path / "live.sqlite"
    now = time.time()
    store = build_live_store(live_db)
    sink = LiveSink(store)
    sink.emit("finished", NODE_START, {"node": "plan"}, ts=now - 20)
    sink.emit("finished", RUN_STATUS, {"status": "done"}, ts=now - 10)
    sink.emit("live-one", NODE_START, {"node": "implement"}, ts=now - 5)
    store.close()
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        status, active = _get_json(port, "/api/runs/active")

    assert status == 200
    # Only the in-flight run; the concluded one is dropped.
    assert [row["thread_id"] for row in active] == ["live-one"]
    assert active[0]["status"] == "running"


def test_fetch_active_runs_drops_concluded_and_silent_runs(tmp_path):
    # Deterministic (injected now): a running run is in-flight; a concluded run (run_status)
    # and a run silent past ACTIVE_WINDOW_S are both dropped from the live fleet.
    live_db = tmp_path / "live.sqlite"
    now = 10_000.0
    store = build_live_store(live_db)
    sink = LiveSink(store)
    sink.emit("running", NODE_START, {"node": "implement"}, ts=now - 30)
    sink.emit("concluded", NODE_START, {"node": "plan"}, ts=now - 40)
    sink.emit("concluded", RUN_STATUS, {"status": "halted"}, ts=now - 35)
    sink.emit("silent", NODE_START, {"node": "implement"}, ts=now - dashboard.ACTIVE_WINDOW_S - 1)
    store.close()

    active = dashboard._fetch_active_runs(live_db, now=now)
    assert [row["thread_id"] for row in active] == ["running"]
    assert active[0]["status"] == "running"


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


# --- GET /live (fleet page, WU-LIVE-UI) ----------------------------------------

# Mount markers the page exposes so the vanilla JS can attach the fleet list, clone a
# per-run card, and drill in to one run's timeline.
LIVE_MOUNT_MARKERS = (
    'id="fleet"',                    # fleet list: one card per active thread_id
    'id="fleet-empty"',              # friendly "no active runs" state
    'id="fleet-run-template"',       # per-run mount points, cloned once per active run
    'id="run-panel"',                # drill-in to one run's full node/unit timeline
)


def test_live_page_served_as_html(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    live_db = tmp_path / "live.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        status, content_type, body = _get_html(port, "/live")

    assert status == 200
    assert "text/html" in content_type
    assert body.lstrip().lower().startswith("<!doctype html")


def test_live_page_served_with_no_configured_live_sink(tmp_path):
    # live_db_path omitted entirely -> the page still serves (its data comes from client-
    # side polling, not from anything read at request time).
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db) as (_host, port):
        status, content_type, _body = _get_html(port, "/live")

    assert status == 200
    assert "text/html" in content_type


def test_live_page_contains_fleet_and_per_run_mount_points(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    live_db = tmp_path / "live.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        _status, _content_type, body = _get_html(port, "/live")

    for marker in LIVE_MOUNT_MARKERS:
        assert marker in body, f"missing mount marker: {marker}"


def test_live_page_wires_eventsource_to_live_thread_endpoint(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    live_db = tmp_path / "live.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        _status, _content_type, body = _get_html(port, "/live")

    # The fleet page opens a live SSE connection per active run, straight to the same
    # per-thread stream exercised above.
    assert "EventSource" in body
    assert "/live/" in body
    assert "/api/runs/active" in body


def test_live_page_has_no_external_asset_urls(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    live_db = tmp_path / "live.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        _status, _content_type, body = _get_html(port, "/live")

    # OFFLINE: no absolute http(s) URL and no external CDN reference anywhere in the page.
    lowered = body.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    for host in ("cdnjs", "jsdelivr", "unpkg", "googleapis", "cloudflare", "bootstrapcdn"):
        assert host not in lowered, f"external CDN reference found: {host}"


def test_live_page_inlines_css_and_js(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    live_db = tmp_path / "live.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        _status, _content_type, body = _get_html(port, "/live")

    assert "<style>" in body
    assert "<script>" in body
    assert 'rel="stylesheet"' not in body


def test_existing_live_thread_stream_route_still_works_alongside_live_page(tmp_path):
    # /live/<thread_id> (SSE) and /live (the fleet page) must coexist without either
    # route shadowing the other.
    live_db = tmp_path / "live.sqlite"
    _seed(live_db)
    metrics_db = tmp_path / "metrics.sqlite"

    with _running_server(metrics_db, live_db) as (_host, port):
        page_status, page_ctype, _page_body = _get_html(port, "/live")
        stream_status, stream_ctype, frames = _get_sse(port, "/live/alpha")

    assert page_status == 200
    assert "text/html" in page_ctype
    assert stream_status == 200
    assert stream_ctype == "text/event-stream"
    assert [f["kind"] for f in frames] == [NODE_START, NODE_END, RUN_STATUS]
