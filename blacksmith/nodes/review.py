"""Review node — a stronger model adversarially reviews the passing unit's diff
(WU-REVIEW-NODE, PRD §4 additive review loop).

Runs ONLY on the test gate's PASS branch (wiring that routing is a separate unit; this
module only adds the node itself). It is deliberately a SECOND, independent pass on top
of the gate: the gate already proved the unit's own tests pass, so this node asks a
stronger model (``config.models.review``, PRD §8's dedicated key) to look for BLOCKING
correctness/regression bugs the tests missed — never style or formatting, which is the
linter's job and already covered by the gate's own lint step.

Tool surface is read-only (Read/Glob/Grep) — same shape as the plan node's read-only
call, but here the model reads the ALREADY-COMMITTED worktree to inspect the unit's
actual diff rather than reasoning about a not-yet-written change. No Write/Edit/Bash:
the reviewer can only flag, never fix (a revision retry, if any, re-enters implement).

The model is prompted to emit a single fenced JSON verdict:
``{"verdict": "clean"|"needs_changes", "findings": [{"severity": "blocking"|"advisory",
"file": str, "detail": str}]}``. The node parses that into ``review_clean`` (bool) and
``review_findings`` (list, append-only reducer). Fail-open by design: an unparseable
verdict, an empty response, or a failed model call is treated as clean with no
findings — review is an additive safety net and must never itself wedge a green unit.
``review_clean`` is derived from the presence of a BLOCKING finding (not the top-level
``verdict`` string alone), so a verdict that says "needs_changes" but lists only
advisory findings still reports clean — advisory findings are surfaced, not gating.

One cost_event is recorded per call (node="review"), same ledger as plan/implement
(WU-COST-EVENTS), so a review call's spend is never lost.

WU-REVIEW-PANEL-NODE: the node runs ``state["review_panel_size"]`` (default 1, seeded by
``prepare_worktree`` from ``config.review.panel_size``) independent ``run_review`` calls
instead of just one. With ``panel_size == 1`` this is BYTE-FOR-BYTE today's behaviour: one
call, the current neutral prompt, no emphasis text. With ``panel_size > 1``, each call gets
a distinct EMPHASIS (correctness / security / regression / edge-cases, cycling) appended to
its prompt so the panel covers diverse perspectives, and every call still ledgers its own
cost_event. The N calls' parsed findings-lists are fed through
``aggregate_panel_verdicts`` (WU-REVIEW-PANEL-AGGREGATE) to produce the same
``review_clean``/``review_findings`` keys the revise loop already consumes -- the loop
itself, its routing, and the gate are all unchanged.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.nodes.plan import cost_event
from blacksmith.state import BlacksmithState

# Read-only tool surface (mirrors the plan node): the reviewer inspects the worktree's
# already-committed diff but can never change it.
_REVIEW_READ_ONLY = ["Read", "Glob", "Grep"]
# NB: no "MultiEdit" — the Agent SDK folded it into Edit and no longer knows that name, so
# denying it just logs "Permission deny rule 'MultiEdit' matches no known tool". Edit / Write /
# NotebookEdit still cover every write path for the read-only reviewer.
_REVIEW_BLOCKED = [
    "Write", "Edit", "NotebookEdit",
    "Bash", "BashOutput", "KillShell", "Agent", "Task", "ToolSearch", "WebSearch", "WebFetch",
]
_REVIEW_MAX_TURNS = 20

# A single fenced ```json ... ``` (or bare ```` ``` ````) block containing the verdict object.
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Built-in emphasis rotation for a panel of reviewers (WU-REVIEW-PANEL-NODE). Cycled by
# call index so a panel_size > len(_PANEL_EMPHASES) just repeats the rotation.
_PANEL_EMPHASES = ("correctness", "security", "regression", "edge-cases")


def review(state: BlacksmithState, *, executor: Executor | None = None) -> dict:
    if executor is None:
        return {}  # skeleton pass-through: no executor wired, nothing to review yet

    prd = state.get("prd")
    unit = state.get("selected_unit")
    if prd is None or unit is None:
        return {"errors": [{"node": "review", "message": "missing prd/selected_unit"}]}

    implementation = state.get("implementation") or {}
    system_prompt = _system_prompt(prd.contract)
    panel_size = state.get("review_panel_size") or 1
    emphases = _panel_emphases(panel_size)

    cost_events = []
    findings_by_reviewer: list[list[dict]] = []
    for emphasis in emphases:
        result = executor.run_review(
            _review_prompt(unit, implementation, emphasis=emphasis),
            system_prompt=system_prompt,
            cwd=state.get("worktree_path"),
            allowed_tools=_REVIEW_READ_ONLY,
            disallowed_tools=_REVIEW_BLOCKED,
            permission_mode="default",
            max_turns=_REVIEW_MAX_TURNS,
            raise_on_error=False,  # fail-open: a model error must never wedge a green unit
        )
        # Ledgered on every call (including a fail-open empty/unparseable verdict), same
        # discipline as plan/implement, so no reviewer's spend is ever lost.
        cost_events.append(cost_event("review", unit.id, result))
        findings_by_reviewer.append(
            [] if result.is_error else _parse_findings(result.text or "")
        )

    review_clean, findings = aggregate_panel_verdicts(findings_by_reviewer)
    return {
        "review_clean": review_clean,
        "review_findings": findings,
        "cost_events": cost_events,
    }


def _panel_emphases(panel_size: int) -> list[str | None]:
    """One entry per ``run_review`` call the node should make.

    ``panel_size <= 1`` yields ``[None]`` -- a single call with no emphasis appended,
    keeping the prompt (and therefore the node's output) BYTE-FOR-BYTE identical to
    today's single-reviewer behaviour. ``panel_size > 1`` yields that many distinct
    emphases cycling through the built-in rotation.
    """
    if panel_size <= 1:
        return [None]
    return [_PANEL_EMPHASES[i % len(_PANEL_EMPHASES)] for i in range(panel_size)]


def _parse_findings(text: str) -> list[dict]:
    """Parse the model's fenced JSON verdict into a findings list.

    Fail-open: any unparseable input (no fenced block, invalid JSON, wrong shape) returns
    an empty list — the caller then reports ``review_clean=True`` since there is no
    blocking finding to report.
    """
    block = _extract_json_block(text)
    if block is None:
        return []
    try:
        parsed = json.loads(block)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    findings = parsed.get("findings")
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, dict)]


def _extract_json_block(text: str) -> str | None:
    if not text or not text.strip():
        return None
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    return None


def aggregate_panel_verdicts(
    findings_by_reviewer: list[list[dict]],
) -> tuple[bool, list[dict]]:
    """Aggregate N reviewers' findings-lists into a single ``(review_clean, findings)``.

    Pure function — no executor/graph wiring. This only changes how ``review_clean`` /
    ``review_findings`` are COMPUTED for panel_size > 1; the single-reviewer path
    (panel_size == 1, i.e. a list of exactly one findings-list) keeps today's semantics
    EXACTLY: ``review_clean`` is the same presence-of-a-blocking-finding check the
    pre-panel code always ran, and ``findings`` is that ONE reviewer's list returned
    VERBATIM -- no ``(file, detail)`` de-dup pass -- so a reviewer that (degenerately)
    reports the same finding twice is reproduced byte-for-byte, exactly as the
    pre-panel path would.

    A reviewer "votes blocking" iff its own findings-list contains at least one
    severity=="blocking" entry. For panel_size > 1, ``review_clean`` is True iff FEWER
    than a majority (``ceil(n/2)``) of reviewers voted blocking -- so a unit is only
    sent back for revision on majority consensus among the panel, not a single
    dissenting vote.

    For panel_size > 1, ``findings`` is the union of every reviewer's findings,
    de-duped by the ``(file, detail)`` pair (first occurrence wins, severity
    preserved), preserving reviewer order and within-reviewer order. This dedup pass
    only ever applies across MULTIPLE reviewers' lists -- the single-reviewer path
    above never runs it.
    """
    n = len(findings_by_reviewer)
    if n == 0:
        return True, []
    if n == 1:
        # Single-reviewer path: return verbatim, no dedup -- BYTE-FOR-BYTE today's
        # pre-panel behaviour, including a degenerate duplicate finding.
        findings = findings_by_reviewer[0]
        review_clean = not any(f.get("severity") == "blocking" for f in findings)
        return review_clean, list(findings)

    blocking_votes = sum(
        1
        for findings in findings_by_reviewer
        if any(f.get("severity") == "blocking" for f in findings)
    )
    review_clean = blocking_votes < math.ceil(n / 2)

    seen: set[tuple[Any, Any]] = set()
    union_findings: list[dict] = []
    for findings in findings_by_reviewer:
        for finding in findings:
            key = (finding.get("file"), finding.get("detail"))
            if key in seen:
                continue
            seen.add(key)
            union_findings.append(finding)

    return review_clean, union_findings


def _review_prompt(
    unit: WorkUnit, implementation: dict[str, Any], *, emphasis: str | None = None
) -> str:
    files = implementation.get("files_touched") or []
    files_line = ", ".join(files) if files else "(none recorded)"
    diff_summary = implementation.get("diff_summary") or "(no diff summary available)"
    prompt = (
        "This work unit's implementation has ALREADY PASSED the project's automated test "
        "gate. Adversarially review its diff for BLOCKING correctness or regression bugs "
        "the tests missed. Do NOT flag style, formatting, or taste issues — that is the "
        "linter's job, not yours. Use Read/Glob/Grep to inspect the actual changed files; "
        "you have no Write, Edit, or Bash access and cannot change anything.\n\n"
        f"Unit {unit.id}: {unit.title}\n"
        f"Layers: {', '.join(unit.layers)}\n"
        f"Target modules: {', '.join(unit.target_modules)}\n"
        f"Test contract (already passing): {unit.test_contract}\n"
        f"Files touched: {files_line}\n"
        f"Diff summary:\n{diff_summary}\n\n"
        "Respond with EXACTLY ONE fenced JSON code block and nothing else of substance "
        "outside it:\n"
        '```json\n'
        '{"verdict": "clean" or "needs_changes", "findings": '
        '[{"severity": "blocking" or "advisory", "file": "<path>", "detail": "<what and '
        'why>"}]}\n'
        '```\n'
        'Only "blocking" findings will halt anything — reserve it for real correctness '
        "bugs and regressions; use \"advisory\" for anything else worth mentioning."
    )
    if emphasis is not None:
        # WU-REVIEW-PANEL-NODE: one panel seat's rotation-assigned emphasis. Distinctly
        # worded ("PANEL EMPHASIS") so it never collides with the neutral prompt text
        # above, which already mentions "correctness"/"regression" generically.
        prompt += (
            f"\n\nPANEL EMPHASIS for this pass: {emphasis}. Weight your review toward "
            f"{emphasis} issues in particular, without ignoring other categories."
        )
    return prompt


def _system_prompt(contract: PRDContract) -> str:
    untouchables = "\n".join(f"- {item}" for item in contract.untouchables)
    return (
        f"You are blacksmith's reviewer for the {contract.component} project "
        f"({contract.primary_target_repo}). A unit's implementation has ALREADY PASSED the "
        "automated test gate; you are a stronger-model adversarial second look for BLOCKING "
        "correctness or regression bugs the tests missed — never style, which is the "
        "linter's job. You have READ-ONLY tools and cannot change anything.\n\n"
        "CONSTITUTION — these are inviolable; a change that touches them without explicit "
        f"human sign-off is itself a blocking finding:\n{untouchables}"
    )
