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
    runner: Runner = subprocess_runner,
) -> PullRequest:
    """Push ``branch`` to ``remote`` and open a PR via ``gh``; return its URL."""
    worktree = Path(worktree_path)

    push = runner(["git", "push", "-u", remote, branch], worktree)
    if push.returncode != 0:
        raise PRError(f"git push failed: {push.stderr.strip() or push.stdout.strip()}")

    argv = ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body]
    if base:
        argv += ["--base", base]
    created = runner(argv, worktree)
    if created.returncode != 0:
        raise PRError(f"gh pr create failed: {created.stderr.strip() or created.stdout.strip()}")

    match = _PR_URL_RE.search(created.stdout)
    if not match:
        raise PRError(f"could not parse PR URL from gh output: {created.stdout!r}")
    return PullRequest(url=match.group(0), branch=branch)


def open_pr(state: BlacksmithState, *, runner: Runner = subprocess_runner) -> dict:
    """Graph node: open a PR for the selected unit. Failures halt rather than crash."""
    unit = state.get("selected_unit")
    worktree_path = state.get("worktree_path")
    if unit is None or not worktree_path:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "open_pr", "message": "missing selected_unit or worktree_path"}],
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
            runner=runner,
        )
    except PRError as exc:
        return {"status": Status.HALTED, "errors": [{"node": "open_pr", "message": str(exc)}]}
    return {"pr_url": pr.url, "status": Status.DONE}


def _pr_title(units: Sequence[WorkUnit]) -> str:
    """A single unit keeps the prior ``id: title`` form; multiple units name the span."""
    if len(units) == 1:
        return f"{units[0].id}: {units[0].title}"
    return f"{len(units)} work units ({units[0].id}..{units[-1].id})"


def _pr_body(state: BlacksmithState) -> str:
    units = list(state.get("work_units") or [])
    if not units and state.get("selected_unit") is not None:
        units = [state["selected_unit"]]
    impl = state.get("implementation") or {}
    results = state.get("test_results") or {}
    lines: list[str] = []
    if len(units) == 1:
        lines.append(f"**Unit:** {units[0].id} — {units[0].title}")
    elif units:
        # All units reached here only because every one passed its gate — name each.
        lines.append(f"**Units built ({len(units)}):**")
        lines.extend(f"- {u.id} — {u.title}" for u in units)
    files = impl.get("files_touched")
    if files:
        lines.append("**Files touched:** " + ", ".join(files))
    if impl.get("diff_summary"):
        lines.append(f"**Summary:** {impl['diff_summary']}")
    if results:
        verdict = "passed" if results.get("passed") else "failed"
        lines.append(f"**Test gate:** {verdict} (`{results.get('command', '')}`)")
    # If this run originated from a GitHub issue, link it so merging the PR closes it.
    # blacksmith only *links* the issue here — it never auto-merges or auto-closes (§5).
    issue_number = state.get("issue_number")
    if issue_number:
        lines.append("")
        lines.append(f"Closes #{issue_number}")
    lines.append("")
    lines.append("Opened by blacksmith for review — not auto-merged.")
    return "\n".join(lines)
