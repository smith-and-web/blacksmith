"""Local metrics SQLite sink (WU-METRICS-RECORD).

A purely ADDITIVE, write-only output channel: at the end of a run the CLI sink derives
a small set of run- and unit-level rows from the FINAL graph state and records them to a
local SQLite file. It is its OWN database (``[metrics] db_path``), never shared with the
checkpointer or the long-term Store, and it is NEVER read back into the graph — a run
reaches the same terminal state and opens the same PR with or without it.

``build_metrics_store`` mirrors ``graph.build_checkpointer`` (open + create schema on the
file). ``record_run`` writes one run row (UPSERT keyed by ``thread_id``) plus N unit rows,
derived from the snapshot's ``cost_events`` / ``unit_results`` / ``status`` / ``errors`` /
``pr_url``. Recording is best-effort at the call site (the CLI swallows any exception), so
metrics disabled or a metrics write that fails behaves exactly as today.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from blacksmith.state import Status

_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_metrics (
    thread_id TEXT PRIMARY KEY,
    status TEXT,
    total_cost REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_hit_rate REAL,
    duration_s REAL,
    success INTEGER,
    failure_reason TEXT,
    escalation_count INTEGER,
    model_tier_mix TEXT,
    units_count INTEGER,
    pr_url TEXT,
    prd TEXT,
    repo TEXT,
    transcripts TEXT,
    started_at REAL,
    ended_at REAL
)
"""

_UNIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS unit_metrics (
    thread_id TEXT,
    unit_id TEXT,
    title TEXT,
    models TEXT,
    cost REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    turns INTEGER,
    gate_result TEXT,
    files_count INTEGER,
    diff_size INTEGER,
    PRIMARY KEY (thread_id, unit_id)
)
"""

# Non-PK columns that may be MISSING from a DB created by an earlier schema version.
# ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a metrics DB created
# before a column was added (e.g. ``transcripts``) would reject inserts referencing it and
# the best-effort sink would drop every row SILENTLY. ``_ensure_columns`` ADDs any missing
# column. Keep these in sync with the CREATE schemas above; PRIMARY KEY columns are omitted
# (ALTER cannot add them, and they always exist from the original CREATE).
_RUN_COLUMNS: dict[str, str] = {
    "status": "TEXT",
    "total_cost": "REAL",
    "input_tokens": "INTEGER",
    "output_tokens": "INTEGER",
    "cache_hit_rate": "REAL",
    "duration_s": "REAL",
    "success": "INTEGER",
    "failure_reason": "TEXT",
    "escalation_count": "INTEGER",
    "model_tier_mix": "TEXT",
    "units_count": "INTEGER",
    "pr_url": "TEXT",
    "prd": "TEXT",
    "repo": "TEXT",
    "transcripts": "TEXT",
    "started_at": "REAL",
    "ended_at": "REAL",
}
_UNIT_COLUMNS: dict[str, str] = {
    "title": "TEXT",
    "models": "TEXT",
    "cost": "REAL",
    "input_tokens": "INTEGER",
    "output_tokens": "INTEGER",
    "turns": "INTEGER",
    "gate_result": "TEXT",
    "files_count": "INTEGER",
    "diff_size": "INTEGER",
}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Add any of ``columns`` missing from ``table`` via ``ALTER TABLE ADD COLUMN``.

    An idempotent forward migration so a metrics DB created by an earlier schema gains new
    columns instead of silently rejecting inserts. ``table``/column names come from fixed
    in-code maps (never user input), so the f-strings are safe.
    """
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def build_metrics_store(db_path: str | Path) -> sqlite3.Connection:
    """Open a file-backed metrics SQLite, creating its schema on open.

    Mirrors ``graph.build_checkpointer``: a fresh instance pointed at the same path
    re-attaches to the existing file, and a DB created by an EARLIER schema is
    forward-migrated (any missing column is added) so recording never silently breaks
    after a schema change. This is the metrics channel's OWN database — it is never shared
    with the checkpointer or the long-term Store.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute(_RUN_SCHEMA)
    conn.execute(_UNIT_SCHEMA)
    _ensure_columns(conn, "run_metrics", _RUN_COLUMNS)
    _ensure_columns(conn, "unit_metrics", _UNIT_COLUMNS)
    conn.commit()
    return conn


def _values(snapshot: Any) -> dict:
    """The final state mapping, accepting either a snapshot (``.values``) or a raw dict."""
    return getattr(snapshot, "values", snapshot) or {}


def _status_str(status: Any) -> str:
    """Normalise a Status enum / string to its plain string value."""
    return status.value if isinstance(status, Status) else str(status or "")


def _usage_sum(events: list[dict], key: str) -> int:
    total = 0
    for event in events:
        usage = event.get("usage") or {}
        total += int(usage.get(key, 0) or 0)
    return total


def _cache_hit_rate(events: list[dict]) -> float:
    """``cache_read / (input + cache_read + cache_creation)`` across all events."""
    input_tokens = _usage_sum(events, "input_tokens")
    cache_read = _usage_sum(events, "cache_read_input_tokens")
    cache_creation = _usage_sum(events, "cache_creation_input_tokens")
    denom = input_tokens + cache_read + cache_creation
    return cache_read / denom if denom else 0.0


def _categorize_failure(status: str, errors: list[dict]) -> str:
    """Categorize the run's failure into the agreed v1 set.

    ``none`` when the run is done; otherwise the most recent (terminal) error's node —
    refined for the implement node by its message — maps to one of the fixed reasons.
    """
    if status == Status.DONE.value:
        return "none"
    for err in reversed(errors or []):
        node = err.get("node", "")
        message = (err.get("message") or "").lower()
        if node == "ingest_prd":
            return "ingest_fail"
        if node == "plan":
            return "plan_fail"
        if node == "test_gate":
            return "gate_fail"
        if node in ("open_pr", "open_draft_pr"):
            return "pr_fail"
        if node == "implement":
            if "untouchable" in message:
                return "untouchable"
            if "no file changes" in message or "no changes" in message:
                return "no_changes"
            if "commit" in message:
                return "commit_error"
            return "gate_fail"
        if node == "join_level":
            return "gate_fail"
    return "none" if not errors else "gate_fail"


def _title_map(values: dict) -> dict[str, str]:
    """Best-effort unit-id -> title lookup from the PRD contract (when present)."""
    prd = values.get("prd")
    contract = getattr(prd, "contract", None)
    if contract is None:
        return {}
    return {unit.id: unit.title for unit in getattr(contract, "work_units", []) or []}


def _unit_rows(values: dict) -> list[dict]:
    """Per-unit aggregation derived from the implement ``cost_events`` (one or more
    attempts per unit), enriched with the unit's retained ``unit_results`` record.

    A unit that escalated has >1 implement event, so its ``models``, ``cost``, ``turns``
    and tokens sum across both attempts and its gate is reported as ``escalated``.
    """
    events = values.get("cost_events") or []
    results = {r.get("unit_id"): r for r in (values.get("unit_results") or [])}
    titles = _title_map(values)

    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for event in events:
        if event.get("node") != "implement":
            continue
        grouped.setdefault(event.get("unit_id"), []).append(event)

    rows: list[dict] = []
    for unit_id, unit_events in grouped.items():
        result = results.get(unit_id) or {}
        models = list(dict.fromkeys(e.get("model") for e in unit_events if e.get("model")))
        attempts = len(unit_events)
        passed = unit_id in results
        if attempts > 1:
            gate_result = "escalated"
        elif passed:
            gate_result = "passed"
        else:
            gate_result = "failed"
        diff_summary = result.get("diff_summary") or ""
        rows.append(
            {
                "unit_id": unit_id,
                "title": result.get("title") or titles.get(unit_id, ""),
                "models": ",".join(models),
                "cost": sum(e.get("cost_usd") or 0.0 for e in unit_events),
                "input_tokens": _usage_sum(unit_events, "input_tokens"),
                "output_tokens": _usage_sum(unit_events, "output_tokens"),
                "turns": sum(int(e.get("num_turns") or 0) for e in unit_events),
                "gate_result": gate_result,
                "files_count": len(result.get("files_touched") or []),
                "diff_size": len(diff_summary),
            }
        )
    return rows


def _transcript_refs(values: dict, transcripts_dir: str | Path | None) -> list[str]:
    """Resolved transcript file paths for this run's per-call session ids.

    Drawn from the run's ``cost_events``: each event carries the ``session_id`` of the
    model call it recorded, and the executor wrote that call's transcript to
    ``<dir>/<session_id>.jsonl`` (WU-TRANSCRIPT-CAPTURE). Returns the de-duplicated,
    order-preserving list of those resolved paths so a run links back to the per-call
    transcripts that belong to it. Empty when transcripts are disabled (no ``dir``) or
    no event carries a session id (nothing was captured).
    """
    if not transcripts_dir:
        return []
    events = values.get("cost_events") or []
    session_ids = list(
        dict.fromkeys(e.get("session_id") for e in events if e.get("session_id"))
    )
    base = Path(transcripts_dir)
    return [str(base / f"{session_id}.jsonl") for session_id in session_ids]


def _rows(cursor: sqlite3.Cursor) -> list[dict]:
    """Materialise a cursor's rows as plain ``column -> value`` dicts."""
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def list_runs(store: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Return recorded run rows, most-recent first (READ-ONLY).

    A pure SELECT over ``run_metrics`` ordered by end time descending and capped at
    ``limit``. This never writes — it is the read model over the metrics sink, which is
    never read back into the graph.
    """
    cursor = store.execute(
        "SELECT * FROM run_metrics ORDER BY ended_at DESC, started_at DESC LIMIT ?",
        (limit,),
    )
    return _rows(cursor)


def get_run(store: sqlite3.Connection, thread_id: str) -> tuple[dict | None, list[dict]]:
    """Return ``(run_row, [unit_rows])`` for one thread (READ-ONLY).

    ``run_row`` is ``None`` when the thread has no recorded run. The unit rows are ordered
    by ``unit_id``. Both are pure SELECTs — nothing is written.
    """
    run_cursor = store.execute(
        "SELECT * FROM run_metrics WHERE thread_id = ?", (thread_id,)
    )
    run_rows = _rows(run_cursor)
    unit_cursor = store.execute(
        "SELECT * FROM unit_metrics WHERE thread_id = ? ORDER BY unit_id", (thread_id,)
    )
    return (run_rows[0] if run_rows else None), _rows(unit_cursor)


def record_run(
    store: sqlite3.Connection,
    snapshot: Any,
    *,
    thread_id: str,
    prd_path: str | Path,
    started_at: float,
    ended_at: float,
    transcripts_dir: str | Path | None = None,
) -> None:
    """Write ONE run row (UPSERT keyed by ``thread_id``) plus N unit rows.

    Derived entirely from the final snapshot's ``cost_events`` / ``unit_results`` /
    ``status`` / ``errors`` / ``pr_url``. A resumed thread re-derives and UPSERTs the SAME
    rows. This is a write-only sink: nothing here is ever read back into the graph.

    ``transcripts_dir`` (the configured transcripts directory) resolves the run's
    per-call ``session_id`` references into ``<dir>/<session_id>.jsonl`` paths recorded
    on the run row, so a run links to the transcripts that belong to it. ``None`` (or a
    run that captured nothing) records an empty list.
    """
    values = _values(snapshot)
    events = values.get("cost_events") or []
    status = _status_str(values.get("status"))
    errors = values.get("errors") or []

    prd = values.get("prd")
    contract = getattr(prd, "contract", None)
    repo = getattr(contract, "primary_target_repo", "") or ""

    unit_rows = _unit_rows(values)
    distinct_units = {e.get("unit_id") for e in events if e.get("node") == "implement"}
    implement_events = sum(1 for e in events if e.get("node") == "implement")
    escalation_count = max(0, implement_events - len(distinct_units))
    model_mix = Counter(e.get("model") for e in events if e.get("model"))

    run_row = {
        "thread_id": thread_id,
        "status": status,
        "total_cost": sum(e.get("cost_usd") or 0.0 for e in events),
        "input_tokens": _usage_sum(events, "input_tokens"),
        "output_tokens": _usage_sum(events, "output_tokens"),
        "cache_hit_rate": _cache_hit_rate(events),
        "duration_s": ended_at - started_at,
        "success": 1 if status == Status.DONE.value else 0,
        "failure_reason": _categorize_failure(status, errors),
        "escalation_count": escalation_count,
        "model_tier_mix": json.dumps(dict(model_mix)),
        "units_count": len(unit_rows),
        "pr_url": values.get("pr_url"),
        "prd": str(prd_path),
        "repo": repo,
        "transcripts": json.dumps(_transcript_refs(values, transcripts_dir)),
        "started_at": started_at,
        "ended_at": ended_at,
    }

    columns = list(run_row)
    placeholders = ", ".join(":" + c for c in columns)
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "thread_id")
    store.execute(
        f"INSERT INTO run_metrics ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(thread_id) DO UPDATE SET {updates}",
        run_row,
    )

    # Replace this thread's unit rows so a resume UPSERTs cleanly rather than duplicating.
    store.execute("DELETE FROM unit_metrics WHERE thread_id = ?", (thread_id,))
    for unit_row in unit_rows:
        row = {"thread_id": thread_id, **unit_row}
        cols = list(row)
        store.execute(
            f"INSERT INTO unit_metrics ({', '.join(cols)}) "
            f"VALUES ({', '.join(':' + c for c in cols)})",
            row,
        )
    store.commit()
