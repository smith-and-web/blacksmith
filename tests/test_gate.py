"""Tests for the toolchain-aware test gate (WU-06).

Test contract (PRD §6, WU-06): run against worktree fixtures — passing repo -> pass,
failing repo -> fail; reads blacksmith.toml. Fixtures use `true`/`false` as stand-in
commands so the gate logic is exercised without a real toolchain.
"""

from pathlib import Path

import pytest

from blacksmith.gate import GateError, load_toolchain, run_gate

REPO_ROOT = Path(__file__).resolve().parent.parent


def _worktree(path: Path, toolchain: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "blacksmith.toml").write_text(toolchain)
    return path


def test_load_toolchain(tmp_path):
    wt = _worktree(tmp_path, 'test_cmd = "pytest"\nlint_cmd = "ruff check"\n')
    tc = load_toolchain(wt)
    assert tc.commands_for() == (None, "pytest", "ruff check")


def test_missing_toolchain_raises(tmp_path):
    with pytest.raises(GateError, match="blacksmith.toml"):
        load_toolchain(tmp_path)


def test_missing_test_cmd_rejected(tmp_path):
    wt = _worktree(tmp_path, 'lint_cmd = "ruff check"\n')
    with pytest.raises(GateError, match="test_cmd"):
        load_toolchain(wt)


def test_unknown_key_rejected(tmp_path):
    wt = _worktree(tmp_path, 'test_cmd = "true"\nsurprise = 1\n')
    with pytest.raises(GateError, match="surprise"):
        load_toolchain(wt)


def test_gate_passes(tmp_path):
    wt = _worktree(tmp_path, 'test_cmd = "true"\nlint_cmd = "true"\n')
    result = run_gate(wt)
    assert result.passed is True
    assert result.as_test_results() == {
        "passed": True,
        "output": result.output,
        "command": "true && true",
    }


def test_gate_fails_on_test(tmp_path):
    wt = _worktree(tmp_path, 'test_cmd = "false"\nlint_cmd = "true"\n')
    result = run_gate(wt)
    assert result.passed is False
    assert result.command == "false"  # failing test short-circuits before lint


def test_gate_fails_on_lint(tmp_path):
    wt = _worktree(tmp_path, 'test_cmd = "true"\nlint_cmd = "false"\n')
    assert run_gate(wt).passed is False


def test_gate_without_lint(tmp_path):
    wt = _worktree(tmp_path, 'test_cmd = "true"\n')
    result = run_gate(wt)
    assert result.passed is True
    assert result.command == "true"


def test_layer_override(tmp_path):
    wt = _worktree(tmp_path, 'test_cmd = "false"\n\n[layers.py-logic]\ntest_cmd = "true"\n')
    assert run_gate(wt, "py-logic").passed is True  # override wins
    assert run_gate(wt, "integration").passed is False  # falls back to default


def test_self_target_toolchain_is_valid():
    # blacksmith's own blacksmith.toml (self-dogfood target, §5/§11) must parse.
    tc = load_toolchain(REPO_ROOT)
    assert "pytest" in tc.test_cmd
    assert tc.lint_cmd is not None and "ruff" in tc.lint_cmd


def test_setup_cmd_runs_before_test(tmp_path):
    # setup writes a marker the test then requires — proves setup runs first, in the worktree.
    wt = _worktree(
        tmp_path,
        'setup_cmd = "echo ok > setup.marker"\ntest_cmd = "test -f setup.marker"\n',
    )
    result = run_gate(wt)
    assert result.passed is True
    assert result.command == "echo ok > setup.marker && test -f setup.marker"


def test_setup_failure_short_circuits_before_test(tmp_path):
    wt = _worktree(tmp_path, 'setup_cmd = "false"\ntest_cmd = "true"\n')
    result = run_gate(wt)
    assert result.passed is False
    assert result.command == "false"  # a failed setup never reaches the test


def test_shell_chaining_without_sh_c(tmp_path):
    # `a && b` runs through the shell directly; the old shlex.split path tokenized
    # this into garbage and forced an explicit `sh -c "..."` wrapper (dogfood landmine).
    assert run_gate(_worktree(tmp_path / "ok", 'test_cmd = "true && true"\n')).passed is True
    assert run_gate(_worktree(tmp_path / "no", 'test_cmd = "true && false"\n')).passed is False


def test_setup_cmd_layer_override(tmp_path):
    wt = _worktree(
        tmp_path,
        'test_cmd = "false"\n\n[layers.node]\nsetup_cmd = "true"\ntest_cmd = "true"\n',
    )
    assert run_gate(wt, "node").passed is True  # layer's setup + test override win
