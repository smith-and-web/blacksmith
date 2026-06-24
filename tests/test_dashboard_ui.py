"""Tests for the self-contained dashboard UI page (WU-DASHBOARD-UI).

``GET /`` serves a single self-contained HTML page (inline CSS + vanilla JS) that polls the
read-only JSON endpoints and renders summary cards, a sortable runs table, simple trends
(inline SVG sparklines), and a per-run drill-down. The JS is NOT executed here — these tests
assert only the SERVER-SIDE contract:

- ``GET /`` returns 200 with a ``text/html`` content-type.
- the body references the ``/api/runs`` endpoint(s) the page polls.
- the body contains the expected client mount markers for each section.
- the response carries NO external-CDN URL (the air-gapped / sandbox case must work).

The page renders the same regardless of store contents, so these tests serve it over an
EMPTY store — exercising the friendly "no runs recorded yet" state too — and never need to
seed the metrics sink.
"""

from __future__ import annotations

import contextlib
import threading
import urllib.request
from pathlib import Path

from blacksmith import dashboard

# Mount markers the page exposes so the vanilla JS can attach each rendered section.
MOUNT_MARKERS = (
    'id="summary-cards"',  # summary cards: success rate, total/avg cost, cache hit, duration
    'id="trends"',         # cost-per-run + success-rate sparklines
    'id="runs-table"',     # sortable runs table
    'id="run-detail"',     # per-run drill-down (unit rows)
    'id="empty-state"',    # friendly "no runs recorded yet" state
)


@contextlib.contextmanager
def _running_server(db_path: Path):
    """Start the dashboard server on an ephemeral port in a thread; yield the chosen port."""
    server = dashboard.build_server(db_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")


def test_index_served_as_html(tmp_path):
    db_path = tmp_path / "metrics.sqlite"  # absent store -> empty history, page still serves
    with _running_server(db_path) as port:
        status, ctype, body = _get(port, "/")

    assert status == 200
    assert "text/html" in ctype
    assert body.lstrip().lower().startswith("<!doctype html")


def test_index_references_api_runs(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    with _running_server(db_path) as port:
        _status, _ctype, body = _get(port, "/")

    # The page must poll the read-only JSON API it renders from.
    assert "/api/runs" in body


def test_index_contains_all_mount_markers(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    with _running_server(db_path) as port:
        _status, _ctype, body = _get(port, "/")

    for marker in MOUNT_MARKERS:
        assert marker in body, f"missing mount marker: {marker}"


def test_index_advertises_empty_state_copy(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    with _running_server(db_path) as port:
        _status, _ctype, body = _get(port, "/")

    # Friendly empty state for a store with no runs yet.
    assert "No runs recorded yet" in body


def test_index_has_no_external_cdn(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    with _running_server(db_path) as port:
        _status, _ctype, body = _get(port, "/")

    # OFFLINE: the served page must not fetch anything from an external origin at runtime.
    # No absolute http(s) scheme and no protocol-relative URL may appear in the static HTML.
    lowered = body.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "//cdn" not in lowered
    for host in ("cdnjs", "jsdelivr", "unpkg", "googleapis", "cloudflare", "bootstrapcdn"):
        assert host not in lowered, f"external CDN reference found: {host}"


def test_index_inlines_css_and_js(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    with _running_server(db_path) as port:
        _status, _ctype, body = _get(port, "/")

    # Styling and behaviour are inline — no <link rel=stylesheet> / external <script src=>.
    assert "<style>" in body
    assert "<script>" in body
    assert "rel=\"stylesheet\"" not in body
    assert "src=" not in body  # no external script/asset references
