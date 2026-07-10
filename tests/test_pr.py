"""Tests for the PR node (WU-08).

Test contract (PRD §6, WU-08): integration against scratch repo / mocked gh. Unit
tests use a recording fake runner; the integration test pushes a real branch to a
bare scratch remote with only `gh` mocked.
"""

import subprocess
from pathlib import Path

import pytest

from blacksmith.contract import WorkUnit
from blacksmith.executor import ExecutorResult
from blacksmith.nodes.pr import (
    CommandResult,
    PRError,
    _pr_body,
    _pr_title,
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


class _FakeSummaryExecutor:
    """Fake ``executor.run_summary`` for WU-PR-SUMMARY-WIRE tests (no live model call)."""

    def __init__(self, *, title="Synth title", summary="Synth summary.", is_error=False):
        self.calls: list[str] = []
        self._title = title
        self._summary = summary
        self._is_error = is_error

    def run_summary(self, prompt, **kwargs):
        self.calls.append(prompt)
        if self._is_error:
            text = ""
        else:
            text = (
                "```json\n"
                f'{{"title": "{self._title}", "summary": "{self._summary}"}}\n'
                "```"
            )
        return ExecutorResult(
            text=text,
            model="claude-fake-summary",
            is_error=self._is_error,
            num_turns=1,
            cost_usd=0.001,
            usage=None,
            session_id="sess-summary-1",
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


# --- open_pr node: executor-driven title/summary synthesis (WU-PR-SUMMARY-WIRE) ----


def test_node_with_executor_synthesizes_title_and_summary():
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/20")
    executor = _FakeSummaryExecutor(title="Add scaffold", summary="Adds the initial scaffold.")
    out = open_pr(
        {"selected_unit": _unit(), "worktree_path": "/tmp/wt"},
        runner=runner,
        executor=executor,
    )
    assert out["status"] == Status.DONE
    create = runner.calls[1]
    title = create[create.index("--title") + 1]
    body = create[create.index("--body") + 1]
    assert title == "Add scaffold"
    assert "## Summary" in body
    assert "Adds the initial scaffold." in body
    assert executor.calls  # the synthesis call was actually made
    assert out["cost_events"] == [
        {
            "node": "summary",
            "unit_id": "WU-01",
            "model": "claude-fake-summary",
            "cost_usd": 0.001,
            "num_turns": 1,
            "usage": None,
            "session_id": "sess-summary-1",
        }
    ]


def test_node_without_executor_falls_back_byte_for_byte():
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/21")
    unit = _unit()
    state = {"selected_unit": unit, "worktree_path": "/tmp/wt"}
    out = open_pr(state, runner=runner)
    create = runner.calls[1]
    title = create[create.index("--title") + 1]
    body = create[create.index("--body") + 1]
    assert title == _pr_title([unit])
    assert "## Summary" not in body
    assert body == _pr_body(state, [unit])
    assert "cost_events" not in out


def test_node_executor_error_falls_back_without_summary():
    # A model error on the synthesis call is FAIL-OPEN: opening the PR still succeeds with
    # the pre-existing title/body, but the cost of the attempted call is still ledgered.
    runner = _recording_runner(gh_url="https://github.com/o/r/pull/22")
    unit = _unit()
    state = {"selected_unit": unit, "worktree_path": "/tmp/wt"}
    executor = _FakeSummaryExecutor(is_error=True)
    out = open_pr(state, runner=runner, executor=executor)
    assert out["status"] == Status.DONE
    create = runner.calls[1]
    title = create[create.index("--title") + 1]
    body = create[create.index("--body") + 1]
    assert title == _pr_title([unit])
    assert "## Summary" not in body
    assert body == _pr_body(state, [unit])
    assert out["cost_events"] == [
        {
            "node": "summary",
            "unit_id": "WU-01",
            "model": "claude-fake-summary",
            "cost_usd": 0.001,
            "num_turns": 1,
            "usage": None,
            "session_id": "sess-summary-1",
        }
    ]


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
