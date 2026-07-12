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
import functools
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from langgraph.types import Command

from blacksmith import __version__
from blacksmith.config import CONFIG_FILENAME, BlacksmithConfig, ConfigError, find_config
from blacksmith.contract import ContractError, PRDContract, parse_prd
from blacksmith.costs import run_costs
from blacksmith.dashboard import serve as serve_dashboard
from blacksmith.events import (
    NODE_END,
    NODE_START,
    RUN_STATUS,
    UNIT_RESULT,
    LiveSink,
    build_live_store,
    run_status_payload,
    unit_result_payloads,
)
from blacksmith.executor import Executor
from blacksmith.gate import run_fix, run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.issue import IssueError, scaffold_from_issue
from blacksmith.memory import build_store
from blacksmith.metrics import build_metrics_store, get_run, list_runs, record_run
from blacksmith.nodes.pr import Runner, subprocess_runner
from blacksmith.render import Renderer
from blacksmith.respond import RespondError, RespondResult, respond_to_pr
from blacksmith.sandbox import SandboxConfig as SandboxSettings
from blacksmith.sandbox import SandboxManager
from blacksmith.state import Status
from blacksmith.worktree import CloneManager, WorktreeManager, normalize_remote_slug


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
    """Compile the graph wired with the real production dependencies.

    The run is isolated in a throwaway local *clone* (CloneManager), not a linked
    worktree: each run gets its own .git with origin pointed at the real remote, so the
    agent works on a disposable copy and can never reach the source checkout (this kills
    the self-targeting hazard). A push from the clone still targets the real GitHub remote,
    so opening a PR works exactly as before from the caller's perspective.
    """
    return compile_graph(
        checkpointer,
        executor=Executor(config),
        worktree_manager=CloneManager(config.resolve_repo_path()),
        gate=run_gate,
        fix=run_fix,
        store=build_store(config.store.db_path),
        limits=config.limits,
        # Wire the post-gate adversarial review loop (WU-REVIEW-LOOP) into production. Without
        # this the review node is never seeded/entered — the whole reviewer feature stays dark
        # on real runs. ReviewConfig.enabled (default True) is the on/off switch.
        review=config.review,
        # Wire the repo-map / search_code index (WU-INDEX-*, WU-PLAN-*) into production. Without
        # this the index config never reaches the graph and the whole indexing feature stays dark
        # on real runs regardless of [index].enabled. IndexConfig.enabled (default False) is the
        # on/off switch.
        index=config.index,
        # Wire the sandbox self-verify container (WU-SANDBOX-*) into production. build_graph_for is
        # the ONLY place a live SandboxManager is constructed — bridge blacksmith's own
        # [sandbox] config into the manager's own SandboxConfig (the two were built separately and
        # never connected, so the feature was dark on real runs). SandboxConfig.enabled (default
        # False) is the on/off switch; the manager is inert until prepare_worktree starts it.
        sandbox=SandboxManager(
            config=SandboxSettings(
                enabled=config.sandbox.enabled,
                image=config.sandbox.image,
                setup_cmd=config.sandbox.setup_cmd,
                exec_timeout_s=config.sandbox.exec_timeout_s,
            )
        ),
        # Wire spectrum-based fault localization (WU-SBFL-*) into production. Without this the
        # SBFL config never reaches the graph and the whole feature stays dark on real runs
        # regardless of [sbfl].enabled — a wired-but-dark feature. SBFLConfig.enabled (default
        # False) is the on/off switch; when enabled it enriches ONLY the fix-retry feedback and
        # never touches the gate's pass/fail decision.
        sbfl=config.sbfl,
        # Open the combined PR against the target repo's configured default branch
        # (``[target].default_branch``) via ``gh pr create --base``, instead of relying on
        # gh to guess the repo default. Without this the field was declared and documented
        # but read nowhere, so a repo whose default isn't ``main`` worked only by luck.
        default_branch=config.target.default_branch,
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


class FreshRunError(Exception):
    """Raised when a fresh run targets a thread paused mid-run (resume it instead)."""


class RepoConsistencyError(Exception):
    """Raised when the target repo's git remote doesn't match the PRD's target repo."""


class RespondCLIError(Exception):
    """Raised when ``blacksmith respond`` cannot look up the target PR's branch."""


def check_repo_consistency(worktree_manager: WorktreeManager, expected_repo: str) -> str:
    """Preflight: confirm the target repo's ``origin`` matches the PRD's primary_target_repo.

    Runs BEFORE any worktree is created or model spend begins (PRD safety guard): it reads
    the configured ``[target].repo_path``'s ``origin`` remote via the existing git plumbing
    and compares its ``owner/name`` slug to the PRD's expected ``primary_target_repo``. SSH
    and HTTPS forms of the same slug are treated as equal. Returns the matched slug on
    success; raises ``RepoConsistencyError`` — naming both the configured remote slug and
    the PRD's expected owner/repo — on a mismatch (or a missing remote), so a misdirected
    run aborts non-zero without touching a worktree or the model.
    """
    expected = normalize_remote_slug(expected_repo)
    actual = worktree_manager.remote_slug()
    if actual is None:
        raise RepoConsistencyError(
            f"target repo {worktree_manager.repo_path} has no usable 'origin' git remote, "
            f"so it cannot be confirmed to match the PRD's primary_target_repo "
            f"{expected_repo!r} (expected slug {expected!r}). Refusing to run."
        )
    if actual != expected:
        raise RepoConsistencyError(
            f"target repo mismatch: the configured remote slug is {actual!r} but the PRD's "
            f"primary_target_repo expects {expected!r} (from {expected_repo!r}). Refusing to "
            "run against the wrong repository — no worktree created, no model spend."
        )
    return actual


# The sequential implement->gate loop spends ~3 LangGraph super-steps per unit, so the
# default recursion_limit of 25 caps a run at roughly 7-8 units before LangGraph raises
# GraphRecursionError. 150 sits comfortably above ~3 super-steps/unit (~50 units of
# headroom), letting large multi-unit DAGs run to completion. This only raises the
# ceiling — it never changes which nodes run or in what order.
RECURSION_LIMIT = 150


def _step(graph, payload, config, *, on_node, on_event=None):
    """Run the graph until it next halts (an interrupt or END), streaming progress.

    Streams ``stream_mode=["updates", "debug", "custom"]``. The ``debug`` stream emits a
    ``task`` event when a node is ABOUT to run and a ``task_result`` event when it finishes,
    so ``on_node(node)`` fires as the node *starts* — not after it finishes. This is what
    keeps a 700s ``plan`` from looking like a hung ``ingest`` on the CLI (the previous
    ``updates``-only stream only named a node once its update arrived, i.e. on completion).
    The ``updates`` stream is still consulted for the ``__interrupt__`` payload the gate
    driver depends on. This only observes the graph — it drives the same nodes in the same
    order as ``invoke``.

    The ``custom`` stream (WU-LIVE-INTRA-NODE) carries whatever a node's executor call
    wrote via ``get_stream_writer()`` from INSIDE its (possibly long) call — per-turn /
    tool_use activity, and each review finding as it is produced — so the live view shows
    progress WITHIN a node, not only at its boundaries. Each chunk is tagged with the
    currently-running node (tracked from the same ``debug`` "task" events used for
    ``on_node``) and forwarded to ``on_event`` under its own ``kind`` (the emitter sets
    ``"kind": "node_activity"``; default to that if a chunk omits it). This is a pure
    ADDITIVE OBSERVATION channel — a node with no stream writer bound (or with the sink
    disabled/failing) emits nothing here and the graph's control flow is entirely unaffected.

    ``on_event(kind, payload)``, when given, additionally receives ``node_start`` /
    ``node_end`` events for the additive live sink (WU-RUN-EVENTS): ``node_end`` carries
    the node's wall-clock ``duration``. It is a pure OBSERVATION hook — it never alters
    which nodes run or in what order, and the emitter itself is best-effort (a failed
    live-sink write is swallowed at the call site, exactly like the metrics sink).

    The graph-invocation ``config`` is augmented with ``RECURSION_LIMIT`` so large
    multi-unit DAGs don't trip LangGraph's default-25 super-step ceiling.
    """
    config = {**config, "recursion_limit": RECURSION_LIMIT}
    interrupt = None
    starts: dict[str, float] = {}
    current_node: str | None = None
    for mode, chunk in graph.stream(payload, config, stream_mode=["updates", "debug", "custom"]):
        if mode == "custom":
            if on_event is not None and isinstance(chunk, dict):
                kind = chunk.get("kind") or "node_activity"
                activity = {k: v for k, v in chunk.items() if k != "kind"}
                if current_node is not None:
                    activity.setdefault("node", current_node)
                on_event(kind, activity)
            continue
        if mode == "debug":
            if not isinstance(chunk, dict):
                continue
            ctype = chunk.get("type")
            cpayload = chunk.get("payload") or {}
            name = cpayload.get("name")
            if ctype == "task" and name:
                task_id = cpayload.get("id")
                if task_id is not None:
                    starts[task_id] = time.monotonic()
                current_node = name
                if on_node is not None:
                    on_node(name)
                if on_event is not None:
                    on_event(NODE_START, {"node": name})
            elif ctype == "task_result" and name:
                if on_event is not None:
                    started = starts.pop(cpayload.get("id"), None)
                    duration = None if started is None else time.monotonic() - started
                    on_event(NODE_END, {"node": name, "duration": duration})
            continue
        # mode == "updates": the only source of the gate's __interrupt__ payload.
        if not isinstance(chunk, dict):
            continue
        for node, update in chunk.items():
            if node == "__interrupt__":
                interrupt = update
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


def _drive_gates(graph, config, *, approver, on_node, result=None, on_wait=None, on_event=None):
    """Drive the graph through its approval gates to END, consulting ``approver``.

    Shared by a fresh ``drive`` and a ``resume``: each iteration reads the current
    snapshot, halts the loop at END, otherwise asks ``approver`` to decide the pending
    gate and injects that decision with ``Command(resume=...)``. ``result`` seeds the
    first gate's payload for a fresh run; resume passes ``None`` and reads it from the
    persisted snapshot instead.

    ``on_wait(seconds)``, when given, is called with the wall-clock spent BLOCKED in each
    ``approver`` call — the time a human took to answer a gate. The caller accumulates it
    to subtract from the run's recorded ``duration_s`` so that figure measures pipeline
    work, not idle approval wait. It never affects control flow.
    """
    while True:
        snapshot = graph.get_state(config)
        if not snapshot.next:  # reached END
            return snapshot
        payload = _gate_payload(result, snapshot)
        wait_start = time.monotonic()
        approved = approver(payload, snapshot.values)
        if on_wait is not None:
            on_wait(time.monotonic() - wait_start)
        result = _step(
            graph, Command(resume=approved), config, on_node=on_node, on_event=on_event
        )


def _isolate_fresh_run(graph, config, thread_id: str) -> None:
    """Stop a fresh run from inheriting a prior run's state on the same thread-id.

    Mirrors WU-RESUME's ``snapshot.created_at`` probe to classify the thread BEFORE the
    fresh run starts:

    - UNUSED thread (no checkpoint): nothing to isolate — proceed exactly as today.
    - PAUSED at a gate (a pending interrupt, ``snapshot.next`` non-empty): a fresh run
      would clobber an in-flight run, so refuse and point at ``blacksmith resume`` instead.
    - TERMINAL (the prior run reached END: done/halted/awaiting_qa, ``snapshot.next``
      empty): clear the thread's checkpoint so the new run starts with clean run-scoped
      state — it must not carry the prior run's accumulated ``errors`` or its ``pr_url``.

    The isolation is enforced here at the checkpoint level (``delete_thread``), NOT by
    weakening the ``errors`` reducer (which correctly accumulates WITHIN a single run).
    """
    snapshot = graph.get_state(config)
    if getattr(snapshot, "created_at", None) is None:
        return  # unused thread-id — a first run behaves exactly as today
    if snapshot.next:  # a pending interrupt — the prior run is paused mid-flight
        raise FreshRunError(
            f"thread-id {thread_id!r} has a run paused at a gate; starting a fresh run "
            f"would clobber it. Continue it with `blacksmith resume --thread-id "
            f"{thread_id}`, or start the fresh run on a different --thread-id."
        )
    # The prior run reached a terminal state: drop its checkpoint so this run starts clean.
    graph.checkpointer.delete_thread(thread_id)


def _safe_event_emitter(sink: LiveSink, thread_id: str):
    """Wrap a live sink in a BEST-EFFORT emitter (WU-RUN-EVENTS).

    Returns ``emit(kind, payload)`` which writes one event for ``thread_id`` and swallows
    ANY exception, so a live-sink write failure never affects the run — exactly like the
    metrics sink. The live channel is purely additive OBSERVATION.
    """

    def emit(kind: str, payload: dict) -> None:
        try:
            sink.emit(thread_id, kind, payload)
        except Exception:
            pass

    return emit


def _live_emitter(config: BlacksmithConfig, thread_id: str):
    """Build the best-effort live run-event emitter for ``thread_id``, or ``None``.

    Returns ``None`` when the sink is disabled (``[live] enabled=false``) or cannot be
    opened, so the drive loop emits nothing and the run behaves exactly as today. Opening
    the sink is itself swallowed (a bad ``db_path`` yields ``None``, never a raised error).
    """
    if not config.live.enabled:
        return None
    try:
        conn = build_live_store(config.live.db_path)
    except Exception:
        return None
    return _safe_event_emitter(LiveSink(conn), thread_id)


def _emit_run_summary(on_event, values) -> None:
    """Emit end-of-unit + end-of-run summary events from the FINAL state (WU-RUN-EVENTS).

    Derived entirely from the EXISTING ``cost_events`` / ``unit_results`` reducers — no new
    graph state. Each ``on_event`` call is best-effort (the emitter swallows errors), so a
    live-sink failure here never affects the run's outcome.
    """
    if on_event is None:
        return
    for payload in unit_result_payloads(values):
        on_event(UNIT_RESULT, payload)
    on_event(RUN_STATUS, run_status_payload(values))


def drive(
    graph, prd_path, *, approver, thread_id: str = "run", on_node=None, issue_number=None,
    on_wait=None, on_event=None,
):
    """Run one work unit, pausing at each approval gate to consult ``approver``.

    ``approver(payload, values) -> bool`` decides each gate. Returns the final state
    snapshot once the graph reaches END. ``on_node(node)``, when given, is called with
    each node's name as it runs (progress output); it never affects control flow.

    ``issue_number``, when given, seeds the run state with the originating GitHub issue
    so the opened PR links ``Closes #N`` in its body (it is never auto-merged/closed).

    A fresh run never inherits a prior run's state on the same thread-id
    (WU-FRESH-RUN-GUARD): see ``_isolate_fresh_run`` — a terminal thread is reset to a
    clean slate, and a thread paused mid-run is left untouched (it must be resumed).
    """
    config = {"configurable": {"thread_id": thread_id}}
    _isolate_fresh_run(graph, config, thread_id)
    payload = {"prd_path": str(prd_path)}
    if issue_number is not None:
        payload["issue_number"] = issue_number
    result = _step(graph, payload, config, on_node=on_node, on_event=on_event)
    final = _drive_gates(
        graph, config, approver=approver, on_node=on_node, result=result,
        on_wait=on_wait, on_event=on_event,
    )
    _emit_run_summary(on_event, final.values)
    return final


def resume(graph, thread_id: str, *, approver, on_node=None, on_wait=None, on_event=None):
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
    final = _drive_gates(
        graph, config, approver=approver, on_node=on_node, on_wait=on_wait,
        on_event=on_event,
    )
    _emit_run_summary(on_event, final.values)
    return final


def _cli_approver(
    payload, values, *, renderer: Renderer | None = None, as_json: bool = False
) -> bool:
    """Interactive gate: render the payload, then prompt for a y/n decision.

    The payload is *rendered* by the presentation layer (plan steps / target modules /
    test contract, or the PR diffstat / test result / files touched) rather than dumped
    as raw JSON; ``--json`` (``as_json``) preserves the legacy raw payload for scripting.
    The ``Approve? [y/N]`` prompt and the bool it returns are unchanged.
    """
    renderer = renderer if renderer is not None else Renderer()
    renderer.gate(payload, as_json=as_json)
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


def _resolve_approver(args, renderer: Renderer):
    """Pick the approver and, for the interactive one, bind the renderer + ``--json`` flag.

    Headless approvers (``--auto-approve`` / ``--approve``) are returned unchanged. The
    interactive ``_cli_approver`` is bound to the presentation-layer renderer (so the gate
    payload is rendered rather than dumped) and to ``args.json`` (legacy raw JSON payload).
    """
    approver = _select_approver(args)
    if approver is _cli_approver:
        return functools.partial(
            _cli_approver, renderer=renderer, as_json=getattr(args, "json", False)
        )
    return approver


def _build_renderer(args) -> Renderer:
    """Build the presentation-layer Renderer from CLI flags + environment.

    ``--plain`` (``args.plain``) and the ``NO_COLOR`` convention both force plain output;
    otherwise the rendering decision is made per-stream by the layer (rich only on a real
    TTY). Built once per command so the TTY-vs-plain choice is decided a single time.
    """
    return Renderer(plain=getattr(args, "plain", False), no_color=bool(os.environ.get("NO_COLOR")))


def _progress_emitter(quiet: bool, renderer: Renderer | None = None):
    """Build the per-node progress callback for ``drive``'s ``on_node`` hook.

    Returns ``None`` when ``quiet`` is set (no progress stream). Otherwise returns a
    callable that renders a concise per-node phase indicator to STDERR via the rendering
    layer, keeping stdout reserved for the final machine-readable report. In plain /
    non-TTY mode that degrades to a flat ``blacksmith: <node>`` line.
    """
    if quiet:
        return None
    renderer = renderer if renderer is not None else Renderer()

    def emit(node: str) -> None:
        renderer.progress(node)

    return emit


def _cost_usds(values) -> list:
    """Per-call ``cost_usd`` figures to sum for the run total (WU-COST-EVENTS).

    Reads the append-only ``cost_events`` ledger — one record per model call, so a
    multi-unit run (and every escalation attempt) is counted, not just plan + the final
    unit's last-write-wins ``implementation`` slice. Falls back to those slices only when
    no ledger is present (e.g. a skeleton run with no executor wired).
    """
    events = values.get("cost_events")
    if events:
        return [e.get("cost_usd") for e in events]
    return [
        (values.get("plan") or {}).get("cost_usd"),
        (values.get("implementation") or {}).get("cost_usd"),
    ]


def _usages(values) -> list:
    """Per-call usage breakdowns to sum for the token line (WU-COST-EVENTS).

    Like ``_cost_usds``, this reads the append-only ``cost_events`` ledger so every call's
    tokens are counted across a multi-unit run, falling back to the per-node slices only
    when no ledger is present."""
    events = values.get("cost_events")
    if events:
        return [e.get("usage") for e in events]
    return [
        (values.get("plan") or {}).get("usage"),
        (values.get("implementation") or {}).get("usage"),
    ]


def _total_cost_line(values) -> str:
    """Build the run-end total-cost line summing every model call's ``cost_usd``.

    The figures come from the append-only ``cost_events`` ledger (WU-COST-EVENTS), so a
    multi-unit run reports the SUM of all units' (and all escalation attempts') spend —
    fixing the prior undercount where only plan + the last-write-wins ``implementation``
    slice were counted. A call that reports ``None`` (no executor wired, or a model call
    that returned no cost) is excluded from the sum rather than crashing it. If nothing
    reported a cost, the spend is unknown — say so plainly.
    """
    known = [c for c in _cost_usds(values) if c is not None]
    if not known:
        return "total cost: cost unavailable"
    return f"total cost: ${sum(known):.2f}"


def _token_line(values) -> str:
    """Build the run-end token line from the per-node usage breakdowns (WU-COST-INSTRUMENT).

    Each model call records a ``usage`` breakdown (input/output + cache counters) in the
    append-only ``cost_events`` ledger alongside ``cost_usd`` (WU-COST-EVENTS). This sums
    those across every call and reports total input, total output, and a cache-hit rate —
    ``cache_read / (input + cache_read + cache_creation)``. A call whose usage is ``None``
    (no executor wired, or a call with no usage) is skipped; if nothing reported usage the
    figure is unknown — say so plainly rather than crash the report.
    """
    known = [u for u in _usages(values) if u]
    if not known:
        return "tokens: unavailable"
    total_input = sum(u.get("input_tokens", 0) for u in known)
    total_output = sum(u.get("output_tokens", 0) for u in known)
    cache_read = sum(u.get("cache_read_input_tokens", 0) for u in known)
    cache_creation = sum(u.get("cache_creation_input_tokens", 0) for u in known)
    denom = total_input + cache_read + cache_creation
    hit_rate = cache_read / denom if denom else 0.0
    return (
        f"tokens: input {total_input}, output {total_output}, "
        f"cache-hit {hit_rate:.1%}"
    )


def _report(snapshot, renderer: Renderer | None = None) -> None:
    """Render the run-end status summary via the presentation layer.

    Computes the cost / token lines (unchanged) and hands the status, PR url, errors and
    those lines to the rendering layer, which color-codes a status panel on a TTY and
    prints the same parseable lines in plain / non-TTY mode.
    """
    renderer = renderer if renderer is not None else Renderer()
    values = snapshot.values
    renderer.report(
        status=values.get("status"),
        pr_url=values.get("pr_url"),
        errors=values.get("errors", []),
        cost_line=_total_cost_line(values),
        token_line=_token_line(values),
    )


def _record_metrics(
    config: BlacksmithConfig,
    snapshot,
    *,
    thread_id: str,
    prd_path: str | Path,
    started_at: float,
    ended_at: float,
    approval_wait_s: float = 0.0,
) -> None:
    """Record run + per-unit metrics rows to the local metrics SQLite (WU-METRICS-RECORD).

    BEST-EFFORT and write-only: any exception (a bad path, a locked DB, a malformed row)
    is swallowed so a metrics failure never changes the run's exit code or outcome. The
    metrics DB is its own file (``[metrics] db_path``) and is never read back into the
    graph, so a run behaves exactly the same with or without this sink.
    """
    try:
        store = build_metrics_store(config.metrics.db_path)
        try:
            record_run(
                store,
                snapshot,
                thread_id=thread_id,
                prd_path=prd_path,
                started_at=started_at,
                ended_at=ended_at,
                transcripts_dir=config.transcripts.dir,
                approval_wait_s=approval_wait_s,
            )
        finally:
            store.close()
    except Exception:
        pass


_NO_RUNS_MESSAGE = "no runs recorded yet"


def _fmt_cost(value) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else "-"


def _fmt_rate(value) -> str:
    return f"{value:.1%}" if isinstance(value, (int, float)) else "-"


def _fmt_duration(value) -> str:
    return f"{value:.1f}" if isinstance(value, (int, float)) else "-"


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a plain, ANSI-free, whitespace-aligned table to stdout (parseable)."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line.rstrip())
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())


def _print_run_list(runs: list[dict]) -> None:
    headers = [
        "thread_id", "status", "total_cost", "cache_hit", "duration_s", "units", "pr_url"
    ]
    rows = [
        [
            str(run.get("thread_id") or "-"),
            str(run.get("status") or "-"),
            _fmt_cost(run.get("total_cost")),
            _fmt_rate(run.get("cache_hit_rate")),
            _fmt_duration(run.get("duration_s")),
            str(run.get("units_count") if run.get("units_count") is not None else "-"),
            str(run.get("pr_url") or "-"),
        ]
        for run in runs
    ]
    _print_table(headers, rows)


def _transcript_paths(run: dict) -> list[str]:
    """The run row's recorded transcript file paths (empty when none were captured).

    Decodes the JSON list recorded by ``metrics.record_run``. A missing / malformed /
    empty field reads as no transcripts, so the detail view omits the section cleanly.
    """
    raw = run.get("transcripts")
    if not raw:
        return []
    try:
        paths = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(p) for p in paths if p]


def _print_run_detail(run: dict, units: list[dict]) -> None:
    print(f"run {run.get('thread_id')} — {run.get('status') or '-'}")
    print(f"total cost: {_fmt_cost(run.get('total_cost'))}")
    print(f"cache-hit: {_fmt_rate(run.get('cache_hit_rate'))}")
    print(f"duration: {_fmt_duration(run.get('duration_s'))}s")
    print(f"units: {run.get('units_count') if run.get('units_count') is not None else '-'}")
    print(f"pr: {run.get('pr_url') or '-'}")
    print("")
    headers = ["unit_id", "title", "models", "cost", "turns", "gate_result", "files", "diff_size"]
    rows = [
        [
            str(u.get("unit_id") or "-"),
            str(u.get("title") or "-"),
            str(u.get("models") or "-"),
            _fmt_cost(u.get("cost")),
            str(u.get("turns") if u.get("turns") is not None else "-"),
            str(u.get("gate_result") or "-"),
            str(u.get("files_count") if u.get("files_count") is not None else "-"),
            str(u.get("diff_size") if u.get("diff_size") is not None else "-"),
        ]
        for u in units
    ]
    _print_table(headers, rows)

    transcripts = _transcript_paths(run)
    if transcripts:
        print("")
        print("transcripts:")
        for path in transcripts:
            print(f"  {path}")


def _runs(argv: list[str] | None = None) -> int:
    """``blacksmith runs``: list recorded run history, or drill into one run (READ-ONLY).

    Reads the local metrics SQLite sink (``[metrics] db_path``) with a strictly read-only
    connection — it never writes, and the metrics DB is never read back into the graph. An
    empty or absent store prints a friendly message and exits 0. ``runs <thread_id>`` prints
    that run's summary plus its per-unit rows. Output is plain text (no control codes).
    """
    parser = argparse.ArgumentParser(
        prog="blacksmith runs",
        description="List recorded run history, or drill into one run by thread-id (read-only).",
    )
    parser.add_argument(
        "thread_id",
        nargs="?",
        help="Show this run's summary and per-unit rows instead of the run list.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="blacksmith config path (default: discovered by walking up to the git root).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of runs to list, most-recent first (default: 20).",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)
    db_path = Path(config.metrics.db_path)
    if not db_path.is_file():
        print(_NO_RUNS_MESSAGE)
        return 0

    store = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if args.thread_id:
            run, units = get_run(store, args.thread_id)
            if run is None:
                print(f"no run recorded for thread-id {args.thread_id!r}")
                return 0
            _print_run_detail(run, units)
            return 0
        runs = list_runs(store, args.limit)
        if not runs:
            print(_NO_RUNS_MESSAGE)
            return 0
        _print_run_list(runs)
        return 0
    except sqlite3.OperationalError:
        # A file that exists but isn't a populated metrics DB (no schema yet) reads as empty.
        print(_NO_RUNS_MESSAGE)
        return 0
    finally:
        store.close()


def _dashboard(argv: list[str] | None = None) -> int:
    """``blacksmith dashboard``: serve a localhost read-only JSON API over the metrics store.

    Binds ``127.0.0.1`` on an ephemeral port (``--port`` to fix it), prints the chosen
    ``http://127.0.0.1:<port>`` URL, and serves until interrupted. It reads the local
    metrics SQLite sink (``[metrics] db_path``) AND the additive live-events sink
    (``[live] db_path``) in READ-ONLY mode — it never writes either DB, never mutates run
    state, and never runs the graph. The live sink powers the ``/live`` fleet view; without
    it wired here that view renders but shows no runs.
    """
    parser = argparse.ArgumentParser(
        prog="blacksmith dashboard",
        description="Serve a localhost (127.0.0.1) read-only JSON API over the metrics store.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="blacksmith config path (default: discovered by walking up to the git root).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind on 127.0.0.1 (default: 0, an ephemeral OS-chosen port).",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)
    return serve_dashboard(
        config.metrics.db_path, port=args.port, live_db_path=config.live.db_path
    )


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
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Force plain, ANSI-free output even on a TTY (rendering is otherwise enabled "
        "only on a real terminal with NO_COLOR unset).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="At an approval gate, print the raw JSON payload (for scripting) instead of "
        "the rendered plan/PR view.",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)
    checkpointer = build_checkpointer(config.checkpointer.db_path)
    graph = build_graph_for(config, checkpointer)
    renderer = _build_renderer(args)

    approval_wait = 0.0

    def _track_wait(seconds: float) -> None:
        nonlocal approval_wait
        approval_wait += seconds

    started_at = time.time()
    try:
        final = resume(
            graph,
            args.thread_id,
            approver=_resolve_approver(args, renderer),
            on_node=_progress_emitter(args.quiet, renderer),
            on_wait=_track_wait,
            on_event=_live_emitter(config, args.thread_id),
        )
    except ResumeError as exc:
        print(f"resume: {exc}", file=sys.stderr)
        return 1
    ended_at = time.time()
    _report(final, renderer)
    _record_metrics(
        config,
        final,
        thread_id=args.thread_id,
        prd_path=final.values.get("prd_path") or "",
        started_at=started_at,
        ended_at=ended_at,
        approval_wait_s=approval_wait,
    )
    return 0 if final.values.get("status") == Status.DONE else 1


def _pr_branch(pr_number: int, *, repo: str | None, runner: Runner, cwd: Path | None) -> str:
    """Look up PR ``pr_number``'s branch (``headRefName``) via ``gh pr view``.

    Raises ``RespondCLIError`` (never a raw traceback) on a `gh` failure, unparseable
    output, or a payload missing ``headRefName``.
    """
    argv = ["gh", "pr", "view", str(pr_number), "--json", "headRefName"]
    if repo:
        argv += ["--repo", repo]
    result = runner(argv, cwd)
    if result.returncode != 0:
        raise RespondCLIError(
            f"could not look up PR #{pr_number}'s branch via gh: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RespondCLIError(
            f"could not parse gh output for PR #{pr_number}: {exc}"
        ) from exc
    branch = data.get("headRefName") if isinstance(data, dict) else None
    if not branch:
        raise RespondCLIError(f"gh returned no branch (headRefName) for PR #{pr_number}")
    return branch


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


_RESPOND_GATE_OUTPUT_TAIL_CHARS = 2000


def _render_respond_result(result: RespondResult, *, out=print) -> None:
    """Render a concise, one-line outcome for ``blacksmith respond`` (WU-RESPOND-CLI)."""
    if result.reason == "no_comments":
        out(f"PR #{result.pr_number}: no review comments — nothing to do")
        return
    comments = f"{result.comment_count} review comment{_plural(result.comment_count)}"
    attempts = f"{result.attempts} attempt{_plural(result.attempts)}"
    if result.pushed:
        out(
            f"PR #{result.pr_number}: addressed {comments} in {attempts} — "
            f"pushed an update to {result.branch}"
        )
        return
    out(
        f"PR #{result.pr_number}: {comments} addressed, but the revision still failed "
        f"the test gate after {attempts} — nothing pushed"
    )
    if result.gate_output:
        tail = result.gate_output[-_RESPOND_GATE_OUTPUT_TAIL_CHARS:]
        out(tail)


def run_respond(
    pr_number: int,
    *,
    config: BlacksmithConfig,
    repo_path: str | Path,
    executor: Executor,
    repo: str | None = None,
    pr_runner: Runner = subprocess_runner,
    gate=None,
    fix=None,
    clone_manager=None,
    contract: PRDContract | None = None,
    out=print,
) -> int:
    """Drive ``blacksmith respond`` for an already-resolved PR: look up its branch, run
    the revise flow (WU-RESPOND-FLOW), and render the outcome.

    This is a NEW, additive entry point — it never runs the normal ingest→plan→
    implement→gate→PR graph and ``respond_to_pr`` never opens a new PR (it only ever
    appends a commit to the PR's existing branch on a passing revision). Returns 0 when
    there was nothing to do or the revision was pushed; 1 when the revision never passed
    the gate, or the PR's branch could not be resolved.

    ``contract``, when given (WU-RESPOND-PRD-CONTRACT), is forwarded to
    ``respond_to_pr`` so the revision's implementer system-prompt constitution carries
    the PRD's real untouchables instead of the ``_default_contract`` placeholder. Left
    as ``None`` (the default), ``respond_to_pr`` behaves byte-for-byte as before.
    """
    repo_path = Path(repo_path)
    try:
        branch = _pr_branch(pr_number, repo=repo, runner=pr_runner, cwd=repo_path)
    except RespondCLIError as exc:
        out(f"respond: {exc}")
        return 1

    try:
        result = respond_to_pr(
            pr_number=pr_number,
            branch=branch,
            repo_path=repo_path,
            config=config,
            executor=executor,
            gate=gate,
            fix=fix,
            clone_manager=clone_manager,
            pr_runner=pr_runner,
            repo=repo,
            contract=contract,
        )
    except RespondError as exc:
        # A push failure on a PASSING revision surfaces as the same clean "respond: ..."
        # message as the branch-lookup failure above, not a raw traceback (reviewer nit).
        out(f"respond: {exc}")
        return 1
    _render_respond_result(result, out=out)
    return 0 if result.reason in ("pushed", "no_comments") else 1


def _respond(argv: list[str] | None = None) -> int:
    """``blacksmith respond --pr N``: revise an already-open PR from its review comments.

    ADDITIVE entry point (WU-RESPOND-CLI): loads config + .env, fetches the PR's review
    comments (WU-PR-COMMENTS), and runs the revise flow (WU-RESPOND-FLOW) against that
    PR's own branch. It never runs the normal ingest→plan→implement→gate→PR graph and
    never opens a new PR — only ever appends a commit to the PR's existing branch, and
    only once the revision passes the authoritative test gate.
    """
    parser = argparse.ArgumentParser(
        prog="blacksmith respond",
        description="Revise an already-open PR from its human review comments "
        "(never runs the normal PRD graph; never opens a new PR).",
    )
    parser.add_argument(
        "--pr", type=int, required=True, dest="pr_number", metavar="N",
        help="Number of the (already-open) PR to revise.",
    )
    parser.add_argument(
        "--repo", default=None,
        help="owner/repo for the PR, if it cannot be inferred from the local git remote.",
    )
    parser.add_argument(
        "--prd", default=None, dest="prd_path", metavar="PATH",
        help="Optional contract-conforming PRD markdown file. When given, its contract's "
        "real untouchables are threaded into the revision's implementer system-prompt "
        "constitution in place of the placeholder default.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="blacksmith config path (default: discovered by walking up to the git root).",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)
    repo_path = config.resolve_repo_path()

    contract = None
    if args.prd_path is not None:
        try:
            contract = parse_prd(args.prd_path).contract
        except ContractError as exc:
            print(f"respond: {exc}")
            return 1

    return run_respond(
        args.pr_number,
        config=config,
        repo_path=repo_path,
        executor=Executor(config),
        repo=args.repo,
        contract=contract,
    )


def _costs(argv: list[str] | None = None) -> int:
    """``blacksmith costs``: org-level usage + cost from the Admin API (read-only).

    An additive, offline-of-the-graph reporting subcommand with no model spend: it reads
    a SEPARATE org-scoped Admin API key from its own configured env var and issues only
    GET requests to api.anthropic.com. Returns 0 on success; 1 with a clear message
    naming the env var when the admin key is unset.
    """
    parser = argparse.ArgumentParser(
        prog="blacksmith costs",
        description="Report org-level usage + cost from the Anthropic Admin API (read-only).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="blacksmith config path (default: discovered by walking up to the git root).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Size of the reporting window in days, ending today (default: 30).",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)
    try:
        run_costs(config, days=args.days)
    except ConfigError as exc:
        print(f"costs: {exc}", file=sys.stderr)
        return 1
    return 0


def _scaffold_issue(issue_number: int, config: BlacksmithConfig) -> int:
    """``blacksmith --issue N`` (no PRD): scaffold a Contract v1 PRD skeleton from issue N.

    Fetches the issue via the existing ``gh`` CLI and writes a ``parse_prd``-valid
    skeleton seeded with the issue's title/body, leaving ``target_modules`` /
    ``test_contract`` as explicit human-TODO placeholders. No model spend, no graph run.
    Returns 0 on success; 1 with a clear message if ``gh`` cannot fetch the issue.
    """
    repo_path = config.resolve_repo_path()
    try:
        path = scaffold_from_issue(
            issue_number,
            component=repo_path.name,
            primary_target_repo=repo_path.name,
            out_dir=repo_path,
            cwd=repo_path,
        )
    except IssueError as exc:
        print(f"issue: {exc}", file=sys.stderr)
        return 1
    print(f"Scaffolded PRD skeleton from issue #{issue_number}: {path}")
    print(
        "Complete the TODO markers (target_modules, test_contract), then run "
        f"`blacksmith --issue {issue_number} {path}` so the PR links Closes #{issue_number}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "validate":
        return _validate(argv[1:])
    if argv and argv[0] == "resume":
        return _resume(argv[1:])
    if argv and argv[0] == "respond":
        return _respond(argv[1:])
    if argv and argv[0] == "costs":
        return _costs(argv[1:])
    if argv and argv[0] == "runs":
        return _runs(argv[1:])
    if argv and argv[0] == "dashboard":
        return _dashboard(argv[1:])

    parser = argparse.ArgumentParser(prog="blacksmith", description="Run one PRD work unit.")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "prd_path",
        nargs="?",
        help="Path to a contract-conforming PRD markdown file. Omit with --issue N to "
        "scaffold a PRD skeleton from that GitHub issue.",
    )
    parser.add_argument(
        "--issue",
        type=int,
        default=None,
        metavar="N",
        help="Originating GitHub issue number. Without a PRD path, scaffold a skeleton "
        "from issue N; with one, run it and link `Closes #N` in the opened PR.",
    )
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
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Force plain, ANSI-free output even on a TTY (rendering is otherwise enabled "
        "only on a real terminal with NO_COLOR unset).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="At an approval gate, print the raw JSON payload (for scripting) instead of "
        "the rendered plan/PR view.",
    )
    args = parser.parse_args(argv)

    if args.prd_path is None and args.issue is None:
        parser.error("a PRD path is required (or pass --issue N to scaffold one)")

    load_dotenv(Path.cwd() / ".env")
    config = _load_config(args.config)

    if args.prd_path is None:
        return _scaffold_issue(args.issue, config)

    # Preflight (runs before any worktree creation or model spend): a conforming PRD must
    # target the repo blacksmith is pointed at. A non-conforming PRD is left for the graph's
    # ingest node to reject with its field-level error (unchanged behaviour).
    try:
        prd = parse_prd(args.prd_path)
    except ContractError:
        prd = None
    if prd is not None:
        try:
            check_repo_consistency(
                WorktreeManager(config.resolve_repo_path()),
                prd.contract.primary_target_repo,
            )
        except RepoConsistencyError as exc:
            print(f"blacksmith: {exc}", file=sys.stderr)
            return 1

    checkpointer = build_checkpointer(config.checkpointer.db_path)
    graph = build_graph_for(config, checkpointer)
    renderer = _build_renderer(args)

    approval_wait = 0.0

    def _track_wait(seconds: float) -> None:
        nonlocal approval_wait
        approval_wait += seconds

    started_at = time.time()
    try:
        final = drive(
            graph,
            args.prd_path,
            approver=_resolve_approver(args, renderer),
            thread_id=args.thread_id,
            on_node=_progress_emitter(args.quiet, renderer),
            issue_number=args.issue,
            on_wait=_track_wait,
            on_event=_live_emitter(config, args.thread_id),
        )
    except FreshRunError as exc:
        print(f"blacksmith: {exc}", file=sys.stderr)
        return 1
    ended_at = time.time()
    _report(final, renderer)
    _record_metrics(
        config,
        final,
        thread_id=args.thread_id,
        prd_path=args.prd_path,
        started_at=started_at,
        ended_at=ended_at,
        approval_wait_s=approval_wait,
    )
    return 0 if final.values.get("status") == Status.DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
