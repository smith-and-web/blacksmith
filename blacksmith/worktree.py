"""Git worktree management for the target repo (PRD §5).

blacksmith does each unit's work in an isolated git worktree — created off the target
repo, one branch per unit, removed on completion or halt — so the target repo's
working tree is never touched directly. Worktrees live in a sibling directory (outside
the target's working tree), so the target repo needs no knowledge of blacksmith.
"""

from __future__ import annotations

import re
import shutil
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


@dataclass(frozen=True)
class Clone:
    path: Path
    branch: str
    repo_path: Path


class CloneManager:
    """Create and clean up per-unit throwaway clones of one target repo.

    Mirrors :class:`WorktreeManager`'s interface but isolates each run in a full
    ``git clone --local`` rather than a linked worktree. Unlike a worktree — whose
    ``.git`` is a file pointing back into the source repo's object store, so edits
    and commits share history with the source — a clone owns its ``.git`` directory
    outright. The clone's ``origin`` is repointed at the *source's* origin URL, so a
    push from the clone targets the real GitHub remote, never the local source repo.
    """

    def __init__(self, repo_path: str | Path, *, base_dir: str | Path | None = None) -> None:
        self.repo_path = Path(repo_path).resolve()
        if base_dir is not None:
            self.base_dir = Path(base_dir).resolve()
        else:
            self.base_dir = self.repo_path.parent / f"{self.repo_path.name}-clones"

    def create(self, unit_id: str) -> Clone:
        """Clone the source repo into an isolated dir on a fresh per-unit branch.

        Uses ``git clone --local`` (fast, object-store hardlinks, own ``.git``), then
        checks out a per-unit branch and repoints ``origin`` at the source's origin URL
        so pushes reach the real remote instead of the local source clone.
        """
        branch = branch_for(unit_id)
        path = self.base_dir / branch.replace("/", "-")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._git(self.repo_path.parent, "clone", "--local", str(self.repo_path), str(path))
        self._git(path, "checkout", "-b", branch)
        # `git clone` writes a fresh .git/config that does NOT inherit the source's *local*
        # user.name/user.email. When the source's identity is local-only and this machine has
        # no global identity, the clone has no author identity and every `git commit` fails
        # ("Author identity unknown") — so propagate the source's resolved identity in.
        self._propagate_identity(path)
        source_origin = self._source_origin_url()
        if source_origin is not None:
            self._git(path, "remote", "set-url", "origin", source_origin)
        return Clone(path=path, branch=branch, repo_path=self.repo_path)

    def remove(self, clone: Clone) -> None:
        """Delete the clone directory (it owns its own ``.git``, so this is total)."""
        shutil.rmtree(clone.path, ignore_errors=True)

    def remote_slug(self, remote: str = "origin") -> str | None:
        """Canonical ``owner/name`` slug of the source repo's ``remote`` (default ``origin``).

        Reads the *source* repo's remote — the real GitHub remote the clone's origin is
        repointed at — and normalizes it (see :func:`normalize_remote_slug`). Returns
        ``None`` when unconfigured or unrecognizable; never raises.
        """
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), "remote", "get-url", remote],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return normalize_remote_slug(result.stdout)

    def _source_origin_url(self) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _propagate_identity(self, clone_path: Path) -> None:
        """Copy the source repo's resolved git author identity into the clone.

        A fresh clone's .git/config inherits neither the source's *local* identity nor (on
        this machine) any global one, so without this the clone cannot commit. If the source
        has no identity either, there's nothing to copy and the clone is no worse off."""
        for key in ("user.name", "user.email"):
            value = self._source_config(key)
            if value:
                self._git(clone_path, "config", key, value)

    def _source_config(self, key: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "--get", key],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _git(self, cwd: Path, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout
