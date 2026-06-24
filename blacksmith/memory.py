"""Persistent long-term memory store for blacksmith (WU-STORE-WIRING).

This is a SEPARATE, additive persistence channel from the per-thread SQLite
checkpointer (``graph.build_checkpointer``): the checkpointer holds per-thread run
state and powers ``resume``; this Store holds long-term, cross-thread memory scoped
per target repo. The two never share a database file and never interfere.

The Store is the stock ``langgraph.store.sqlite.SqliteStore`` (ships in the already
pinned ``langgraph-checkpoint-sqlite``). Its semantic/vector search is deliberately
NOT enabled — that would require an embeddings provider blacksmith must not carry, so
memory retrieval is non-semantic (namespace search / get by key) only.

Memory is purely additive context plus an audit record: a run with no store, or with
an empty store, behaves exactly as today.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.config import get_store
from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore

from blacksmith.contract import PRDContract

# Lessons live in a sub-namespace under the repo namespace so they never collide with
# any other memory written for the same repo, while still being scoped per target repo.
_LESSONS = "lessons"


def build_store(db_path: str | Path) -> SqliteStore:
    """Open a file-backed SQLite memory Store (mirrors ``graph.build_checkpointer``).

    Opens a sqlite3 connection at ``db_path``, constructs a ``SqliteStore`` over it, and
    calls ``.setup()`` so the schema exists. A fresh instance pointed at the same path
    re-attaches to the existing memory, which is how memory persists across runs. No
    embeddings/vector index is configured (non-semantic retrieval only).
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None puts the connection in autocommit mode: SqliteStore issues
    # its own explicit BEGIN/COMMIT, so Python's implicit transaction handling must be
    # off (mirrors SqliteStore.from_conn_string).
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    store = SqliteStore(conn)
    store.setup()
    return store


def repo_namespace(contract: PRDContract) -> tuple[str, ...]:
    """A stable namespace tuple derived from the contract, scoping memory per target repo.

    Combines the contract's ``component`` and ``primary_target_repo`` so memory written
    for one target repo is never read by a run against a different one.
    """
    return (contract.component, contract.primary_target_repo)


def current_store() -> BaseStore | None:
    """The Store bound to the running graph, or ``None`` when none is configured.

    Wraps ``langgraph.config.get_store`` so a node can opt into memory without ever
    failing: it returns ``None`` both when the graph was compiled with ``store=None``
    and when called outside any runnable context (e.g. a node invoked directly in a
    unit test). Memory access is purely optional — callers degrade to today's behaviour.
    """
    try:
        return get_store()
    except Exception:
        return None


def record_lesson(store: BaseStore, namespace: tuple[str, ...], lesson: dict) -> None:
    """Persist one concise gate-failure ``lesson`` under ``namespace`` (per repo).

    The lesson is keyed by its ``unit_id`` (latest failure per unit wins) inside a
    ``lessons`` sub-namespace of the repo namespace. It is a purely additive audit
    record: writing it never changes the run's control flow.
    """
    key = str(lesson.get("unit_id") or "lesson")
    store.put((*namespace, _LESSONS), key, lesson)


def recent_lessons(
    store: BaseStore, namespace: tuple[str, ...], limit: int
) -> list[dict]:
    """Up to ``limit`` lessons for this repo, most-recent first.

    Uses the Store's NON-semantic namespace search (no ``query=``), which orders by
    ``updated_at`` descending — so the freshest lessons come first. Returns the stored
    lesson dicts directly.
    """
    items = store.search((*namespace, _LESSONS), limit=limit)
    return [item.value for item in items]
