"""Critical per-run operation journal, separate from best-effort observability.

Intent commits before an effect starts; outcome and its events commit together.
Every operation uses a new connection, so one Journal can be shared by workers.
Pending/uncertain paid calls are never silently replayed. Reconciliation is owned
by the stage that knows the external effect, not by a generic retry loop here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({"completed", "failed", "interrupted", "unknown"})


class ReceiptError(Exception):
    """Critical integrity/storage failure: stop before performing another effect."""


def encoded(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (ValueError, TypeError) as exc:
        raise ReceiptError("receipt payload is not finite JSON data") from exc


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ReceiptError("receipt identity must be a nonempty string without control characters")


@dataclass(frozen=True)
class OperationKey:
    run_id: str
    unit_id: str
    attempt_id: str
    kind: str

    def __post_init__(self):
        try:
            UUID(self.run_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ReceiptError("invalid receipt run identity") from exc
        for value in (self.unit_id, self.attempt_id, self.kind):
            _identifier(value)

    @property
    def id(self) -> str:
        return digest(encoded([self.run_id, self.unit_id, self.attempt_id, self.kind]))

    @property
    def trailer(self) -> str:
        return f"Blacksmith-Operation-Id: {self.id}"


@dataclass(frozen=True)
class OperationReceipt:
    key: OperationKey
    status: str
    intent_json: str
    outcome_json: str | None
    created: bool = False

    @property
    def intent(self) -> dict:
        return json.loads(self.intent_json)

    @property
    def outcome(self) -> dict | None:
        return json.loads(self.outcome_json) if self.outcome_json is not None else None


_SCHEMA = (
    "CREATE TABLE metadata (schema_version INTEGER NOT NULL, run_id TEXT NOT NULL, "
    "context_hash TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
    "unit_id TEXT NOT NULL, attempt_id TEXT NOT NULL, kind TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK(status IN "
    "('intent','completed','failed','interrupted','unknown')), "
    "intent_json TEXT NOT NULL, intent_hash TEXT NOT NULL, outcome_json TEXT, outcome_hash TEXT, "
    "created_at TEXT NOT NULL, finished_at TEXT, UNIQUE(run_id,unit_id,attempt_id,kind))",
    "CREATE TABLE events (event_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL "
    "REFERENCES operations(operation_id), kind TEXT NOT NULL, payload_json TEXT NOT NULL, "
    "payload_hash TEXT NOT NULL, created_at TEXT NOT NULL)",
)
_COLUMNS = {
    "metadata": {"schema_version", "run_id", "context_hash", "created_at"},
    "operations": {
        "operation_id",
        "run_id",
        "unit_id",
        "attempt_id",
        "kind",
        "status",
        "intent_json",
        "intent_hash",
        "outcome_json",
        "outcome_hash",
        "created_at",
        "finished_at",
    },
    "events": {"event_id", "operation_id", "kind", "payload_json", "payload_hash", "created_at"},
}


class OperationJournal:
    def __init__(self, path: str | Path, run_id: str, context_hash: str):
        self.path = Path(path).absolute()
        OperationKey(run_id, "__run__", "journal", "identity")
        if (
            not isinstance(context_hash, str)
            or len(context_hash) != 64
            or any(c not in "0123456789abcdef" for c in context_hash)
        ):
            raise ReceiptError("invalid journal context fingerprint")
        self.run_id = run_id
        self.context_hash = context_hash

    @classmethod
    def for_context(cls, context) -> OperationJournal:
        return cls(
            context.directory / "operations.sqlite",
            context.run_id,
            digest(encoded(context.model_dump())),
        )

    @classmethod
    def create(cls, context) -> OperationJournal:
        journal = cls.for_context(context)
        try:
            fd = os.open(
                journal.path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(fd)
            with journal._connection(validate=False) as conn:
                if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                    raise ReceiptError("critical journal requires WAL")
                conn.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO metadata VALUES(?,?,?,?)",
                    (
                        SCHEMA_VERSION,
                        journal.run_id,
                        journal.context_hash,
                        _now(),
                    ),
                )
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.commit()
            journal.verify()
            return journal
        except (OSError, sqlite3.Error) as exc:
            raise ReceiptError(f"cannot initialize critical journal: {journal.path}") from exc

    @contextmanager
    def _connection(self, *, validate: bool = True) -> Iterator[sqlite3.Connection]:
        conn = None
        try:
            if self.path.is_symlink():
                raise ReceiptError("critical journal cannot be a symlink")
            # mode=rw is deliberate: a missing recovery DB must not become an empty new run.
            conn = sqlite3.connect(
                f"file:{quote(str(self.path), safe='/')}?mode=rw",
                uri=True,
                timeout=5.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            if validate:
                self._validate(conn)
            yield conn
        except sqlite3.Error as exc:
            raise ReceiptError(f"critical journal read/write failed: {self.path}") from exc
        finally:
            if conn is not None:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def _validate(self, conn: sqlite3.Connection) -> None:
        for table, expected in _COLUMNS.items():
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if columns != expected:
                raise ReceiptError("critical journal schema mismatch")
        rows = conn.execute("SELECT * FROM metadata").fetchall()
        if len(rows) != 1 or tuple(rows[0])[:3] != (
            SCHEMA_VERSION,
            self.run_id,
            self.context_hash,
        ):
            raise ReceiptError(f"critical journal identity/schema mismatch: {self.path}")
        if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ReceiptError("unsupported critical journal version")
        if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            raise ReceiptError("critical journal no longer uses WAL")

    def verify(self) -> None:
        with self._connection() as conn:
            if conn.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
                raise ReceiptError("critical journal integrity check failed")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ReceiptError("critical journal event has no operation")
            for row in conn.execute("SELECT * FROM operations"):
                self._receipt(row)
            for row in conn.execute("SELECT * FROM events"):
                self._event(row)

    def _check_key(self, key: OperationKey) -> None:
        if key.run_id != self.run_id:
            raise ReceiptError("operation belongs to a different run")

    def _receipt(self, row: sqlite3.Row, *, created: bool = False) -> OperationReceipt:
        key = OperationKey(row["run_id"], row["unit_id"], row["attempt_id"], row["kind"])
        self._check_key(key)
        if key.id != row["operation_id"] or digest(row["intent_json"]) != row["intent_hash"]:
            raise ReceiptError("corrupt operation intent")
        outcome = row["outcome_json"]
        if (row["status"] == "intent") != (outcome is None):
            raise ReceiptError("operation state/outcome mismatch")
        if row["status"] != "intent" and row["status"] not in TERMINAL_STATES:
            raise ReceiptError("unknown operation state")
        if outcome is not None and digest(outcome) != row["outcome_hash"]:
            raise ReceiptError("corrupt operation outcome")
        try:
            if not isinstance(json.loads(row["intent_json"]), dict):
                raise ValueError("invalid intent")
            if outcome is not None and not isinstance(json.loads(outcome), dict):
                raise ValueError("invalid outcome")
        except ValueError as exc:
            raise ReceiptError("corrupt operation JSON") from exc
        return OperationReceipt(key, row["status"], row["intent_json"], outcome, created)

    def get(self, key: OperationKey) -> OperationReceipt | None:
        self._check_key(key)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (key.id,),
            ).fetchone()
            return self._receipt(row) if row else None

    def intent(self, key: OperationKey, payload: Mapping[str, Any]) -> OperationReceipt:
        self._check_key(key)
        text = encoded(dict(payload))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (key.id,),
            ).fetchone()
            if row:
                receipt = self._receipt(row)
                if receipt.intent_json != text:
                    raise ReceiptError(f"operation intent changed: {key.id}")
            else:
                conn.execute(
                    "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        key.id,
                        key.run_id,
                        key.unit_id,
                        key.attempt_id,
                        key.kind,
                        "intent",
                        text,
                        digest(text),
                        None,
                        None,
                        _now(),
                        None,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM operations WHERE operation_id=?",
                    (key.id,),
                ).fetchone()
                receipt = self._receipt(row, created=True)
            conn.commit()
            return receipt

    def command_intent(
        self, unit_id: str, attempt_id: str, kind: str, payload: Mapping[str, Any]
    ) -> OperationReceipt:
        """Allocate an ordered observation/effect ID under the same durable lock.

        Commands are never transparently replayed: observations must be fresh and
        mutation reconciliation belongs to the owning stage's stable receipt.
        """
        text = encoded(dict(payload))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ordinal = (
                conn.execute(
                    "SELECT COUNT(*) FROM operations WHERE run_id=? AND unit_id=? "
                    "AND attempt_id=? AND kind LIKE 'command:%'",
                    (self.run_id, unit_id, attempt_id),
                ).fetchone()[0]
                + 1
            )
            key = OperationKey(self.run_id, unit_id, attempt_id, f"command:{ordinal}:{kind}")
            conn.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key.id,
                    key.run_id,
                    key.unit_id,
                    key.attempt_id,
                    key.kind,
                    "intent",
                    text,
                    digest(text),
                    None,
                    None,
                    _now(),
                    None,
                ),
            )
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (key.id,),
            ).fetchone()
            receipt = self._receipt(row, created=True)
            conn.commit()
            return receipt

    def finish(
        self,
        key: OperationKey,
        status: str,
        outcome: Mapping[str, Any],
        *,
        events: Sequence[tuple[str, str, Mapping[str, Any]]] = (),
    ) -> OperationReceipt:
        self._check_key(key)
        if status not in TERMINAL_STATES:
            raise ReceiptError("invalid terminal operation state")
        text = encoded(dict(outcome))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (key.id,),
            ).fetchone()
            if row is None:
                raise ReceiptError("outcome has no committed intent")
            previous = self._receipt(row)
            if previous.status != "intent":
                if previous.status != status or previous.outcome_json != text:
                    raise ReceiptError("conflicting operation outcome; retain for reconciliation")
            else:
                conn.execute(
                    "UPDATE operations SET status=?,outcome_json=?,outcome_hash=?,"
                    "finished_at=? WHERE operation_id=?",
                    (status, text, digest(text), _now(), key.id),
                )
            for event_id, kind, payload in events:
                self._insert_event(conn, key, event_id, kind, payload)
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (key.id,),
            ).fetchone()
            receipt = self._receipt(row)
            conn.commit()
            return receipt

    def _insert_event(self, conn, key, event_id, kind, payload) -> None:
        _identifier(event_id)
        _identifier(kind)
        text = encoded(dict(payload))
        existing = conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if existing:
            self._event(existing)
            if (existing["operation_id"], existing["kind"], existing["payload_json"]) != (
                key.id,
                kind,
                text,
            ):
                raise ReceiptError("conflicting event identity")
            return
        conn.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?)",
            (
                event_id,
                key.id,
                kind,
                text,
                digest(text),
                _now(),
            ),
        )

    def record_event(self, key, event_id, kind, payload) -> None:
        self._check_key(key)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_event(conn, key, event_id, kind, payload)
            conn.commit()

    @staticmethod
    def _event(row) -> dict:
        if digest(row["payload_json"]) != row["payload_hash"]:
            raise ReceiptError("corrupt event payload")
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise ValueError("event must be an object")
        except ValueError as exc:
            raise ReceiptError("corrupt event JSON") from exc
        return {
            "event_id": row["event_id"],
            "operation_id": row["operation_id"],
            "kind": row["kind"],
            "payload": payload,
        }

    def events(self) -> list[dict]:
        with self._connection() as conn:
            return [self._event(row) for row in conn.execute("SELECT * FROM events ORDER BY rowid")]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def merge_events(left: list[dict], right: list[dict]) -> list[dict]:
    """Keyed idempotent reduction; legacy events without IDs retain append behavior."""
    result, seen = [], {}
    for event in [*left, *right]:
        if "event_id" not in event:
            result.append(event)
            continue
        event_id = event["event_id"]
        _identifier(event_id)
        text = encoded(event)
        if event_id in seen:
            if seen[event_id] != text:
                raise ReceiptError("conflicting reduced event identity")
        else:
            seen[event_id] = text
            result.append(event)
    return result


def attempt_identity(state: Mapping[str, Any]) -> str:
    return state.get("attempt_id") or encoded(
        [
            state.get("fix_attempts", 0),
            bool(state.get("escalated")),
            state.get("review_revisions", 0),
            state.get("implement_continuations", 0),
        ]
    )


@dataclass(frozen=True)
class CallContext:
    journal_path: str
    run_id: str
    context_hash: str
    unit_id: str
    attempt_id: str

    def journal(self) -> OperationJournal:
        return OperationJournal(self.journal_path, self.run_id, self.context_hash)

    def key(self, kind: str) -> OperationKey:
        return OperationKey(self.run_id, self.unit_id, self.attempt_id, kind)


def call_context_for(
    state: Mapping[str, Any],
    *,
    unit_id: str,
    attempt_id: str | None = None,
) -> CallContext | None:
    if state.get("run_context") is None:
        return None  # Legacy/library-only node seams; the production CLI always supplies v2.
    from blacksmith.run_context import context_from_state

    context = context_from_state(dict(state))
    journal = OperationJournal.for_context(context)
    return CallContext(
        str(journal.path),
        context.run_id,
        journal.context_hash,
        unit_id,
        attempt_id or attempt_identity(state),
    )
