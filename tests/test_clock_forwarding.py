"""Actual CLI clock boundaries and native remaining-deadline forwarding."""

import sys
import time

import pytest
from test_publication_integrity import setup as install
from test_run_context import create, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.commands import CommandError, command_scope, run_command
from blacksmith.run_clock import current, used
from blacksmith.run_context import context_from_state


def test_real_cli_approval_wait_has_no_execution_clock_and_saved_limits_reach_sdk(
    project, monkeypatch,
):
    project[1].write_text(project[1].read_text() + '[limits]\nmax_call_cost_usd=0.5\n'
        'model_timeout_s=2\nmax_run_seconds=100\n')
    calls, remote, _ = install(project, monkeypatch)
    original = cli._resolve_approver
    waits = []

    def approver(args, renderer):
        decide = original(args, renderer)

        def wait(payload, state):
            context = context_from_state(state)
            before = used(context)
            assert current() is None
            time.sleep(.2)
            assert used(context) == before
            waits.append(payload['gate'])
            return decide(payload, state)
        return wait
    monkeypatch.setattr(cli, '_resolve_approver', approver)
    assert cli.main([str(project[2]), '--config', str(project[1]), '--thread-id', 'publication',
                     '--approve', 'plan,pr']) == 0
    state = saved_values(project[1], 'publication')
    context = context_from_state(state)
    assert waits == ['plan', 'pr']
    assert len(calls) == 4 and all(c[3].max_budget_usd == .5 for c in calls)
    assert len(remote.effects()) == 2
    charged, incomplete = used(context)
    assert 0 < charged < 100 and incomplete is False
    assert current() is None


def test_native_command_uses_remaining_run_time_and_reaps_before_return(project):
    project[1].write_text(project[1].read_text() + '[limits]\nmax_run_seconds=2\n')
    context = create(project)
    with command_scope(context, stage='deadline-test'):
        with pytest.raises(CommandError) as stopped:
            run_command([sys.executable, '-c', 'import time; time.sleep(20)'], timeout=100)
    result = stopped.value.result
    assert result.timed_out or result.cancelled
    rows = [r for r in context.journal().operations()
            if r.key.kind.startswith('command:') and r.intent['stage'] == 'deadline-test']
    assert len(rows) == 1 and 0 < rows[0].intent['timeout_s'] < 2
    assert rows[0].outcome['returncode'] is not None
    assert used(context)[0] >= 2
