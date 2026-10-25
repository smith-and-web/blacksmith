"""Persisted execution clock; real threads/SQLite, no provider transport."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_run_context import create
from test_run_context import project as project

from blacksmith.receipts import OperationKey, ReceiptError
from blacksmith.run_clock import RunDeadline, current, execution_scope, initialize, used


def setup(project, seconds=5):
    project[1].write_text(project[1].read_text() + f'[limits]\nmax_run_seconds={seconds}\n')
    context = create(project)
    initialize(context)
    return context


def test_clock_excludes_pause_and_nested_parallel_work_counts_wall_time_once(project):
    context = setup(project)
    baseline = used(context)[0]
    barrier = Barrier(3)

    def active(_):
        with execution_scope(context) as clock:
            assert current() is clock
            barrier.wait()
            time.sleep(.15)
            with execution_scope(context) as nested:
                assert nested is clock
            return clock.owner
    with ThreadPoolExecutor(max_workers=3) as pool:
        owners = list(pool.map(active, range(3)))
    assert len(set(owners)) == 1
    first, incomplete = used(context)
    assert .14 <= first - baseline < .4 and incomplete is False
    time.sleep(.3)  # Approval/offline wait: no active scope and no execution debit.
    assert used(context) == (first, False)
    with execution_scope(context):
        time.sleep(.05)
    assert .04 <= used(context)[0] - first < .2


def test_unfinished_crash_slice_is_charged_once_without_inventing_pause_time(project):
    context = setup(project, 20)
    baseline = used(context)[0]
    policy = next(r for r in context.journal().operations() if r.key.kind == 'clock.policy')
    key = OperationKey(context.run_id, '__run__', 'old-owner:0', 'clock.slice')
    context.journal().intent(key, {'owner': 'old-owner', 'sequence': 0,
        'reserved_seconds': 5.0, 'policy_id': policy.key.id})
    original = context.journal().get(key)
    time.sleep(.1)
    with execution_scope(context) as clock:
        assert 9.8 - baseline < clock.remaining <= 10 - baseline
        assert clock.incomplete
    assert context.journal().get(key) == original
    assert 10 <= used(context)[0] - baseline < 10.2 and used(context)[1]


def test_deadline_is_persistent_and_cannot_reset_on_resume(project):
    context = setup(project, 5)
    policy = next(r for r in context.journal().operations() if r.key.kind == 'clock.policy')
    key = OperationKey(context.run_id, '__run__', 'previous:0', 'clock.slice')
    baseline = used(context)[0]
    context.journal().intent(key, {'owner': 'previous', 'sequence': 0,
        'reserved_seconds': 5.0, 'policy_id': policy.key.id})
    context.journal().finish(key, 'completed', {'elapsed_seconds': 4.85 - baseline})
    with execution_scope(context) as clock:
        time.sleep(.2)
        assert clock.cancelled.is_set()
        with pytest.raises(RunDeadline):
            clock.check()
    original = context.journal().operations()
    with pytest.raises(RunDeadline):
        with execution_scope(context):
            pytest.fail('execution after accumulated deadline')
    assert context.journal().operations() == original


def test_missing_clock_policy_refuses_without_inventing_old_timestamps(project):
    context = create(project)
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute("DELETE FROM operations WHERE kind='clock.policy'")
    original = context.journal().operations()
    with pytest.raises(ReceiptError, match='legacy_clock'):
        used(context)
    assert context.journal().operations() == original


def test_clock_critical_reservation_failure_prevents_active_work(project):
    context = setup(project)
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute("CREATE TRIGGER fail_clock BEFORE INSERT ON operations "
                     "WHEN NEW.kind='clock.slice' BEGIN SELECT RAISE(ABORT,'failed'); END")
    with pytest.raises(ReceiptError, match='critical journal'):
        with execution_scope(context):
            pytest.fail('unaccounted active work')
