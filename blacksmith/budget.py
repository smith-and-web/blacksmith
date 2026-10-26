"""Atomic run-wide reservations against the critical operation journal.

SDK results and reservations are read in the same locked transaction. A response
recorded before its reservation is settled therefore cannot hide an overrun from
the next admission. Missing usage is charged conservatively, never estimated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from blacksmith.receipts import OperationKey, ReceiptError, _now, digest, encoded


class AdmissionDenied(ReceiptError):
    """A paid call was not started; a positive budget amendment may permit retry."""

    def __init__(self, reason, call_id, denial_id):
        self.reason = reason
        self.call_id = call_id
        self.denial_id = denial_id
        self.started = False
        super().__init__(f"model admission denied before start: {reason}")


def _amount(value):
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return None
    return Decimal(str(value))


def _policy_key(context):
    return OperationKey(context.run_id, "__run__", "budget-v1", "budget.policy")


def initialize(context, *, unlimited_cost=False):
    """Explicit fresh-run setup; execution never creates a missing legacy policy."""
    limits = context.execution_config().limits
    if type(unlimited_cost) is not bool or (limits.max_run_cost_usd is None) != unlimited_cost:
        raise ValueError("uncapped runs require the recorded --unlimited-cost choice")
    policy = {"version": 1, "max_run_cost_usd": limits.max_run_cost_usd,
              "max_call_cost_usd": limits.max_call_cost_usd, "unlimited_cost": unlimited_cost,
              "model_timeout_s": limits.model_timeout_s, "max_run_seconds": limits.max_run_seconds}
    key = _policy_key(context)
    context.journal().intent(key, policy)
    context.journal().finish(key, "completed", {"sha256": digest(encoded(policy))})
    return policy


@dataclass(frozen=True)
class Reservation:
    operation_id: str
    call_id: str
    amount_usd: float


@dataclass(frozen=True)
class Balance:
    known_cost_usd: float
    charged_usd: float
    reserved_usd: float
    remaining_usd: float | None
    accounting_incomplete: bool
    unacknowledged_overrun: bool
    overrun_usd: float = 0.0


def _insert(conn, key, intent, *, status="intent", outcome=None):
    body = encoded(intent)
    result = encoded(outcome) if outcome is not None else None
    conn.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
        key.id, key.run_id, key.unit_id, key.attempt_id, key.kind, status, body, digest(body),
        result, digest(result) if result is not None else None, _now(),
        _now() if result is not None else None))


class BudgetLedger:
    @classmethod
    def for_call(cls, call):
        from blacksmith.run_context import RunContext
        if not call.run_context:
            raise ReceiptError("legacy_admission_requires_manual_recovery: missing call context")
        context = RunContext.model_validate(call.run_context)
        context.execution_config().require_supported_execution()
        journal = context.journal()
        if (journal.path != Path(call.journal_path).absolute() or context.run_id != call.run_id
                or journal.context_hash != call.context_hash):
            raise ReceiptError("foreign model admission run context")
        context.verify_inputs()
        return cls(context)

    def __init__(self, context):
        self.context = context
        self.journal = context.journal()
        row = self.journal.get(_policy_key(context))
        limits = context.execution_config().limits
        expected = {"version": 1, "max_run_cost_usd": limits.max_run_cost_usd,
            "max_call_cost_usd": limits.max_call_cost_usd,
            "unlimited_cost": limits.max_run_cost_usd is None,
            "model_timeout_s": limits.model_timeout_s, "max_run_seconds": limits.max_run_seconds}
        if (row is None or row.status != "completed" or row.intent != expected
                or row.outcome != {"sha256": digest(encoded(expected))}):
            raise ReceiptError("legacy_admission_requires_manual_recovery: missing/corrupt policy")
        self.policy = expected

    def _cap(self):
        # Reuse the existing verified, idempotent amendment calculation.
        from blacksmith.recovery_control import _limits
        value = _limits(self.context, None)[0]["max_run_cost_usd"]
        return _amount(value) if value is not None else None

    def _grants(self, conn):
        rows = [self.journal._receipt(r) for r in conn.execute(
            "SELECT * FROM operations WHERE kind='admission.reserve' ORDER BY rowid")]
        for row in rows:
            data = row.intent
            try:
                key = OperationKey(**data["model_key"])
                prefix = f"admission:{key.id}:"
                cap = _amount(data["run_cap_at_admission"])
                base = _amount(self.policy["max_run_cost_usd"])
                if (key.run_id != self.context.run_id or key.id != data["call_id"]
                        or not key.kind.startswith("model.")
                        or row.key.unit_id != key.unit_id
                        or not row.key.attempt_id.startswith(prefix)
                        or not row.key.attempt_id[len(prefix):].isdigit()
                        or (base is not None and (cap is None or cap < base))
                        or (base is None and data["run_cap_at_admission"] is not None)
                        or data["policy_id"] != _policy_key(self.context).id
                        or (_amount(data["reservation_usd"]) or 0) <= 0
                        or _amount(data["reservation_usd"]) > _amount(
                            self.policy["max_call_cost_usd"])):
                    raise ValueError("invalid reservation identity")
            except (ValueError, TypeError, KeyError) as exc:
                raise ReceiptError("corrupt admission reservation") from exc
        return rows

    def _charge(self, conn, grant):
        """Return state, debit, held, known and incomplete from actual critical data."""
        reserve = _amount(grant.intent["reservation_usd"])
        raw = conn.execute("SELECT * FROM operations WHERE operation_id=?",
                           (grant.intent["call_id"],)).fetchone()
        model = self.journal._receipt(raw) if raw else None
        if model is None:
            if grant.status == "completed" and grant.outcome == {
                    "call_state": "not_started", "charged_usd": 0.0,
                    "accounting_incomplete": False}:
                return "not_started", Decimal(0), Decimal(0), Decimal(0), False
            if grant.status != "intent":
                raise ReceiptError("settled admission lost its critical model result")
            return "reserved", Decimal(0), reserve, Decimal(0), False
        if _amount(model.intent.get("max_budget_usd")) != reserve:
            raise ReceiptError("model SDK budget differs from its critical reservation")
        if model.status == "intent":
            return "started", Decimal(0), reserve, Decimal(0), False
        result = (model.outcome or {}).get("result") or {}
        cost = _amount(result.get("cost_usd"))
        incomplete = cost is None or model.status in {"interrupted", "unknown"}
        charge = max(reserve, cost or Decimal(0)) if incomplete else cost
        state = ("interrupted" if model.status == "interrupted" else
                 "unknown" if model.status == "unknown" or incomplete else "completed")
        expected = {"call_state": state, "charged_usd": float(charge),
                    "accounting_incomplete": incomplete}
        if grant.status != "intent" and (grant.status != "completed" or grant.outcome != expected):
            raise ReceiptError("admission settlement differs from critical model accounting")
        return state, charge, Decimal(0), cost or Decimal(0), incomplete

    def _balance(self, conn, cap):
        known = charged = held = excess = Decimal(0)
        incomplete = overrun = False
        for row in self._grants(conn):
            _, debit, reserved, measured, missing = self._charge(conn, row)
            charged += debit
            held += reserved
            known += measured
            incomplete |= missing
            before = _amount(row.intent["run_cap_at_admission"])
            acknowledged = cap is not None and before is not None and cap > before
            overrun |= debit > _amount(row.intent["reservation_usd"]) and not acknowledged
            excess += max(Decimal(0), debit - _amount(row.intent["reservation_usd"]))
        return Balance(float(known), float(charged), float(held),
                       float(cap - charged - held) if cap is not None else None,
                       incomplete, overrun, float(excess))

    def balance(self):
        cap = self._cap()
        with self.journal._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._balance(conn, cap)
            conn.commit()
        return result

    def denied_before_start(self, key):
        """Validate retained denial identity; an admitted/started call is never unstarted."""
        if self.journal.get(key) is not None:
            return False
        for row in self.journal.operations():
            if row.key.kind != "admission.denied" or row.intent.get("call_id") != key.id:
                continue
            intent = row.intent
            expected = OperationKey(key.run_id, key.unit_id,
                "denied:" + digest(encoded(intent)), "admission.denied")
            if (row.key != expected or intent.get("model_key") != key.__dict__
                    or intent.get("reason") not in {"run_budget", "reservation_overrun"}
                    or row.status != "failed" or row.outcome != {
                        "call_state": "denied", "model_started": False, "charged_usd": 0.0}):
                raise ReceiptError("corrupt denied-before-start admission")
            return True
        return False

    def completed_overrun(self, key):
        """A completed successful response can be reused after explicit acknowledgement."""
        row = self.journal.get(key)
        if row is None or row.status != "completed":
            return False
        result = (row.outcome or {}).get("result") or {}
        if result.get("is_error") is not False or result.get("terminal_received") is not True:
            return False
        actual = _amount(result.get("cost_usd"))
        grant = self.reservation_for(key)
        return actual is not None and actual > _amount(grant.amount_usd)

    def reserve(self, key):
        self.journal._check_key(key)
        if not key.kind.startswith("model."):
            raise ReceiptError("admission requires a model call identity")
        cap = self._cap()
        denied = None
        grant = None
        with self.journal._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = [r for r in self._grants(conn) if r.intent["call_id"] == key.id]
            for row in existing:
                state, *_ = self._charge(conn, row)
                if state == "not_started":
                    continue
                if state in {"started", "interrupted", "unknown"}:
                    raise ReceiptError(f"interrupted_attempt_requires_resume: {key.id}")
                conn.commit()
                return Reservation(row.key.id, key.id, row.intent["reservation_usd"])
            balance = self._balance(conn, cap)
            available = _amount(balance.remaining_usd) if cap is not None else _amount(
                self.policy["max_call_cost_usd"])
            if balance.unacknowledged_overrun or available is None or available <= 0:
                denied = "reservation_overrun" if balance.unacknowledged_overrun else "run_budget"
                intent = {"call_id": key.id, "model_key": key.__dict__, "reason": denied,
                          "run_cap": float(cap) if cap is not None else None,
                          "balance": balance.__dict__}
                denial = OperationKey(key.run_id, key.unit_id,
                    "denied:" + digest(encoded(intent)), "admission.denied")
                if conn.execute("SELECT 1 FROM operations WHERE operation_id=?",
                                (denial.id,)).fetchone() is None:
                    _insert(conn, denial, intent, status="failed", outcome={
                        "call_state": "denied", "model_started": False, "charged_usd": 0.0})
            else:
                amount = min(available, _amount(self.policy["max_call_cost_usd"]))
                reserve_key = OperationKey(key.run_id, key.unit_id,
                    f"admission:{key.id}:{len(existing)}", "admission.reserve")
                intent = {"call_id": key.id, "model_key": key.__dict__,
                          "reservation_usd": float(amount),
                          "policy_id": _policy_key(self.context).id,
                          "run_cap_at_admission": float(cap) if cap is not None else None}
                _insert(conn, reserve_key, intent)
                grant = Reservation(reserve_key.id, key.id, float(amount))
            conn.commit()
        if denied:
            raise AdmissionDenied(denied, key.id, denial.id)
        return grant

    def reservation_for(self, key):
        """A cached call keeps its exact original grant and SDK option."""
        with self.journal._connection() as conn:
            rows = [r for r in self._grants(conn) if r.intent["call_id"] == key.id
                    and self._charge(conn, r)[0] != "not_started"]
            if len(rows) != 1:
                raise ReceiptError("model call lacks exactly one original reservation")
            row = rows[0]
            return Reservation(row.key.id, key.id, row.intent["reservation_usd"])

    def settle(self, reservation):
        with self.journal._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = [r for r in self._grants(conn) if r.key.id == reservation.operation_id]
            if len(rows) != 1 or rows[0].intent["call_id"] != reservation.call_id:
                raise ReceiptError("foreign model reservation")
            row = rows[0]
            state, charged, held, _, incomplete = self._charge(conn, row)
            if held:
                raise ReceiptError("cannot settle a call without a terminal critical result")
            outcome = {"call_state": state, "charged_usd": float(charged),
                       "accounting_incomplete": incomplete}
            if row.status == "intent":
                body = encoded(outcome)
                conn.execute("UPDATE operations SET status='completed',outcome_json=?,"
                    "outcome_hash=?,finished_at=? WHERE operation_id=?", (
                        body, digest(body), _now(), row.key.id))
            conn.commit()
