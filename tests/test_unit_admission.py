"""The next auto unit cannot start paid work on stale worker spend totals."""

from collections import Counter

import pytest
from test_integration_integrity import configure
from test_publication_integrity import setup as install
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli
from blacksmith.budget import BudgetLedger
from blacksmith.run_context import context_from_state


@pytest.mark.parametrize('parallel', [False, True])
def test_next_unit_budget_denial_preserves_work_and_budget_only_resume_does_not_replay_siblings(
    project, monkeypatch, parallel,
):
    configure(project, sequential=not parallel)
    cap = .02 if parallel else .04
    project[1].write_text(project[1].read_text() + f'[limits]\nmax_run_cost_usd={cap}\n'
                           'max_implement_continuations=0\n')
    calls, remote, _ = install(project, monkeypatch)
    remote.log.touch()
    args = ['--config', str(project[1]), '--thread-id', 'publication', '--approve', 'plan,pr']
    assert cli.main([str(project[2]), *args]) == 1
    before = saved_values(project[1], 'publication')
    context = context_from_state(before)
    failed = {'A', 'B'} if parallel else {'B'}
    for identity in failed:
        outcome = before['unit_outcomes'][identity]
        assert not outcome['ok'] and outcome['failed_stage'] == 'implement'
        assert outcome['gate_receipt'] is None
        assert outcome['implement_continuations'] == 0
    counts = Counter((c[0], c[1]) for c in calls)
    assert counts[('implement', 'B')] == 0
    assert counts[('implement', 'A')] == int(not parallel)
    assert remote.effects() == [] and BudgetLedger(context).balance().remaining_usd == 0
    original = [r for r in context.journal().operations() if r.key.kind.startswith('model.')]
    assert cli.main(['resume', *args, '--add-budget-usd', '1']) == 0
    after = saved_values(project[1], 'publication')
    assert after['publication_receipt'] and len(remote.effects()) == 2
    for identity in ('A', 'B'):
        assert after['unit_outcomes'][identity]['ok']
        assert after['unit_outcomes'][identity]['implement_continuations'] == 0
    if not parallel:
        assert after['unit_outcomes']['A'] == before['unit_outcomes']['A']
    assert all(context.journal().get(r.key) == r for r in original)
    counts = Counter((c[0], c[1]) for c in calls)
    assert counts[('implement', 'A')] == counts[('implement', 'B')] == 1
    assert len(calls) == 7
