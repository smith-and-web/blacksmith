"""blacksmith — agentic development orchestrator.

A LangGraph state machine that drives a single work unit through
plan → implement → test-gate → review → PR, with durable checkpointed state
and human approval gates, using the Claude Agent SDK as the per-node executor.
"""

__version__ = "0.0.0"
