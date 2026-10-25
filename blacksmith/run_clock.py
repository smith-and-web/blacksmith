"""Persistent active execution time, excluding approval and offline waiting.

Before allowing execution, reserve a small time slice in the critical journal.
Normal closure records measured monotonic elapsed time. A killed owner's last
slice stays conservatively charged in full; a restart never invents its end time
or charges the intervening offline pause. One in-process owner serves parallel
workers; graph invocation boundaries release it before human approval.
"""

from __future__ import annotations

import math
import signal
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from blacksmith.receipts import OperationKey, ReceiptError, digest, encoded

QUANTUM_SECONDS = 5.0
CLEANUP_ALLOWANCE_SECONDS = 5.0
_active = ContextVar("blacksmith_execution_clock", default=None)
_owners = {}
_owners_lock = threading.RLock()


class RunDeadline(ReceiptError):
    """No further execution is allowed; retained recovery inspection remains available."""


class RunCancelled(ReceiptError):
    """The active execution was cancelled; every owned worker must stop."""


def _policy(context):
    return OperationKey(context.run_id, "__run__", "active-clock-v1", "clock.policy")


def initialize(context):
    body = {"version": 1, "max_run_seconds": context.execution_config().limits.max_run_seconds,
            "quantum_seconds": QUANTUM_SECONDS, "cleanup_allowance_seconds":
            CLEANUP_ALLOWANCE_SECONDS}
    context.journal().intent(_policy(context), body)
    context.journal().finish(_policy(context), "completed", {"sha256": digest(encoded(body))})


def used(context):
    """Validate original policy and all measured/conservatively charged slices."""
    journal = context.journal()
    policy = journal.get(_policy(context))
    expected = {"version": 1,
        "max_run_seconds": context.execution_config().limits.max_run_seconds,
        "quantum_seconds": QUANTUM_SECONDS,
        "cleanup_allowance_seconds": CLEANUP_ALLOWANCE_SECONDS}
    if (not policy or policy.status != "completed" or policy.intent != expected
            or policy.outcome != {"sha256": digest(encoded(expected))}):
        raise ReceiptError("legacy_clock_requires_manual_recovery: missing/corrupt active clock")
    charged, incomplete = 0.0, False
    for row in journal.operations():
        if row.key.kind != "clock.slice":
            continue
        data = row.intent
        try:
            reserve = data['reserved_seconds']
            if (set(data) != {'owner', 'sequence', 'reserved_seconds', 'policy_id'}
                    or type(data['sequence']) is not int or data['sequence'] < 0
                    or row.key != OperationKey(context.run_id, '__run__',
                        f"{data['owner']}:{data['sequence']}", 'clock.slice')
                    or data['policy_id'] != policy.key.id
                    or type(reserve) not in {int, float} or not math.isfinite(reserve)
                    or not 0 < reserve <= min(QUANTUM_SECONDS, expected['max_run_seconds'])):
                raise ValueError('foreign clock slice')
            if row.status == 'intent':
                active = _owners.get(str(context.directory))
                if (active is not None and active.error is None
                        and active.open_slice and active.key == row.key):
                    charged += time.monotonic() - active.slice_started
                else:
                    # The failed owner cannot record its stop/cleanup time. Charge
                    # its full slice plus the bounded TERM grace, without inventing
                    # a timestamp or counting the intervening offline pause.
                    charged += reserve + CLEANUP_ALLOWANCE_SECONDS
                    incomplete = True
            else:
                elapsed = row.outcome['elapsed_seconds']
                if (row.status != 'completed' or set(row.outcome) != {'elapsed_seconds'}
                        or type(elapsed) not in {int, float} or not math.isfinite(elapsed)
                        or elapsed < 0):
                    raise ValueError('invalid elapsed execution')
                charged += elapsed
        except (ValueError, TypeError, KeyError) as exc:
            raise ReceiptError('corrupt active execution clock slice') from exc
    return charged, incomplete


class _Clock:
    def __init__(self, context):
        self.context, self.journal = context, context.journal()
        elapsed, self.incomplete = used(context)
        remaining = context.execution_config().limits.max_run_seconds - elapsed
        if remaining <= 0:
            raise RunDeadline('run execution deadline exhausted; inspect retained artifacts')
        self.deadline = time.monotonic() + remaining
        self.owner, self.sequence = str(uuid4()), 0
        self.cancelled, self.stop = threading.Event(), threading.Event()
        self.cancellation_reason = None
        self.error, self.references = None, 1
        self.open_slice, self.finish_elapsed = False, None
        self._reserve()
        self.thread = threading.Thread(target=self._tick, name='blacksmith-clock', daemon=True)
        self.thread.start()

    @property
    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())

    def check(self):
        if self.error:
            raise self.error
        if self.cancelled.is_set() and self.cancellation_reason:
            raise RunCancelled('run execution cancelled: ' + self.cancellation_reason)
        if self.cancelled.is_set() or self.remaining <= 0:
            raise RunDeadline('run execution deadline exhausted; inspect retained artifacts')
        return self.remaining

    def cancel(self, reason):
        if not self.cancelled.is_set():
            self.cancellation_reason = reason
            self.cancelled.set()

    def _reserve(self):
        self.slice_started = time.monotonic()
        self.reserved = min(QUANTUM_SECONDS, self.check())
        self.key = OperationKey(self.context.run_id, '__run__',
                               f'{self.owner}:{self.sequence}', 'clock.slice')
        self.journal.intent(self.key, {'owner': self.owner, 'sequence': self.sequence,
            'reserved_seconds': self.reserved, 'policy_id': _policy(self.context).id})
        self.open_slice, self.finish_elapsed = True, None

    def _finish(self):
        if not self.open_slice:
            return
        if self.finish_elapsed is None:
            self.finish_elapsed = time.monotonic() - self.slice_started
        self.journal.finish(self.key, 'completed', {
            'elapsed_seconds': self.finish_elapsed})
        self.open_slice = False

    def _tick(self):
        try:
            while not self.stop.wait(.05):
                if self.cancelled.is_set():
                    return
                if self.remaining <= 0:
                    self.cancelled.set()
                    return
                if time.monotonic() - self.slice_started >= self.reserved:
                    # Commit the next reservation before sealing the previous
                    # slice. Any critical rotation failure then leaves durable
                    # uncertainty; it can never make unmeasured execution free.
                    started = time.monotonic()
                    reserve = min(QUANTUM_SECONDS, self.check())
                    following = OperationKey(self.context.run_id, '__run__',
                        f'{self.owner}:{self.sequence + 1}', 'clock.slice')
                    self.journal.intent(following, {'owner': self.owner,
                        'sequence': self.sequence + 1, 'reserved_seconds': reserve,
                        'policy_id': _policy(self.context).id})
                    self.finish_elapsed = started - self.slice_started
                    self._finish()
                    self.sequence += 1
                    self.key, self.slice_started, self.reserved = following, started, reserve
                    self.open_slice, self.finish_elapsed = True, None
        except (RunDeadline, RunCancelled):
            self.cancelled.set()
        except ReceiptError as exc:
            self.error = exc
            self.cancelled.set()

    def close(self):
        self.stop.set()
        self.thread.join()
        if self.error:
            raise self.error
        self._finish()


def current():
    return _active.get()


def cancel_current(reason, *, context=None):
    owner = current() or (_owners.get(str(context.directory)) if context is not None else None)
    if owner is not None:
        owner.cancel(reason)


@contextmanager
def failure_scope(context=None):
    owner = current() or (_owners.get(str(context.directory)) if context is not None else None)
    if owner is not None:
        owner.check()
    try:
        yield
    except BaseException:
        cancel_current('node failed', context=context)
        raise


@contextmanager
def cancellation_signals(owner):
    """Notify SDK/native workers before LangGraph waits for its thread executor."""
    if owner is None or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}

    def interrupt(number, frame):
        owner.cancel('signal ' + signal.Signals(number).name)
        handler = previous[number]
        if callable(handler):
            handler(number, frame)
        raise RunCancelled('run execution cancelled by ' + signal.Signals(number).name)

    try:
        for number, handler in previous.items():
            if handler != signal.SIG_IGN:
                signal.signal(number, interrupt)
        yield
    finally:
        for number, handler in previous.items():
            if signal.getsignal(number) is interrupt:
                signal.signal(number, handler)


@contextmanager
def retention_scope():
    """Bounded recovery capture remains available after active execution stops."""
    token = _active.set(None)
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def execution_scope(context):
    if context is None:
        yield None
        return
    active = current()
    if active is not None:
        if active.context != context:
            raise ReceiptError('foreign nested active execution clock')
        active.check()
        yield active
        return
    identity = str(context.directory)
    with _owners_lock:
        owner = _owners.get(identity)
        if owner is None:
            owner = _owners[identity] = _Clock(context)
        else:
            owner.check()
            owner.references += 1
    token = _active.set(owner)
    try:
        yield owner
    finally:
        _active.reset(token)
        with _owners_lock:
            owner.references -= 1
            if owner.references == 0:
                del _owners[identity]
                owner.close()
