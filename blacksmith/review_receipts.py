"""Typed review decisions and immutable evidence, separate from gate evidence."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blacksmith.receipts import OperationKey, ReceiptError, digest, encoded

ReviewStatus = Literal["clean", "blocking", "unavailable", "disabled"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValidatedFinding(_Record):
    severity: Literal["blocking", "advisory"]
    file: str = Field(strict=True, min_length=1)
    detail: str = Field(strict=True, min_length=1)

    @field_validator("file", "detail")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("finding fields must contain nonblank text")
        return value


class Verdict(_Record):
    verdict: Literal["clean", "needs_changes"]
    findings: tuple[ValidatedFinding, ...]


class ReviewPatch(_Record):
    base_commit: str
    candidate_commit: str
    path: str
    sha256: str
    bytes: int = Field(ge=0)


class PanelResult(_Record):
    seat: int = Field(ge=0)
    status: Literal["clean", "blocking", "unavailable"]
    findings: tuple[ValidatedFinding, ...] = ()
    call_id: str | None = None
    response_sha256: str | None = None
    diagnostic: str = ""

    @model_validator(mode="after")
    def decision_matches_findings(self):
        blocking = any(f.severity == "blocking" for f in self.findings)
        if self.status != "unavailable" and blocking != (self.status == "blocking"):
            raise ValueError("panel decision differs from validated findings")
        return self


class ReviewReceipt(_Record):
    schema_version: Literal[1] = 1
    receipt_id: str
    run_id: str
    unit_id: str
    attempt_id: str
    base_commit: str | None
    candidate_commit: str | None
    candidate_tree: str | None
    status: ReviewStatus
    findings: tuple[ValidatedFinding, ...] = ()
    panel_results: tuple[PanelResult, ...] = ()
    cost_references: tuple[str, ...] = ()
    patch: ReviewPatch | None = None
    artifact_path: str = ""
    diagnostic: str = ""

    @model_validator(mode="after")
    def decision_matches_evidence(self):
        blocking = any(f.severity == "blocking" for f in self.findings)
        if self.status == "disabled":
            if self.findings or self.panel_results or self.cost_references:
                raise ValueError("disabled review cannot have reviewer results")
        elif self.status in {"clean", "blocking"}:
            if (not self.base_commit or not self.candidate_commit or not self.patch
                    or not self.panel_results
                    or blocking != (self.status == "blocking")):
                raise ValueError("review decision lacks candidate/patch/panel evidence")
            if self.status == "clean" and any(r.status != "clean" for r in self.panel_results):
                raise ValueError("clean review has an unavailable/blocking panelist")
        if [r.seat for r in self.panel_results] != list(range(len(self.panel_results))):
            raise ValueError("panel results must be a complete ordered prefix")
        if self.cost_references != tuple(r.call_id for r in self.panel_results if r.call_id):
            raise ValueError("review cost references differ from panel call identities")
        if self.patch and (self.patch.base_commit != self.base_commit
                           or self.patch.candidate_commit != self.candidate_commit):
            raise ValueError("review patch belongs to a different candidate")
        if self.status in {"clean", "blocking"}:
            status, findings = aggregate_panel(self.panel_results)
            if status != self.status or findings != self.findings:
                raise ValueError("review decision differs from panel evidence")
        return self


def parse_verdict(text):
    """Accept one complete JSON object, optionally fenced; never infer a clean vote."""
    value = text.strip()
    if value.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\s*```", value, re.DOTALL)
        if not match:
            raise ValueError("expected exactly one JSON verdict")
        value = match.group(1)

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON verdict field")
            result[key] = item
        return result

    return Verdict.model_validate(json.loads(value, object_pairs_hook=unique))


def aggregate_panel(results):
    findings = []
    for row in results:
        for finding in row.findings:
            if finding not in findings:
                findings.append(finding)
    status = ("blocking" if any(f.severity == "blocking" for f in findings)
              else "unavailable" if not results or any(r.status == "unavailable" for r in results)
              else "clean")
    return status, tuple(findings)


def artifact(context, key):
    path = context.directory / "reviews" / (key.id + ".json")
    if path.resolve() != path or path.is_symlink():
        raise ReceiptError("foreign review artifact path")
    return path


def retain(context, key, record=None):
    """Seal before publication; a lost artifact can restore only its original bytes."""
    from blacksmith.run_context import atomic_write

    journal, path = context.journal(), artifact(context, key)
    seal_key = OperationKey(key.run_id, key.unit_id, key.attempt_id, "review.seal:" + key.id)
    previous, seal = journal.get(key), journal.get(seal_key)
    if seal:
        data = seal.intent["receipt"]
        if (seal.status not in {"intent", "completed"} or seal.intent["operation_id"] != key.id
                or (seal.status == "completed"
                    and seal.outcome != {"sha256": digest(encoded(data) + "\n")})):
            raise ReceiptError("invalid critical review seal")
        sealed = ReviewReceipt.model_validate(data)
        if record is not None and record != sealed:
            raise ReceiptError("conflicting sealed review receipt")
        record = sealed
    if record is None:
        if previous and previous.status != "intent":
            raise ReceiptError("review outcome is missing its critical seal")
        return None
    if (record.receipt_id != key.id or record.run_id != key.run_id
            or record.unit_id != key.unit_id or record.attempt_id != key.attempt_id
            or record.artifact_path != str(path)):
        raise ReceiptError("review artifact identity differs from critical operation")
    data = record.model_dump(mode="json")
    journal.intent(seal_key, {"operation_id": key.id, "receipt": data})
    journal.finish(seal_key, "completed", {"sha256": digest(encoded(data) + "\n")})
    if path.exists():
        if path.read_text() != encoded(data) + "\n":
            raise ReceiptError("changed retained review receipt")
    else:
        atomic_write(path, encoded(data) + "\n")
    journal.finish(key, "completed", {"receipt": data})
    return record


def summary(state):
    """Describe latest per-unit decisions; absent evidence never means clean."""
    receipts = [row.get("review_receipt") for row in (state.get("unit_outcomes") or {}).values()
                if not row.get("human")]
    if not receipts and state.get("review_receipt"):
        receipts = [state["review_receipt"]]
    statuses = [r.status if isinstance(r, ReviewReceipt) else "unavailable" for r in receipts]
    if "blocking" in statuses:
        return "blocking"
    if "unavailable" in statuses:
        return "unavailable"
    if statuses:
        return "disabled" if "disabled" in statuses else "clean"
    if state.get("unresolved_review_findings"):
        return "blocking"
    return "unavailable" if state.get("review_enabled") else "disabled"


def current_blockers(state):
    records = [row.get("review_receipt") for row in (state.get("unit_outcomes") or {}).values()
               if not row.get("human")]
    if not records and state.get("review_receipt"):
        records = [state["review_receipt"]]
    if any(isinstance(record, ReviewReceipt) for record in records):
        return [finding.model_dump() for record in records if isinstance(record, ReviewReceipt)
                and record.status == "blocking" for finding in record.findings
                if finding.severity == "blocking"]
    return state.get("unresolved_review_findings") or []


def verify_receipt(record, state, *, unit, base, candidate, eligible=True):
    """Read-only acceptance of original per-unit review evidence, even at a later HEAD."""
    from pathlib import Path

    from blacksmith.review_patch import verify_patch
    from blacksmith.run_context import context_from_state

    if not isinstance(record, ReviewReceipt):
        raise ReceiptError("missing typed review receipt")
    context = context_from_state(state)
    enabled = context.execution_config().review.enabled
    expected = {"clean"} if enabled else {"disabled"}
    if not eligible and enabled:
        expected |= {"blocking", "unavailable"}
    if (record.status not in expected or record.run_id != context.run_id
            or record.unit_id != unit.id or record.base_commit != base
            or record.candidate_commit != candidate):
        raise ReceiptError("review unavailable/blocking or belongs to a different unit/candidate")
    if (record.candidate_tree is None or not base or not candidate):
        raise ReceiptError("review lacks original committed candidate identity")
    journal = context.journal()
    operation = journal.get_by_id(record.receipt_id)
    if (operation is None or operation.status != "completed"
            or operation.key.unit_id != unit.id or operation.key.attempt_id != record.attempt_id
            or not operation.key.kind.startswith("review.verdict:")
            or operation.intent.get("enabled") != enabled
            or operation.intent.get("base_commit") != base
            or operation.intent.get("candidate_commit") != candidate
            or operation.outcome != {"receipt": record.model_dump(mode="json")}):
        raise ReceiptError("review differs from critical decision receipt")
    path = artifact(context, operation.key)
    if (record.artifact_path != str(path) or not path.is_file()
            or path.read_text() != encoded(record.model_dump(mode="json")) + "\n"):
        raise ReceiptError("missing/changed review receipt artifact")
    if enabled:
        if record.status == "clean" and len(
                record.panel_results) != context.execution_config().review.panel_size:
            raise ReceiptError("incomplete review panel")
        if record.patch:
            verify_patch(record.patch, root=context.directory / "reviews" / "patches")
        for seat in record.panel_results:
            if seat.status == "unavailable":
                continue
            call = journal.get_by_id(seat.call_id)
            if (not call or call.status != "completed" or call.key.kind != "model.review"
                    or call.key.unit_id != unit.id):
                raise ReceiptError("review panel lacks a completed model receipt")
            saved = call.outcome.get("result") or {}
            if (saved.get("is_error") or not saved.get("terminal_received")
                    or digest(saved.get("text", "")) != seat.response_sha256
                    or parse_verdict(saved["text"]).findings != seat.findings):
                raise ReceiptError("review panel differs from original validated model response")
    if Path(state["worktree_path"]).resolve() == context.planning_path:
        raise ReceiptError("review source cannot be the planning snapshot")
    return record


def validate_state(state):
    """Validate retained review files before constructing an executor on CLI resume."""
    from blacksmith.run_context import context_from_state

    context = context_from_state(state)
    for row in (state.get("unit_outcomes") or {}).values():
        if row.get("human"):
            continue
        record = row.get("review_receipt")
        if record is None and not row.get("ok"):
            continue  # Failure occurred before review; do not invent a receipt.
        unit = context.prd().contract.work_unit_by_id(row["unit_id"])
        verify_receipt(record, state, unit=unit, base=row.get("base_commit"),
                       candidate=row.get("source_commit"), eligible=False)


def retry_overrides(state, context, amendment_id):
    """Keep completed valid seats, replace only unavailable/uncertain call identities."""
    from blacksmith.receipts import CallContext, attempt_identity

    unit_id = state["selected_unit"].id
    overrides = dict(state.get("recovery_call_overrides") or {})
    journal = context.journal()
    for seat in range(state.get("review_panel_size") or 1):
        attempt = attempt_identity(state) + f":review-seat:{seat}"
        call = CallContext(str(journal.path), context.run_id, journal.context_hash,
                           unit_id, attempt, overrides)
        operation = journal.get(call.key("model.review"))
        if operation is None:
            continue
        saved = (operation.outcome or {}).get("result") or {}
        valid = operation.status == "completed" and not saved.get("is_error") \
            and saved.get("terminal_received")
        if valid:
            try:
                parse_verdict(saved.get("text", ""))
            except ValueError:
                valid = False
        if not valid:
            overrides[encoded([unit_id, attempt, "model.review"])] = (
                attempt + ":retry:" + amendment_id[:16])
    return overrides
