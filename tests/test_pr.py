"""Tests for the PR node (WU-08).

Test contract (PRD §6, WU-08): integration against scratch repo / mocked gh. Unit
tests use a recording fake runner; the integration test pushes a real branch to a
bare scratch remote with only `gh` mocked.
"""

import subprocess
from pathlib import Path

import pytest

from blacksmith.contract import WorkUnit
from blacksmith.nodes.pr import (
    CommandResult,
    PRError,
    open_pr,
    open_pull_request,
    subprocess_runner,
)
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager


def _recording_runner(*, gh_url=None, gh_rc=0, push_rc=0):
    calls: list[list[str]] = []

    def run(argv, cwd=None):
        calls.append(list(argv))
        if argv and argv[0] == "gh":
            stdout = (gh_url + "\n") if gh_url else ""
            return CommandResult(gh_rc, stdout, "" if gh_rc == 0 else "boom")
        if argv[:2] == ["git", "push"]:
            return CommandResult(push_rc, "", "" if push_rc == 0 else "push rejected")
        return CommandResult(0, "", "")

    run.calls = calls
    return run


def _unit() -> WorkUnit:
    return WorkUnit(
        id="WU-01",
        title="scaffold",
        layers=["py-logic"],
        target_modules=["pyproject.toml"],
        test_contract="pytest",
        depends_on=[],
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


# --- open_pull_request -------------------------------------------------------


def test_pushes_then_creates(tmp_path):
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/42")
    pr = open_pull_request(
        worktree_path=tmp_path, branch="blacksmith/wu-01", title="T", body="B", runner=runner
    )
    assert pr.url == "https://github.com/o/r/pull/42"
    assert runner.calls[0][:2] == ["git", "push"]
    assert runner.calls[1][:3] == ["gh", "pr", "create"]
    assert "--head" in runner.calls[1] and "blacksmith/wu-01" in runner.calls[1]


def test_draft_adds_flag(tmp_path):
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/7")
    open_pull_request(
        worktree_path=tmp_path,
        branch="b",
        title="T",
        body="B",
        draft=True,
        runner=runner,
    )
    assert "--draft" in runner.calls[1]


def test_default_omits_draft_flag(tmp_path):
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/8")
    open_pull_request(worktree_path=tmp_path, branch="b", title="T", body="B", runner=runner)
    assert "--draft" not in runner.calls[1]


def test_base_adds_flag(tmp_path):
    # An explicit base branch is passed through as ``gh pr create --base <branch>`` so the PR
    # targets the target repo's configured default rather than whatever gh guesses.
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/10")
    open_pull_request(
        worktree_path=tmp_path, branch="b", title="T", body="B", base="develop", runner=runner
    )
    create = runner.calls[1]
    assert "--base" in create and create[create.index("--base") + 1] == "develop"


def test_default_omits_base_flag(tmp_path):
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/11")
    open_pull_request(worktree_path=tmp_path, branch="b", title="T", body="B", runner=runner)
    assert "--base" not in runner.calls[1]


def test_node_passes_default_branch_as_base():
    # The open_pr node reads the target repo's default branch from state (seeded by
    # prepare_worktree from [target].default_branch) and opens the PR against it.
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/12")
    open_pr(
        {"selected_unit": _unit(), "worktree_path": "/tmp/wt", "default_branch": "master"},
        runner=runner,
    )
    create = runner.calls[1]
    assert "--base" in create and create[create.index("--base") + 1] == "master"


def test_node_without_default_branch_omits_base():
    # A graph compiled without default_branch (e.g. tests) leaves it unset — no --base, so
    # gh falls back to the repo default exactly as before.
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/13")
    open_pr({"selected_unit": _unit(), "worktree_path": "/tmp/wt"}, runner=runner)
    assert "--base" not in runner.calls[1]


def test_push_failure_raises(tmp_path):
    runner = _recording_runner(push_rc=1)
    with pytest.raises(PRError, match="push"):
        open_pull_request(worktree_path=tmp_path, branch="b", title="T", body="B", runner=runner)


def test_gh_failure_raises(tmp_path):
    runner = _recording_runner(gh_rc=1)
    with pytest.raises(PRError, match="gh pr create"):
        open_pull_request(worktree_path=tmp_path, branch="b", title="T", body="B", runner=runner)


def test_unparseable_url_raises(tmp_path):
    runner = _recording_runner(gh_url=None)  # gh "succeeds" but prints no URL
    with pytest.raises(PRError, match="parse"):
        open_pull_request(worktree_path=tmp_path, branch="b", title="T", body="B", runner=runner)


# --- open_pr node ------------------------------------------------------------


def test_node_success_sets_pr_url_and_done():
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/9")
    out = open_pr({"selected_unit": _unit(), "worktree_path": "/tmp/wt"}, runner=runner)
    assert out["pr_url"] == "https://github.com/o/r/pull/9"
    assert out["status"] == Status.DONE


def test_node_missing_inputs_halts():
    out = open_pr({})
    assert out["status"] == Status.HALTED
    assert out["errors"][0]["node"] == "open_pr"


def test_node_pr_error_halts():
    runner = _recording_runner(gh_rc=1)
    out = open_pr({"selected_unit": _unit(), "worktree_path": "/tmp/wt"}, runner=runner)
    assert out["status"] == Status.HALTED
    assert "gh pr create" in out["errors"][0]["message"]


# --- integration: real push to a bare scratch remote, mocked gh --------------


def test_integration_real_push_mocked_gh(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "remote", "add", "origin", str(remote))

    wt = WorktreeManager(repo, base_dir=tmp_path / "wt").create("WU-01")
    (wt.path / "feature.txt").write_text("hi\n")
    _git(wt.path, "add", "-A")
    _git(wt.path, "commit", "-m", "feature")

    def runner(argv, cwd=None):
        if argv and argv[0] == "gh":
            return CommandResult(0, "https://github.com/smith-and-web/kindling/pull/3\n", "")
        return subprocess_runner(argv, cwd)  # real git

    pr = open_pull_request(
        worktree_path=wt.path, branch=wt.branch, title="WU-01", body="body", runner=runner
    )
    assert pr.url.endswith("/pull/3")
    # the branch was really pushed to the bare remote
    assert wt.branch in _git(remote, "branch", "--list", wt.branch)
