"""Unknown usage and overruns remain visible in reports and migrated metrics."""

import io
import json
import sqlite3
from types import SimpleNamespace

from test_budget_admission import key, ledger, result
from test_run_context import project as project

from blacksmith.accounting import clock_line, cost_line, snapshot
from blacksmith.cli import _report
from blacksmith.metrics import build_metrics_store, record_run
from blacksmith.render import Renderer


def test_unknown_measured_usage_is_not_reported_as_a_free_or_exact_total(project):
    context, budget = ledger(project)
    call = key(context, 'unknown')
    grant = budget.reserve(call)
    result(context, call, None, status='unknown')
    budget.settle(grant)
    data = snapshot(context)
    assert data['reported_usd'] == 0 and data['charged_usd'] == 5
    assert data['accounting_incomplete'] and data['remaining_usd'] == 5
    text = cost_line(data)
    assert 'reported $0.00' in text and 'charged $5.00' in text
    assert 'accounting incomplete' in text and 'total cost: $0.00' not in text
    out = io.StringIO()
    _report(SimpleNamespace(values=context.initial_state()), Renderer(out_stream=out, plain=True))
    assert 'accounting incomplete' in out.getvalue() and 'execution time:' in out.getvalue()


def test_actual_overrun_remains_visible_as_measured_cost_and_excess(project):
    context, budget = ledger(project)
    call = key(context, 'overrun')
    grant = budget.reserve(call)
    result(context, call, 5.25)
    budget.settle(grant)
    data = snapshot(context)
    assert data['reported_usd'] == data['charged_usd'] == 5.25
    assert data['overrun_usd'] == .25 and data['unacknowledged_overrun']
    assert 'total cost: $5.25' in cost_line(data)
    assert 'reservation overrun $0.25' in cost_line(data)
    assert data['execution_seconds'] > 0 and 'remaining' in clock_line(data)


def test_metrics_migration_keeps_unknown_total_null_and_separate_conservative_accounting(project):
    context, budget = ledger(project)
    call = key(context, 'unknown')
    grant = budget.reserve(call)
    result(context, call, None, status='interrupted')
    budget.settle(grant)
    path = project[0].parent / 'old-metrics.sqlite'
    with sqlite3.connect(path) as old:
        old.execute('CREATE TABLE run_metrics(thread_id TEXT PRIMARY KEY,total_cost REAL)')
        old.execute("INSERT INTO run_metrics VALUES('old', 2.5)")
    store = build_metrics_store(path)
    try:
        record_run(store, SimpleNamespace(values=context.initial_state()), thread_id='current',
                   prd_path=project[2], started_at=0, ended_at=100, approval_wait_s=50)
        columns = {row[1] for row in store.execute('PRAGMA table_info(run_metrics)')}
        assert 'accounting' in columns
        row = store.execute("SELECT total_cost,duration_s,accounting FROM run_metrics "
                            "WHERE thread_id='current'").fetchone()
        assert row[0] is None
        data = json.loads(row[2])
        assert data['accounting_incomplete'] and data['reported_usd'] == 0
        assert data['charged_usd'] == 5 and row[1] == data['execution_seconds']
        old = store.execute("SELECT total_cost FROM run_metrics WHERE thread_id='old'").fetchone()
        assert old[0] == 2.5
    finally:
        store.close()
