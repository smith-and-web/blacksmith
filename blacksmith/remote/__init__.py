"""blacksmith.remote — a standalone LangGraph-fluency spike.

STANDALONE SPIKE — NOT wired into blacksmith's production graph (``blacksmith.graph``).
Nothing under ``blacksmith/remote/`` is imported by ``build_graph``/``build_graph_for``,
the plan/implement/test_gate/review nodes, or ``CloneManager``. A normal
``blacksmith <prd>`` run is byte-for-byte unchanged whether or not this package exists.

This package demonstrates a small, independently deployable LangGraph "workspace" graph
(``blacksmith.remote.workspace_graph``) that can be served locally with the LangGraph
dev server (``uv run --with langgraph-cli langgraph dev``, invoked ephemerally — the
same pattern SBFL uses for coverage) and driven remotely via ``langgraph.pregel.remote``
or the ``langgraph-sdk`` client, both of which are already pinned dependencies of the
main project. It adds no new dependency and touches no cloud service.
"""
