"""blacksmith CLI entrypoint (PRD §4 cli, §12 decision 2: CLI HITL).

Loads config (+ .env for the dedicated key), builds the graph with the real
executor / worktree manager / test gate / PR runner, and drives one run — pausing at
the plan and PR approval gates for a terminal y/n. The drive loop is separated from
the interactive prompt so it can be tested with an injected approver.

For non-interactive / CI use, ``--auto-approve`` approves every gate and
``--approve plan,pr`` approves only the named gates (any gate not listed is denied,
which halts the run there — e.g. ``--approve plan`` builds and stops before the PR).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from langgraph.types import Command

from blacksmith import __version__
from blacksmith.config import BlacksmithConfig
from blacksmith.contract import ContractError, parse_prd
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


def _auto_approver(gates: set[str] | None):
    """Non-interactive approver: approve gates in ``gates`` (``None`` = all), deny others.

    A denied gate routes to ``human_halt`` (the same path as a terminal "no"), so
    ``--approve plan`` runs through implementation and stops at the PR gate.
    """

    def approve(payload, values) -> bool:
        gate = payload.get("gate", "?") if isinstance(payload, dict) else "?"
        decision = gates is None or gate in gates
        print(f"[auto] gate '{gate}': {'approved' if decision else 'denied'}")
        return decision

    return approve


def _select_approver(args):
    """Pick the approver from CLI flags: --auto-approve > --approve > interactive."""
    if args.auto_approve:
        return _auto_approver(None)
    if args.approve is not None:
        return _auto_approver({g.strip() for g in args.approve.split(",") if g.strip()})
    return _cli_approver


def _report(snapshot) -> None:
    values = snapshot.values
    print(f"\nstatus: {values.get('status')}")
    if values.get("pr_url"):
        print(f"PR: {values['pr_url']}")
    for err in values.get("errors", []):
        print(f"error [{err.get('node')}]: {err.get('message')}")


def _validate(argv: list[str] | None = None) -> int:
    """Dry-run a PRD against the contract: parse only, no model spend, no network I/O.

    Builds no Executor and runs no graph — it just calls ``parse_prd`` and reports.
    Returns 0 on a conforming PRD; 1 with the field-level ``ContractError`` message
    (printed to stderr) on any contract failure or a missing file.
    """
    parser = argparse.ArgumentParser(
        prog="blacksmith validate",
        description="Validate a PRD against Contract v1 (offline dry run; zero model spend).",
    )
    parser.add_argument("prd_path", help="Path to a PRD markdown file to validate.")
    args = parser.parse_args(argv)

    try:
        prd = parse_prd(args.prd_path)
    except ContractError as exc:
        print(f"validate: {exc}", file=sys.stderr)
        return 1

    contract = prd.contract
    count = len(contract.work_units)
    print(f"OK: {contract.component} — {count} work unit(s) — contract valid")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "validate":
        return _validate(argv[1:])

    parser = argparse.ArgumentParser(prog="blacksmith", description="Run one PRD work unit.")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("prd_path", help="Path to a contract-conforming PRD markdown file.")
    parser.add_argument(
        "--config", default="blacksmith.config.toml", help="blacksmith config path."
    )
    parser.add_argument("--thread-id", default="run", help="Checkpointer thread id for this run.")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve every gate non-interactively (headless/CI).",
    )
    parser.add_argument(
        "--approve",
        metavar="GATES",
        help="Comma-separated gates to auto-approve (e.g. 'plan,pr'); unlisted gates "
        "are denied, halting the run there. Non-interactive.",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = BlacksmithConfig.load(args.config)
    checkpointer = build_checkpointer(config.checkpointer.db_path)
    graph = build_graph_for(config, checkpointer)

    final = drive(
        graph, args.prd_path, approver=_select_approver(args), thread_id=args.thread_id
    )
    _report(final)
    return 0 if final.values.get("status") == Status.DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
