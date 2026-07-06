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
"""

from __future__ import annotations

import json
import re
from typing import Any

from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.nodes.plan import cost_event
from blacksmith.state import BlacksmithState

# Read-only tool surface (mirrors the plan node): the reviewer inspects the worktree's
# already-committed diff but can never change it.
_REVIEW_READ_ONLY = ["Read", "Glob", "Grep"]
_REVIEW_BLOCKED = [
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Bash", "BashOutput", "KillShell", "Agent", "Task", "ToolSearch", "WebSearch", "WebFetch",
]
_REVIEW_MAX_TURNS = 20

# A single fenced ```json ... ``` (or bare ```` ``` ````) block containing the verdict object.
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def review(state: BlacksmithState, *, executor: Executor | None = None) -> dict:
    if executor is None:
        return {}  # skeleton pass-through: no executor wired, nothing to review yet

    prd = state.get("prd")
    unit = state.get("selected_unit")
    if prd is None or unit is None:
        return {"errors": [{"node": "review", "message": "missing prd/selected_unit"}]}

    implementation = state.get("implementation") or {}
    result = executor.run_review(
        _review_prompt(unit, implementation),
        system_prompt=_system_prompt(prd.contract),
        cwd=state.get("worktree_path"),
        allowed_tools=_REVIEW_READ_ONLY,
        disallowed_tools=_REVIEW_BLOCKED,
        permission_mode="default",
        max_turns=_REVIEW_MAX_TURNS,
        raise_on_error=False,  # fail-open: a model error must never wedge a green unit
    )
    # Ledgered on every path (including a fail-open empty/unparseable verdict), same
    # discipline as plan/implement, so a review call's spend is never lost.
    event = cost_event("review", unit.id, result)
    findings = [] if result.is_error else _parse_findings(result.text or "")
    review_clean = not any(f.get("severity") == "blocking" for f in findings)
    return {
        "review_clean": review_clean,
        "review_findings": findings,
        "cost_events": [event],
    }


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


def _review_prompt(unit: WorkUnit, implementation: dict[str, Any]) -> str:
    files = implementation.get("files_touched") or []
    files_line = ", ".join(files) if files else "(none recorded)"
    diff_summary = implementation.get("diff_summary") or "(no diff summary available)"
    return (
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
