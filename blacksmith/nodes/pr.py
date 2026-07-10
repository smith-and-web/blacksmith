"""PR node — open a pull request on the target repo (PRD §4 node 8, §5).

Pushes the unit's branch and runs ``gh pr create`` with a generated summary,
recording the PR URL in state. PRs are **never auto-merged** (§5) — this node only
opens them.

Shelling out is routed through an injectable ``Runner`` so tests can mock ``gh`` (and
run git for real against a scratch repo). The graph node reads the runner from the
LangGraph config (``configurable.pr_runner``), defaulting to a real subprocess runner.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from blacksmith.contract import WorkUnit
from blacksmith.state import BlacksmithState, Status
from blacksmith.worktree import branch_for

_PR_URL_RE = re.compile(r"https://\S+/pull/\d+")


class PRError(Exception):
    """Raised when pushing the branch or creating the PR fails."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], "Path | None"], CommandResult]


@dataclass(frozen=True)
class PullRequest:
    url: str
    branch: str


def subprocess_runner(argv: Sequence[str], cwd: Path | None = None) -> CommandResult:
    proc = subprocess.run(
        list(argv), cwd=str(cwd) if cwd else None, capture_output=True, text=True
    )
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def open_pull_request(
    *,
    worktree_path: str | Path,
    branch: str,
    title: str,
    body: str,
    base: str | None = None,
    remote: str = "origin",
    draft: bool = False,
    runner: Runner = subprocess_runner,
) -> PullRequest:
    """Push ``branch`` to ``remote`` and open a PR via ``gh``; return its URL.

    Pass ``draft=True`` to open a draft PR (``gh pr create --draft``) — used for
    human-gated units awaiting QA. The default opens a normal PR, unchanged.
    """
    worktree = Path(worktree_path)

    push = runner(["git", "push", "-u", remote, branch], worktree)
    if push.returncode != 0:
        raise PRError(f"git push failed: {push.stderr.strip() or push.stdout.strip()}")

    argv = ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body]
    if base:
        argv += ["--base", base]
    if draft:
        argv += ["--draft"]
    created = runner(argv, worktree)
    if created.returncode != 0:
        raise PRError(f"gh pr create failed: {created.stderr.strip() or created.stdout.strip()}")

    match = _PR_URL_RE.search(created.stdout)
    if not match:
        raise PRError(f"could not parse PR URL from gh output: {created.stdout!r}")
    return PullRequest(url=match.group(0), branch=branch)


def open_pr(state: BlacksmithState, *, runner: Runner = subprocess_runner) -> dict:
    """Graph node: open a PR for the selected unit. Failures halt rather than crash."""
    return _open_pr(state, runner=runner, draft=False, done=Status.DONE, node="open_pr")


def open_draft_pr(state: BlacksmithState, *, runner: Runner = subprocess_runner) -> dict:
    """Graph node: open a DRAFT PR for a human-gated unit that implemented successfully.

    Mirrors ``open_pr`` but passes ``--draft`` and ends the run at ``AWAITING_QA`` (its
    work parked behind a draft PR for manual QA), not ``DONE``. Because ``pr_url`` is set,
    cleanup preserves the branch — the draft PR needs it. The QA itself happens on the
    draft PR; this node opens no automated gate (PRD §4 human-gated routing)."""
    return _open_pr(
        state, runner=runner, draft=True, done=Status.AWAITING_QA, node="open_draft_pr"
    )


def _open_pr(
    state: BlacksmithState, *, runner: Runner, draft: bool, done: Status, node: str
) -> dict:
    unit = state.get("selected_unit")
    worktree_path = state.get("worktree_path")
    if unit is None or not worktree_path:
        return {
            "status": Status.HALTED,
            "errors": [{"node": node, "message": "missing selected_unit or worktree_path"}],
        }
    units = _built_units(state)
    try:
        pr = open_pull_request(
            # One combined PR against the run's single shared branch, covering every unit
            # built this run; falls back to the unit's own branch when the shared-branch
            # field is absent (e.g. a single-unit node-level call in tests).
            worktree_path=worktree_path,
            branch=state.get("branch") or branch_for(unit.id),
            title=_pr_title(units),
            body=_pr_body(state, units),
            # The target repo's default branch (``[target].default_branch``), seeded into
            # state by prepare_worktree. When present, the PR is opened against it
            # explicitly; when absent (a graph compiled without it, e.g. tests) ``base`` is
            # None and gh falls back to the repo's own default, exactly as before.
            base=state.get("default_branch"),
            draft=draft,
            runner=runner,
        )
    except PRError as exc:
        return {"status": Status.HALTED, "errors": [{"node": node, "message": str(exc)}]}
    return {"pr_url": pr.url, "status": done}


def _built_units(state: BlacksmithState) -> list[WorkUnit]:
    """The units actually built this run, in declaration order.

    Derived from the built-set signal — the accumulated ``unit_results`` (one record per
    unit whose gate passed) plus the current ``selected_unit`` (the human-gated unit being
    drafted, which skips the auto gate and so is absent from ``unit_results``) — NOT the
    full ``work_units`` DAG. So a mid-DAG draft PR names only the units really built, never
    later units that were never reached. At the terminal auto-PR every unit has passed its
    gate (and the last is ``selected_unit``), so this still yields the whole DAG.

    Falls back to the lone ``selected_unit`` when ``work_units`` is absent (a single-unit
    node-level call in tests)."""
    selected = state.get("selected_unit")
    built_ids = {r.get("unit_id") for r in state.get("unit_results") or []}
    if selected is not None:
        built_ids.add(selected.id)
    units = [u for u in (state.get("work_units") or []) if u.id in built_ids]
    if units:
        return units
    return [selected] if selected is not None else []


def _pr_title(units: Sequence[WorkUnit]) -> str:
    """A single unit keeps the prior ``id: title`` form; multiple units name the span."""
    if len(units) == 1:
        return f"{units[0].id}: {units[0].title}"
    return f"{len(units)} work units ({units[0].id}..{units[-1].id})"


def _change_lines(
    files: Sequence[str] | None,
    diff_summary: str | None,
    results: dict | None,
    *,
    indent: str = "",
) -> list[str]:
    """Render the files/summary/test-gate lines for one unit's changes."""
    lines: list[str] = []
    if files:
        lines.append(f"{indent}**Files touched:** " + ", ".join(files))
    if diff_summary:
        lines.append(f"{indent}**Summary:** {diff_summary}")
    if results:
        verdict = "passed" if results.get("passed") else "failed"
        lines.append(f"{indent}**Test gate:** {verdict} (`{results.get('command', '')}`)")
    return lines


def _dedup_findings(findings) -> list[dict]:
    """De-dupe findings by (file, detail), preserving first-seen order. ``review_findings``
    accumulates across units and re-review passes, so the same note can appear more than once."""
    seen: set = set()
    out: list[dict] = []
    for finding in findings:
        key = (finding.get("file"), finding.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def _reviewer_notes_lines(state: BlacksmithState) -> list[str]:
    """The "Reviewer notes" section (WU-REVIEW-RENDER): sourced straight from state so it
    needs no plumbing through the gate payload. Surfaces BOTH the unresolved BLOCKING findings
    the post-gate review loop gave up on AND the reviewer's ADVISORY findings — non-blocking
    notes that never enter ``unresolved_review_findings`` (so they were silently dropped from
    the PR before) but are still worth a reviewer's eyes. Only a review with neither collapses
    to a single "Reviewer: clean" line."""
    unresolved = state.get("unresolved_review_findings") or []
    advisory = _dedup_findings(
        f for f in (state.get("review_findings") or []) if f.get("severity") == "advisory"
    )
    lines = ["", "**Reviewer notes:**"]
    if not unresolved and not advisory:
        lines.append("Reviewer: clean")
        return lines
    revisions = state.get("review_revisions", 0)
    if revisions:
        lines.append(f"- resolved via revision: {revisions}")
    for finding in unresolved:
        lines.append(
            f"- unresolved (blocking): {finding.get('file', '(unknown file)')}: "
            f"{finding.get('detail', '')}"
        )
    for finding in advisory:
        lines.append(
            f"- advisory: {finding.get('file', '(unknown file)')}: {finding.get('detail', '')}"
        )
    return lines


def _pr_body(state: BlacksmithState, units: Sequence[WorkUnit] | None = None) -> str:
    units = list(_built_units(state) if units is None else units)
    lines: list[str] = []
    if len(units) == 1:
        # Single-unit body is unchanged: the last-write-wins implementation/test_results
        # describe the lone unit.
        impl = state.get("implementation") or {}
        results = state.get("test_results") or {}
        lines.append(f"**Unit:** {units[0].id} — {units[0].title}")
        lines.extend(_change_lines(impl.get("files_touched"), impl.get("diff_summary"), results))
    elif units:
        # Summarize each built unit from its OWN retained result (unit_results), so a unit's
        # files/summary are attributed to that unit rather than lumped under the last unit's
        # implementation. A mid-DAG draft PR's human-gated unit has no retained result yet
        # (it skipped the auto gate), so it appears as a bare bullet — which is correct.
        by_id = {r.get("unit_id"): r for r in state.get("unit_results") or []}
        lines.append(f"**Units built ({len(units)}):**")
        for u in units:
            lines.append(f"- {u.id} — {u.title}")
            result = by_id.get(u.id)
            if result is not None:
                lines.extend(
                    _change_lines(
                        result.get("files_touched"),
                        result.get("diff_summary"),
                        {"passed": True, "command": result.get("test_command", "")},
                        indent="  ",
                    )
                )
    lines.extend(_reviewer_notes_lines(state))
    # If this run originated from a GitHub issue, link it so merging the PR closes it.
    # blacksmith only *links* the issue here — it never auto-merges or auto-closes (§5).
    issue_number = state.get("issue_number")
    if issue_number:
        lines.append("")
        lines.append(f"Closes #{issue_number}")
    lines.append("")
    lines.append("Opened by blacksmith for review — not auto-merged.")
    return "\n".join(lines)
