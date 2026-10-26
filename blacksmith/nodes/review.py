"""Read-only, cumulative exact-candidate review with honest fail-closed receipts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from blacksmith.budget import AdmissionDenied
from blacksmith.config import IndexConfig
from blacksmith.context import render_context, requirements_prd
from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor, ExecutorResult, ModelCallStopped
from blacksmith.index import (
    INDEX_MCP_SERVER_NAME,
    QUALIFIED_INDEX_TOOL_NAMES,
    create_index_mcp_server,
    index_tools_prompt_section,
)
from blacksmith.nodes.plan import cost_event
from blacksmith.plan_artifacts import PlanIntegrityError, verify_approved_plan
from blacksmith.receipts import ReceiptError, attempt_identity, call_context_for, digest, encoded
from blacksmith.review_patch import (
    DIFF_SERVER,
    DIFF_TOOL,
    INLINE_PATCH_BYTES,
    capture_patch,
    diff_server,
    prompt_patch,
    verify_patch,
)
from blacksmith.review_receipts import (
    PanelResult,
    ReviewReceipt,
    aggregate_panel,
    artifact,
    parse_verdict,
    retain,
)
from blacksmith.run_context import context_from_state
from blacksmith.state import BlacksmithState, Status
from blacksmith.verification import candidate_identity, required_layers, verify_gate_receipt

_REVIEW_READ_ONLY = ["Read", "Glob", "Grep"]
_REVIEW_BLOCKED = [
    "Write", "Edit", "NotebookEdit", "Bash", "BashOutput", "KillShell",
    "Agent", "Task", "ToolSearch", "WebSearch", "WebFetch",
]
_REVIEW_MAX_TURNS = 20
_PANEL_EMPHASES = ("correctness", "security", "regression", "edge-cases")


def _costs(context, record):
    result = []
    for call_id in record.cost_references:
        operation = context.journal().get_by_id(call_id)
        if operation is None or not (operation.outcome or {}).get("result"):
            continue  # Admission denial/interruption may have no measured usage.
        saved = ExecutorResult(**operation.outcome["result"])
        result.append(cost_event("review", record.unit_id, saved))
    return result


def _output(record, costs):
    result = {
        "review_receipt": record,
        "review_clean": record.status == "clean",
        "review_findings": [f.model_dump() for f in record.findings],
        "cost_events": costs,
    }
    if record.status == "unavailable":
        result.update(status=Status.HALTED, unit_failed_stage="review",
                      errors=[{"node": "review", "message": "review unavailable: "
                               + record.diagnostic}])
    return result


def review(state: BlacksmithState, *, executor: Executor | None = None,
           index_config: IndexConfig | None = None) -> dict:
    enabled = state.get("review_enabled", executor is not None)
    prd, unit = requirements_prd(state), state.get("selected_unit")
    if prd is None or unit is None:
        if not enabled and state.get("state_version") is None:
            identity = digest(encoded(["disabled", getattr(unit, "id", "__none__"),
                                       attempt_identity(state)]))
            return _output(ReviewReceipt(receipt_id=identity, run_id="legacy",
                unit_id=getattr(unit, "id", "__none__"), attempt_id=attempt_identity(state),
                base_commit=None, candidate_commit=None, candidate_tree=None,
                status="disabled"), [])
        return {"status": Status.HALTED, "review_clean": False, "review_receipt": None,
                "errors": [{"node": "review", "message": "missing prd/selected_unit"}]}
    try:
        verify_approved_plan(state, unit)
    except PlanIntegrityError as exc:
        return {"status": Status.HALTED, "review_clean": False, "review_receipt": None,
                "errors": [{"node": "review", "message": str(exc)}]}

    panel_size = state.get("review_panel_size") or 1
    indexed = bool(index_config and index_config.enabled)
    context = context_from_state(state) if state.get("run_context") else None
    if context:
        saved = context.execution_config().review
        if state.get("review_version") != 1:
            raise ReceiptError("legacy_review_requires_manual_recovery")
        if enabled != saved.enabled or panel_size != saved.panel_size:
            raise ReceiptError("review policy differs from immutable execution configuration")
    main = call_context_for(state, unit_id=unit.id)
    base = state.get("unit_base_commit") or state.get("pre_implement_ref")
    candidate = tree = None
    identity_error = ""
    try:
        candidate, tree, dirty = candidate_identity(state["worktree_path"])
        if dirty:
            raise ValueError("review requires a clean committed candidate")
        if context:
            verify_gate_receipt(state.get("gate_receipt"), state, layers=required_layers(state))
    except ReceiptError:
        raise
    except Exception as exc:
        identity_error = str(exc)[:2048] if enabled or context else ""

    calls = [call_context_for(state, unit_id=unit.id,
                             attempt_id=attempt_identity(state) + f":review-seat:{seat}")
             for seat in range(panel_size)] if enabled else []
    keys = [c.key("model.review").id if c else f"legacy-seat:{i}"
            for i, c in enumerate(calls)]
    kind = "review.verdict:" + digest(encoded(
        [1, enabled, panel_size, indexed, keys, state.get("review_retry_token", "")]))[:24]
    key = main.key(kind) if main else None
    receipt_id = key.id if key else digest(encoded([unit.id, base, candidate, kind]))
    path = str(artifact(context, key)) if context else ""
    if context:
        context.journal().intent(key, {
            "base_commit": base, "candidate_commit": candidate, "candidate_tree": tree,
            "enabled": enabled, "panel_size": panel_size, "review_calls": keys,
            "requirements_sha256": digest(render_context(state, unit, include_plan=True)),
        })
        cached = retain(context, key)
        if cached:
            if identity_error:
                raise ReceiptError("review candidate changed before receipt reuse: "
                                   + identity_error)
            if cached.patch:
                verify_patch(cached.patch, root=context.directory / "reviews" / "patches")
            return _output(cached, _costs(context, cached))

    common = dict(receipt_id=receipt_id, run_id=context.run_id if context else "legacy",
                  unit_id=unit.id, attempt_id=key.attempt_id if key else attempt_identity(state),
                  base_commit=base, candidate_commit=candidate, candidate_tree=tree,
                  artifact_path=path)
    patch, panels, costs = None, [], []
    diagnostic = identity_error
    if not enabled and not identity_error:
        status, findings = "disabled", ()
    else:
        try:
            if identity_error:
                raise ValueError(identity_error)
            if executor is None:
                raise ValueError("review enabled but executor is unavailable")
            patch = capture_patch(state, context=context)
            system_prompt = _system_prompt(prd.contract, index_enabled=indexed,
                                           worktree_path=state.get("worktree_path"))
            allowed, servers = list(_REVIEW_READ_ONLY), {}
            if indexed:
                allowed.extend(QUALIFIED_INDEX_TOOL_NAMES)
                servers[INDEX_MCP_SERVER_NAME] = create_index_mcp_server(
                    state["worktree_path"], exclude=index_config.exclude)
            if patch.bytes > INLINE_PATCH_BYTES:
                allowed.append(DIFF_TOOL)
                servers[DIFF_SERVER] = diff_server(patch)
            for seat, emphasis in enumerate(_panel_emphases(panel_size)):
                call = calls[seat]
                result = None
                try:
                    kwargs = dict(system_prompt=system_prompt, cwd=state["worktree_path"],
                                  allowed_tools=allowed, tools=list(_REVIEW_READ_ONLY),
                                  disallowed_tools=_REVIEW_BLOCKED, permission_mode="default",
                                  max_turns=_REVIEW_MAX_TURNS, raise_on_error=False)
                    if servers:
                        kwargs["mcp_servers"] = servers
                    if call:
                        kwargs["call_context"] = call
                    result = executor.run_review(
                        _review_prompt(unit, state.get("implementation") or {}, emphasis=emphasis)
                        + "\n\n" + prompt_patch(patch)
                        + render_context(state, unit, include_plan=True), **kwargs)
                    costs.append(cost_event("review", unit.id, result))
                    if result.is_error or not result.terminal_received:
                        raise ValueError("review model result is unavailable/error/nonterminal")
                    verdict = parse_verdict(result.text or "")
                    status = "blocking" if any(f.severity == "blocking" for f in verdict.findings) \
                        else "clean"
                    panels.append(PanelResult(seat=seat, status=status,
                        findings=verdict.findings, call_id=result.call_id,
                        response_sha256=digest(result.text or "")))
                except ReceiptError as exc:
                    if not isinstance(exc, (AdmissionDenied, ModelCallStopped)) and not str(
                            exc).startswith("interrupted_attempt_requires_resume:"):
                        raise  # Critical persistence/integrity errors are never advisory.
                    panels.append(PanelResult(seat=seat, status="unavailable",
                                  call_id=call.key("model.review").id if call else None,
                                  diagnostic=str(exc)[:2048]))
                    break
                except Exception as exc:
                    panels.append(PanelResult(seat=seat, status="unavailable",
                        call_id=result.call_id if result else None,
                        response_sha256=digest(result.text or "") if result else None,
                        diagnostic=str(exc)[:2048]))
                    break  # No automatic retry or later seat after unavailable admission/result.
            status, findings = aggregate_panel(panels)
            diagnostic = "; ".join(p.diagnostic for p in panels if p.diagnostic)
            if candidate_identity(state["worktree_path"]) != (candidate, tree, ""):
                status, diagnostic = "unavailable", "review candidate changed during review"
        except ReceiptError:
            raise
        except Exception as exc:
            status, findings, diagnostic = "unavailable", (), str(exc)[:2048]
    record = ReviewReceipt(**common, status=status, findings=findings, panel_results=tuple(panels),
                           cost_references=tuple(p.call_id for p in panels if p.call_id),
                           patch=patch, diagnostic=diagnostic)
    if context:
        record = retain(context, key, record)
        known = {c.get("event_id") for c in costs}
        costs.extend(c for c in _costs(context, record) if c.get("event_id") not in known)
    return _output(record, costs)


def _panel_emphases(panel_size):
    return [None] if panel_size <= 1 else [
        _PANEL_EMPHASES[i % len(_PANEL_EMPHASES)] for i in range(panel_size)]


def _parse_findings(text):
    return [f.model_dump() for f in parse_verdict(text).findings]


def aggregate_panel_verdicts(findings_by_reviewer):
    """Compatibility helper: any validated blocking vote blocks, including a minority."""
    from blacksmith.review_receipts import ValidatedFinding
    rows = [PanelResult(seat=i, status="blocking" if any(
        f.get("severity") == "blocking" for f in findings) else "clean",
        findings=tuple(ValidatedFinding.model_validate(f) for f in findings))
        for i, findings in enumerate(findings_by_reviewer)]
    status, findings = aggregate_panel(rows)
    # Preserve the single-seat list API; multiple-seat dedup may never hide a blocker.
    if len(findings_by_reviewer) == 1:
        return status == "clean", list(findings_by_reviewer[0])
    return status == "clean", [f.model_dump() for f in findings]


def _review_prompt(unit: WorkUnit, implementation: dict[str, Any], *, emphasis=None):
    prompt = (
        "This work unit's implementation has ALREADY PASSED the project's automated test "
        "gate. Adversarially review the FULL CUMULATIVE PATCH for BLOCKING correctness or "
        "regression bugs the tests missed. Include deletions and renames; current files "
        "alone cannot show removed behavior. Revisions are cumulative from the same unit "
        "base, so earlier defects must be verified again. Never treat a diffstat as review "
        "evidence. Do not flag style/formatting/taste. Use read-only tools to inspect context; "
        "you have no Write, Edit or Bash access.\n\n"
        f"Unit {unit.id}: {unit.title}\nLayers: {', '.join(unit.layers)}\n"
        f"Target modules: {', '.join(unit.target_modules)}\n"
        f"Test contract (already passing): {unit.test_contract}\n"
        f"Files touched: {', '.join(implementation.get('files_touched') or [])}\n"
        f"Diff summary (orientation only):\n{implementation.get('diff_summary', '')}\n\n"
        'Respond with EXACTLY ONE fenced JSON block and no surrounding prose: '
        '{"verdict":"clean" or "needs_changes","findings":'
        '[{"severity":"blocking" or "advisory","file":"<path>","detail":"<what and why>"}]}. '
        "All fields are required; no extra fields or unknown severities. Any blocking "
        "finding from any panelist blocks the candidate."
    )
    if emphasis is not None:
        prompt += f"\n\nPANEL EMPHASIS for this pass: {emphasis}. Inspect all relevant categories."
    return prompt


def _system_prompt(
    contract: PRDContract,
    *,
    index_enabled: bool = False,
    worktree_path: str | Path | None = None,
) -> str:
    prompt = (
        f"You are blacksmith's reviewer for the {contract.component} project "
        f"({contract.primary_target_repo}). A unit's implementation has ALREADY PASSED the "
        "automated test gate; you are a stronger-model adversarial second look for BLOCKING "
        "correctness or regression bugs the tests missed — never style, which is the "
        "linter's job. You have READ-ONLY tools and cannot change anything.\n\n"
        "CONSTITUTION — these are inviolable; a change that touches them without explicit "
        "human sign-off is itself a blocking finding:\n"
        + "\n".join(f"- {item}" for item in contract.untouchables)
    )
    # Index guidance (parity with the implement/plan tiers): the reviewer was granted the
    # index TOOLS by WU-REVIEW-INDEX but never the instructions, and the observed result
    # was full-file Reads and zero/low index use. Same additive gating as the tool wiring:
    # with the index disabled this prompt is byte-for-byte unchanged.
    if index_enabled:
        prompt += (
            "\n\n"
            + index_tools_prompt_section()
            + " These tools are read-only, which is all you need. Reach for Read only when "
            "the index cannot answer your question, and read a slice (offset/limit), not the "
            "whole file, when you do."
        )
        if worktree_path:
            worktree_abs = str(Path(worktree_path).resolve())
            prompt += (
                f"\n\nThis worktree's absolute path is: {worktree_abs}\n"
                "Every Read tool call MUST use an absolute path under that worktree path — "
                "never a relative path, and never a guessed root like /repo or another "
                "user's home directory. Guessed paths have been observed to waste turns "
                "failing."
            )
    return prompt
