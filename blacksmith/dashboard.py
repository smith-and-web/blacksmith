"""Localhost read-only JSON API over the metrics store (WU-DASHBOARD-SERVER).

A small stdlib ``http.server`` that exposes the local metrics SQLite sink
(``[metrics] db_path``) as JSON for a dashboard. It is strictly READ-ONLY: it opens the
metrics DB in SQLite read-only mode and reads through the existing
``metrics.list_runs`` / ``metrics.get_run`` helpers. It never writes the metrics DB,
never mutates run state, and never runs the graph.

Safety properties (inviolable):

- LOCALHOST ONLY — the server binds ``127.0.0.1`` (never ``0.0.0.0``); runs contain
  prompts and code, so no auth and no CORS are added because it is never network-exposed.
- OFFLINE — no runtime network dependency; this module serves JSON only and fetches
  nothing from any external CDN.

Endpoints (GET only):

- ``GET /api/runs`` -> JSON list of run rows, most-recent first; optional ``?limit=``.
- ``GET /api/runs/<thread_id>`` -> JSON ``{"run": <row|null>, "units": [<row>, ...]}``.
- any non-GET method -> ``405 Method Not Allowed``.

An empty or absent metrics store yields ``/api/runs == []`` (not an error).
"""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from blacksmith.metrics import get_run, list_runs

# Inviolable: bind loopback only. Runs contain prompts and code, so the server is never
# network-exposed and therefore carries no auth.
HOST = "127.0.0.1"

# A dashboard lists more history than the terse CLI table; still bounded so a huge store
# can't render an unbounded response.
DEFAULT_LIMIT = 100


def _open_ro(db_path: str | Path) -> sqlite3.Connection | None:
    """Open the metrics SQLite in READ-ONLY mode, or ``None`` if the file is absent.

    An absent store is not an error — the API reports it as an empty history. The
    ``file:...?mode=ro`` URI guarantees the connection can never write the metrics DB.
    """
    path = Path(db_path)
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _fetch_runs(db_path: str | Path, limit: int) -> list[dict]:
    """Return recorded run rows (most-recent first), or ``[]`` for an empty/absent store."""
    store = _open_ro(db_path)
    if store is None:
        return []
    try:
        return list_runs(store, limit)
    except sqlite3.OperationalError:
        # A file that exists but has no metrics schema yet reads as empty, not an error.
        return []
    finally:
        store.close()


def _fetch_run(db_path: str | Path, thread_id: str) -> tuple[dict | None, list[dict]]:
    """Return ``(run_row | None, [unit_rows])`` for one thread (read-only)."""
    store = _open_ro(db_path)
    if store is None:
        return None, []
    try:
        return get_run(store, thread_id)
    except sqlite3.OperationalError:
        return None, []
    finally:
        store.close()


def _parse_limit(query: str) -> int:
    """Parse an optional ``?limit=`` integer, falling back to ``DEFAULT_LIMIT``."""
    values = parse_qs(query).get("limit")
    if not values:
        return DEFAULT_LIMIT
    try:
        limit = int(values[0])
    except ValueError:
        return DEFAULT_LIMIT
    return limit if limit > 0 else DEFAULT_LIMIT


def make_handler(db_path: str | Path) -> type[BaseHTTPRequestHandler]:
    """Build a GET-only request handler class bound to ``db_path`` (read-only)."""

    class _DashboardHandler(BaseHTTPRequestHandler):
        # Keep the server quiet by default — request logging would leak to stderr.
        def log_message(self, *args, **kwargs):
            pass

        def _send_json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _method_not_allowed(self) -> None:
            """Any mutating/non-GET method is rejected — this API is read-only."""
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # Every non-GET verb maps to 405 (not 501): the API only reads.
        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_HEAD = _method_not_allowed
        do_OPTIONS = _method_not_allowed

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if parts == ["api", "runs"]:
                self._send_json(_fetch_runs(db_path, _parse_limit(parsed.query)))
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "runs":
                run, units = _fetch_run(db_path, unquote(parts[2]))
                self._send_json({"run": run, "units": units})
                return
            self._send_json({"error": "not found"}, status=404)

    return _DashboardHandler


def build_server(db_path: str | Path, *, port: int = 0) -> ThreadingHTTPServer:
    """Build a ``ThreadingHTTPServer`` bound to ``127.0.0.1`` on ``port`` (0 = ephemeral).

    The server is bound on construction; read the chosen port from
    ``server.server_address[1]``.
    """
    return ThreadingHTTPServer((HOST, port), make_handler(db_path))


def serve(db_path: str | Path, *, port: int = 0) -> int:
    """Serve the read-only JSON API on ``127.0.0.1`` until interrupted.

    Binds an ephemeral port by default (``port=0``), prints the chosen
    ``http://127.0.0.1:<port>`` URL, and serves forever until Ctrl-C. Returns 0.
    """
    server = build_server(db_path, port=port)
    host, chosen_port = server.server_address[0], server.server_address[1]
    print(f"http://{host}:{chosen_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
