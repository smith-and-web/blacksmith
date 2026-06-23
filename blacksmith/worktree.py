"""Git worktree management for the target repo (PRD §5).

blacksmith does each unit's work in an isolated git worktree — created off the target
repo, one branch per unit, removed on completion or halt — so the target repo's
working tree is never touched directly. Worktrees live in a sibling directory (outside
the target's working tree), so the target repo needs no knowledge of blacksmith.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

BRANCH_PREFIX = "blacksmith"


class WorktreeError(Exception):
    """Raised when a git worktree operation fails."""


def normalize_remote_slug(url: str) -> str | None:
    """Reduce a git remote URL to its canonical ``owner/name`` slug, or ``None``.

    SSH (``git@github.com:owner/name.git``), HTTPS
    (``https://github.com/owner/name[.git]``), ``ssh://`` URLs, and a bare
    ``owner/name`` all reduce to the same lowercase ``owner/name``, so the preflight
    guard treats the two remote forms of one repo as equal. Returns ``None`` when the
    URL is empty or has fewer than two path segments.
    """
    url = url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    url = url.rstrip("/")
    parts = [part for part in re.split(r"[/:]", url) if part]
    if len(parts) < 2:
        return None
    return "/".join(parts[-2:]).lower()


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    repo_path: Path


def branch_for(unit_id: str) -> str:
    """Branch name for a work unit, e.g. 'WU-01' -> 'blacksmith/wu-01'."""
    slug = re.sub(r"[^a-z0-9]+", "-", unit_id.lower()).strip("-")
    return f"{BRANCH_PREFIX}/{slug}"


class WorktreeManager:
    """Create and clean up per-unit worktrees against one target repo."""

    def __init__(self, repo_path: str | Path, *, base_dir: str | Path | None = None) -> None:
        self.repo_path = Path(repo_path).resolve()
        if base_dir is not None:
            self.base_dir = Path(base_dir).resolve()
        else:
            self.base_dir = self.repo_path.parent / f"{self.repo_path.name}-worktrees"

    def create(self, unit_id: str, *, base_ref: str = "HEAD") -> Worktree:
        """Add a worktree on a fresh per-unit branch, checked out from ``base_ref``."""
        branch = branch_for(unit_id)
        path = self.base_dir / branch.replace("/", "-")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "-b", branch, str(path), base_ref)
        return Worktree(path=path, branch=branch, repo_path=self.repo_path)

    def remove(self, worktree: Worktree, *, delete_branch: bool = True) -> None:
        """Remove a worktree (even if dirty) and, by default, delete its branch."""
        self._git("worktree", "remove", "--force", str(worktree.path))
        if delete_branch:
            # The branch may already be gone; a failed delete must not break cleanup.
            self._git("branch", "-D", worktree.branch, check=False)

    def list_paths(self) -> list[Path]:
        """Paths of all worktrees registered with the repo (including the main one)."""
        out = self._git("worktree", "list", "--porcelain")
        prefix = "worktree "
        return [Path(line[len(prefix):]) for line in out.splitlines() if line.startswith(prefix)]

    def remote_slug(self, remote: str = "origin") -> str | None:
        """Canonical ``owner/name`` slug of the repo's ``remote`` (default ``origin``).

        Reads the remote URL with ``git remote get-url`` (the existing git plumbing) and
        normalizes it (see :func:`normalize_remote_slug`). Returns ``None`` when the
        remote is unconfigured or its URL has no recognizable slug — never raises — so the
        preflight guard can report a clear "no remote" message rather than a git failure.
        """
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), "remote", "get-url", remote],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return normalize_remote_slug(result.stdout)

    def _git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), *args],
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout
