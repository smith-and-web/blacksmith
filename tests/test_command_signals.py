"""Native cleanup reconciles EPERM only after observing no live group members."""

import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_run_context import create
from test_run_context import project as project

from blacksmith import commands
from blacksmith.commands import CommandError, command_scope, run_command
from blacksmith.receipts import ReceiptError


def deny_target(monkeypatch, target):
    original = os.killpg

    def killpg(pid, sig):
        if pid == target:
            raise PermissionError('injected group signal denial')
        return original(pid, sig)

    monkeypatch.setattr(os, 'killpg', killpg)


def reaped_group():
    process = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
    assert process.wait(timeout=5) == 0
    return process.pid


def test_reaped_group_denial_requires_positive_observation(monkeypatch):
    group = reaped_group()
    deny_target(monkeypatch, group)
    assert commands._group_signal(group, signal.SIGTERM) is False


def test_live_group_denial_cannot_be_reconciled_as_stopped(monkeypatch):
    group = os.getpgrp()
    deny_target(monkeypatch, group)
    with pytest.raises(PermissionError, match='injected group signal denial'):
        commands._group_signal(group, signal.SIGTERM)


def test_completed_command_seals_success_after_observing_reaped_group(project, monkeypatch):
    context = create(project)
    original = os.killpg
    denied = []

    def deny_first_group(pid, sig):
        if not denied:
            denied.append(pid)
        if pid == denied[0]:
            raise PermissionError('injected reaped native group')
        return original(pid, sig)

    monkeypatch.setattr(os, 'killpg', deny_first_group)
    with command_scope(context, stage='native_permission'):
        result = run_command([sys.executable, '-c', "print('COMPLETE')"], timeout=10)
    assert result.returncode == 0 and result.stdout == 'COMPLETE\n'
    rows = [r for r in context.journal().operations()
            if r.key.kind.startswith('command:') and ':native_permission:' in r.key.kind]
    assert len(rows) == 1 and rows[0].status == 'completed'
    assert rows[0].outcome['returncode'] == 0 and rows[0].outcome['error_type'] is None
    observations = [r for r in context.journal().operations()
                    if 'native_group_observe:' in r.key.kind]
    assert observations and all(r.status == 'completed' for r in observations)


def test_unavailable_inventory_never_reconciles_denial(monkeypatch):
    group = reaped_group()
    deny_target(monkeypatch, group)

    def unavailable(argv, *, timeout, kind):
        assert argv == ['ps', '-A', '-o', 'pid=,pgid=,stat='] and timeout == 1
        raise CommandError('inventory unavailable')

    monkeypatch.setattr(commands, 'run_command', unavailable)
    with pytest.raises(CommandError, match='inventory unavailable'):
        commands._group_signal(group, signal.SIGTERM)


def test_truncated_inventory_never_reconciles_denial(monkeypatch):
    group = reaped_group()
    deny_target(monkeypatch, group)

    def incomplete():
        raise CommandError('truncated inventory')

    result = SimpleNamespace(stdout='123 456 S\n', check_returncode=lambda: None,
                             require_complete_output=incomplete)
    monkeypatch.setattr(commands, 'run_command', lambda *args, **kwargs: result)
    with pytest.raises(CommandError, match='truncated inventory'):
        commands._group_signal(group, signal.SIGTERM)


@pytest.mark.parametrize('inventory', ['', 'malformed\n', '123 456\n'])
def test_incomplete_inventory_is_not_evidence_of_an_empty_group(monkeypatch, inventory):
    group = reaped_group()
    deny_target(monkeypatch, group)
    result = SimpleNamespace(stdout=inventory, check_returncode=lambda: None,
                             require_complete_output=lambda: None)
    monkeypatch.setattr(commands, 'run_command', lambda *args, **kwargs: result)
    with pytest.raises(ReceiptError, match='process observation'):
        commands._group_signal(group, signal.SIGTERM)


def test_inventory_cleanup_denial_cannot_recursively_spawn_observers(monkeypatch):
    group = reaped_group()
    original = subprocess.Popen
    observers = []

    def observe(*args, **kwargs):
        child = original(*args, **kwargs)
        observers.append(child)
        return child

    def denied(pid, sig):
        raise PermissionError('observer group also denied')

    monkeypatch.setattr(subprocess, 'Popen', observe)
    monkeypatch.setattr(os, 'killpg', denied)
    with pytest.raises(PermissionError, match='observer group also denied'):
        commands._group_signal(group, signal.SIGTERM)
    assert len(observers) == 1
    assert observers[0].poll() is not None
