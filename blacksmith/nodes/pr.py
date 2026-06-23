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
    units = state.get("work_units") or [unit]
    try:
        pr = open_pull_request(
            # One combined PR against the run's single shared branch, covering every unit
            # built this run; falls back to the unit's own branch when the shared-branch
            # field is absent (e.g. a single-unit node-level call in tests).
            worktree_path=worktree_path,
            branch=state.get("branch") or branch_for(unit.id),
            title=_pr_title(units),
            body=_pr_body(state),
            draft=draft,
            runner=runner,
        )
    except PRError as exc:
        return {"status": Status.HALTED, "errors": [{"node": node, "message": str(exc)}]}
    return {"pr_url": pr.url, "status": done}


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


def _pr_body(state: BlacksmithState) -> str:
    units = list(state.get("work_units") or [])
    if not units and state.get("selected_unit") is not None:
        units = [state["selected_unit"]]
    lines: list[str] = []
    if len(units) == 1:
        # Single-unit body is unchanged: the last-write-wins implementation/test_results
        # describe the lone unit.
        impl = state.get("implementation") or {}
        results = state.get("test_results") or {}
        lines.append(f"**Unit:** {units[0].id} — {units[0].title}")
        lines.extend(_change_lines(impl.get("files_touched"), impl.get("diff_summary"), results))
    elif units:
        # All units reached here only because every one passed its gate — summarize each
        # from its OWN retained result (unit_results), so a unit's files/summary are
        # attributed to that unit rather than lumped under the last unit's implementation.
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
    # If this run originated from a GitHub issue, link it so merging the PR closes it.
    # blacksmith only *links* the issue here — it never auto-merges or auto-closes (§5).
    issue_number = state.get("issue_number")
    if issue_number:
        lines.append("")
        lines.append(f"Closes #{issue_number}")
    lines.append("")
    lines.append("Opened by blacksmith for review — not auto-merged.")
    return "\n".join(lines)
