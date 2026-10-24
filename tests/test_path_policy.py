"""Write-boundary regressions with real Git and real executor option validation."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from blacksmith.config import BlacksmithConfig
from blacksmith.contract import PRD, parse_prd
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.graph import auto_fix, route_after_test_gate
from blacksmith.graph import test_gate as gate_node
from blacksmith.nodes.implement import implement, make_pre_edit_guard
from blacksmith.nodes.pr import open_pr
from blacksmith.path_policy import PathPolicy, PolicyViolation, compile_path_rules
from blacksmith.state import Status

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "source.txt").write_text("unchanged\n")
    (root / "blacksmith.toml").write_text('test_cmd = "false"\n')
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


@pytest.mark.parametrize("tool,payload", [
    ("Write", {"file_path": "../source-sentinel.txt"}),
    ("Edit", {"file_path": "/outside/checkout/file"}),
    ("Write", {}), ("Write", {"file_path": []}),
    ("Write", {"file_path": "source.txt", "path": "../escape"}),
    ("Write", {"path": ".git/config"}),
    ("NotebookEdit", {"notebook_path": "../outside.ipynb"}),
    ("NotebookEdit", {"file_path": "not-the-notebook.ipynb"}),
    ("NotebookEdit", {"notebook_path": ".git/config"}),
    ("MultiEdit", {"file_path": "blacksmith.toml"}),
    ("Write", {"file_path": ".gitmodules"}),
    ("Write", {"file_path": ".mcp.json"}),
    ("Write", {"file_path": ".claude/settings.local.json"}),
    ("Write", {"file_path": "blacksmith.config.toml"}),
    ("UnknownWriter", {"path": "source.txt"}),
    ("Bash", {"command": "touch source.txt"}),
    ("mcp__unwired__run", {}),
])
def test_unsafe_tool_calls_are_denied(repository, tmp_path, tool, payload):
    sentinel = tmp_path / "source-sentinel.txt"
    sentinel.write_text("source unchanged")
    guard = make_pre_edit_guard(worktree_root=repository)
    assert isinstance(asyncio.run(guard(tool, payload, None)), PermissionResultDeny)
    assert len(guard.blocked) == 1
    assert sentinel.read_text() == "source unchanged"
    assert git(repository, "status", "--porcelain") == ""


def test_relative_symlink_and_resolved_protected_targets_are_denied(repository, tmp_path):
    (repository / "linked").symlink_to(tmp_path, target_is_directory=True)
    (repository / "internal").symlink_to(repository, target_is_directory=True)
    (repository / "metadata").symlink_to(repository / ".git", target_is_directory=True)
    guard = make_pre_edit_guard(worktree_root=repository)
    for value in ("linked/source.txt", "internal/source.txt", "metadata/config"):
        assert isinstance(
            asyncio.run(guard("Write", {"file_path": value}, None)), PermissionResultDeny,
        )
    assert len(guard.blocked) == 3


def test_only_explicitly_wired_tools_and_valid_writes_are_allowed(repository):
    guard = make_pre_edit_guard(
        worktree_root=repository, allowed_mcp_tools=["mcp__blacksmith-index__search_code"],
    )
    for tool, payload in [
        ("Read", {"file_path": ".git/config"}),
        ("Write", {"file_path": str(repository / "new.py")}),
        ("Edit", {"path": "source.txt"}),
        ("NotebookEdit", {"notebook_path": "new.ipynb"}),
        ("mcp__blacksmith-index__search_code", {"query": "symbol"}),
    ]:
        assert isinstance(asyncio.run(guard(tool, payload, None)), PermissionResultAllow)
    assert not guard.blocked


def test_hard_rules_compile_without_treating_prose_as_a_pattern(repository):
    rules = compile_path_rules([
        "path: src/locked", "glob: **/secret*.py", "legacy/file.txt", "*.frozen",
        "Never add a new subscription or change the brand.",
    ])
    policy = PathPolicy(repository, rules)
    for value in ("src/locked/child.py", "pkg/secret_key.py", "legacy/file.txt", "x.frozen"):
        assert policy.violation(value) == "untouchable"
    assert policy.violation("src/safe.py") is None
    assert not any("subscription" in value for value in rules)


def test_literal_paths_and_glob_literal_brackets_are_protected(repository):
    policy = PathPolicy(repository, compile_path_rules([
        "path: src/file[1].py", "glob: data/[[]literal].txt",
    ]))
    assert policy.violation("src/file[1].py") == "untouchable"
    assert policy.violation("data/[literal].txt") == "untouchable"
    assert policy.violation("src/file1.py") is None


@pytest.mark.parametrize("value", ["path:", "glob: [oops", "path: ../escape", "path: /abs"])
def test_malformed_explicit_rules_are_rejected(value):
    with pytest.raises(PolicyViolation, match="malformed"):
        compile_path_rules([value])


@pytest.mark.parametrize("operation", ["delete", "rename", "modify", "untracked"])
def test_actual_diff_audit_covers_all_changed_paths(repository, operation):
    protected = repository / "blacksmith.toml"
    base = git(repository, "rev-parse", "HEAD")
    if operation == "delete":
        protected.unlink()
    elif operation == "rename":
        protected.rename(repository / "innocent.txt")
    elif operation == "modify":
        protected.write_text('test_cmd = "true"\n')
        git(repository, "add", "-A")
        git(repository, "commit", "-m", "replace verifier")
    else:
        (repository / ".mcp.json").write_text("{}")
    with pytest.raises(PolicyViolation, match="untouchable"):
        PathPolicy(repository).audit(base)


def test_audit_failure_is_not_an_empty_success(tmp_path):
    with pytest.raises(PolicyViolation, match="audit failed"):
        PathPolicy(tmp_path).audit("HEAD")


class EditingTransport(Executor):
    """Mock only billed transport; build_options retains its real strict signature."""

    def __init__(self, monkeypatch, edit):
        monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "test-only-unused-key")
        super().__init__(BlacksmithConfig())
        self.edit = edit
        self.calls = []

    async def _collect(self, prompt, options, *, emit_findings=False):
        self.calls.append((prompt, options))
        assert options.setting_sources == []
        assert options.strict_mcp_config is True
        await asyncio.to_thread(self.edit, options)
        return ExecutorResult("done", options.model, False, 1, 0.01, {}, "offline")


def state_for(root):
    prd = parse_prd(VENDORED_PRD)
    return {
        "prd": prd, "selected_unit": prd.contract.work_units[0], "worktree_path": str(root),
        "pr_base_ref": git(root, "rev-parse", "HEAD"),
    }


def test_denied_attempt_halts_without_committing_valid_sibling_work(repository, monkeypatch):
    state = state_for(repository)

    def edit(options):
        asyncio.run(options.can_use_tool("Write", {"file_path": "../escape.txt"}, None))
        (repository / "valid.txt").write_text("recover me")

    out = implement(state, executor=EditingTransport(monkeypatch, edit))
    assert out["status"] == Status.HALTED
    assert out["implement_error_kind"] == "policy_violation"
    assert git(repository, "rev-parse", "HEAD") == state["pr_base_ref"]
    assert (repository / "valid.txt").read_text() == "recover me"
    assert route_after_test_gate({**out, "test_results": {"passed": True}}) == "human_halt"


def test_unmediated_protected_edit_cannot_commit_or_publish(repository, monkeypatch):
    state = state_for(repository)

    def edit(options):
        (repository / "blacksmith.toml").write_text('test_cmd = "true"\n')

    out = implement(state, executor=EditingTransport(monkeypatch, edit))
    assert out["status"] == Status.HALTED
    assert git(repository, "rev-parse", "HEAD") == state["pr_base_ref"]

    def forbidden_runner(argv, cwd):
        pytest.fail(f"publication must not execute: {argv}")

    assert open_pr({**state, **out}, runner=forbidden_runner)["status"] == Status.HALTED


def test_prd_rules_reach_implement_guard(repository, monkeypatch):
    state = state_for(repository)
    prd = state["prd"]
    state["prd"] = PRD(prd.path, prd.contract.model_copy(update={
        "untouchables": [*prd.contract.untouchables, "path: source.txt"],
    }), prd.body)

    def edit(options):
        assert isinstance(asyncio.run(options.can_use_tool(
            "Write", {"file_path": "source.txt"}, None,
        )), PermissionResultDeny)

    out = implement(state, executor=EditingTransport(monkeypatch, edit))
    assert out["status"] == Status.HALTED
    assert (repository / "source.txt").read_text() == "unchanged\n"


def test_formatter_policy_failure_prevents_the_gate(repository):
    state = state_for(repository)

    def bad_formatter(path, layer):
        (Path(path) / "blacksmith.toml").write_text('test_cmd = "true"\n')

    fixed = auto_fix(state, fix=bad_formatter)
    assert fixed["status"] == Status.HALTED

    def forbidden_gate(path, layer):
        pytest.fail("a policy-violating formatter must never reach the gate")

    result = gate_node({**state, **fixed}, gate=forbidden_gate)
    assert not result["test_results"]["passed"]
