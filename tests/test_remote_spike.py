"""Tests for the standalone WU-REMOTE-RUN spike CLI entrypoint.

These MOCK ``blacksmith.remote.spike.run_remote_command`` — no LangGraph dev server is
started, no network call is made. This spike is not wired into blacksmith's production
graph; nothing here touches ``blacksmith.graph``, ``build_graph``, or any production node.
"""

import pytest

import blacksmith.remote.spike as spike_module
from blacksmith.remote.spike import main


def test_main_calls_run_remote_command_with_parsed_args(monkeypatch):
    calls = []

    def fake_run_remote_command(server_url, command, *, cwd=None, **kwargs):
        calls.append({"server_url": server_url, "command": command, "cwd": cwd})
        return {"stdout": "hi\n", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(spike_module, "run_remote_command", fake_run_remote_command)

    exit_code = main(
        [
            "--command",
            "echo hi",
            "--server-url",
            "http://localhost:9999",
            "--cwd",
            "/tmp",
        ]
    )

    assert calls == [{"server_url": "http://localhost:9999", "command": "echo hi", "cwd": "/tmp"}]
    assert exit_code == 0


def test_main_defaults_server_url_and_cwd_when_omitted(monkeypatch):
    calls = []

    def fake_run_remote_command(server_url, command, *, cwd=None, **kwargs):
        calls.append({"server_url": server_url, "command": command, "cwd": cwd})
        return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(spike_module, "run_remote_command", fake_run_remote_command)

    main(["--command", "echo hi"])

    assert calls == [{"server_url": "http://127.0.0.1:2024", "command": "echo hi", "cwd": None}]


def test_main_returns_the_remote_results_exit_code(monkeypatch):
    monkeypatch.setattr(
        spike_module,
        "run_remote_command",
        lambda server_url, command, *, cwd=None, **kwargs: {
            "stdout": "",
            "stderr": "boom",
            "exit_code": 7,
        },
    )

    exit_code = main(["--command", "false"])

    assert exit_code == 7


def test_main_prints_stdout_stderr_and_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        spike_module,
        "run_remote_command",
        lambda server_url, command, *, cwd=None, **kwargs: {
            "stdout": "hello from the remote node\n",
            "stderr": "a warning\n",
            "exit_code": 0,
        },
    )

    main(["--command", "echo hello from the remote node"])

    captured = capsys.readouterr()
    assert "hello from the remote node" in captured.out
    assert "exit_code: 0" in captured.out
    assert "a warning" in captured.err


def test_missing_command_exits_nonzero_with_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "--command" in captured.err
    assert captured.out == ""
