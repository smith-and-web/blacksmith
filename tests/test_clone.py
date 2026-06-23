"""Tests for the clone manager (WU-CLONE-MANAGER).

A CloneManager isolates a run in a throwaway *local clone* (own .git, not a linked
worktree) whose origin points at the real remote, so a push targets the remote and a
commit in the clone never leaks into the source repo's working tree. These run real
git against throwaway repos in tmp_path.
"""

import subprocess
from pathlib import Path

from blacksmith.worktree import CloneManager


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("scratch\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "initial")
    return path


def _init_bare(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--bare", "-b", "main")
    return path


def _source_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A source repo whose origin is a bare repo standing in for the real remote."""
    bare = _init_bare(tmp_path / "remote.git")
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "main")
    return repo, bare


def test_default_base_dir_is_sibling(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = CloneManager(repo)
    assert mgr.base_dir == repo.resolve().parent / "repo-clones"


def test_create_clone_has_own_git_dir(tmp_path):
    repo, _ = _source_with_remote(tmp_path)
    mgr = CloneManager(repo, base_dir=tmp_path / "clones")
    clone = mgr.create("WU-01")

    assert clone.path.is_dir()
    assert (clone.path / "README.md").exists()
    assert clone.repo_path == repo.resolve()

    # The clone owns a real .git directory, NOT a worktree .git file pointing back.
    git_dir = _git(clone.path, "rev-parse", "--git-dir").strip()
    git_dir_path = (clone.path / git_dir) if not Path(git_dir).is_absolute() else Path(git_dir)
    assert git_dir_path.resolve() == (clone.path / ".git").resolve()
    assert (clone.path / ".git").is_dir()  # a clone, not a worktree's .git file


def test_create_clone_on_fresh_branch(tmp_path):
    repo, _ = _source_with_remote(tmp_path)
    mgr = CloneManager(repo, base_dir=tmp_path / "clones")
    clone = mgr.create("WU-01")

    assert clone.branch == "blacksmith/wu-01"
    head = _git(clone.path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert head == "blacksmith/wu-01"


def test_clone_origin_targets_real_remote(tmp_path):
    repo, bare = _source_with_remote(tmp_path)
    mgr = CloneManager(repo, base_dir=tmp_path / "clones")
    clone = mgr.create("WU-02")

    # A branch pushed from the clone appears in the bare remote — not the source.
    # No manual identity setup: create() propagates the source's identity into the clone.
    (clone.path / "new.txt").write_text("from clone\n")
    _git(clone.path, "add", "-A")
    _git(clone.path, "commit", "-m", "work in clone")
    _git(clone.path, "push", "origin", clone.branch)

    bare_branches = _git(bare, "branch", "--list", clone.branch)
    assert clone.branch in bare_branches  # push reached the real remote

    # The commit never appears in the source repo's working tree.
    assert not (repo / "new.txt").exists()
    source_log = _git(repo, "log", "--all", "--oneline")
    assert "work in clone" not in source_log


def test_clone_propagates_source_identity_so_it_can_commit(tmp_path):
    # A fresh clone's .git/config has no user.name/email; create() must copy the source's
    # resolved identity in, or every `git commit` in the clone fails "Author identity unknown".
    repo, _ = _source_with_remote(tmp_path)
    _git(repo, "config", "user.email", "src@example.com")
    _git(repo, "config", "user.name", "Source Dev")
    mgr = CloneManager(repo, base_dir=tmp_path / "clones")
    clone = mgr.create("WU-ID")

    # The clone resolves to the SOURCE's identity (proves propagation, not a global fallback).
    assert _git(clone.path, "config", "--get", "user.email").strip() == "src@example.com"
    assert _git(clone.path, "config", "--get", "user.name").strip() == "Source Dev"
    # ...and a commit succeeds with NO manual identity setup (_git uses check=True).
    (clone.path / "x.txt").write_text("x\n")
    _git(clone.path, "add", "-A")
    _git(clone.path, "commit", "-m", "commits without manual identity")


def test_remote_slug(tmp_path):
    repo, _ = _source_with_remote(tmp_path)
    mgr = CloneManager(repo, base_dir=tmp_path / "clones")
    assert mgr.remote_slug() == normalize_expected(repo)


def normalize_expected(repo: Path) -> str | None:
    from blacksmith.worktree import normalize_remote_slug

    url = _git(repo, "remote", "get-url", "origin").strip()
    return normalize_remote_slug(url)


def test_remove_deletes_clone_dir(tmp_path):
    repo, _ = _source_with_remote(tmp_path)
    mgr = CloneManager(repo, base_dir=tmp_path / "clones")
    clone = mgr.create("WU-03")
    assert clone.path.is_dir()

    mgr.remove(clone)
    assert not clone.path.exists()
