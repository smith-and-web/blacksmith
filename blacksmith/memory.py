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

from langgraph.store.sqlite import SqliteStore

from blacksmith.contract import PRDContract


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
