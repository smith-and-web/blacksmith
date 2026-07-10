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


# --- conventional commit message (the commitlint failure that discarded whole runs) ------

_CONVENTIONAL_TYPES = (
    "feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert",
)


class _Unit:
    """Minimal stand-in: conventional_commit_message only reads .id and .title."""

    def __init__(self, unit_id, title):
        self.id = unit_id
        self.title = title


def test_conventional_commit_message_is_commitlint_safe():
    from blacksmith.nodes.implement import conventional_commit_message

    # The exact title shape that was rejected in the wild: PascalCase first word, mixed case.
    msg = conventional_commit_message(
        _Unit("WU-03", "FeedbackDialog.svelte form with validation and submit states")
    )
    type_, _, rest = msg.partition("(")
    assert type_ in _CONVENTIONAL_TYPES  # type-enum
    assert type_ == type_.lower()  # type-case
    scope, _, subject = rest.partition("): ")
    assert scope == "wu-03"  # unit id rides the (lower-case) scope — scope-case
    assert subject and subject[0].islower()  # subject-case: never upper/sentence/start/pascal
    assert not subject.endswith(".")  # subject-full-stop
    assert len(msg) <= 100  # header-max-length


def test_conventional_message_long_title_is_truncated_and_empty_title_has_subject():
    from blacksmith.nodes.implement import conventional_commit_message

    long = conventional_commit_message(_Unit("WU-LONG", "x " * 200))
    assert len(long) <= 100
    empty = conventional_commit_message(_Unit("WU-EMPTY", "   "))
    # An empty title still yields a non-empty subject (subject-empty rule).
    assert empty == "feat(wu-empty): implement wu-empty"


def _conventional_hook_repo(tmp_path):
    """A git repo with a commit-msg hook enforcing the two rules that bit us in the wild:
    a Conventional-Commits type AND a lower-case subject (commitlint type-enum + subject-case)."""
    repo = tmp_path / "hooked"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text(
        "#!/bin/sh\n"
        'msg=$(head -n1 "$1")\n'
        "types='feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert'\n"
        'echo "$msg" | grep -Eq "^($types)(\\(.+\\))?: " \\\n'
        '  || { echo "type must be one of"; exit 1; }\n'
        'subject=$(echo "$msg" | sed -E "s/^[^:]+: //")\n'
        'case "$subject" in [A-Z]*) echo "subject must be lower-case"; exit 1;; esac\n'
        "exit 0\n"
    )
    hook.chmod(0o755)
    return repo


def test_commit_survives_conventional_commit_msg_hook(tmp_path):
    # Reproduces the wild failure: the OLD "blacksmith: WU-03 <Title>" message is rejected by
    # a commitlint-style hook, while the new conventional message commits cleanly.
    from blacksmith.nodes.implement import conventional_commit_message

    repo = _conventional_hook_repo(tmp_path)
    unit = _Unit("WU-03", "FeedbackDialog.svelte form with validation and submit states")

    # The old format trips BOTH rules and would discard the run at commit time.
    (repo / "old.txt").write_text("x\n")
    with pytest.raises(CommitError):
        _stage_and_commit(str(repo), f"blacksmith: {unit.id} {unit.title}")

    # The new conventional message passes the hook and lands the commit.
    (repo / "new.txt").write_text("y\n")
    files, _ = _stage_and_commit(str(repo), conventional_commit_message(unit))
    assert "new.txt" in files
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True
    ).stdout
    assert "feat(wu-03):" in log


def test_implement_prompt_feeds_back_prior_gate_failure():
    from blacksmith.nodes.implement import _implement_prompt

    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    base = _implement_prompt(unit)
    assert "PREVIOUS ATTEMPT" not in base  # no feedback on the first attempt
    retry = _implement_prompt(unit, prior_failure="E   assert 1 == 2\nFAILED tests/test_x.py")
    assert "FAILED tests/test_x.py" in retry  # the gate output is fed back
    # The honesty rule travels with the feedback: fix the code, never weaken the gate.
    assert "weaken" in retry.lower() and "tests" in retry.lower()


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
        self.calls.append({**kwargs, "prompt": prompt})
        Path(kwargs["cwd"], "feature.txt").write_text("hello\n")
        return _result()


class BlockingFakeExecutor:
    """Simulates the agent attempting an untouchable edit (denied by the guard)."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append({**kwargs, "prompt": prompt})
        target = str(Path(kwargs["cwd"], "Cargo.lock"))
        asyncio.run(kwargs["can_use_tool"]("Write", {"file_path": target}, None))
        return _result(text="blocked")


class OutsideWorktreeFakeExecutor:
    """Agent fumbles an absolute path outside the worktree (guard blocks it) but still
    lands a real, in-worktree edit."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_implement(self, prompt, **kwargs):
        self.calls.append({**kwargs, "prompt": prompt})
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
        self.calls.append({**kwargs, "prompt": prompt})
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
    # The commit is a Conventional-Commits header so a target repo's commit-msg hook accepts
    # it; the unit id rides the (lower-case) scope: "feat(wu-01): ...".
    assert "feat(wu-01):" in log


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


# --- sandbox self-verify tool + prompt (WU-SANDBOX-IMPLEMENT) ----------------


class _SandboxConfigStub:
    """Mirrors ``blacksmith.sandbox.SandboxManager.config``'s ``enabled`` flag only —
    the field the implement node actually consults."""

    def __init__(self, enabled: bool):
        self.enabled = enabled


class FakeSandboxForImplement:
    """Minimal double for a started ``SandboxManager``. ``exec`` must never be called by
    these tests — the fake executor never actually invokes the ``run_command`` tool, it
    only inspects the kwargs the implement node built for the call."""

    def __init__(self, *, enabled: bool):
        self.config = _SandboxConfigStub(enabled)

    def exec(self, command, timeout=None):
        raise AssertionError("run_command must never be invoked from these tests")


def test_implement_grants_run_command_tool_and_self_verify_prompt_when_sandbox_enabled(tmp_path):
    from blacksmith.nodes.implement import (
        _DISALLOWED_TOOLS,
        _SANDBOX_SERVER_NAME,
        _SANDBOX_TOOL_NAME,
    )
    from blacksmith.sandbox import RUN_COMMAND_TOOL_NAME

    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    fake = EditingFakeExecutor()
    sandbox = FakeSandboxForImplement(enabled=True)

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=fake,
        sandbox=sandbox,
    )

    assert out["status"] == Status.TESTING
    call = fake.calls[0]
    # (a) the run_command sandbox tool is granted...
    assert RUN_COMMAND_TOOL_NAME in _SANDBOX_TOOL_NAME
    assert _SANDBOX_TOOL_NAME in call["allowed_tools"]
    assert _SANDBOX_SERVER_NAME in call["mcp_servers"]
    # ...while raw Bash (and every other escape tool) stays disallowed, unchanged.
    assert call["disallowed_tools"] == _DISALLOWED_TOOLS
    assert "Bash" in call["disallowed_tools"]
    # ...and the prompt instructs the agent to self-verify in the sandbox before finishing.
    prompt = call["prompt"].lower()
    assert "run_command" in prompt
    assert "sandbox" in prompt
    assert "before you finish" in prompt
    assert "fix" in prompt


def test_implement_sandbox_disabled_tool_surface_and_prompt_are_byte_for_byte_unchanged(tmp_path):
    from blacksmith.nodes.implement import _ALLOWED_TOOLS, _implement_prompt

    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    wt_baseline = _scratch_worktree(baseline_dir)
    fake_baseline = EditingFakeExecutor()
    implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt_baseline.path)},
        executor=fake_baseline,
    )

    disabled_dir = tmp_path / "disabled"
    disabled_dir.mkdir()
    wt_disabled = _scratch_worktree(disabled_dir)
    fake_disabled = EditingFakeExecutor()
    implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt_disabled.path)},
        executor=fake_disabled,
        sandbox=FakeSandboxForImplement(enabled=False),
    )

    baseline_call = fake_baseline.calls[0]
    disabled_call = fake_disabled.calls[0]
    # No sandbox tool, no mcp_servers, and the identical (unqualified) allowed/disallowed
    # tool lists and prompt/system_prompt text — a disabled (or unwired) sandbox changes
    # nothing about the call.
    assert "mcp_servers" not in baseline_call
    assert "mcp_servers" not in disabled_call
    assert baseline_call["allowed_tools"] == disabled_call["allowed_tools"] == _ALLOWED_TOOLS
    assert baseline_call["disallowed_tools"] == disabled_call["disallowed_tools"]
    assert baseline_call["prompt"] == disabled_call["prompt"]
    assert baseline_call["system_prompt"] == disabled_call["system_prompt"]
    # And the prompt is exactly today's no-shell instruction — sandboxed vocabulary never
    # leaks in when disabled.
    assert baseline_call["prompt"] == _implement_prompt(unit)
    assert "run_command" not in baseline_call["prompt"].lower()
    assert "sandbox" not in baseline_call["prompt"].lower()


def test_implement_untouchable_guard_still_blocks_when_sandbox_enabled(tmp_path):
    # (c) the pre-edit guard (AC-7) is unaffected by the sandbox being enabled.
    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=BlockingFakeExecutor(),
        sandbox=FakeSandboxForImplement(enabled=True),
    )

    assert out["implementation"]["blocked"]
    assert "Cargo.lock" in out["errors"][0]["message"]
    assert "untouchable" in out["errors"][0]["message"]


def test_implement_prompt_sandbox_enabled_reverses_no_shell_instruction():
    from blacksmith.nodes.implement import _implement_prompt

    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    disabled = _implement_prompt(unit)
    enabled = _implement_prompt(unit, sandbox_enabled=True)

    assert "no shell" in disabled.lower() and "cannot run" in disabled.lower()
    assert "no shell" not in enabled.lower()
    assert "run_command" in enabled.lower()
    assert "sandbox" in enabled.lower()
    assert "before you finish" in enabled.lower()


# --- repo map injection (WU-REPO-MAP-INJECT) ---------------------------------


def _commit_helper_module(wt):
    """Add and commit a tracked python file with a known top-level symbol, so
    ``build_repo_map`` (git ls-files-backed) picks it up."""
    (Path(wt.path) / "helper.py").write_text("def known_symbol():\n    pass\n")
    subprocess.run(["git", "-C", str(wt.path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt.path), "commit", "-m", "add helper"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_implement_injects_repo_map_when_index_enabled(tmp_path):
    from blacksmith.config import IndexConfig

    wt = _scratch_worktree(tmp_path)
    _commit_helper_module(wt)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    fake = EditingFakeExecutor()

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=fake,
        index_config=IndexConfig(enabled=True),
    )

    assert out["status"] == Status.TESTING
    system_prompt = fake.calls[0]["system_prompt"]
    assert "REPO MAP" in system_prompt  # clearly-labelled section
    assert "known_symbol" in system_prompt  # a known symbol from the worktree
    # existing sections are unaffected by the addition
    assert "CONSTITUTION" in system_prompt


def test_implement_system_prompt_unchanged_when_index_disabled(tmp_path):
    from blacksmith.config import IndexConfig

    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    wt_baseline = _scratch_worktree(baseline_dir)
    _commit_helper_module(wt_baseline)
    fake_baseline = EditingFakeExecutor()
    implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt_baseline.path)},
        executor=fake_baseline,
    )

    disabled_dir = tmp_path / "disabled"
    disabled_dir.mkdir()
    wt_disabled = _scratch_worktree(disabled_dir)
    _commit_helper_module(wt_disabled)
    fake_disabled = EditingFakeExecutor()
    implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt_disabled.path)},
        executor=fake_disabled,
        index_config=IndexConfig(enabled=False),
    )

    baseline_call = fake_baseline.calls[0]
    disabled_call = fake_disabled.calls[0]
    # No index_config at all vs. an explicitly disabled one produce the identical
    # (byte-for-byte) system prompt -- no repo-map section, no other change.
    assert baseline_call["system_prompt"] == disabled_call["system_prompt"]
    assert "REPO MAP" not in baseline_call["system_prompt"]
    assert "REPO MAP" not in disabled_call["system_prompt"]
    # the constitution and (absent, since no CLAUDE.md here) project-context sections
    # are present/absent exactly as before this unit
    assert "CONSTITUTION" in baseline_call["system_prompt"]
    assert "PROJECT CONTEXT" not in baseline_call["system_prompt"]


def test_implement_untouchable_guard_unaffected_by_index_enabled(tmp_path):
    from blacksmith.config import IndexConfig

    wt = _scratch_worktree(tmp_path)
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")

    out = implement(
        {"prd": prd, "selected_unit": unit, "worktree_path": str(wt.path)},
        executor=BlockingFakeExecutor(),
        index_config=IndexConfig(enabled=True),
    )

    assert out["implementation"]["blocked"]
    assert "Cargo.lock" in out["errors"][0]["message"]
    assert "untouchable" in out["errors"][0]["message"]


def test_system_prompt_appends_repo_map_after_project_context():
    contract = parse_prd(VENDORED_PRD).contract
    assert "REPO MAP" not in _system_prompt(contract)  # omitted when absent

    with_map = _system_prompt(contract, "House rule.", "helper.py\n  def known_symbol():")
    assert "REPO MAP" in with_map
    assert "known_symbol" in with_map
    # constitution and project context both precede (and outrank) the repo map
    assert with_map.index("CONSTITUTION") < with_map.index("PROJECT CONTEXT") < with_map.index(
        "REPO MAP"
    )


def test_build_repo_map_disabled_returns_none(tmp_path):
    from blacksmith.config import IndexConfig
    from blacksmith.nodes.implement import _build_repo_map

    wt = _scratch_worktree(tmp_path)
    assert _build_repo_map(str(wt.path), None) is None
    assert _build_repo_map(str(wt.path), IndexConfig(enabled=False)) is None


def test_build_repo_map_enabled_bounded_by_max_map_bytes(tmp_path):
    from blacksmith.config import IndexConfig
    from blacksmith.nodes.implement import _build_repo_map

    wt = _scratch_worktree(tmp_path)
    _commit_helper_module(wt)
    result = _build_repo_map(str(wt.path), IndexConfig(enabled=True, max_map_bytes=5))
    assert result is not None
    # Even with a budget too small for symbol outlines, every tracked file's path is
    # still listed (paths are never dropped or cut) and the omitted-symbols marker
    # explains why the symbol outline itself is missing.
    assert "README.md" in result
    assert "helper.py" in result
    assert "known_symbol" not in result
    assert "symbols omitted" in result
