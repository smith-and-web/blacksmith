"""blacksmith CLI entrypoint (PRD §4 cli, §12 decision 2: CLI HITL).

Loads config (+ .env for the dedicated key), builds the graph with the real
executor / worktree manager / test gate / PR runner, and drives one run — pausing at
the plan and PR approval gates for a terminal y/n. The drive loop is separated from
the interactive prompt so it can be tested with an injected approver.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from langgraph.types import Command

from blacksmith import __version__
from blacksmith.config import BlacksmithConfig
from blacksmith.executor import Executor
from blacksmith.gate import run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager


def load_dotenv(env_file: Path) -> None:
    """Minimal .env loader: KEY=value lines into the environment (no overwrite)."""
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def build_graph_for(config: BlacksmithConfig, checkpointer):
    """Compile the graph wired with the real production dependencies."""
    return compile_graph(
        checkpointer,
        executor=Executor(config),
        worktree_manager=WorktreeManager(config.target.repo_path),
        gate=run_gate,
    )


def drive(graph, prd_path, *, approver, thread_id: str = "run"):
    """Run one work unit, pausing at each approval gate to consult ``approver``.

    ``approver(payload, values) -> bool`` decides each gate. Returns the final state
    snapshot once the graph reaches END.
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"prd_path": str(prd_path)}, config)
    while True:
        snapshot = graph.get_state(config)
        if not snapshot.next:  # reached END
            return snapshot
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        payload = interrupts[0].value if interrupts else {}
        approved = approver(payload, snapshot.values)
        result = graph.invoke(Command(resume=approved), config)


def _cli_approver(payload, values) -> bool:
    gate = payload.get("gate", "?") if isinstance(payload, dict) else "?"
    print(f"\n=== blacksmith: approval needed at the '{gate}' gate ===")
    print(json.dumps(payload, indent=2, default=str))
    return input("Approve? [y/N] ").strip().lower() in ("y", "yes")


def _report(snapshot) -> None:
    values = snapshot.values
    print(f"\nstatus: {values.get('status')}")
    if values.get("pr_url"):
        print(f"PR: {values['pr_url']}")
    for err in values.get("errors", []):
        print(f"error [{err.get('node')}]: {err.get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blacksmith", description="Run one PRD work unit.")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("prd_path", help="Path to a contract-conforming PRD markdown file.")
    parser.add_argument(
        "--config", default="blacksmith.config.toml", help="blacksmith config path."
    )
    parser.add_argument("--thread-id", default="run", help="Checkpointer thread id for this run.")
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = BlacksmithConfig.load(args.config)
    checkpointer = build_checkpointer(config.checkpointer.db_path)
    graph = build_graph_for(config, checkpointer)

    final = drive(graph, args.prd_path, approver=_cli_approver, thread_id=args.thread_id)
    _report(final)
    return 0 if final.values.get("status") == Status.DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
