"""Offline suite boundaries: temporary repositories must not inherit host Git policy."""

import os
import socket

import pytest
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport


@pytest.fixture(autouse=True)
def isolated_git_environment(monkeypatch, tmp_path_factory):
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # No inferred OS identity and no inherited hooks. A hook test must explicitly
    # configure its repository's hooksPath, as the conventional-commit fixture does.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    # Native Git children do not use Python's socket guard. Only scratch local
    # transports are available, even if a crash injection misses its exit point.
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "protocol.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "never")
    monkeypatch.setenv("GIT_CONFIG_KEY_2", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_2", "always")
    gh_config = tmp_path_factory.getbasetemp() / "offline-gh"
    gh_config.mkdir(exist_ok=True)
    monkeypatch.setenv("GH_CONFIG_DIR", str(gh_config))
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        # dotenv must not restore a developer's publication credentials.
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("GH_PROMPT_DISABLED", "1")


@pytest.fixture(autouse=True)
def prohibit_live_model_and_network_calls(monkeypatch):
    """A developer's credentials/.env can never turn this suite into a paid run.

    Local dashboard HTTP remains testable. SDK tests must replace billed transport;
    an unexpected transport path fails the test rather than returning a fake answer.
    """
    async def forbidden_sdk_connect(self):
        pytest.fail("live SDK transport is forbidden in the offline test suite")

    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo
    loopback = {None, "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"}

    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in loopback:
            pytest.fail(f"external network connection forbidden: {address[0]}")
        return original_connect(sock, address)

    def local_getaddrinfo(host, *args, **kwargs):
        if host not in loopback:
            pytest.fail(f"external network lookup forbidden: {host}")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(SubprocessCLITransport, "connect", forbidden_sdk_connect)
    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket, "getaddrinfo", local_getaddrinfo)
