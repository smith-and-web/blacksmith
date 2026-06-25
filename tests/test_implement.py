"""Tests for the implement node (WU-10).

Test contract (PRD §6, WU-10): the live agent edit is a manual smoke; here we cover
the auto-testable core — the untouchable guard (AC-7), the diff capture + commit, and
the node logic with a fake executor.
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.nodes.implement import (
    CommitError,
    _read_project_context,
    _stage_and_commit,
    _system_prompt,
    implement,
    is_protected,
    make_pre_edit_guard,
)
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"


def test_implement_denies_shell_and_escape_tools():
    # The implementer must not reach a shell or spawn shell-capable helpers — that is what let a
    # run flail hunting for a way to run tests until it blew its turn budget (transcript-debugged).
    from blacksmith.nodes.implement import _DISALLOWED_TOOLS

    for tool in ("Bash", "Agent", "Task", "ToolSearch", "WebSearch", "WebFetch"):
        assert tool in _DISALLOWED_TOOLS


def test_implement_prompt_forbids_running_anything():
    from blacksmith.nodes.implement import _implement_prompt

    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    prompt = _implement_prompt(unit).lower()
    assert "no shell" in prompt and "cannot run" in prompt
    assert "do not try to run" in prompt
    assert "gate" in prompt  # the agent is told the gate verifies — it must not self-verify


def _result(text="done") -> ExecutorResult:
    return ExecutorResult(
        text=text, model="claude-opus-4-8", is_error=False, num_turns=3,
        cost_usd=0.5, usage={}, session_id="s",
    )


def _scratch_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    g("add", "-A")
    g("commit", "-m", "init")
    return WorktreeManager(repo, base_dir=tmp_path / "wt").create("WU-01")


# --- commit safety -----------------------------------------------------------


def test_stage_and_commit_raises_when_commit_fails(tmp_path, monkeypatch):
    # Strip ambient git identity so `git commit` fails deterministically regardless of the
    # machine's global config — mirrors a fresh clone with no propagated identity. The commit
    # exit code must be checked: a silent failure here surfaces only later as an empty PR.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "noid"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    (repo / "f.txt").write_text("x\n")  # a staged change but no identity to commit it
    with pytest.raises(CommitError):
        _stage_and_commit(str(repo), "should fail")


# --- path matching -----------------------------------------------------------


def test_is_protected_blocks_untouchable_paths():
    assert is_protected("/wt/Cargo.lock")
    assert is_protected("Cargo.lock")
    assert is_protected("/wt/.kindling.yaml")
    assert is_protected("/wt/db/migrations/001_init.sql")
    assert is_protected("/wt/blacksmith/contract.py")


def test_is_protected_allows_safe_paths():
    assert not is_protected("/wt/blacksmith/config.py")
    assert not is_protected("/wt/src/main.rs")
    assert not is_protected("README.md")
    assert not is_protected("/wt/Cargo.toml")  # only Cargo.lock is locked, not Cargo.toml


# --- pre-edit guard ----------------------------------------------------------


def test_guard_blocks_protected_write():
    guard = make_pre_edit_guard()
    result = asyncio.run(guard("Write", {"file_path": "/wt/Cargo.lock"}, None))
    assert isinstance(result, PermissionResultDeny)
    assert guard.blocked[0]["path"] == "/wt/Cargo.lock"


def test_guard_allows_safe_write():
    guard = make_pre_edit_guard()
    result = asyncio.run(guard("Write", {"file_path": "/wt/src/main.rs"}, None))
    assert isinstance(result, PermissionResultAllow)
    assert guard.blocked == []


def test_guard_ignores_reads():
    guard = make_pre_edit_guard()
    result = asyncio.run(guard("Read", {"file_path": "/wt/Cargo.lock"}, None))
    assert isinstance(result, PermissionResultAllow)  # reading an untouchable is fine
    assert guard.blocked == []


# --- node --------------------------------------------------------------------


class EditingFakeExecutor:
    """Simulates the agent editing a safe file in the worktree."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append(kwargs)
        Path(kwargs["cwd"], "feature.txt").write_text("hello\n")
        return _result()


class BlockingFakeExecutor:
    """Simulates the agent attempting an untouchable edit (denied by the guard)."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append(kwargs)
        target = str(Path(kwargs["cwd"], "Cargo.lock"))
        asyncio.run(kwargs["can_use_tool"]("Write", {"file_path": target}, None))
        return _result(text="blocked")


class OutsideWorktreeFakeExecutor:
    """Agent fumbles an absolute path outside the worktree (guard blocks it) but still
    lands a real, in-worktree edit."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append(kwargs)
        asyncio.run(
            kwargs["can_use_tool"]("Write", {"file_path": "/Users/someone/realrepo/cli.py"}, None)
        )
        Path(kwargs["cwd"], "feature.txt").write_text("hello\n")
        return _result()


class MixedBlockFakeExecutor:
    """Agent trips both guards — an out-of-worktree write and an untouchable write."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append(kwargs)
        asyncio.run(
            kwargs["can_use_tool"]("Write", {"file_path": "/Users/someone/realrepo/cli.py"}, None)
        )
        target = str(Path(kwargs["cwd"], "Cargo.lock"))
        asyncio.run(kwargs["can_use_tool"]("Write", {"file_path": target}, None))
        Path(kwargs["cwd"], "feature.txt").write_text("hello\n")
        return _result()


def test_implement_edits_captures_and_commits(tmp_path):
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    fake = EditingFakeExecutor()

    state = {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)}
    out = implement(state, executor=fake)

    assert out["status"] == Status.TESTING
    assert "feature.txt" in out["implementation"]["files_touched"]
    assert fake.calls[0]["can_use_tool"] is not None  # guard wired into the call
    assert "CONSTITUTION" in fake.calls[0]["system_prompt"]  # untouchables travel as constitution
    log = subprocess.run(
        ["git", "-C", str(wt.path), "log", "--oneline"], capture_output=True, text=True
    ).stdout
    assert "WU-01" in log  # the edit was committed to the branch


def test_implement_surfaces_blocked_untouchable(tmp_path):
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=BlockingFakeExecutor(),
    )

    assert out["implementation"]["blocked"]
    assert "Cargo.lock" in out["errors"][0]["message"]  # surfaced for sign-off (AC-7)
    assert "untouchable" in out["errors"][0]["message"]  # labeled as an untouchable block


def test_implement_outside_worktree_block_is_audit_not_error(tmp_path):
    # Out-of-worktree blocks are the isolation boundary working as intended: benign audit
    # info, not a run error. With real committed changes the node proceeds to TESTING.
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=OutsideWorktreeFakeExecutor(),
    )

    assert out["status"] == Status.TESTING
    assert "errors" not in out  # benign — no implement error surfaced
    blocked = out["implementation"]["blocked"]
    assert blocked and blocked[0]["reason"] == "outside_worktree"  # recorded for audit


def test_implement_mixed_blocks_label_each_by_real_reason(tmp_path):
    # When both kinds of block occur, only the untouchable one is a run error, and the
    # out-of-worktree path is never mislabeled as "untouchable".
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=MixedBlockFakeExecutor(),
    )

    reasons = {b["reason"] for b in out["implementation"]["blocked"]}
    assert reasons == {"outside_worktree", "untouchable"}
    message = out["errors"][0]["message"]
    assert "untouchable" in message and "Cargo.lock" in message
    # the out-of-worktree path must not be dragged into the "untouchable" message
    assert "realrepo" not in message


def test_implement_missing_inputs_halts():
    out = implement({}, executor=EditingFakeExecutor())
    assert out["status"] == Status.HALTED
    assert out["errors"][0]["node"] == "implement"


def test_implement_noop_without_executor():
    assert implement({}) == {"status": Status.IMPLEMENTING}


class NoOpFakeExecutor:
    """Simulates an agent that writes nothing (e.g. wrongly thinks files exist)."""

    def run_implement(self, prompt, **kwargs):
        return _result(text="I believe the target files already exist.")


def test_implement_halts_when_no_changes(tmp_path):
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    state = {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)}

    out = implement(state, executor=NoOpFakeExecutor())
    assert out["status"] == Status.HALTED
    assert "no file changes" in out["errors"][0]["message"]
    assert "implementation" not in out  # nothing to gate or PR


def test_guard_blocks_writes_outside_worktree():
    guard = make_pre_edit_guard(worktree_root="/tmp/wt")
    out = asyncio.run(guard("Write", {"file_path": "/Users/someone/realrepo/cli.py"}, None))
    assert isinstance(out, PermissionResultDeny)
    assert guard.blocked[0]["reason"] == "outside_worktree"


def test_guard_allows_writes_inside_worktree():
    guard = make_pre_edit_guard(worktree_root="/tmp/wt")
    assert isinstance(
        asyncio.run(guard("Write", {"file_path": "/tmp/wt/sub/file.py"}, None)),
        PermissionResultAllow,
    )


def test_guard_allows_relative_paths_under_worktree():
    guard = make_pre_edit_guard(worktree_root="/tmp/wt")
    assert isinstance(
        asyncio.run(guard("Write", {"file_path": "blacksmith/cli.py"}, None)),
        PermissionResultAllow,
    )


# --- target-repo CLAUDE.md as project context --------------------------------


def test_read_project_context_absent_then_present(tmp_path):
    wt = _scratch_worktree(tmp_path)
    assert _read_project_context(wt.path) is None  # no CLAUDE.md in the repo
    (Path(wt.path) / "CLAUDE.md").write_text("Run `npm test`. Prefer small functions.\n")
    assert "npm test" in _read_project_context(wt.path)


def test_system_prompt_appends_project_context_after_constitution():
    contract = parse_prd(VENDORED_PRD).contract
    assert "PROJECT CONTEXT" not in _system_prompt(contract)  # omitted when absent

    withctx = _system_prompt(contract, "House rule: prefer composition.")
    assert "PROJECT CONTEXT" in withctx
    assert "House rule: prefer composition." in withctx
    # the inviolable constitution must precede (and thus outrank) the repo's own guidance
    assert withctx.index("CONSTITUTION") < withctx.index("PROJECT CONTEXT")


def test_implement_injects_claude_md_into_system_prompt(tmp_path):
    wt = _scratch_worktree(tmp_path)
    (Path(wt.path) / "CLAUDE.md").write_text("Project rule: keep functions tiny.\n")
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    fake = EditingFakeExecutor()

    implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)}, executor=fake
    )

    system_prompt = fake.calls[0]["system_prompt"]
    assert "PROJECT CONTEXT" in system_prompt
    assert "keep functions tiny" in system_prompt
