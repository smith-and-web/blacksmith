"""Atomic critical budget ledger; SDK outcomes below are explicit offline receipts."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_run_context import create
from test_run_context import project as project

from blacksmith.budget import AdmissionDenied, BudgetLedger, initialize
from blacksmith.config import BlacksmithConfig, LimitsConfig
from blacksmith.contract import parse_prd
from blacksmith.receipts import OperationKey, ReceiptError, digest, encoded
from blacksmith.run_context import create_run_context


def ledger(project, cap=10, call=5):
    project[1].write_text(project[1].read_text() +
                          f"[limits]\nmax_run_cost_usd={cap}\nmax_call_cost_usd={call}\n")
    context = create(project)
    initialize(context)
    return context, BudgetLedger(context)


def key(context, name):
    return OperationKey(context.run_id, name, "initial", "model.implement")


def result(context, call, cost, *, status="completed"):
    context.journal().intent(call, {"max_budget_usd": 5.0})
    context.journal().finish(call, status, {"result": {"cost_usd": cost}})


def test_settled_spec_defaults_are_finite_and_per_call_bounded():
    limits = LimitsConfig()
    assert limits.max_run_cost_usd == 50.0 and limits.max_call_cost_usd == 5.0
    assert limits.model_timeout_s == 1800 and limits.max_run_seconds == 21600
    for name in ("max_run_cost_usd", "max_call_cost_usd", "model_timeout_s", "max_run_seconds"):
        for invalid in (0, -1, float("inf"), float("nan")):
            with pytest.raises(ValueError):
                LimitsConfig(**{name: invalid})


def test_missing_policy_refuses_without_creating_or_changing_receipts(project):
    context = create(project)
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute("DELETE FROM operations WHERE kind='budget.policy'")
    original = context.journal().operations()
    with pytest.raises(ReceiptError, match="legacy_admission"):
        BudgetLedger(context)
    assert context.journal().operations() == original


def test_concurrent_reservations_sum_within_run_cap_and_denials_start_no_model(project):
    context, budget = ledger(project, cap=12)
    barrier = Barrier(8)

    def reserve(number):
        barrier.wait()
        call = key(context, str(number))
        try:
            return budget.reserve(call)
        except AdmissionDenied as exc:
            assert exc.started is False and exc.call_id == call.id
            assert context.journal().get_by_id(exc.denial_id).outcome["model_started"] is False
            assert context.journal().get(call) is None
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(reserve, range(8)))
    assert sorted(r.amount_usd for r in reservations if r) == [2.0, 5.0, 5.0]
    assert sum(r is None for r in reservations) == 5
    balance = budget.balance()
    assert balance.reserved_usd == 12 and balance.charged_usd == 0
    assert balance.remaining_usd == 0 and not balance.accounting_incomplete
    denied = [r for r in context.journal().operations() if r.key.kind == "admission.denied"]
    assert len(denied) == 5
    assert all(r.outcome == {"call_state": "denied", "model_started": False,
                             "charged_usd": 0.0} for r in denied)


def test_same_call_reservation_is_idempotent_and_survives_new_ledger_owner(project):
    context, budget = ledger(project)
    call = key(context, "A")
    first = budget.reserve(call)
    second = BudgetLedger(context).reserve(call)
    assert first == second and budget.balance().reserved_usd == 5
    assert len([r for r in context.journal().operations()
                if r.key.kind == "admission.reserve"]) == 1


def test_recorded_actual_cost_controls_next_admission_before_separate_settlement(project):
    context, budget = ledger(project)
    a, b, c = [key(context, s) for s in ("A", "B", "C")]
    first, second = budget.reserve(a), budget.reserve(b)
    result(context, a, 2.0)
    assert budget.balance().remaining_usd == 3
    third = budget.reserve(c)
    assert third.amount_usd == 3.0 and second.amount_usd == 5.0
    assert budget.balance().charged_usd == 2 and budget.balance().reserved_usd == 8
    budget.settle(first)
    assert budget.reserve(a) == first  # Cached SDK receipt must retain the original $5 option.
    assert budget.balance().remaining_usd == 0
    budget.settle(first)
    assert budget.balance().charged_usd == 2


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted", "unknown"])
def test_missing_cost_consumes_full_reservation_and_remains_incomplete_across_restart(
    project, status,
):
    context, budget = ledger(project)
    call = key(context, "A")
    first = budget.reserve(call)
    result(context, call, None, status=status)
    budget.settle(first)
    balance = BudgetLedger(context).balance()
    assert balance.charged_usd == 5 and balance.reserved_usd == 0
    assert balance.remaining_usd == 5 and balance.known_cost_usd == 0
    assert balance.accounting_incomplete
    row = context.journal().get_by_id(first.operation_id)
    assert row.outcome["call_state"] in {"unknown", "interrupted"}
    assert row.outcome["accounting_incomplete"] is True


def test_known_zero_cost_is_measured_zero_and_refunds_the_full_reservation(project):
    context, budget = ledger(project)
    call = key(context, "A")
    first = budget.reserve(call)
    result(context, call, 0.0)
    budget.settle(first)
    balance = budget.balance()
    assert balance.charged_usd == 0 and balance.remaining_usd == 10
    assert not balance.accounting_incomplete


def test_overrun_is_visible_before_settlement_and_denies_every_later_call(project):
    context, budget = ledger(project)
    call = key(context, "A")
    first = budget.reserve(call)
    result(context, call, 6.0)
    balance = budget.balance()
    assert balance.charged_usd == 6 and balance.remaining_usd == 4
    assert balance.unacknowledged_overrun
    with pytest.raises(AdmissionDenied, match="overrun"):
        budget.reserve(key(context, "B"))
    budget.settle(first)
    with pytest.raises(AdmissionDenied, match="overrun"):
        BudgetLedger(context).reserve(key(context, "C"))
    assert context.journal().get(key(context, "B")) is None
    assert context.journal().get(key(context, "C")) is None


def test_inflight_or_interrupted_paid_identity_cannot_reacquire_and_replay(project):
    context, budget = ledger(project)
    call = key(context, "A")
    first = budget.reserve(call)
    context.journal().intent(call, {"max_budget_usd": first.amount_usd})
    with pytest.raises(ReceiptError, match="interrupted_attempt"):
        BudgetLedger(context).reserve(call)
    assert budget.balance().reserved_usd == 5
    context.journal().finish(call, "interrupted", {"error_type": "Killed"})
    assert budget.balance().charged_usd == 5 and budget.balance().accounting_incomplete
    with pytest.raises(ReceiptError, match="interrupted_attempt"):
        BudgetLedger(context).reserve(call)


def test_critical_insert_failure_rolls_back_and_allocates_no_grant_or_model(project, monkeypatch):
    context, budget = ledger(project)
    original = context.journal().operations()

    def fault(*args, **kwargs):
        raise sqlite3.OperationalError("injected reservation write failure")
    monkeypatch.setattr("blacksmith.budget._insert", fault)
    with pytest.raises(ReceiptError, match="critical journal"):
        budget.reserve(key(context, "A"))
    assert context.journal().operations() == original
    assert budget.balance().reserved_usd == 0


def test_hash_valid_but_oversized_reservation_is_rejected_as_corrupt(project):
    context, budget = ledger(project)
    first = budget.reserve(key(context, "A"))
    row = context.journal().get_by_id(first.operation_id)
    payload = {**row.intent, "reservation_usd": 7.0}
    text = encoded(payload)
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute("UPDATE operations SET intent_json=?,intent_hash=? WHERE operation_id=?",
                     (text, digest(text), first.operation_id))
    with pytest.raises(ReceiptError, match="corrupt admission"):
        budget.balance()
    assert json.loads(text)["reservation_usd"] == 7.0


def test_sdk_budget_must_match_reservation_before_any_later_admission(project):
    context, budget = ledger(project)
    call = key(context, "A")
    budget.reserve(call)
    context.journal().intent(call, {"max_budget_usd": 100.0})
    with pytest.raises(ReceiptError, match="SDK budget differs"):
        budget.reserve(key(context, "B"))
    assert context.journal().get(key(context, "B")) is None


@pytest.mark.parametrize("change", ["call", "admitted_cap"])
def test_hash_valid_reservation_cannot_reassign_usage_or_weaken_overrun_identity(project, change):
    context, budget = ledger(project)
    first = budget.reserve(key(context, "A"))
    row = context.journal().get_by_id(first.operation_id)
    payload = row.intent
    if change == "call":
        other = key(context, "A")
        other = OperationKey(other.run_id, other.unit_id, "foreign-attempt", other.kind)
        result(context, other, 0.0)
        payload.update(call_id=other.id, model_key=other.__dict__)
    else:
        payload["run_cap_at_admission"] = 0.0
    body = encoded(payload)
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute("UPDATE operations SET intent_json=?,intent_hash=? WHERE operation_id=?",
                     (body, digest(body), row.key.id))
    with pytest.raises(ReceiptError, match="corrupt admission"):
        budget.reserve(key(context, "B"))
    assert context.journal().get(key(context, "B")) is None


def test_uncapped_run_requires_explicit_recorded_choice_and_still_reserves_per_call(project):
    config = BlacksmithConfig.load(project[1])
    config = config.model_copy(update={"limits": config.limits.model_copy(
        update={"max_run_cost_usd": None})})
    context = create_run_context(config, parse_prd(project[2]), "uncapped", required_tools=("git",),
                                 unlimited_cost=True)
    with pytest.raises(ValueError, match="unlimited-cost"):
        initialize(context)
    initialize(context, unlimited_cost=True)
    budget = BudgetLedger(context)
    grants = [budget.reserve(key(context, str(i))) for i in range(12)]
    assert all(g.amount_usd == 5 for g in grants)
    assert budget.balance().reserved_usd == 60 and budget.balance().remaining_usd is None
