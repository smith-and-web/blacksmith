"""Tests for the deterministic auto-fix step (``fix_cmd`` / ``run_fix`` / the ``auto_fix`` node).

``fix_cmd`` runs the target repo's own formatter/auto-fixer (e.g. ``cargo fmt --all``) after
the implement commit and BEFORE the gate, folding the result into the unit's commit. The point
is that a mechanical, zero-correctness failure (``cargo fmt --check``, ``prettier --check``) is
100% auto-fixable yet the agent is worst at reproducing it by hand — so blacksmith fixes it for
free instead of burning a model retry/escalation. These tests use a real git repo with a trivial
shell ``fix_cmd`` (a ``printf`` that canonicalizes a file) so the git/amend logic is exercised
without depending on a real toolchain.
"""

import subprocess
from types import SimpleNamespace

from blacksmith.gate import (
    FixResult,
    GateError,
    LayerOverride,
    TargetToolchain,
    load_toolchain,
    run_fix,
    run_gate,
)
from blacksmith.graph import auto_fix


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


def _unit_commit(repo, name, content):
    """Stand in for the implement node's commit: write the unit's file and commit it."""
    (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "blacksmith: WU-X unit")


def _head(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _head_count(repo):
    return int(_git(repo, "rev-list", "--count", "HEAD").stdout.strip())


# --- run_fix: the deterministic fixer ----------------------------------------


def test_no_fix_cmd_is_a_noop(tmp_path):
    # AC-2: with no fix_cmd, behaviour is byte-for-byte unchanged — the commit is untouched.
    repo = _repo(tmp_path)
    _unit_commit(repo, "code.txt", "hello   \n")
    before = _head(repo)
    res = run_fix(repo, toolchain=TargetToolchain(test_cmd="true"))
    assert res == FixResult(applied=False, changed=False, ok=True, output="", command="")
    assert _head(repo) == before


def test_fix_amends_unit_commit_then_gate_passes(tmp_path):
    # AC-1 + AC-3: a correct-but-unformatted unit passes the gate after the fix is folded into
    # the SAME commit (so join_level's cherry-pick carries it), with no model escalation.
    repo = _repo(tmp_path)
    _unit_commit(repo, "code.txt", "hello   \n")  # trailing whitespace == "unformatted"
    count_before = _head_count(repo)
    tc = TargetToolchain(
        test_cmd="grep -qx hello code.txt",       # exact-line check fails on the trailing space
        fix_cmd="printf 'hello\\n' > code.txt",    # the deterministic "formatter"
    )
    # Without the fix the gate would fail on the unformatted file.
    assert run_gate(repo, toolchain=tc).passed is False

    res = run_fix(repo, toolchain=tc)
    assert res.applied and res.changed and res.ok
    # Folded into the unit's commit via amend — no new commit appears.
    assert _head_count(repo) == count_before
    assert (repo / "code.txt").read_text() == "hello\n"
    # The fix is committed, not left dangling in the worktree.
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    # And the gate now passes on the fixed, committed tree.
    assert run_gate(repo, toolchain=tc).passed is True


def test_already_formatted_tree_is_not_amended(tmp_path):
    # A no-op fixer must not create an empty amend — same SHA out.
    repo = _repo(tmp_path)
    _unit_commit(repo, "code.txt", "hello\n")  # already canonical
    before = _head(repo)
    tc = TargetToolchain(test_cmd="true", fix_cmd="printf 'hello\\n' > code.txt")
    res = run_fix(repo, toolchain=tc)
    assert res.applied is True and res.changed is False and res.ok is True
    assert _head(repo) == before


def test_failing_fix_cmd_is_best_effort(tmp_path):
    # A fixer that does nothing and exits non-zero never raises/halts; nothing is committed.
    repo = _repo(tmp_path)
    _unit_commit(repo, "code.txt", "hello   \n")
    before = _head(repo)
    res = run_fix(repo, toolchain=TargetToolchain(test_cmd="true", fix_cmd="false"))
    assert res.applied is True and res.ok is False and res.changed is False
    assert _head(repo) == before


def test_partial_fix_is_committed_even_when_cmd_exits_nonzero(tmp_path):
    # Whatever the fixer managed to change is still folded in, even on a non-zero exit.
    repo = _repo(tmp_path)
    _unit_commit(repo, "code.txt", "hello   \n")
    count_before = _head_count(repo)
    tc = TargetToolchain(test_cmd="true", fix_cmd="printf 'hello\\n' > code.txt; false")
    res = run_fix(repo, toolchain=tc)
    assert res.applied is True and res.ok is False and res.changed is True
    assert (repo / "code.txt").read_text() == "hello\n"
    assert _head_count(repo) == count_before  # amended, not a new commit


def test_fix_does_not_mask_a_real_failure(tmp_path):
    # AC-4: the fixer canonicalizes whitespace, but a genuine test failure it can't resolve
    # still fails the gate — which then routes to the existing escalate/halt path.
    repo = _repo(tmp_path)
    _unit_commit(repo, "code.txt", "hello   \n")
    tc = TargetToolchain(test_cmd="false", fix_cmd="printf 'hello\\n' > code.txt")
    assert run_fix(repo, toolchain=tc).changed is True  # the fixer did its mechanical job
    assert run_gate(repo, toolchain=tc).passed is False  # ...but the real failure still fails


# --- config resolution -------------------------------------------------------


def test_fix_cmd_per_layer_override():
    tc = TargetToolchain(
        test_cmd="true",
        fix_cmd="default-fix",
        layers={"rust": LayerOverride(fix_cmd="cargo fmt --all")},
    )
    assert tc.commands_for()[3] == "default-fix"
    assert tc.commands_for("rust")[3] == "cargo fmt --all"
    assert tc.commands_for("other")[3] == "default-fix"  # unknown layer falls back to default


def test_fix_cmd_loads_from_toml(tmp_path):
    (tmp_path / "blacksmith.toml").write_text(
        'test_cmd = "cargo test"\n'
        'fix_cmd = "cargo fmt --all"\n\n'
        '[layers.frontend]\n'
        'fix_cmd = "npm run format"\n'
    )
    tc = load_toolchain(tmp_path)
    assert tc.fix_cmd == "cargo fmt --all"
    assert tc.commands_for("frontend")[3] == "npm run format"


# --- the auto_fix graph node -------------------------------------------------


def test_auto_fix_node_is_passthrough_without_fix():
    # No fix injected (skeleton / deterministic graph tests) -> pure pass-through.
    assert auto_fix({"worktree_path": "/wt", "selected_unit": SimpleNamespace(layers=[])}) == {}


def test_auto_fix_node_invokes_injected_fix_with_unit_layer():
    calls = []

    def fake_fix(path, layer):
        calls.append((path, layer))
        return FixResult(applied=True, changed=True, ok=True, output="", command="x")

    out = auto_fix(
        {"worktree_path": "/wt", "selected_unit": SimpleNamespace(layers=["rust"])},
        fix=fake_fix,
    )
    assert out == {}
    assert calls == [("/wt", "rust")]


def test_auto_fix_node_swallows_gate_error():
    # A missing/invalid toolchain is surfaced by the gate, not here — auto_fix never halts.
    def boom(path, layer):
        raise GateError("no toolchain")

    state = {"worktree_path": "/wt", "selected_unit": SimpleNamespace(layers=[])}
    assert auto_fix(state, fix=boom) == {}
