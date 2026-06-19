"""Tests for the worktree manager (WU-05).

Test contract (PRD §6, WU-05): integration test against a scratch git repo
(create + cleanup). These run real git against a throwaway repo in tmp_path.
"""

import subprocess
import types
from pathlib import Path

import pytest

from blacksmith.graph import cleanup_worktree
from blacksmith.worktree import WorktreeError, WorktreeManager, branch_for


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


def test_branch_for():
    assert branch_for("WU-01") == "blacksmith/wu-01"
    assert branch_for("WU-11") == "blacksmith/wu-11"


def test_default_base_dir_is_sibling(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo)
    assert mgr.base_dir == repo.resolve().parent / "repo-worktrees"


def test_create_worktree(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-01")

    assert wt.path.is_dir()
    assert (wt.path / "README.md").exists()  # base_ref content is checked out
    assert wt.branch == "blacksmith/wu-01"
    head = _git(wt.path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert head == "blacksmith/wu-01"  # worktree sits on its own branch


def test_remove_worktree(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-02")
    assert wt.path.is_dir()

    mgr.remove(wt)
    assert not wt.path.exists()  # worktree dir removed
    assert wt.branch not in _git(repo, "branch", "--list", wt.branch)  # branch deleted


def test_create_duplicate_branch_fails(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    mgr.create("WU-03")
    with pytest.raises(WorktreeError):
        mgr.create("WU-03")  # branch already exists


def test_list_paths_includes_created_worktree(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-04")
    paths = {p.resolve() for p in mgr.list_paths()}
    assert wt.path.resolve() in paths
    assert repo.resolve() in paths  # the main worktree is listed too


def test_cleanup_worktree_removes_worktree_and_branch(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-01")
    state = {"worktree_path": str(wt.path), "selected_unit": types.SimpleNamespace(id="WU-01")}

    cleanup_worktree(state, worktree_manager=mgr)
    assert not wt.path.exists()  # worktree removed
    assert wt.branch not in _git(repo, "branch", "--list", wt.branch)  # branch deleted (no PR)


def test_cleanup_worktree_keeps_branch_when_pr_opened(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-02")
    state = {
        "worktree_path": str(wt.path),
        "selected_unit": types.SimpleNamespace(id="WU-02"),
        "pr_url": "https://github.com/o/r/pull/1",
    }

    cleanup_worktree(state, worktree_manager=mgr)
    assert not wt.path.exists()  # worktree removed
    assert wt.branch in _git(repo, "branch", "--list", wt.branch)  # branch kept for the PR


def test_cleanup_worktree_noop_without_manager():
    assert cleanup_worktree({"worktree_path": "/x", "selected_unit": None}) == {}
