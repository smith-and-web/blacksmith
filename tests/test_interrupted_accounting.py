"""Cancellation retains observed progress; missing usage is not free."""

import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from test_run_context import create
from test_run_context import project as project

from blacksmith.budget import BudgetLedger
from blacksmith.executor import Executor
from blacksmith.receipts import call_context_for


@pytest.mark.parametrize('terminal', [False, True])
def test_cancelled_paid_call_retains_known_progress_usage_and_unique_transcript(
    project, monkeypatch, terminal,
):
    monkeypatch.setenv('BLACKSMITH_ANTHROPIC_API_KEY', 'unused-offline')
    context = create(project)

    async def cancelled(*, prompt, options):
        yield AssistantMessage(content=[TextBlock('partial before cancellation')],
                               model=options.model, session_id='observed-session')
        if terminal:
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=3, session_id='observed-session', result='done',
                total_cost_usd=.25, usage={'input_tokens': 7})
        raise asyncio.CancelledError
    call = call_context_for(context.initial_state(), unit_id='U1', attempt_id='cancelled')
    with pytest.raises(asyncio.CancelledError):
        Executor(context.execution_config(), query_fn=cancelled).run_plan('retained',
                                                                       call_context=call)
    operation = context.journal().get(call.key('model.plan'))
    assert operation.status == 'interrupted'
    saved = operation.outcome['result']
    assert saved['num_turns'] == (3 if terminal else 1)
    assert saved['cost_usd'] == (.25 if terminal else None)
    assert saved['usage'] == ({'input_tokens': 7} if terminal else None)
    assert saved['session_id'] == 'observed-session' and saved['terminal_received'] is False
    assert saved['call_id'] == call.key('model.plan').id
    assert Path(saved['transcript_path']).stem == saved['call_id']
    assert 'partial before cancellation' in Path(saved['transcript_path']).read_text()
    balance = BudgetLedger(context).balance()
    assert balance.charged_usd == 5 and balance.accounting_incomplete
    assert balance.known_cost_usd == (.25 if terminal else 0)
    events = [e for e in context.journal().events() if e['kind'] == 'model_cost']
    assert len(events) == 1 and events[0]['payload']['num_turns'] == (3 if terminal else 1)
