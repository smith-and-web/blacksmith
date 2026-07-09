"""Tests for ``blacksmith respond --pr N`` (WU-RESPOND-CLI): wire the CLI entry point
to fetch a PR's branch, run the revise flow (WU-RESPOND-FLOW), and render the outcome.

A fake `gh`+git runner (real git, faked `gh`) drives ``run_respond`` directly — the
same pattern ``tests/test_respond_flow.py`` uses for ``respond_to_pr`` itself — plus one
argv-level test proving ``--pr`` is required. No network call and no model call is ever
made.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from blacksmith.cli import main, run_respond
from blacksmith.config import BlacksmithConfig, RespondConfig
from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import ExecutorResult
from blacksmith.gate import FixResult, GateResult
from blacksmith.nodes.pr import CommandResult, subprocess_runner
from blacksmith.respond import PRBranchCloneManager, RespondResult


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


def _fake_respond_runner(*, branch: str, reviews=None, inline=None):
    """Fakes every `gh` call (including the CLI's own branch lookup); every other
    command (git) runs for real."""
    calls: list[list[str]] = []

    def run(argv, cwd=None):
        calls.append(list(argv))
        if argv[:1] == ["gh"]:
            if argv[1:3] == ["pr", "view"]:
                if "headRefName" in argv:
                    return CommandResult(0, json.dumps({"headRefName": branch}), "")
                if "reviews" in argv:
                    return CommandResult(0, json.dumps({"reviews": reviews or []}), "")
                return CommandResult(1, "", f"unexpected gh pr view call: {argv}")
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


def _sentinel_contract() -> PRDContract:
    """A real (non-placeholder) PRD contract distinct from ``_default_contract``, used
    to prove ``run_respond``/``respond_to_pr`` actually thread the given contract
    through rather than silently falling back to the placeholder."""
    return PRDContract(
        contract_version=1,
        component="sentinel-component",
        version="v1",
        primary_target_repo="owner/name",
        layers={"py-logic": "auto"},
        untouchables=["do not touch the real untouchable file"],
        work_units=[
            WorkUnit(
                id="WU-REAL",
                title="real unit",
                layers=["py-logic"],
                target_modules=["real.py"],
                test_contract="the gate passes",
                depends_on=[],
            )
        ],
    )


# A minimal Contract v1 PRD file (frontmatter + required prose sections) used to prove
# ``blacksmith respond --pr N --prd PATH`` actually parses the file and threads its
# contract through, rather than the ``_default_contract`` placeholder.
PRD_TEMPLATE = """\
---
contract_version: 1
component: demo-respond
version: v1
primary_target_repo: owner/name
layers:
  py-logic: auto
untouchables:
  - "do not touch the brand files"
work_units:
  - id: WU-E1
    title: "trivial unit"
    layers: [py-logic]
    target_modules: ["out.txt"]
    test_contract: "the gate command passes"
    depends_on: []
---
# Demo PRD

## 1. Purpose
demo.

## 2. Scope fences
demo.

## 7. Untouchables
none.

## 10. Acceptance criteria
done.
"""


def _branch_log(bare: Path, branch: str) -> list[str]:
    return _git(bare, "log", "--oneline", branch).strip().splitlines()


class _ExplodingCloneManager:
    """Proves ``run_respond`` never touches cloning when there is nothing to revise."""

    def create(self, branch):
        raise AssertionError("clone_manager.create must not be called for empty comments")

    def remove(self, clone):
        raise AssertionError("clone_manager.remove must not be called for empty comments")


class _ExplodingExecutor:
    def run_implement(self, prompt, **kwargs):
        raise AssertionError("executor must not run for empty comments")


def _exploding_gate(path, layer):
    raise AssertionError("gate must not run for empty comments")


# --- (a) seeded comments drive the flow and the CLI reports the pushed update -----


def test_respond_with_seeded_comments_drives_flow_and_reports_pushed_update(tmp_path, capsys):
    branch = "blacksmith/wu-01"
    repo, bare = _repo_with_pr_branch(tmp_path, branch)
    baseline = _branch_log(bare, branch)
    runner = _fake_respond_runner(
        branch=branch,
        reviews=[{"body": "please fix the docstring", "author": {"login": "alice"}}],
    )
    manager = PRBranchCloneManager(repo, base_dir=tmp_path / "clones")
    executor = FakeExecutor()
    gate = FakeGate([GateResult(passed=True, output="ok", command="pytest")])

    code = run_respond(
        42,
        config=_config(),
        repo_path=repo,
        executor=executor,
        pr_runner=runner,
        gate=gate,
        fix=_no_op_fix,
        clone_manager=manager,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "42" in captured.out
    assert "pushed" in captured.out.lower()
    assert len(executor.calls) == 1
    assert gate.calls == 1
    # Never runs the normal graph / opens a new PR — only the branch's own commit log grew.
    assert not any(call[:3] == ["gh", "pr", "create"] for call in runner.calls)
    after = _branch_log(bare, branch)
    assert len(after) == len(baseline) + 1


# --- (b) no review comments exits cleanly, reporting nothing to do ----------------


def test_respond_with_no_comments_reports_nothing_to_do(tmp_path, capsys):
    branch = "blacksmith/wu-02"
    repo, bare = _repo_with_pr_branch(tmp_path, branch)
    baseline = _branch_log(bare, branch)
    runner = _fake_respond_runner(branch=branch, reviews=[], inline=[])

    code = run_respond(
        44,
        config=_config(),
        repo_path=repo,
        executor=_ExplodingExecutor(),
        pr_runner=runner,
        gate=_exploding_gate,
        clone_manager=_ExplodingCloneManager(),
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "nothing to do" in captured.out.lower()
    assert not any(call[:2] == ["git", "push"] for call in runner.calls)
    assert not any(call[:3] == ["gh", "pr", "create"] for call in runner.calls)
    assert _branch_log(bare, branch) == baseline  # nothing pushed


# --- (c) --pr is required ----------------------------------------------------------


def test_respond_requires_pr_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["respond"])

    assert excinfo.value.code != 0
    captured = capsys.readouterr()
    assert "--pr" in captured.err


# --- (d) a push failure on a passing revision renders cleanly, not as a traceback ---


def test_respond_renders_clean_message_when_push_fails(tmp_path, capsys, monkeypatch):
    # respond_to_pr raises RespondError when the git push of a PASSING revision fails. That
    # must surface as the same clean "respond: ..." line as the branch-lookup failure — not an
    # uncaught traceback (reviewer advisory on #69).
    from blacksmith import cli
    from blacksmith.respond import RespondError

    branch = "blacksmith/wu-04"
    repo, _bare = _repo_with_pr_branch(tmp_path, branch)
    runner = _fake_respond_runner(
        branch=branch, reviews=[{"body": "fix it", "author": {"login": "alice"}}]
    )

    def boom(**kwargs):
        raise RespondError("git push failed: remote rejected")

    monkeypatch.setattr(cli, "respond_to_pr", boom)

    code = run_respond(
        7,
        config=_config(),
        repo_path=repo,
        executor=FakeExecutor(),
        pr_runner=runner,
        gate=FakeGate([]),
        fix=_no_op_fix,
        clone_manager=PRBranchCloneManager(repo, base_dir=tmp_path / "clones"),
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "respond: git push failed" in captured.out
    assert "Traceback" not in captured.out


# --- (e) run_respond(..., contract=<c>) forwards <c> to respond_to_pr -------------


def test_run_respond_forwards_given_contract_to_respond_to_pr(tmp_path, monkeypatch):
    from blacksmith import cli

    branch = "blacksmith/wu-05"
    repo, _bare = _repo_with_pr_branch(tmp_path, branch)
    runner = _fake_respond_runner(
        branch=branch, reviews=[{"body": "fix it", "author": {"login": "alice"}}]
    )
    sentinel_contract = _sentinel_contract()
    captured_kwargs: dict = {}

    def fake_respond_to_pr(**kwargs):
        captured_kwargs.update(kwargs)
        return RespondResult(
            pr_number=kwargs["pr_number"],
            branch=kwargs["branch"],
            comment_count=1,
            attempts=1,
            pushed=True,
            reason="pushed",
        )

    monkeypatch.setattr(cli, "respond_to_pr", fake_respond_to_pr)

    code = run_respond(
        44,
        config=_config(),
        repo_path=repo,
        executor=FakeExecutor(),
        pr_runner=runner,
        gate=FakeGate([]),
        fix=_no_op_fix,
        clone_manager=PRBranchCloneManager(repo, base_dir=tmp_path / "clones"),
        contract=sentinel_contract,
    )

    assert code == 0
    assert captured_kwargs["contract"] is sentinel_contract


# --- (f) --prd omitted forwards contract=None unchanged (backward compatible) -----


def test_run_respond_defaults_contract_to_none(tmp_path, monkeypatch):
    from blacksmith import cli

    branch = "blacksmith/wu-06"
    repo, _bare = _repo_with_pr_branch(tmp_path, branch)
    runner = _fake_respond_runner(
        branch=branch, reviews=[{"body": "fix it", "author": {"login": "alice"}}]
    )
    captured_kwargs: dict = {}

    def fake_respond_to_pr(**kwargs):
        captured_kwargs.update(kwargs)
        return RespondResult(
            pr_number=kwargs["pr_number"],
            branch=kwargs["branch"],
            comment_count=1,
            attempts=1,
            pushed=True,
            reason="pushed",
        )

    monkeypatch.setattr(cli, "respond_to_pr", fake_respond_to_pr)

    code = run_respond(
        45,
        config=_config(),
        repo_path=repo,
        executor=FakeExecutor(),
        pr_runner=runner,
        gate=FakeGate([]),
        fix=_no_op_fix,
        clone_manager=PRBranchCloneManager(repo, base_dir=tmp_path / "clones"),
    )

    assert code == 0
    assert captured_kwargs["contract"] is None


# --- (g) `respond --pr N --prd <valid-prd>` parses the PRD and threads its contract --


def test_respond_prd_flag_parses_file_and_threads_contract(tmp_path, monkeypatch):
    from blacksmith import cli

    repo = tmp_path / "target"
    repo.mkdir()
    cfg = tmp_path / "blacksmith.config.toml"
    cfg.write_text(f'[target]\nrepo_path = "{repo}"\n')
    prd_path = tmp_path / "prd.md"
    prd_path.write_text(PRD_TEMPLATE)

    captured: dict = {}

    def fake_run_respond(pr_number, **kwargs):
        captured["pr_number"] = pr_number
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_respond", fake_run_respond)

    code = main(
        [
            "respond",
            "--pr", "44",
            "--repo", "owner/name",
            "--prd", str(prd_path),
            "--config", str(cfg),
        ]
    )

    assert code == 0
    assert captured["pr_number"] == 44
    contract = captured["contract"]
    assert contract is not None
    assert contract.component == "demo-respond"


# --- (h) --prd omitted at the CLI leaves contract=None (backward compatible) -------


def test_respond_without_prd_flag_forwards_no_contract(tmp_path, monkeypatch):
    from blacksmith import cli

    repo = tmp_path / "target"
    repo.mkdir()
    cfg = tmp_path / "blacksmith.config.toml"
    cfg.write_text(f'[target]\nrepo_path = "{repo}"\n')

    captured: dict = {}

    def fake_run_respond(pr_number, **kwargs):
        captured["pr_number"] = pr_number
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_respond", fake_run_respond)

    code = main(["respond", "--pr", "44", "--repo", "owner/name", "--config", str(cfg)])

    assert code == 0
    assert captured["contract"] is None


# --- (i) an invalid/missing --prd path exits non-zero with a clean message --------


def test_respond_invalid_prd_path_exits_cleanly_without_traceback(tmp_path, capsys, monkeypatch):
    from blacksmith import cli

    repo = tmp_path / "target"
    repo.mkdir()
    cfg = tmp_path / "blacksmith.config.toml"
    cfg.write_text(f'[target]\nrepo_path = "{repo}"\n')
    missing_prd = tmp_path / "does-not-exist.md"

    def fail_run_respond(*_args, **_kwargs):
        raise AssertionError("run_respond must not be called when --prd fails to parse")

    monkeypatch.setattr(cli, "run_respond", fail_run_respond)

    code = main(
        ["respond", "--pr", "44", "--prd", str(missing_prd), "--config", str(cfg)]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "respond:" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
