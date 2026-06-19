"""Tests for the implement node (WU-10).

Test contract (PRD §6, WU-10): the live agent edit is a manual smoke; here we cover
the auto-testable core — the untouchable guard (AC-7), the diff capture + commit, and
the node logic with a fake executor.
"""

import asyncio
import subprocess
from pathlib import Path

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.nodes.implement import implement, is_protected, make_pre_edit_guard
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"


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


def test_implement_missing_inputs_halts():
    out = implement({}, executor=EditingFakeExecutor())
    assert out["status"] == Status.HALTED
    assert out["errors"][0]["node"] == "implement"


def test_implement_noop_without_executor():
    assert implement({}) == {"status": Status.IMPLEMENTING}
