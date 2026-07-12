"""blacksmith/remote/workspace_graph.py — the standalone "workspace" LangGraph.

STANDALONE SPIKE — see ``blacksmith/remote/__init__.py``. This module is not imported
by anything on blacksmith's production path; it exists to be served on its own (e.g.
via ``uv run --with langgraph-cli langgraph dev``, pointed at the repo-root
``langgraph.json``) and driven as a remote command-execution surface.

The graph has a single node, ``run_command``, which runs ``state["command"]`` as a
subprocess in ``state["cwd"]`` (defaulting to the server process's own cwd when unset)
and captures stdout/stderr/exit_code into state. It is best-effort: a command that
fails, or can't even be started, populates ``exit_code``/``stderr`` rather than raising.
"""

from __future__ import annotations

import subprocess
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


class WorkspaceState(TypedDict, total=False):
    """State for the workspace graph.

    ``command``/``cwd`` are inputs; ``stdout``/``stderr``/``exit_code`` are the result
    fields populated by the ``run_command`` node.
    """

    command: str
    cwd: str | None
    stdout: str
    stderr: str
    exit_code: int


def run_command(state: WorkspaceState) -> dict:
    """Run ``state["command"]`` as a subprocess, capturing its result.

    Uses ``cwd`` from state when present, otherwise defaults to the server's own
    working directory (``cwd=None`` in :func:`subprocess.run`). Never raises: a
    non-zero exit, or an ``OSError`` from a command that can't be started at all, is
    surfaced through ``exit_code``/``stderr`` instead.
    """
    command = state.get("command", "")
    cwd = state.get("cwd")
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except OSError as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
        }


def build_workspace_graph() -> CompiledStateGraph:
    """Build and compile the workspace graph: ``START -> run_command -> END``."""
    builder = StateGraph(WorkspaceState)
    builder.add_node("run_command", run_command)
    builder.add_edge(START, "run_command")
    builder.add_edge("run_command", END)
    return builder.compile()


# Module-level compiled graph so a LangGraph server (``langgraph dev``, per the
# repo-root ``langgraph.json``) can import it directly.
graph = build_workspace_graph()
