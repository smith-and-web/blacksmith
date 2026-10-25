"""Optional outages stay visible without changing actual CLI/Git/gate/ledger effects."""

import pytest
from langgraph.store.base import SearchOp
from langgraph.store.sqlite import SqliteStore
from test_publication_integrity import setup as install_pipeline
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli
from blacksmith.budget import BudgetLedger
from blacksmith.run_context import context_from_state
from blacksmith.state import Status

PRIVATE_DETAIL = "PRIVATE_OPTIONAL_DETAIL"


@pytest.mark.parametrize("fault", [
    "memory_store", "memory_lookup", "live_store", "live_events",
    "metrics_open", "metrics_write", "progress_display",
])
def test_optional_outages_are_visible_without_changing_run_effects(
    project, monkeypatch, capsys, fault,
):
    calls, remote, _ = install_pipeline(project, monkeypatch)

    def fail_open(db_path):
        raise OSError(PRIVATE_DETAIL)

    def fail_event(self, thread_id, kind, payload=None, *, ts=None):
        raise OSError(PRIVATE_DETAIL)

    def fail_record(store, snapshot, *, thread_id, prd_path=None, started_at=None,
                    ended_at=None, transcripts_dir=None, approval_wait_s=0.0):
        raise OSError(PRIVATE_DETAIL)

    def fail_progress(self, node):
        raise OSError(PRIVATE_DETAIL)

    if fault == "memory_store":
        monkeypatch.setattr(cli, "build_store", fail_open)
    elif fault == "memory_lookup":
        original = SqliteStore.batch

        def fail_search(self, ops):
            ops = tuple(ops)
            if any(isinstance(op, SearchOp) for op in ops):
                raise OSError(PRIVATE_DETAIL)
            return original(self, ops)
        monkeypatch.setattr(SqliteStore, "batch", fail_search)
    elif fault == "live_store":
        monkeypatch.setattr(cli, "build_live_store", fail_open)
    elif fault == "live_events":
        monkeypatch.setattr(cli.LiveSink, "emit", fail_event)
    elif fault == "metrics_open":
        monkeypatch.setattr(cli, "build_metrics_store", fail_open)
    elif fault == "metrics_write":
        monkeypatch.setattr(cli, "record_run", fail_record)
    else:
        monkeypatch.setattr(cli.Renderer, "progress", fail_progress)

    assert cli.main([str(project[2]), "--config", str(project[1]), "--thread-id", "publication",
                     "--approve", "plan,pr"]) == 0
    state = saved_values(project[1], "publication")
    assert state["status"] == Status.DONE and state["publication_receipt"]
    assert len(remote.effects()) == 2 and len(calls) == len(state["cost_events"]) == 4
    balance = BudgetLedger(context_from_state(state)).balance()
    assert balance.known_cost_usd == balance.charged_usd == .04
    assert balance.reserved_usd == 0 and not balance.accounting_incomplete
    result = capsys.readouterr()
    feature = {"memory_store": "memory store", "memory_lookup": "memory lookup",
               "live_store": "live store", "live_events": "live events",
               "metrics_open": "metrics", "metrics_write": "metrics",
               "progress_display": "progress display"}[fault]
    warning = f"optional {feature} unavailable (OSError)"
    assert 1 <= result.err.count(warning) <= 3
    assert PRIVATE_DETAIL not in result.out + result.err


def test_direct_optional_lookup_returns_empty_with_a_safe_diagnostic(capsys):
    from blacksmith.memory import recent_lessons

    class BrokenStore:
        def search(self, namespace_prefix, *, limit):
            raise OSError(PRIVATE_DETAIL)

    assert recent_lessons(BrokenStore(), ("example", "repo"), 5) == []
    output = capsys.readouterr()
    assert "optional memory lookup unavailable (OSError)" in output.err
    assert PRIVATE_DETAIL not in output.out + output.err


def test_optional_warning_budget_is_per_invocation_and_shared_with_workers(capsys):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    from blacksmith.observability import observation_scope, warn_optional

    def work():
        for _ in range(20):
            warn_optional("memory lookup", OSError(PRIVATE_DETAIL))

    for _ in range(2):
        with observation_scope():
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(copy_context().run, work) for _ in range(2)]
                for future in futures:
                    future.result()
    output = capsys.readouterr()
    assert output.err.count("optional memory lookup unavailable (OSError)") == 2
    assert PRIVATE_DETAIL not in output.out + output.err
