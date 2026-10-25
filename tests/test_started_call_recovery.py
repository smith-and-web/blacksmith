"""Started budget/unknown calls and completed overruns through real recovery CLI."""

from collections import Counter
from dataclasses import replace

import pytest
from test_publication_integrity import setup as install
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli
from blacksmith.budget import BudgetLedger
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.run_context import context_from_state


def role_for(prompt, findings):
    if prompt.startswith('Produce a concise'):
        return 'plan'
    if prompt.startswith('You are reviewing a proposed implementation plan'):
        return 'critic'
    if prompt.startswith('Write a concise pull request'):
        return 'summary'
    return 'review' if findings else 'implement'


@pytest.mark.parametrize('role', ['plan', 'critic', 'implement', 'review', 'summary'])
@pytest.mark.parametrize('mode', ['budget', 'unknown', 'overrun'])
def test_started_call_resume_preserves_original_evidence_and_uses_only_required_allowance(
    project, monkeypatch, role, mode,
):
    panel = 3 if role == 'review' else 1
    continuations = 0 if mode == 'overrun' else 1
    project[1].write_text(project[1].read_text() + f'[limits]\n'
        f'max_implement_continuations={continuations}\nmax_review_revisions=0\n'
        f'[review]\npanel_size={panel}\n[critic]\nenabled={str(role == "critic").lower()}\n')
    calls, remote, _ = install(project, monkeypatch)
    remote.log.touch()
    original = Executor._collect
    seen = Counter()

    async def stopped_transport(self, prompt, options, *, emit_findings=False):
        current = role_for(prompt, emit_findings)
        seen[current] += 1
        if current == 'critic':
            assert set(options.tools) == {'Read', 'Glob', 'Grep'}
            calls.append(('critic', 'U1', prompt, options))
            result = ExecutorResult('{"approved":true,"critique":""}', options.model,
                                    False, 1, .01, {}, 'offline')
        else:
            result = await original(self, prompt, options, emit_findings=emit_findings)
        target = 2 if role == 'review' else 1
        if current == role and seen[current] == target:
            if mode == 'budget':
                return replace(result, is_error=True, error_kind='budget')
            if mode == 'unknown':
                return replace(result, terminal_received=False, error_kind='missing_terminal')
            return replace(result, cost_usd=options.max_budget_usd + .01)
        return result
    monkeypatch.setattr(Executor, '_collect', stopped_transport)
    args = ['--config', str(project[1]), '--thread-id', 'publication', '--approve', 'plan,pr']
    assert cli.main([str(project[2]), *args]) == 1
    before = saved_values(project[1], 'publication')
    context = context_from_state(before)
    original_models = [r for r in context.journal().operations() if r.key.kind.startswith('model.')]
    stopped = [r for r in original_models if r.key.kind == 'model.' + role][-1]
    assert stopped.status == {'budget': 'failed', 'unknown': 'unknown',
                              'overrun': 'completed'}[mode]
    assert remote.effects() == []
    count = len(calls)
    assert cli.main(['resume', *args]) == 1
    assert len(calls) == count
    flags = ['--add-budget-usd', '0.1'] if mode == 'overrun' else ['--continue-attempt']
    assert cli.main(['resume', *args, *flags]) == 0
    after = saved_values(project[1], 'publication')
    assert after['publication_receipt'] and len(remote.effects()) == 2
    assert all(context.journal().get(r.key) == r for r in original_models)
    counts = Counter(c[0] for c in calls)
    expected = panel if role == 'review' else 1
    assert counts[role] == expected + int(mode != 'overrun')
    outcome = after['unit_outcomes']['U1']
    expected_continuations = int(mode != 'overrun' and role in {'implement', 'review'})
    assert outcome['implement_continuations'] == expected_continuations
    assert outcome['fix_attempts'] == outcome['review_revisions'] == 0
    if role in {'plan', 'critic', 'summary'}:
        assert sum(after.get('recovery_plan_continuations', {}).values()) == int(mode != 'overrun')
    if mode == 'overrun':
        assert outcome['attempt_number'] == 1
        assert not BudgetLedger(context).balance().unacknowledged_overrun
        assert BudgetLedger(context).balance().overrun_usd == pytest.approx(.01)
        assert 'reservation overrun $0.01' in remote.rows()[0]['body']
    if role == 'review':
        first = before['unit_outcomes']['U1']['review_receipt']
        assert outcome['review_receipt'].panel_results[0] == first.panel_results[0]
    if mode == 'unknown':
        assert BudgetLedger(context).balance().accounting_incomplete
        assert 'accounting incomplete' in remote.rows()[0]['body']
    assert len([r for r in context.journal().operations() if r.key.kind == 'recovery.amend']) == 1
