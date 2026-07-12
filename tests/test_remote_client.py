"""Tests for the standalone WU-REMOTE-CLIENT ``run_remote_command`` client.

These MOCK ``blacksmith.remote.client.RemoteGraph`` — no LangGraph dev server is started,
no network call is made. This spike is not wired into blacksmith's production graph;
nothing here touches ``blacksmith.graph``, ``build_graph``, or any production node.
"""

import blacksmith.remote.client as client_module
from blacksmith.remote.client import run_remote_command


class _RecordingRemoteGraph:
    """Fake RemoteGraph recording how it was constructed and invoked."""

    instances: list["_RecordingRemoteGraph"] = []

    def __init__(self, graph_name, *, url=None, **kwargs):
        self.graph_name = graph_name
        self.url = url
        self.kwargs = kwargs
        self.invoke_calls: list[dict] = []
        _RecordingRemoteGraph.instances.append(self)

    def invoke(self, payload, **kwargs):
        self.invoke_calls.append(payload)
        return {"stdout": "hi\n", "stderr": "", "exit_code": 0}


class _InvokeRaisingRemoteGraph:
    """Fake RemoteGraph whose construction succeeds but invoke() raises."""

    def __init__(self, graph_name, *, url=None, **kwargs):
        pass

    def invoke(self, payload, **kwargs):
        raise ConnectionError("server unreachable")


class _ConstructorRaisingRemoteGraph:
    """Fake RemoteGraph that fails at construction time."""

    def __init__(self, graph_name, *, url=None, **kwargs):
        raise OSError("connection refused")


class _PartialResultRemoteGraph:
    """Fake RemoteGraph returning a result missing some of the expected keys."""

    def __init__(self, graph_name, *, url=None, **kwargs):
        pass

    def invoke(self, payload, **kwargs):
        return {"stdout": "partial"}


def test_constructs_remote_graph_with_graph_name_and_url_and_invokes_with_payload(monkeypatch):
    _RecordingRemoteGraph.instances = []
    monkeypatch.setattr(client_module, "RemoteGraph", _RecordingRemoteGraph)

    result = run_remote_command(
        "http://localhost:2024", "echo hi", cwd="/tmp", graph_name="workspace"
    )

    assert len(_RecordingRemoteGraph.instances) == 1
    fake = _RecordingRemoteGraph.instances[0]
    assert fake.graph_name == "workspace"
    assert fake.url == "http://localhost:2024"
    assert fake.invoke_calls == [{"command": "echo hi", "cwd": "/tmp"}]
    assert result == {"stdout": "hi\n", "stderr": "", "exit_code": 0}


def test_graph_name_defaults_to_workspace(monkeypatch):
    _RecordingRemoteGraph.instances = []
    monkeypatch.setattr(client_module, "RemoteGraph", _RecordingRemoteGraph)

    run_remote_command("http://localhost:2024", "echo hi")

    assert _RecordingRemoteGraph.instances[0].graph_name == "workspace"


def test_cwd_defaults_to_none(monkeypatch):
    _RecordingRemoteGraph.instances = []
    monkeypatch.setattr(client_module, "RemoteGraph", _RecordingRemoteGraph)

    run_remote_command("http://localhost:2024", "echo hi")

    assert _RecordingRemoteGraph.instances[0].invoke_calls == [
        {"command": "echo hi", "cwd": None}
    ]


def test_custom_graph_name_is_forwarded_to_remote_graph(monkeypatch):
    _RecordingRemoteGraph.instances = []
    monkeypatch.setattr(client_module, "RemoteGraph", _RecordingRemoteGraph)

    run_remote_command("http://localhost:2024", "echo hi", graph_name="custom")

    assert _RecordingRemoteGraph.instances[0].graph_name == "custom"


def test_invoke_failure_returns_structured_error_without_raising(monkeypatch):
    monkeypatch.setattr(client_module, "RemoteGraph", _InvokeRaisingRemoteGraph)

    result = run_remote_command("http://localhost:2024", "echo hi")

    assert result["stdout"] == ""
    assert "server unreachable" in result["stderr"]
    assert result["exit_code"] != 0


def test_construction_failure_returns_structured_error_without_raising(monkeypatch):
    monkeypatch.setattr(client_module, "RemoteGraph", _ConstructorRaisingRemoteGraph)

    result = run_remote_command("http://localhost:2024", "echo hi")

    assert result["stdout"] == ""
    assert "connection refused" in result["stderr"]
    assert result["exit_code"] != 0


def test_missing_result_keys_default_to_empty_and_nonzero_exit(monkeypatch):
    monkeypatch.setattr(client_module, "RemoteGraph", _PartialResultRemoteGraph)

    result = run_remote_command("http://localhost:2024", "echo hi")

    assert result == {"stdout": "partial", "stderr": "", "exit_code": 1}


def test_timeout_kwarg_is_accepted_and_does_not_change_behaviour(monkeypatch):
    _RecordingRemoteGraph.instances = []
    monkeypatch.setattr(client_module, "RemoteGraph", _RecordingRemoteGraph)

    result = run_remote_command("http://localhost:2024", "echo hi", timeout=5.0)

    assert result == {"stdout": "hi\n", "stderr": "", "exit_code": 0}
