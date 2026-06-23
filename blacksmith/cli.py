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
from blacksmith.config import CONFIG_FILENAME, BlacksmithConfig, find_config
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
        worktree_manager=WorktreeManager(config.resolve_repo_path()),
        gate=run_gate,
    )


def _load_config(config_arg: str | None) -> BlacksmithConfig:
    """Load the runtime config, discovering it by walking up to the git root.

    When ``--config`` is not given (``config_arg is None``), the config is discovered
    from the current working directory up to the git root (WU-INSTALL), so a globally
    installed ``blacksmith`` can be run from any nested path inside the repo. An
    explicit ``--config`` path is honoured unchanged.
    """
    if config_arg is not None:
        return BlacksmithConfig.load(config_arg)
    discovered = find_config()
    # Fall back to the default name so load() raises its clear not-found message.
    return BlacksmithConfig.load(discovered or CONFIG_FILENAME)


class ResumeError(Exception):
    """Raised when a resume targets a thread-id with no persisted checkpoint."""


def _step(graph, payload, config, *, on_node):
    """Run the graph until it next halts (an interrupt or END), streaming progress.

    Uses LangGraph's built-in ``stream`` (stream_mode="updates") so each node update
    arrives as it runs; ``on_node(node)`` is invoked per node for progress output. This
    drives the same nodes in the same order as ``invoke`` — it only observes them. Returns
    a dict carrying ``__interrupt__`` when the graph paused at a gate (matching ``invoke``).
    """
    interrupt = None
    for chunk in graph.stream(payload, config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for node, update in chunk.items():
            if node == "__interrupt__":
                interrupt = update
            elif on_node is not None:
                on_node(node)
    return {"__interrupt__": interrupt} if interrupt is not None else {}


def _gate_payload(result, snapshot) -> dict:
    """Find the payload of the gate the graph is paused at.

    Prefers the ``__interrupt__`` carried by the most recent ``_step`` (the fresh-run
    path); falls back to the persisted snapshot's pending task, which is the only source
    available when *resuming* after a process restart (no in-memory ``result`` exists).
    """
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        return interrupts[0].value
    for task in getattr(snapshot, "tasks", ()) or ():
        task_interrupts = getattr(task, "interrupts", ()) or ()
        if task_interrupts:
            return task_interrupts[0].value
    return {}


def _drive_gates(graph, config, *, approver, on_node, result=None):
    """Drive the graph through its approval gates to END, consulting ``approver``.

    Shared by a fresh ``drive`` and a ``resume``: each iteration reads the current
    snapshot, halts the loop at END, otherwise asks ``approver`` to decide the pending
    gate and injects that decision with ``Command(resume=...)``. ``result`` seeds the
    first gate's payload for a fresh run; resume passes ``None`` and reads it from the
    persisted snapshot instead.
    """
    while True:
        snapshot = graph.get_state(config)
        if not snapshot.next:  # reached END
            return snapshot
        payload = _gate_payload(result, snapshot)
        approved = approver(payload, snapshot.values)
        result = _step(graph, Command(resume=approved), config, on_node=on_node)


def drive(graph, prd_path, *, approver, thread_id: str = "run", on_node=None):
    """Run one work unit, pausing at each approval gate to consult ``approver``.

    ``approver(payload, values) -> bool`` decides each gate. Returns the final state
    snapshot once the graph reaches END. ``on_node(node)``, when given, is called with
    each node's name as it runs (progress output); it never affects control flow.
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = _step(graph, {"prd_path": str(prd_path)}, config, on_node=on_node)
    return _drive_gates(graph, config, approver=approver, on_node=on_node, result=result)


def resume(graph, thread_id: str, *, approver, on_node=None):
    """Continue an interrupted run from its persisted SQLite checkpoint (WU-RESUME).

    Re-attaches to ``thread_id`` and drives the *existing* graph state to END. It sends
    no fresh ``prd_path`` input, so the checkpointer replays already-completed nodes
    (ingest/plan) from disk rather than re-running them — the run continues from the gate
    it paused at, spending nothing on work already done. Raises ``ResumeError`` if no
    checkpoint exists for ``thread_id`` (an unknown / never-started run).
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if getattr(snapshot, "created_at", None) is None:
        raise ResumeError(
            f"no checkpoint found for thread-id {thread_id!r}; nothing to resume "
            "(start a run with `blacksmith <prd>` first)"
        )
    return _drive_gates(graph, config, approver=approver, on_node=on_node)


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


def _progress_emitter(quiet: bool):
    """Build the per-node progress callback for ``drive``'s ``on_node`` hook.

    Returns ``None`` when ``quiet`` is set (no progress stream). Otherwise returns a
    callable that writes a concise line naming each node to STDERR, keeping stdout
    reserved for the final machine-readable report.
    """
    if quiet:
        return None

    def emit(node: str) -> None:
        print(f"blacksmith: {node}", file=sys.stderr)

    return emit


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


def _resume(argv: list[str] | None = None) -> int:
    """``blacksmith resume --thread-id X``: continue an interrupted run (WU-RESUME).

    Re-attaches to the run's SQLite checkpoint and drives it from its paused gate to
    END without re-running already-completed nodes. Returns 1 with a clear message if
    the thread-id has no persisted checkpoint; otherwise mirrors the run path's exit
    code (0 on DONE, 1 otherwise).
    """
    parser = argparse.ArgumentParser(
        prog="blacksmith resume",
        description="Continue an interrupted run from its SQLite checkpoint (by thread-id).",
    )
    parser.add_argument(
        "--thread-id", required=True, help="Thread id of the run to resume."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="blacksmith config path (default: discovered by walking up to the git root).",
    )
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
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the per-node progress stream on STDERR (the final report still prints).",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)
    checkpointer = build_checkpointer(config.checkpointer.db_path)
    graph = build_graph_for(config, checkpointer)

    try:
        final = resume(
            graph,
            args.thread_id,
            approver=_select_approver(args),
            on_node=_progress_emitter(args.quiet),
        )
    except ResumeError as exc:
        print(f"resume: {exc}", file=sys.stderr)
        return 1
    _report(final)
    return 0 if final.values.get("status") == Status.DONE else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "validate":
        return _validate(argv[1:])
    if argv and argv[0] == "resume":
        return _resume(argv[1:])

    parser = argparse.ArgumentParser(prog="blacksmith", description="Run one PRD work unit.")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("prd_path", help="Path to a contract-conforming PRD markdown file.")
    parser.add_argument(
        "--config",
        default=None,
        help="blacksmith config path (default: discovered by walking up to the git root).",
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
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the per-node progress stream on STDERR (the final report still prints).",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)
    checkpointer = build_checkpointer(config.checkpointer.db_path)
    graph = build_graph_for(config, checkpointer)

    final = drive(
        graph,
        args.prd_path,
        approver=_select_approver(args),
        thread_id=args.thread_id,
        on_node=_progress_emitter(args.quiet),
    )
    _report(final)
    return 0 if final.values.get("status") == Status.DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
