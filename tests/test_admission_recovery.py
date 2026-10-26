"""Budget-only recovery keeps unstarted identities and valid paid evidence."""

from collections import Counter

import pytest
from test_publication_integrity import setup as install
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli
from blacksmith.budget import BudgetLedger
from blacksmith.run_context import context_from_state


def run(project, thread, *flags):
    return cli.main([str(project[2]), "--config", str(project[1]), "--thread-id", thread,
                     "--approve", "plan,pr", *flags])


@pytest.mark.parametrize("role,cap,panel,critic", [
    ("critic", .01, 1, True), ("implement", .01, 1, False),
    ("review", .03, 3, False), ("summary", .03, 1, False),
])
def test_budget_only_resume_retries_denied_call_without_continuation_or_replay(
    project, monkeypatch, role, cap, panel, critic,
):
    project[1].write_text(project[1].read_text() + f"[limits]\nmax_run_cost_usd={cap}\n"
        f"max_implement_continuations=0\n[review]\npanel_size={panel}\n"
        f"[critic]\nenabled={str(critic).lower()}\n")
    calls, remote, _ = install(project, monkeypatch)
    remote.log.touch()
    # The strict base fake normally handles plan/implement/review/summary. Add
    # just the critic response, still through actual options/reservation/receipts.
    if critic:
        from blacksmith.executor import Executor, ExecutorResult
        collect = Executor._collect

        async def critic_transport(self, prompt, options, *, emit_findings=False):
            if prompt.startswith("You are reviewing a proposed implementation plan"):
                calls.append(("critic", "U1", prompt, options))
                return ExecutorResult('{"approved":true,"critique":""}', options.model,
                                      False, 1, .01, {}, "offline")
            return await collect(self, prompt, options, emit_findings=emit_findings)
        monkeypatch.setattr(Executor, "_collect", critic_transport)
    assert run(project, "publication") == 1
    before = saved_values(project[1], "publication")
    context = context_from_state(before)
    journal = context.journal()
    denials = [r for r in journal.operations() if r.key.kind == "admission.denied"]
    assert len(denials) == 1 and denials[0].intent['model_key']['kind'] == 'model.' + role
    denied_call = denials[0].intent['call_id']
    assert journal.get_by_id(denied_call) is None
    original = [r for r in journal.operations() if r.key.kind.startswith('model.')]
    assert remote.effects() == []
    if role == 'review':
        review = before['unit_outcomes']['U1']['review_receipt']
        assert review.status == 'unavailable' and len(review.panel_results) == 2
        assert review.panel_results[0].status == 'clean'
    # Ordinary resume cannot reset the run allowance or start the denied role.
    count = len(calls)
    assert cli.main(['resume', '--config', str(project[1]), '--thread-id', 'publication',
                     '--approve', 'plan,pr']) == 1
    assert len(calls) == count
    assert cli.main(['resume', '--config', str(project[1]), '--thread-id', 'publication',
                     '--add-budget-usd', '1', '--approve', 'plan,pr']) == 0
    after = saved_values(project[1], 'publication')
    assert after['publication_receipt'] and len(remote.effects()) == 2
    assert journal.get_by_id(denied_call).status == 'completed'
    assert all(journal.get(r.key) == r for r in original)
    outcome = after['unit_outcomes']['U1']
    assert outcome['implement_continuations'] == 0 and outcome['attempt_number'] == 1
    assert outcome['fix_attempts'] == outcome['review_revisions'] == 0
    counts = Counter(c[0] for c in calls)
    assert counts['plan'] == counts['implement'] == counts['summary'] == 1
    assert counts['review'] == panel
    assert counts['critic'] == int(critic)
    if role == 'review':
        assert outcome['review_receipt'].panel_results[0] == review.panel_results[0]
    balance = BudgetLedger(context).balance()
    assert balance.remaining_usd == pytest.approx(cap + 1 - .01 * len(calls))
    assert journal.get(denials[0].key) == denials[0]
    assert len([r for r in journal.operations() if r.key.kind == 'recovery.amend']) == 1


def test_unlimited_cost_is_explicit_and_frozen_in_real_cli_policy(project, monkeypatch):
    project[1].write_text(project[1].read_text() + '[limits]\nmax_run_cost_usd=0.01\n')
    calls, remote, _ = install(project, monkeypatch)
    assert run(project, 'publication', '--unlimited-cost') == 0
    context = context_from_state(saved_values(project[1], 'publication'))
    assert context.execution_config().limits.max_run_cost_usd is None
    policy = next(r for r in context.journal().operations() if r.key.kind == 'budget.policy')
    assert policy.intent['unlimited_cost'] is True
    assert BudgetLedger(context).balance().remaining_usd is None
    assert all(c[3].max_budget_usd == 5 for c in calls) and len(remote.effects()) == 2
