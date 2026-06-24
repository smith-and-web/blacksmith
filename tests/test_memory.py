"""Tests for the persistent memory Store wiring (WU-STORE-WIRING).

Test contract: ``build_store`` opens a persistent ``SqliteStore`` that supports a
put/get/search roundtrip (search by namespace prefix, non-semantic — no ``query=``);
``repo_namespace`` derives a stable per-target-repo namespace from the contract;
``compile_graph`` accepts ``store=None`` (unchanged) and ``store=<SqliteStore>`` and a
fresh run still reaches its terminal state with empty memory (no regression); the
checkpointer/resume path is untouched (the Store is a separate, additive channel).
"""

from pathlib import Path

from langgraph.store.sqlite import SqliteStore
from langgraph.types import Command

from blacksmith.contract import parse_prd
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.memory import build_store, repo_namespace
from blacksmith.state import Status

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_PRD = REPO_ROOT / "blacksmith-v0-prd.md"


def test_build_store_returns_persistent_sqlite_store(tmp_path):
    store = build_store(tmp_path / "store.sqlite")
    assert isinstance(store, SqliteStore)
    store.conn.close()


def test_build_store_creates_parent_directory(tmp_path):
    # Mirrors build_checkpointer: nested parent dirs are created on demand.
    db = tmp_path / "nested" / "dir" / "store.sqlite"
    store = build_store(db)
    assert db.exists()
    store.conn.close()


def test_put_get_search_roundtrip(tmp_path):
    store = build_store(tmp_path / "store.sqlite")
    namespace = ("blacksmith", "smith-and-web/blacksmith")
    store.put(namespace, "note-1", {"text": "remember this"})

    # get by key returns the written record.
    item = store.get(namespace, "note-1")
    assert item is not None
    assert item.value == {"text": "remember this"}

    # search by namespace prefix, NON-semantic (no query=), returns the written record.
    results = store.search(namespace)
    assert [r.key for r in results] == ["note-1"]
    assert results[0].value == {"text": "remember this"}
    store.conn.close()


def test_store_persists_across_reopen(tmp_path):
    db = tmp_path / "store.sqlite"
    namespace = ("blacksmith", "repo")
    store1 = build_store(db)
    store1.put(namespace, "k", {"v": 1})
    store1.conn.close()

    # A fresh instance on the same path re-attaches to the existing memory.
    store2 = build_store(db)
    assert store2.get(namespace, "k").value == {"v": 1}
    store2.conn.close()


def test_repo_namespace_is_stable_and_scoped():
    contract = parse_prd(VENDORED_PRD).contract
    ns = repo_namespace(contract)
    assert ns == (contract.component, contract.primary_target_repo)
    assert isinstance(ns, tuple)
    # Stable across calls.
    assert repo_namespace(contract) == ns


def test_compile_graph_accepts_store_none(tmp_path):
    saver = build_checkpointer(tmp_path / "c.sqlite")
    compiled = compile_graph(saver, store=None)
    assert compiled is not None
    saver.conn.close()


def _fresh_run_reaches_terminal(saver, store) -> tuple:
    graph = compile_graph(saver, store=store)
    cfg = {"configurable": {"thread_id": "store-wiring"}}
    graph.invoke({"status": Status.PENDING}, cfg)  # halts at approve_plan
    graph.invoke(Command(resume=True), cfg)  # approve; run to END
    return graph.get_state(cfg).next


def test_fresh_run_reaches_terminal_state_with_empty_store(tmp_path):
    # With an attached but EMPTY store, a fresh run still reaches its terminal state —
    # memory is purely additive and never changes which nodes fire (no regression).
    saver = build_checkpointer(tmp_path / "c.sqlite")
    store = build_store(tmp_path / "store.sqlite")
    assert _fresh_run_reaches_terminal(saver, store) == ()  # reached END
    saver.conn.close()
    store.conn.close()


def test_fresh_run_reaches_terminal_state_without_store(tmp_path):
    # store=None behaves exactly as before the Store existed.
    saver = build_checkpointer(tmp_path / "c.sqlite")
    assert _fresh_run_reaches_terminal(saver, None) == ()  # reached END
    saver.conn.close()
