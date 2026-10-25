"""S18 disabled-flow refusals with real scratch Git branch preservation.

Only the low-level branch clone helper remains independently exercisable. No
passing mocked revision is promoted to an enabled respond workflow.
"""

import json
import subprocess
from pathlib import Path

import pytest

from blacksmith.config import BlacksmithConfig, RespondConfig
from blacksmith.executor import ExecutorResult
from blacksmith.gate import FixResult, GateResult
from blacksmith.nodes.pr import CommandResult, subprocess_runner
from blacksmith.respond import PRBranchCloneManager, RespondUnsupportedError, respond_to_pr


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


def _repo_with_pr_branch(tmp_path: Path, branch: str) -> tuple[Path, Path]:
    """A source repo (origin pointed at a bare remote) that already has a PR's branch
    pushed to it — the state ``respond`` acts on."""
    bare = _init_bare(tmp_path / "remote.git")
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "main")
    _git(repo, "checkout", "-b", branch)
    (repo / "feature.py").write_text("original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: original PR commit")
    _git(repo, "push", "origin", branch)
    _git(repo, "checkout", "main")
    return repo, bare


def _fake_pr_runner(*, reviews=None, inline=None):
    """Fakes every `gh` call; every other command (git) runs for real."""
    calls: list[list[str]] = []

    def run(argv, cwd=None):
        calls.append(list(argv))
        if argv[:1] == ["gh"]:
            if argv[1:3] == ["pr", "view"]:
                return CommandResult(0, json.dumps({"reviews": reviews or []}), "")
            if argv[1] == "api":
                return CommandResult(0, json.dumps(inline or []), "")
            return CommandResult(1, "", f"unexpected gh call: {argv}")
        return subprocess_runner(argv, cwd)  # real git

    run.calls = calls
    return run


def _executor_result() -> ExecutorResult:
    return ExecutorResult(
        text="done",
        model="claude-sonnet-4-6",
        is_error=False,
        num_turns=2,
        cost_usd=0.1,
        usage={},
        session_id="s1",
    )


class FakeExecutor:
    """Simulates the agent addressing review feedback by editing a tracked file."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append({**kwargs, "prompt": prompt})
        Path(kwargs["cwd"], "feature.py").write_text("revised\n")
        return _executor_result()


class FakeGate:
    """Returns canned results in order; the last one repeats for any further call."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def __call__(self, path, layer):
        result = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return result


def _no_op_fix(path, layer) -> FixResult:
    return FixResult(applied=False, changed=False, ok=True, output="", command="")


def _config(max_attempts: int = 1) -> BlacksmithConfig:
    return BlacksmithConfig(respond=RespondConfig(max_attempts=max_attempts))


def _branch_log(bare: Path, branch: str) -> list[str]:
    return _git(bare, "log", "--oneline", branch).strip().splitlines()


# --- disabled entry points preserve exact remote history -------------------


def test_disabled_respond_preserves_branch_without_comment_model_gate_clone_or_push(tmp_path):
    branch = "blacksmith/wu-01"
    repo, bare = _repo_with_pr_branch(tmp_path, branch)
    baseline = _branch_log(bare, branch)
    runner = _fake_pr_runner(reviews=[{"body": "please revise", "author": {"login": "reviewer"}}])
    manager = PRBranchCloneManager(repo, base_dir=tmp_path / "clones")
    executor = FakeExecutor()
    gate = FakeGate([GateResult(passed=True, output="ok", command="pytest")])
    with pytest.raises(RespondUnsupportedError, match="respond_unsupported_until_hardened"):
        respond_to_pr(pr_number=42, branch=branch, repo_path=repo, config=_config(),
                      executor=executor, gate=gate, fix=_no_op_fix,
                      clone_manager=manager, pr_runner=runner)
    assert runner.calls == [] and executor.calls == [] and gate.calls == 0
    assert _branch_log(bare, branch) == baseline
    assert not (tmp_path / "clones").exists()


# --- retries and gate/push failures are unreachable while disabled ---------


def test_disabled_respond_never_enters_retry_or_failed_push_paths(tmp_path):
    branch = "blacksmith/wu-02"
    repo, bare = _repo_with_pr_branch(tmp_path, branch)
    baseline = _branch_log(bare, branch)
    runner = _fake_pr_runner(reviews=[{"body": "please revise", "author": {"login": "reviewer"}}])
    executor = FakeExecutor()
    gate = FakeGate([GateResult(passed=False, output="tests failed", command="pytest")])
    with pytest.raises(RespondUnsupportedError, match="respond_unsupported_until_hardened"):
        respond_to_pr(pr_number=43, branch=branch, repo_path=repo,
                      config=_config(max_attempts=3), executor=executor, gate=gate,
                      fix=_no_op_fix, clone_manager=PRBranchCloneManager(
                          repo, base_dir=tmp_path / "clones"), pr_runner=runner)
    assert runner.calls == [] and executor.calls == [] and gate.calls == 0
    assert _branch_log(bare, branch) == baseline
    assert not (tmp_path / "clones").exists()


# --- no comment fetch is attempted, even for an empty review ----------------


class _ExplodingCloneManager:
    """Proves ``respond_to_pr`` never touches cloning when there is nothing to revise."""

    def create(self, branch):
        raise AssertionError("clone_manager.create must not be called for empty comments")

    def remove(self, clone):
        raise AssertionError("clone_manager.remove must not be called for empty comments")


class _ExplodingExecutor:
    def run_implement(self, prompt, **kwargs):
        raise AssertionError("executor must not run for empty comments")


def _exploding_gate(path, layer):
    raise AssertionError("gate must not run for empty comments")


def test_disabled_respond_refuses_before_even_an_empty_comment_lookup(tmp_path):
    branch = "blacksmith/wu-03"
    repo, bare = _repo_with_pr_branch(tmp_path, branch)
    baseline = _branch_log(bare, branch)
    runner = _fake_pr_runner(reviews=[], inline=[])
    with pytest.raises(RespondUnsupportedError, match="respond_unsupported_until_hardened"):
        respond_to_pr(pr_number=44, branch=branch, repo_path=repo, config=_config(),
                      executor=_ExplodingExecutor(), gate=_exploding_gate,
                      clone_manager=_ExplodingCloneManager(), pr_runner=runner)
    assert runner.calls == []
    assert _branch_log(bare, branch) == baseline


def test_pr_branch_clone_manager_fetches_a_remote_only_branch(tmp_path):
    # The PR's branch lives on the REMOTE, not in the operator's local source checkout
    # (blacksmith pushes each PR branch from a throwaway clone, so the source only tracks main).
    # PRBranchCloneManager must fetch it from the repointed remote, not the --local clone's
    # source origin. Regression for the "couldn't find remote ref" failure the original tests
    # hid by seeding the branch into the source repo too.
    branch = "blacksmith/wu-remote-only"
    bare = _init_bare(tmp_path / "remote.git")
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "main")
    _git(repo, "checkout", "-b", branch)
    (repo / "feature.py").write_text("original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: original PR commit")
    _git(repo, "push", "origin", branch)
    _git(repo, "checkout", "main")
    _git(repo, "branch", "-D", branch)  # exists ONLY on the remote now — the real respond state

    clone = PRBranchCloneManager(repo, base_dir=tmp_path / "clones").create(branch)

    assert _git(clone.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    assert (clone.path / "feature.py").read_text() == "original\n"
    # origin points at the real remote (so the later push lands on the PR's branch).
    assert _git(clone.path, "remote", "get-url", "origin").strip() == str(bare)
