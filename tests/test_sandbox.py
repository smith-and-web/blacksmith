"""Tests for the sandbox manager (WU-SANDBOX-MANAGER).

Test contract: exercises start/exec/stop entirely against a FAKE command runner —
the real ``docker`` CLI is never invoked. The fake records every argv it is called
with and replays canned responses (or raises), so we can assert on the issued docker
commands and on how the manager reacts to success, timeout, and failure.
"""

from __future__ import annotations

import subprocess

import pytest

from blacksmith.sandbox import (
    DEFAULT_CONTAINER_NAME,
    ExecResult,
    SandboxConfig,
    SandboxError,
    SandboxManager,
)


class FakeRunner:
    """Records every call and replays canned responses/exceptions in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[list[str], float | None]] = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _ok(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_start_mounts_path_and_runs_setup_cmd_once(tmp_path):
    runner = FakeRunner([_ok(), _ok(stdout="deps installed")])
    manager = SandboxManager(
        config=SandboxConfig(setup_cmd="pip install -e ."), runner=runner
    )

    manager.start(tmp_path)

    assert len(runner.calls) == 2
    run_argv, run_timeout = runner.calls[0]
    assert "run" in run_argv
    assert f"{tmp_path.resolve()}:{tmp_path.resolve()}" in run_argv
    assert run_timeout is None

    setup_argv, _ = runner.calls[1]
    assert "exec" in setup_argv
    assert setup_argv[-1] == "pip install -e ."


def test_start_without_setup_cmd_runs_only_docker_run(tmp_path):
    runner = FakeRunner([_ok()])
    manager = SandboxManager(runner=runner)

    manager.start(tmp_path)

    assert len(runner.calls) == 1


def test_start_failing_setup_cmd_raises(tmp_path):
    runner = FakeRunner([_ok(), _ok(stderr="no such package", returncode=1)])
    manager = SandboxManager(config=SandboxConfig(setup_cmd="pip install nope"), runner=runner)

    with pytest.raises(SandboxError, match="setup_cmd"):
        manager.start(tmp_path)


def test_exec_passing_command_returns_structured_result(tmp_path):
    runner = FakeRunner([_ok(), _ok(stdout="3 passed", stderr="")])
    manager = SandboxManager(runner=runner)
    manager.start(tmp_path)

    result = manager.exec("pytest", timeout=30)

    assert isinstance(result, ExecResult)
    assert result.exit_code == 0
    assert result.stdout == "3 passed"
    assert result.stderr == ""
    assert result.ok is True

    exec_argv, exec_timeout = runner.calls[-1]
    assert "exec" in exec_argv
    assert exec_argv[-1] == "pytest"
    assert exec_timeout == 30


def test_exec_failing_command_returns_nonzero_result(tmp_path):
    runner = FakeRunner([_ok(), _ok(stdout="", stderr="boom", returncode=1)])
    manager = SandboxManager(runner=runner)
    manager.start(tmp_path)

    result = manager.exec("false")

    assert result.exit_code == 1
    assert result.ok is False
    assert result.stderr == "boom"


def test_exec_timeout_is_killed_and_returns_nonzero_result(tmp_path):
    runner = FakeRunner(
        [_ok(), subprocess.TimeoutExpired(cmd=["docker", "exec"], timeout=1)]
    )
    manager = SandboxManager(runner=runner)
    manager.start(tmp_path)

    result = manager.exec("sleep 999", timeout=1)

    assert isinstance(result, ExecResult)
    assert result.exit_code != 0
    assert result.ok is False
    assert "timed out" in result.stderr


def test_exec_before_start_raises():
    manager = SandboxManager(runner=FakeRunner([]))
    with pytest.raises(SandboxError, match="not been started"):
        manager.exec("pytest")


def test_stop_removes_container(tmp_path):
    runner = FakeRunner([_ok(), _ok()])
    manager = SandboxManager(runner=runner)
    manager.start(tmp_path)

    manager.stop()

    rm_argv, _ = runner.calls[-1]
    assert "rm" in rm_argv
    assert "-f" in rm_argv
    assert DEFAULT_CONTAINER_NAME in rm_argv


def test_stop_is_idempotent(tmp_path):
    runner = FakeRunner([_ok(), _ok()])
    manager = SandboxManager(runner=runner)
    manager.start(tmp_path)

    manager.stop()
    manager.stop()  # second call must not raise, and must not re-invoke docker
    manager.stop()

    assert len(runner.calls) == 2  # one `run` + one `rm`, the extra stop()s were no-ops


def test_stop_before_start_is_a_noop():
    manager = SandboxManager(runner=FakeRunner([]))
    manager.stop()  # must not raise even though the container was never started


def test_stop_swallows_docker_failure(tmp_path):
    runner = FakeRunner([_ok(), FileNotFoundError("docker: command not found")])
    manager = SandboxManager(runner=runner)
    manager.start(tmp_path)

    manager.stop()  # best-effort teardown: a failed `docker rm` must not raise


def test_start_raises_typed_error_when_docker_binary_missing(tmp_path):
    runner = FakeRunner([FileNotFoundError("no such file or directory: 'docker'")])
    manager = SandboxManager(runner=runner)

    with pytest.raises(SandboxError, match="docker"):
        manager.start(tmp_path)


def test_start_raises_typed_error_on_docker_run_failure(tmp_path):
    runner = FakeRunner([_ok(stderr="Cannot connect to the Docker daemon", returncode=1)])
    manager = SandboxManager(runner=runner)

    with pytest.raises(SandboxError, match="docker run failed"):
        manager.start(tmp_path)
