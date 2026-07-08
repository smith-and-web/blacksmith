"""Durable, thread-keyed run-event stream to an additive live SQLite sink (WU-RUN-EVENTS).

A purely ADDITIVE OBSERVATION channel, mirroring the metrics sink (WU-METRICS-RECORD): the
drive loop emits a structured event at each node boundary (``node_start`` / ``node_end``)
and, at run end, one summary event per unit (``unit_result``) plus a final ``run_status``
event — all derived from the EXISTING graph state reducers (``cost_events`` /
``unit_results`` / ``test_results``), with no new graph state. Events are written
append-only to a SEPARATE live database (``[live] db_path``), keyed by ``thread_id`` with a
per-thread monotonic ``seq``, so a fleet of runs can share one sink and each thread keeps
its own ordered stream.

The sink is NEVER read back into the graph and, exactly like the metrics sink, is
best-effort: with the sink disabled or on any write error a run is byte-for-byte
unaffected. (The best-effort swallowing lives at the CLI call site, mirroring
``_record_metrics``.)
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blacksmith.state import Status

# Event kinds (at least these four per the WU-RUN-EVENTS contract).
NODE_START = "node_start"
NODE_END = "node_end"
UNIT_RESULT = "unit_result"
RUN_STATUS = "run_status"


@dataclass(frozen=True)
class RunEvent:
    """One structured run event: thread-keyed, with a per-thread monotonic ``seq``.

    ``payload`` is a small JSON-serialisable mapping whose shape depends on ``kind``:
    ``node_start``/``node_end`` carry the ``node`` name (``node_end`` also its
    ``duration`` in seconds); ``unit_result`` carries ``unit_id`` / ``gate_result`` /
    ``cost_usd``; ``run_status`` carries ``status`` and an optional ``pr_url``.
    """

    thread_id: str
    seq: int
    ts: float
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_events (
    thread_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT,
    PRIMARY KEY (thread_id, seq)
)
"""


def build_live_store(db_path: str | Path) -> sqlite3.Connection:
    """Open a file-backed live-events SQLite, creating its append-only schema on open.

    Mirrors ``metrics.build_metrics_store`` / ``graph.build_checkpointer``: a fresh
    instance pointed at the same path re-attaches to the existing file. This is the live
    channel's OWN database (``[live] db_path``) — it is never shared with the checkpointer,
    the long-term Store, or the metrics sink, and never read back into the graph.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


class LiveSink:
    """Append-only writer over the live-events DB, keyed by ``thread_id``.

    ``emit`` assigns the next per-thread monotonic ``seq`` (``MAX(seq) + 1`` for that
    thread), so two thread-ids sharing one sink write independent, contiguous streams
    (fleet). Writes are additive inserts only — rows are never updated or read back into
    the graph.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _next_seq(self, thread_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM run_events WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return int(row[0]) + 1

    def emit(
        self,
        thread_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        ts: float | None = None,
    ) -> RunEvent:
        """Append one event for ``thread_id`` and return the written ``RunEvent``."""
        seq = self._next_seq(thread_id)
        when = time.time() if ts is None else ts
        body = payload or {}
        self._conn.execute(
            "INSERT INTO run_events (thread_id, seq, ts, kind, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, seq, when, kind, json.dumps(body)),
        )
        self._conn.commit()
        return RunEvent(thread_id=thread_id, seq=seq, ts=when, kind=kind, payload=body)


def read_events(conn: sqlite3.Connection, thread_id: str) -> list[RunEvent]:
    """Return one thread's events ordered by ``seq`` (READ-ONLY view over the sink)."""
    cursor = conn.execute(
        "SELECT thread_id, seq, ts, kind, payload FROM run_events "
        "WHERE thread_id = ? ORDER BY seq",
        (thread_id,),
    )
    return [
        RunEvent(
            thread_id=row[0],
            seq=row[1],
            ts=row[2],
            kind=row[3],
            payload=json.loads(row[4] or "{}"),
        )
        for row in cursor.fetchall()
    ]


def _cost_and_attempts_by_unit(events: list[dict]) -> dict[str, dict]:
    """Sum implement ``cost_usd`` and count attempts per unit from the cost_events ledger.

    A unit that escalated has >1 implement event (its cost sums across attempts), which is
    what distinguishes an ``escalated`` summary from a plain ``passed`` one.
    """
    out: dict[str, dict] = {}
    for event in events:
        if event.get("node") != "implement":
            continue
        unit_id = event.get("unit_id")
        entry = out.setdefault(unit_id, {"cost": 0.0, "attempts": 0})
        entry["cost"] += event.get("cost_usd") or 0.0
        entry["attempts"] += 1
    return out


def unit_result_payloads(values: dict) -> list[dict]:
    """End-of-unit summary payloads derived from the EXISTING reducers (no new state).

    One payload per passed unit (an entry in ``unit_results``), enriched with the summed
    implement cost from ``cost_events``. ``gate_result`` is ``escalated`` when the unit
    took more than one implement attempt, else ``passed``.
    """
    results = values.get("unit_results") or []
    by_unit = _cost_and_attempts_by_unit(values.get("cost_events") or [])
    payloads: list[dict] = []
    for result in results:
        unit_id = result.get("unit_id")
        info = by_unit.get(unit_id, {"cost": None, "attempts": 0})
        payloads.append(
            {
                "unit_id": unit_id,
                "gate_result": "escalated" if info["attempts"] > 1 else "passed",
                "cost_usd": info["cost"],
            }
        )
    return payloads


def run_status_payload(values: dict) -> dict:
    """End-of-run summary payload: the terminal ``status`` and an optional ``pr_url``."""
    status = values.get("status")
    if isinstance(status, Status):
        status_str: str | None = status.value
    elif status is None:
        status_str = None
    else:
        status_str = str(status)
    payload: dict[str, Any] = {"status": status_str}
    pr_url = values.get("pr_url")
    if pr_url:
        payload["pr_url"] = pr_url
    return payload
