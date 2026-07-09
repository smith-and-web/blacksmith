"""Tests for the run-level sandbox lifecycle wiring (WU-SANDBOX-LIFECYCLE).

Test contract: when ``config.sandbox.enabled``, ``prepare_worktree`` starts the run's
ONE sandbox over the run's freshly-created clone (reused across every unit built on it)
and ``cleanup_worktree`` stops it, best-effort, on every terminal path -- including
after a halt. Disabled (or simply unwired, ``sandbox=None``) leaves both nodes
byte-for-byte unchanged from today. A start failure never halts the run: the sandbox
is an additive, opt-in self-verify channel, and ``blacksmith/gate.py`` remains the
sole authoritative pass/fail backstop regardless.

Exercised entirely against a FAKE sandbox manager mirroring the public surface of
``blacksmith.sandbox.SandboxManager`` (``config.enabled``, ``start(path)``, ``stop()``)
-- the real docker CLI is never invoked, here or transitively (``prepare_worktree``/
``cleanup_worktree`` only ever touch git, via a real scratch repo, exactly as the
existing worktree tests do).
"""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

from blacksmith.graph import cleanup_worktree, prepare_worktree
from blacksmith.sandbox import SandboxError
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager


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


class _FakeSandboxConfig:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled


class FakeSandbox:
    """Records every start/stop call; never touches a real docker container.

    Mirrors ``SandboxManager``'s public surface exactly: a ``.config.enabled`` flag
    (graph.py's on/off check) plus ``start(path)``/``stop()``.
    """

    def __init__(self, *, enabled: bool = True, fail_start: bool = False) -> None:
        self.config = _FakeSandboxConfig(enabled)
        self.fail_start = fail_start
        self.start_calls: list[str] = []
        self.stop_calls = 0

    def start(self, path) -> None:
        self.start_calls.append(str(path))
        if self.fail_start:
            raise SandboxError("docker run failed (exit 1): boom")

    def stop(self) -> None:
        self.stop_calls += 1


class FailingStopSandbox(FakeSandbox):
    """A sandbox whose ``stop`` always raises, to prove teardown swallows the error."""

    def stop(self) -> None:
        super().stop()
        raise SandboxError("docker rm failed (exit 1): boom")


# --- (a) enabled: start on prepare, stop on cleanup --------------------------------


def test_enabled_starts_sandbox_on_prepare_over_the_new_clone(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    sandbox = FakeSandbox(enabled=True)
    state = {"selected_unit": types.SimpleNamespace(id="WU-01")}

    update = prepare_worktree(state, worktree_manager=mgr, sandbox=sandbox)

    assert sandbox.start_calls == [update["worktree_path"]]
    assert sandbox.stop_calls == 0  # not stopped yet


def test_enabled_starts_once_per_run_reused_across_units(tmp_path):
    """prepare_worktree is the run's single entry point -- one start, not one per unit."""
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    sandbox = FakeSandbox(enabled=True)
    state = {"selected_unit": types.SimpleNamespace(id="WU-02")}

    prepare_worktree(state, worktree_manager=mgr, sandbox=sandbox)

    assert len(sandbox.start_calls) == 1


def test_enabled_stops_sandbox_on_cleanup(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    sandbox = FakeSandbox(enabled=True)
    state = {"selected_unit": types.SimpleNamespace(id="WU-03")}

    update = prepare_worktree(state, worktree_manager=mgr, sandbox=sandbox)
    cleanup_worktree({**state, **update}, worktree_manager=mgr, sandbox=sandbox)

    assert sandbox.stop_calls == 1


# --- (b) disabled: no container calls, unchanged behaviour --------------------------


def test_disabled_sandbox_never_starts():
    repo_state = {"selected_unit": types.SimpleNamespace(id="WU-04")}
    sandbox = FakeSandbox(enabled=False)

    # No worktree_manager needed: a disabled sandbox must never even be consulted for a
    # start, exactly like the dependency-free skeleton pass-through.
    update = prepare_worktree(repo_state, worktree_manager=None, sandbox=sandbox)

    assert update == {}
    assert sandbox.start_calls == []


def test_disabled_sandbox_never_starts_with_a_real_worktree_manager(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    sandbox = FakeSandbox(enabled=False)
    state = {"selected_unit": types.SimpleNamespace(id="WU-05")}

    prepare_worktree(state, worktree_manager=mgr, sandbox=sandbox)

    assert sandbox.start_calls == []


def test_disabled_sandbox_never_stops(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-06")
    sandbox = FakeSandbox(enabled=False)
    state = {"worktree_path": str(wt.path), "selected_unit": types.SimpleNamespace(id="WU-06")}

    result = cleanup_worktree(state, worktree_manager=mgr, sandbox=sandbox)

    assert sandbox.stop_calls == 0
    assert result == {}


def test_unwired_sandbox_is_a_pure_noop(tmp_path):
    """No ``sandbox`` argument at all (the default) behaves exactly as before the WU."""
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    state = {"selected_unit": types.SimpleNamespace(id="WU-07")}

    update = prepare_worktree(state, worktree_manager=mgr)
    assert update["worktree_path"]  # prepare still runs its normal job

    result = cleanup_worktree({**state, **update}, worktree_manager=mgr)
    assert result == {}  # cleanup still runs its normal job, no sandbox involved


# --- (c) a start failure never halts the run -----------------------------------------


def test_start_failure_does_not_halt_and_worktree_still_created(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    sandbox = FakeSandbox(enabled=True, fail_start=True)
    state = {"selected_unit": types.SimpleNamespace(id="WU-08")}

    update = prepare_worktree(state, worktree_manager=mgr, sandbox=sandbox)

    assert len(sandbox.start_calls) == 1  # the start WAS attempted
    assert update.get("status") != Status.HALTED
    assert "errors" not in update
    # The run's normal worktree/clone setup is entirely unaffected by the sandbox failure.
    assert update["worktree_path"]
    assert Path(update["worktree_path"]).is_dir()


# --- stop is always attempted on cleanup, even after a halt --------------------------


def test_cleanup_stops_sandbox_even_after_a_halt(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-09")
    sandbox = FakeSandbox(enabled=True)
    state = {
        "worktree_path": str(wt.path),
        "selected_unit": types.SimpleNamespace(id="WU-09"),
        "status": Status.HALTED,
    }

    cleanup_worktree(state, worktree_manager=mgr, sandbox=sandbox)

    assert sandbox.stop_calls == 1


def test_cleanup_stops_sandbox_even_when_worktree_state_is_missing():
    """Stop is unconditional -- even when there is nothing else for cleanup to do."""
    sandbox = FakeSandbox(enabled=True)

    result = cleanup_worktree({}, worktree_manager=object(), sandbox=sandbox)

    assert sandbox.stop_calls == 1
    assert result == {}


def test_cleanup_swallows_a_failing_sandbox_stop(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    mgr = WorktreeManager(repo, base_dir=tmp_path / "wt")
    wt = mgr.create("WU-10")
    sandbox = FailingStopSandbox(enabled=True)
    state = {"worktree_path": str(wt.path), "selected_unit": types.SimpleNamespace(id="WU-10")}

    cleanup_worktree(state, worktree_manager=mgr, sandbox=sandbox)  # must not raise

    assert sandbox.stop_calls == 1


def test_build_graph_wires_the_sandbox_into_the_implement_node(monkeypatch):
    # Gap that hid for a whole feature: prepare_worktree/cleanup got the sandbox but the IMPLEMENT
    # node's add_node omitted it, so the run_command tool was never granted even with a sandbox
    # wired in. Spy on _node_with to assert the implement node is injected with the sandbox (and
    # its exec timeout). The existing implement test passes sandbox directly, so only this — the
    # graph wiring — guards the gap.
    from blacksmith import graph as graph_mod
    from blacksmith.nodes.implement import implement as implement_fn
    from blacksmith.sandbox import SandboxConfig, SandboxManager

    calls: list[tuple] = []
    real_node_with = graph_mod._node_with

    def spy(fn, **injected):
        calls.append((fn, injected))
        return real_node_with(fn, **injected)

    monkeypatch.setattr(graph_mod, "_node_with", spy)

    sandbox = SandboxManager(config=SandboxConfig(enabled=True, exec_timeout_s=42))  # inert
    graph_mod.build_graph(executor=object(), sandbox=sandbox)

    impl_injected = next(inj for fn, inj in calls if fn is implement_fn)
    assert impl_injected.get("sandbox") is sandbox
    assert impl_injected.get("sandbox_exec_timeout_s") == 42
