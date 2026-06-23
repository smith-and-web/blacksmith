"""Implement node — the executor writes the unit's code in the worktree (PRD §4 node 5).

The untouchables (PRD §7) are enforced two ways, per the chosen design:
1. **Constitution** — the untouchables are in the executor's system prompt.
2. **Hard pre-edit guard** — a ``can_use_tool`` callback BLOCKS any file write/edit
   whose target matches a protected path and records the attempt, so the agent
   literally cannot touch an untouchable path (the #1 failure to prevent, §7 / AC-7).
   Bash is disallowed for the implementer in v0, so the guard only needs to cover
   direct file writes; the behavioral untouchables ("no AI/cloud/subscription code",
   brand aesthetics) ride the constitution + the human PR gate.

After the agent runs, the node stages, captures, and commits the worktree diff into
``state["implementation"]`` so the PR node has something to push. Without an executor
wired (skeleton/tests), it is a status-only pass-through.
"""

from __future__ import annotations

import fnmatch
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.state import BlacksmithState, Status

# Tools whose target file the guard gates. Bash is disallowed for the implementer.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_ALLOWED_TOOLS = ["Read", "Glob", "Grep"]  # auto-allowed read-only tools
_DISALLOWED_TOOLS = ["Bash"]  # no shell escape around the guard in v0
_IMPLEMENT_MAX_TURNS = 40

# The target repo's CLAUDE.md (if committed) is injected into the implementer's system
# prompt as project context, so a repo with no claude.ai Project still carries its own
# conventions into the run. See _read_project_context for why we read it ourselves
# rather than enabling the SDK's setting_sources loader.
_PROJECT_CONTEXT_FILE = "CLAUDE.md"

# Path-like untouchables (PRD §7). Matched against the file's POSIX path with fnmatch
# (so `*` spans separators). The behavioral untouchables are NOT path-matchable.
DEFAULT_PROTECTED_GLOBS: tuple[str, ...] = (
    "*Cargo.lock",
    "*.kindling.yaml",
    "*/migrations/*",
    "*blacksmith/contract.py",
)


def is_protected(path: str, protected_globs: Sequence[str] = DEFAULT_PROTECTED_GLOBS) -> bool:
    """True if a (relative or absolute) path matches an untouchable glob."""
    posix = PurePosixPath(path).as_posix()
    return any(fnmatch.fnmatch(posix, glob) for glob in protected_globs)


def make_pre_edit_guard(
    protected_globs: Sequence[str] = DEFAULT_PROTECTED_GLOBS,
    *,
    worktree_root: str | Path | None = None,
):
    """Build a ``can_use_tool`` callback that denies dangerous writes.

    It blocks two things and records each in ``.blocked`` (so the node can surface them
    for human sign-off, AC-7): (1) writes whose path is outside ``worktree_root`` — the
    isolation boundary (§4/§5), so the agent can never edit the real checkout; and
    (2) writes to untouchable paths (§7).
    """
    blocked: list[dict] = []
    root = Path(worktree_root).resolve() if worktree_root is not None else None

    async def can_use_tool(tool_name, tool_input, context):
        if tool_name in _WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path") or ""
            if path and root is not None and not _within_worktree(root, path):
                blocked.append({"tool": tool_name, "path": path, "reason": "outside_worktree"})
                return PermissionResultDeny(
                    message=(
                        f"BLOCKED: {path} is outside the worktree ({root}). Every edit must "
                        "stay inside the worktree — use a path within it."
                    )
                )
            if path and is_protected(path, protected_globs):
                blocked.append({"tool": tool_name, "path": path, "reason": "untouchable"})
                return PermissionResultDeny(
                    message=(
                        f"BLOCKED: {path} is an untouchable path (PRD §7). "
                        "It may not be edited without explicit human sign-off."
                    )
                )
        return PermissionResultAllow()

    can_use_tool.blocked = blocked
    return can_use_tool


def _within_worktree(root: Path, path: str) -> bool:
    """A relative path resolves against the agent's cwd (the worktree); an absolute path
    must resolve to somewhere under the worktree root."""
    candidate = Path(path)
    if not candidate.is_absolute():
        return True
    try:
        candidate.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def implement(state: BlacksmithState, *, executor: Executor | None = None) -> dict:
    if executor is None:
        return {"status": Status.IMPLEMENTING}  # skeleton pass-through

    prd = state.get("prd")
    unit = state.get("selected_unit")
    worktree_path = state.get("worktree_path")
    if prd is None or unit is None or not worktree_path:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "implement", "message": "missing prd/selected_unit/worktree_path"}],
        }

    guard = make_pre_edit_guard(worktree_root=worktree_path)
    result = executor.run_implement(
        _implement_prompt(unit),
        system_prompt=_system_prompt(prd.contract, _read_project_context(worktree_path)),
        cwd=worktree_path,
        # Read tools auto-approve; Write/Edit are NOT auto-approved, so under the
        # "default" permission mode they evaluate to "ask" and route through the
        # can_use_tool guard (which allows non-protected paths, blocks untouchables).
        allowed_tools=_ALLOWED_TOOLS,
        disallowed_tools=_DISALLOWED_TOOLS,
        permission_mode="default",
        can_use_tool=guard,
        max_turns=_IMPLEMENT_MAX_TURNS,
        raise_on_error=False,  # surface failures into state, don't crash the graph
    )
    if result.is_error:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "implement", "message": f"implement call failed: {result.text}"}],
        }

    try:
        commit_message = f"blacksmith: {unit.id} {unit.title}"
        files, diff_summary = _stage_and_commit(worktree_path, commit_message)
    except CommitError as exc:
        return {
            "status": Status.HALTED,
            "errors": [
                {"node": "implement", "message": f"failed to commit the unit's changes: {exc}"}
            ],
        }
    if not files and not guard.blocked:
        # The agent changed nothing — the unit wasn't implemented. Halt rather than
        # gate an unchanged tree and then fail on an empty-diff PR.
        return {
            "status": Status.HALTED,
            "errors": [
                {"node": "implement", "message": "implement produced no file changes; "
                 "nothing to gate or open a PR"}
            ],
        }
    update: dict = {
        "implementation": {
            "files_touched": files,
            "diff_summary": diff_summary,
            "blocked": list(guard.blocked),
            "cost_usd": result.cost_usd,
        },
        "status": Status.TESTING,
    }
    # Only untouchable blocks (§7) are run errors that need human sign-off. Out-of-worktree
    # blocks are the isolation boundary doing its job — benign audit info, recorded under
    # implementation["blocked"] above but never surfaced as an implement error.
    untouchable = [b["path"] for b in guard.blocked if b.get("reason") == "untouchable"]
    if untouchable:
        update["errors"] = [
            {"node": "implement", "message": f"blocked untouchable edit(s): {untouchable}"}
        ]
    return update


class CommitError(Exception):
    """Raised when committing the unit's staged changes fails (e.g. the worktree has no git
    author identity). Surfacing it loudly stops a silent no-op from masquerading as a
    successful implement and only failing later as an empty-diff PR."""


def _stage_and_commit(worktree_path: str, message: str) -> tuple[list[str], str]:
    """Stage all changes, capture the staged file list + diffstat, and commit if any.

    Raises ``CommitError`` if the commit fails. Previously the commit's exit code was
    ignored, so a failed commit (e.g. a fresh clone with no author identity) looked like
    success and surfaced only at PR time as "No commits between main and <branch>"."""
    _git(worktree_path, "add", "-A")
    files = [
        line for line in _git(worktree_path, "diff", "--cached", "--name-only").splitlines() if line
    ]
    diff_summary = _git(worktree_path, "diff", "--cached", "--stat")
    if files:
        commit = subprocess.run(
            ["git", "-C", str(worktree_path), "commit", "-m", message],
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
    return files, diff_summary


def _git(worktree_path: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree_path), *args], capture_output=True, text=True
    )
    return result.stdout


def _implement_prompt(unit: WorkUnit) -> str:
    return (
        "Implement this work unit fully inside the current working directory by creating or "
        "editing the target modules. Make the minimal changes needed to satisfy the test "
        "contract; do not add anything beyond what the unit asks for. Do NOT assume a target "
        "module already exists — verify with Read and create it if missing. You are done only "
        "once the target modules exist on disk with the required content.\n\n"
        "Work ONLY within the current working directory, using paths relative to it. Never "
        "edit a file by an absolute path or outside this directory — it is an isolated "
        "worktree, not the canonical repo.\n\n"
        f"Unit {unit.id}: {unit.title}\n"
        f"Layers: {', '.join(unit.layers)}\n"
        f"Target modules: {', '.join(unit.target_modules)}\n"
        f"Test contract (must be satisfied): {unit.test_contract}"
    )


def _read_project_context(worktree_path: str | Path) -> str | None:
    """Return the target repo's CLAUDE.md text (if committed), for use as agent context.

    blacksmith deliberately does NOT enable the SDK's ``setting_sources`` loader to pick
    this up: ``setting_sources=["project"]`` would also import the target repo's
    ``.claude/settings.json`` — its permissions (which could auto-allow ``Write`` and so
    bypass the pre-edit guard) and its hooks (arbitrary commands run from an arbitrary
    repo). Reading CLAUDE.md ourselves and injecting it as static system context gives the
    agent the repo's guidance while keeping the guard authoritative and executing nothing.
    """
    path = Path(worktree_path) / _PROJECT_CONTEXT_FILE
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _system_prompt(contract: PRDContract, project_context: str | None = None) -> str:
    untouchables = "\n".join(f"- {item}" for item in contract.untouchables)
    prompt = (
        f"You are blacksmith's implementer for the {contract.component} project "
        f"({contract.primary_target_repo}). Implement precisely and minimally.\n\n"
        "CONSTITUTION — these are inviolable. Never create, edit, or delete anything that "
        "touches them; attempts to edit protected files are blocked and surfaced for human "
        f"sign-off:\n{untouchables}"
    )
    if project_context:
        prompt += (
            "\n\nPROJECT CONTEXT — the target repo's CLAUDE.md (this codebase's own "
            "conventions and guidance). Follow it, but the CONSTITUTION above wins on any "
            f"conflict:\n{project_context}"
        )
    return prompt
