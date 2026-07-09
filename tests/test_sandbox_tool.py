"""Tests for the run_command sandbox tool (WU-SANDBOX-TOOL).

Test contract: exercises the ``run_command`` tool entirely against a FAKE sandbox
manager -- the real ``SandboxManager``/``docker`` CLI is never invoked. Tests call the
tool's handler directly (the same coroutine the Claude Agent SDK's in-process MCP
server would invoke for a tool call), asserting:

  (a) a passing command routes to ``manager.exec`` and returns the formatted success
      result;
  (b) a failing command returns the non-zero result plus its error tail, so the agent
      can react and fix it;
  (c) a large command output is truncated to a bounded tail rather than ballooning the
      turn's tokens.

It is bounded by the configured ``exec_timeout_s`` and never executes on the host.
"""

from __future__ import annotations

import asyncio
import subprocess

from blacksmith.sandbox import (
    DEFAULT_TAIL_CHARS,
    RUN_COMMAND_TOOL_NAME,
    ExecResult,
    create_sandbox_mcp_server,
    format_exec_result,
    make_run_command_tool,
)


class FakeSandboxManager:
    """Records every ``exec()`` call and replays one canned result -- never touches docker."""

    def __init__(self, result: ExecResult) -> None:
        self._result = result
        self.calls: list[tuple[str, float | None]] = []

    def exec(self, command: str, timeout: float | None = None) -> ExecResult:
        self.calls.append((command, timeout))
        return self._result


def _invoke(tool_def, command: str) -> dict:
    return asyncio.run(tool_def.handler({"command": command}))


def test_tool_is_named_run_command():
    manager = FakeSandboxManager(ExecResult(exit_code=0, stdout="", stderr=""))
    tool_def = make_run_command_tool(manager, exec_timeout_s=30)
    assert tool_def.name == RUN_COMMAND_TOOL_NAME == "run_command"


def test_passing_command_routes_to_manager_exec_and_returns_success_result():
    manager = FakeSandboxManager(ExecResult(exit_code=0, stdout="3 passed", stderr=""))
    tool_def = make_run_command_tool(manager, exec_timeout_s=30)

    result = _invoke(tool_def, "pytest")

    assert manager.calls == [("pytest", 30)]
    assert result["is_error"] is False
    text = result["content"][0]["text"]
    assert "exit_code=0" in text
    assert "3 passed" in text


def test_command_is_bounded_by_the_configured_exec_timeout():
    manager = FakeSandboxManager(ExecResult(exit_code=0, stdout="ok", stderr=""))
    tool_def = make_run_command_tool(manager, exec_timeout_s=7)

    _invoke(tool_def, "echo hi")

    assert manager.calls[-1] == ("echo hi", 7)


def test_failing_command_returns_nonzero_result_and_error_tail():
    manager = FakeSandboxManager(ExecResult(exit_code=1, stdout="", stderr="AssertionError: boom"))
    tool_def = make_run_command_tool(manager, exec_timeout_s=30)

    result = _invoke(tool_def, "pytest")

    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "exit_code=1" in text
    assert "AssertionError: boom" in text


def test_large_output_is_truncated_to_a_bounded_tail():
    huge_stdout = "x" * (DEFAULT_TAIL_CHARS * 5)
    manager = FakeSandboxManager(ExecResult(exit_code=0, stdout=huge_stdout, stderr=""))
    tool_def = make_run_command_tool(manager, exec_timeout_s=30)

    result = _invoke(tool_def, "cat bigfile")

    text = result["content"][0]["text"]
    assert len(text) < len(huge_stdout)
    assert "truncated" in text
    # the retained tail is the END of the output -- the most relevant part of a log.
    assert text.rstrip().endswith("x" * 20)


def test_large_stderr_is_also_truncated_independently_of_stdout():
    huge_stderr = "e" * (DEFAULT_TAIL_CHARS * 5)
    manager = FakeSandboxManager(ExecResult(exit_code=1, stdout="short", stderr=huge_stderr))
    tool_def = make_run_command_tool(manager, exec_timeout_s=30)

    result = _invoke(tool_def, "pytest -v")

    text = result["content"][0]["text"]
    assert "short" in text
    assert len(text) < len(huge_stderr)
    assert "truncated" in text


def test_format_exec_result_omits_empty_streams():
    assert format_exec_result(ExecResult(exit_code=0, stdout="", stderr="")) == "exit_code=0"


def test_create_sandbox_mcp_server_exposes_run_command_in_process():
    """VERIFY-AT-BUILD: the installed claude_agent_sdk really can expose an in-process
    custom tool -- this builds a live McpSdkServerConfig via create_sdk_mcp_server/tool
    (no fake/mocked SDK internals) and checks it wires up as expected."""
    manager = FakeSandboxManager(ExecResult(exit_code=0, stdout="", stderr=""))

    server_config = create_sandbox_mcp_server(manager, exec_timeout_s=30)

    assert server_config["type"] == "sdk"
    assert server_config["name"] == "blacksmith-sandbox"
    assert server_config["instance"] is not None


def test_run_command_never_executes_on_the_host(monkeypatch):
    """Guard against a regression that shells out directly instead of routing every
    command through the sandbox manager: patching subprocess.run must have no effect."""

    def _boom(*args, **kwargs):
        raise AssertionError("run_command must never execute on the host")

    monkeypatch.setattr(subprocess, "run", _boom)

    manager = FakeSandboxManager(ExecResult(exit_code=0, stdout="ok", stderr=""))
    tool_def = make_run_command_tool(manager, exec_timeout_s=30)

    result = _invoke(tool_def, "ls")

    assert result["is_error"] is False
    assert manager.calls == [("ls", 30)]
