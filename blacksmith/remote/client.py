"""blacksmith/remote/client.py — RemoteGraph client for the standalone workspace graph.

STANDALONE SPIKE — see ``blacksmith/remote/__init__.py``. This module is not imported by
anything on blacksmith's production path.

:func:`run_remote_command` drives the ``workspace`` graph (``blacksmith.remote.workspace_graph``)
over the network via ``langgraph.pregel.remote.RemoteGraph`` (an already-pinned dependency),
pointed at a local LangGraph dev server (e.g. ``uv run --with langgraph-cli langgraph dev``,
per the repo-root ``langgraph.json``). No cloud service is involved — the server is local.

It is best-effort: any failure to construct or invoke the ``RemoteGraph`` (connection
refused, server unreachable, a remote error, ...) is captured and returned as a structured
``{"stdout": "", "stderr": <error>, "exit_code": <non-zero>}`` result rather than raised, so
the caller always gets a result dict back.
"""

from __future__ import annotations

from typing import Any

from langgraph.pregel.remote import RemoteGraph


def run_remote_command(
    server_url: str,
    command: str,
    *,
    cwd: str | None = None,
    graph_name: str = "workspace",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run ``command`` on the workspace graph served at ``server_url``.

    Constructs ``RemoteGraph(graph_name, url=server_url)`` and invokes it with
    ``{"command": command, "cwd": cwd}``, returning the workspace graph's result as
    ``{"stdout", "stderr", "exit_code"}``.

    ``timeout`` is accepted for API completeness (bounding how long the caller is
    willing to wait on a hung remote call) but is not otherwise interpreted here.

    Never raises: a connection error, an unreachable server, or a failure inside the
    remote invocation itself all come back as a structured error result instead.
    """
    del timeout  # accepted for API completeness; not otherwise used by this spike
    try:
        remote = RemoteGraph(graph_name, url=server_url)
        result = remote.invoke({"command": command, "cwd": cwd})
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exit_code": 1}

    result = result or {}
    return {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", 1),
    }
