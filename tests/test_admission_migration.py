"""Old/corrupt policies refuse before SDK/native work or journal mutation."""

import sqlite3

import pytest
from test_integration_integrity import transport
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli
from blacksmith.receipts import digest, encoded
from blacksmith.run_context import context_from_state


@pytest.mark.parametrize('policy', ['budget', 'clock'])
@pytest.mark.parametrize('fault', ['missing', 'corrupt'])
def test_policy_migration_refuses_before_native_executor_or_journal_mutation(
    project, monkeypatch, capsys, policy, fault,
):
    calls = transport(monkeypatch)
    args = ['--config', str(project[1]), '--thread-id', 'policy', '--approve', '']
    assert cli.main([str(project[2]), *args]) == 1
    before = saved_values(project[1], 'policy')
    context = context_from_state(before)
    kind = policy + '.policy'
    row = next(r for r in context.journal().operations() if r.key.kind == kind)
    with sqlite3.connect(context.journal().path) as conn:
        if fault == 'missing':
            conn.execute('DELETE FROM operations WHERE operation_id=?', (row.key.id,))
        else:
            payload = row.intent
            payload['max_call_cost_usd' if policy == 'budget' else 'max_run_seconds'] = 999999
            text = encoded(payload)
            outcome = encoded({'sha256': digest(text)})
            conn.execute('UPDATE operations SET intent_json=?,intent_hash=?,outcome_json=?,'
                         'outcome_hash=? WHERE operation_id=?',
                         (text, digest(text), outcome, digest(outcome), row.key.id))
    operations, events = context.journal().operations(), context.journal().events()
    count = len(calls)
    monkeypatch.setattr(cli, 'Executor', lambda *a, **k: pytest.fail('constructed executor'))
    monkeypatch.setattr('blacksmith.run_context.git_read',
                        lambda *a, **k: pytest.fail('native observation before policy refusal'))
    assert cli.main(['resume', *args]) == 1
    expected = 'legacy_admission' if policy == 'budget' else 'legacy_clock'
    assert expected in capsys.readouterr().err
    assert context.journal().operations() == operations and context.journal().events() == events
    assert saved_values(project[1], 'policy') == before and len(calls) == count
