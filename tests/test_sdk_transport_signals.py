"""Permission errors are uncertainty until live membership is observed explicitly."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import blacksmith.sdk_transport as transport
from blacksmith.commands import CommandError


def deny_group(*args):
    raise PermissionError('injected signal permission denial')


def test_permission_denial_on_a_live_group_never_means_stopped(monkeypatch):
    group = os.getpgrp()
    assert transport._group_gone(group) is False  # This pytest process is a live member.
    monkeypatch.setattr(transport, 'os', SimpleNamespace(killpg=deny_group, kill=os.kill))
    with pytest.raises(PermissionError):
        transport._signal(group, 0, group=True)


def test_permission_denial_after_positive_empty_inventory_can_reconcile_a_reaped_group(monkeypatch):
    process = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
    assert process.wait(timeout=5) == 0
    assert transport._group_gone(process.pid) is True
    monkeypatch.setattr(transport, 'os', SimpleNamespace(killpg=deny_group, kill=os.kill))
    assert transport._signal(process.pid, 0, group=True) is False


def test_unavailable_process_inventory_cannot_reconcile_denied_cleanup(monkeypatch):
    monkeypatch.setattr(transport, 'os', SimpleNamespace(killpg=deny_group, kill=os.kill))

    def unavailable(argv, *, timeout, kind):
        assert argv == ['ps', '-A', '-o', 'pid=,pgid=,stat='] and timeout == 1
        raise CommandError('injected inventory failure')
    monkeypatch.setattr(transport, 'run_command', unavailable)
    with pytest.raises(CommandError, match='inventory failure'):
        transport._signal(os.getpgrp(), 0, group=True)
