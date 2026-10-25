"""Every actual Executor paid-role entry uses critical grants and real SDK options."""

import sqlite3

import pytest
from test_integration_integrity import configure
from test_publication_approval import run
from test_publication_integrity import setup as install_pipeline
from test_run_context import create, saved_values
from test_run_context import project as project

from blacksmith.budget import AdmissionDenied, BudgetLedger
from blacksmith.executor import Executor, ExecutorResult, ModelCallStopped
from blacksmith.receipts import ReceiptError, call_context_for
from blacksmith.run_context import context_from_state

ROLES = ["run_plan", "run_plan_critique", "run_implement", "run_implement_escalate",
         "run_review", "run_summary"]


def setup(project, monkeypatch, cap=10, *, cost=.25, terminal=True):
    project[1].write_text(project[1].read_text() + f"[limits]\nmax_run_cost_usd={cap}\n")
    context = create(project)
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    calls = []

    async def collect(self, prompt, options, *, emit_findings=False):
        calls.append((prompt, options))
        assert options.setting_sources == [] and options.strict_mcp_config
        assert options.max_budget_usd is not None
        return ExecutorResult("ok", options.model, False, 1, cost, {}, None,
                              terminal_received=terminal)
    monkeypatch.setattr(Executor, "_collect", collect)
    executor = Executor(context.execution_config())
    return context, executor, calls


def call(context, name):
    return call_context_for(context.initial_state(), unit_id="U1", attempt_id=name)


@pytest.mark.parametrize("role", ROLES)
def test_each_role_receives_reserved_sdk_budget_and_persists_exact_call_charge(
    project, monkeypatch, role,
):
    context, executor, calls = setup(project, monkeypatch, cap=2)
    result = getattr(executor, role)("immutable prompt", call_context=call(context, role))
    assert len(calls) == 1 and calls[0][1].max_budget_usd == 2
    rows = [r for r in context.journal().operations() if r.key.kind == "admission.reserve"]
    assert len(rows) == 1 and rows[0].intent["call_id"] == result.call_id
    assert rows[0].outcome == {"call_state": "completed", "charged_usd": .25,
                               "accounting_incomplete": False}
    model = context.journal().get_by_id(result.call_id)
    assert model.intent["max_budget_usd"] == 2 and model.intent["admission_id"] == rows[0].key.id
    assert BudgetLedger(context).balance().remaining_usd == 1.75


@pytest.mark.parametrize("role", ROLES)
def test_each_role_is_denied_before_model_intent_and_transport_when_cap_exhausted(
    project, monkeypatch, role,
):
    context, executor, calls = setup(project, monkeypatch, cap=.25)
    executor.run_plan("consume the remaining cap", call_context=call(context, "first"))
    before = len(context.journal().events())
    denied = call(context, role)
    with pytest.raises(AdmissionDenied) as error:
        getattr(executor, role)("must not start", call_context=denied)
    assert error.value.started is False and len(calls) == 1
    assert context.journal().get_by_id(error.value.call_id) is None
    assert len(context.journal().events()) == before == 1
    assert BudgetLedger(context).balance().remaining_usd == 0


def test_cached_partial_cap_keeps_original_option_and_seals_a_lost_settlement(project, monkeypatch):
    context, executor, calls = setup(project, monkeypatch, cap=1)
    identity = call(context, "cached")
    first = executor.run_plan("immutable", call_context=identity)
    grant = next(r for r in context.journal().operations()
                 if r.key.kind == "admission.reserve")
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute("UPDATE operations SET status='intent',outcome_json=NULL,outcome_hash=NULL,"
                     "finished_at=NULL WHERE operation_id=?", (grant.key.id,))
    original = context.journal().get_by_id(first.call_id)
    second = executor.run_plan("immutable", call_context=identity)
    assert second.call_id == first.call_id and len(calls) == 1
    assert calls[0][1].max_budget_usd == 1  # Remaining is only .75; never rewrite the old option.
    assert context.journal().get_by_id(first.call_id) == original
    assert context.journal().get(grant.key).status == "completed"
    assert BudgetLedger(context).balance().charged_usd == .25
    assert len(context.journal().events()) == 1


def test_nonterminal_partial_cost_is_still_charged_at_full_reservation(project, monkeypatch):
    context, executor, calls = setup(project, monkeypatch, cap=5, cost=.1, terminal=False)
    with pytest.raises(ReceiptError, match="interrupted_attempt"):
        executor.run_plan("stream lost", call_context=call(context, "unknown"))
    balance = BudgetLedger(context).balance()
    assert balance.known_cost_usd == .1 and balance.charged_usd == 5
    assert balance.accounting_incomplete and balance.remaining_usd == 0
    with pytest.raises(AdmissionDenied):
        executor.run_summary("later summary", call_context=call(context, "summary"))
    assert len(calls) == 1


def test_critical_reservation_failure_stops_before_sdk_transport(project, monkeypatch):
    context, executor, calls = setup(project, monkeypatch)
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute("CREATE TRIGGER fail_reserve BEFORE INSERT ON operations "
                     "WHEN NEW.kind='admission.reserve' BEGIN SELECT RAISE(ABORT,'failed'); END")
    with pytest.raises(ReceiptError, match="critical journal"):
        executor.run_plan("never admitted", call_context=call(context, "failed"))
    assert calls == []
    assert not any(r.key.kind.startswith("model.") for r in context.journal().operations())


@pytest.mark.parametrize("role", ROLES)
def test_actual_overrun_stops_every_role_including_advisory_paths(project, monkeypatch, role):
    context, executor, calls = setup(project, monkeypatch, cap=2, cost=2.5)
    with pytest.raises(ModelCallStopped, match="budget") as stopped:
        getattr(executor, role)("overrun", call_context=call(context, role), raise_on_error=False)
    assert stopped.value.started and stopped.value.result.cost_usd == 2.5
    assert BudgetLedger(context).balance().unacknowledged_overrun
    assert BudgetLedger(context).balance().remaining_usd == -.5
    with pytest.raises(AdmissionDenied):
        executor.run_summary("forbidden later spend", call_context=call(context, 'later'))
    assert len(calls) == 1


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("qa", [False, True])
def test_real_cli_both_shapes_and_publications_bind_every_paid_role_to_grants(
    project, monkeypatch, parallel, qa,
):
    configure(project, sequential=not parallel, qa=qa)
    calls, remote, _ = install_pipeline(project, monkeypatch)
    assert run(project, "publication") == (2 if qa else 0)
    state = saved_values(project[1], "publication")
    context = context_from_state(state)
    grants = [r for r in context.journal().operations() if r.key.kind == "admission.reserve"]
    assert len(grants) == len(calls) == len(state["cost_events"]) == 7
    assert all(0 < g.intent["reservation_usd"] <= 5 and g.status == "completed" for g in grants)
    for grant in grants:
        model = context.journal().get_by_id(grant.intent["call_id"])
        assert model.intent["max_budget_usd"] == grant.intent["reservation_usd"]
        assert model.intent["admission_id"] == grant.key.id
    balance = BudgetLedger(context).balance()
    assert balance.charged_usd == pytest.approx(.07) and balance.reserved_usd == 0
    assert balance.remaining_usd == pytest.approx(49.93)
    assert len(remote.effects()) == 2 and state["publication_receipt"]


@pytest.mark.parametrize("role", ["run", *ROLES])
def test_default_sdk_without_context_refuses_before_options_transport_or_journal_change(
    project, monkeypatch, role,
):
    context, executor, calls = setup(project, monkeypatch)
    operations, events = context.journal().operations(), context.journal().events()

    def forbidden_options(**kwargs):
        pytest.fail("options/credentials requested before missing-context refusal")
    monkeypatch.setattr(executor, "build_options", forbidden_options)
    kwargs = {"model": context.execution_config().models.plan} if role == "run" else {}
    with pytest.raises(ReceiptError, match="run_admission_context_required"):
        getattr(executor, role)("not admitted", **kwargs)
    assert calls == []
    assert context.journal().operations() == operations and context.journal().events() == events
