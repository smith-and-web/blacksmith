"""Tests for the standalone WU-REMOTE-GRAPH "workspace" LangGraph.

These invoke the COMPILED graph in-process — no LangGraph server, no network. This
spike is not wired into blacksmith's production graph; nothing here touches
``blacksmith.graph``, ``build_graph``, or any production node.
"""

from langgraph.graph.state import CompiledStateGraph

from blacksmith.remote.workspace_graph import build_workspace_graph, graph


def test_simple_command_captures_stdout_and_zero_exit_code():
    result = graph.invoke({"command": "echo hi", "cwd": None})

    assert "hi" in result["stdout"]
    assert result["exit_code"] == 0


def test_failing_command_yields_nonzero_exit_code_without_raising():
    result = graph.invoke({"command": "exit 1", "cwd": None})

    assert result["exit_code"] != 0


def test_build_workspace_graph_returns_a_compiled_graph():
    built = build_workspace_graph()

    assert isinstance(built, CompiledStateGraph)


def test_module_exposes_a_compiled_graph_instance():
    assert isinstance(graph, CompiledStateGraph)


def test_cwd_defaults_to_server_cwd_when_omitted():
    result = graph.invoke({"command": "echo hi"})

    assert "hi" in result["stdout"]
    assert result["exit_code"] == 0
