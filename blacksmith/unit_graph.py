"""One checkpointed unit state machine for sequential and parallel scheduling."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from langgraph.config import get_config, get_stream_writer
from langgraph.constants import CONFIG_KEY_CHECKPOINTER
from langgraph.graph import END, START, StateGraph

from blacksmith.commands import command_scope_for_state
from blacksmith.nodes.implement import audit_and_commit, conventional_commit_message
from blacksmith.path_policy import policy_for_state
from blacksmith.receipts import (
    ReceiptError,
    attempt_identity,
    call_context_for,
    digest,
    encoded,
    outcome_history,
    outcome_record,
)
from blacksmith.run_context import RunContextError, context_from_state, unit_hash
from blacksmith.state import BlacksmithState, Status, UnitAttemptState
from blacksmith.unit_state import new_unit_attempt
from blacksmith.verification import candidate_identity
from blacksmith.worktree import CloneManager, RunCloneManager

_parent_writer = ContextVar("blacksmith_unit_parent_writer", default=None)


def _tag(state, phase, update):
    """Stable fallback IDs also make legacy/offline transports replay safe."""
    unit = state.get("selected_unit")
    identity = [
        state.get("run_id", "legacy"),
        unit.id if unit else "__none__",
        attempt_identity(state),
        phase,
    ]
    update = dict(update)
    for field in ("cost_events", "errors", "review_findings", "unresolved_review_findings"):
        if field in update:
            update[field] = [
                row
                if row.get("event_id")
                else {
                    **row,
                    "event_id": digest(encoded([*identity, field, index, row])),
                    "unit_id": unit.id if unit else "__none__",
                    "attempt_id": attempt_identity(state),
                }
                for index, row in enumerate(update[field])
            ]
    if "review_current_findings" in update and "review_findings" in update:
        update["review_current_findings"] = update["review_findings"]
    update["unit_stage"] = phase
    if phase == "audit_commit" and update.get("status") != Status.HALTED:
        update["implementation_attempt_id"] = attempt_identity(state)
    if update.get("status") == Status.HALTED and phase != "finalize_unit":
        update.setdefault("unit_failed_stage", phase)
    return update


def build_unit_graph(deps, worktree_manager=None):
    """Compile once; a parent invocation inherits isolated checkpoint namespaces."""
    from blacksmith.config import reject_enabled_sandbox
    reject_enabled_sandbox(deps.sandbox is not None and deps.sandbox.config.enabled)
    from blacksmith import graph as gm

    def wrap(phase, fn):
        def node(state):
            unit = state.get("selected_unit")
            try:
                writer = _parent_writer.get() or get_stream_writer()
                from blacksmith.observability import runtime_identity
                writer(
                    {
                        "kind": "unit_stage",
                        "phase": phase,
                        **runtime_identity(state),
                        "unit_id": unit.id if unit else "__run__",
                        "attempt_id": attempt_identity(state),
                    }
                )
            except Exception as exc:
                from blacksmith.observability import warn_optional
                warn_optional("activity stream", exc)
            from blacksmith.run_clock import failure_scope
            with failure_scope():
                with command_scope_for_state(state, phase) as scope, failure_scope(scope.context):
                    update = fn(state)
                    return _tag(state, phase, update)

        return node

    def prepare(state):
        if state.get("unit_initialized"):
            return {"status": Status.IMPLEMENTING}
        unit = state.get("selected_unit")
        if unit is None:
            return {"unit_initialized": True}
        path = state.get("worktree_path")
        branch = state.get("branch")
        if state.get("run_context") and state.get("unit_mode") != "parallel":
            expected = context_from_state(state).workspace_path
            if path is None or Path(path).resolve() != expected:
                raise RunContextError("unit workspace differs from immutable run ownership")
        if state.get("unit_mode") == "parallel":
            manager = worktree_manager
            if state.get("run_context"):
                manager = RunCloneManager(context_from_state(state))
            if not isinstance(manager, CloneManager):
                raise ValueError("parallel unit requires clone ownership")
            clone = manager.create_from(state["shared_clone_path"], unit.id)
            path, branch = str(clone.path), clone.branch
        base = None
        if path and (state.get("run_context") or (Path(path) / ".git").exists()):
            base = gm._git_run(path, "rev-parse", "HEAD").stdout.strip()
        initial = new_unit_attempt(unit.id, base)
        # Sequential preparation may already be checkpointed by S07. Do not
        # reset consumed recovery counters on entry/resume of that same unit.
        if state.get("unit_mode") != "parallel" and (
            state.get("current_unit_id") == unit.id
            or (not state.get("run_context") and worktree_manager is None)
        ):
            initial.update({k: state[k] for k in UnitAttemptState.__annotations__ if k in state})
        human = bool(state.get("prd") and state["prd"].contract.gate_for(unit) == "human")
        prepared = {
            **initial,
            "worktree_path": path,
            "branch": branch,
            "unit_initialized": True,
            "unit_namespace": f"{state.get('run_id', 'legacy')}/{unit_hash(unit.id)}",
            "unit_human": human,
        }
        if state.get("run_context") and not human:
            from blacksmith.budget import AdmissionDenied, BudgetLedger
            from blacksmith.receipts import call_context_for
            ledger = BudgetLedger(context_from_state(state))
            balance = ledger.balance()
            if balance.unacknowledged_overrun or (
                    balance.remaining_usd is not None and balance.remaining_usd <= 0):
                call = call_context_for({**state, **prepared}, unit_id=unit.id)
                try:
                    # This writes the original denied-before-start identity. A
                    # concurrent refund may instead grant it; the SDK then reuses
                    # that exact reservation rather than allocating a second one.
                    ledger.reserve(call.key("model.implement"))
                except AdmissionDenied as exc:
                    prepared.update(status=Status.HALTED, unit_failed_stage="implement",
                        errors=[{"node": "prepare_attempt", "message":
                            f"budget denied before new unit {unit.id}: {exc.reason}"}])
        return prepared

    original_model = gm.implement
    bound_model = gm._node_with(original_model, defer_commit=True, **deps.implement_kwargs())

    def model(state):
        if gm.implement is original_model:
            return bound_model(state)
        return gm._node_with(gm.implement, defer_commit=True, **deps.implement_kwargs())(state)

    def gate(state):
        result = gm.test_gate(state, gate=deps.gate)
        # Only a terminal unit outcome reaches the parent's keyed result view.
        result.pop("unit_results", None)
        return result

    def recover(state):
        phase = state.get("unit_stage")
        action = "finalize_unit"
        if state.get("status") == Status.HALTED:
            if phase == "implement" and gm._can_continue_implement(state):
                action = "continue_implement"
        elif phase == "test_gate" and not (state.get("test_results") or {}).get("passed"):
            if gm._can_fix_retry(state):
                action = "fix_retry"
            elif gm._will_escalate(state) and gm._within_budget(state):
                action = "escalate"
            else:
                return {
                    "recovery_action": action,
                    "status": Status.HALTED,
                    "unit_failed_stage": phase,
                }
        elif phase == "review" and not state.get("review_clean"):
            receipt = state.get("review_receipt")
            if receipt is None or receipt.status != "disabled":
                action = (
                    "prepare_review_revision" if gm._can_review_revise(state) else "finalize_review"
                )
        return {"recovery_action": action}

    def finalize(state):
        unit = state.get("selected_unit")
        unit_id = unit.id if unit else "__none__"
        path = state.get("worktree_path")
        human = bool(state.get("unit_human"))
        ok = state.get("status") != Status.HALTED and (
            human or bool((state.get("test_results") or {}).get("passed"))
        )
        impl = state.get("implementation") or {}
        files, summary, head = (
            list(impl.get("files_touched") or []),
            impl.get("diff_summary", ""),
            None,
        )
        if path and (state.get("run_context") or (Path(path) / ".git").exists()):
            head = gm._git_run(path, "rev-parse", "HEAD").stdout.strip()
            base = state.get("unit_base_commit")
            if ok and not human and base:
                files = policy_for_state(state, path).audit(base)
                summary = gm._git_run(path, "diff", "--stat", f"{base}..HEAD").stdout
        result = {
            "unit_id": unit_id,
            "failed_stage": state.get("unit_failed_stage") if not ok else None,
            "title": unit.title if unit else "",
            "ok": ok,
            "human": human,
            "level": state.get("level_cursor", 0),
            "clone_path": path,
            "branch": state.get("branch"),
            "base_commit": state.get("unit_base_commit"),
            "source_commit": head,
            "files_touched": files,
            "diff_summary": summary,
            "test_command": (state.get("test_results") or {}).get("command", ""),
            "gate_receipt": state.get("gate_receipt"),
            "review_clean": state.get("review_clean", False),
            "review_receipt": state.get("review_receipt"),
            "unit_namespace": state.get("unit_namespace"),
            "attempt_id": attempt_identity(state),
            "attempt_number": state.get("attempt_number", 1),
            "model_tier": state.get("model_tier", "initial"),
            "fix_attempts": state.get("fix_attempts", 0),
            "implement_continuations": state.get("implement_continuations", 0),
            "review_revisions": state.get("review_revisions", 0),
            "error": "; ".join(row["message"] for row in state.get("errors") or [])
            if not ok
            else "",
        }
        if state.get("recovery_generation"):
            result["recovery_generation"] = state.get("recovery_generation", 0)
            result["recovery_amendment_id"] = state.get("recovery_amendment_id")
        if previous := state.get("recovery_previous_outcome"):
            result["recovery_history"] = [*outcome_history(previous), outcome_record(previous)]
        return {"unit_outcome": result, "status": Status.TESTING if ok else Status.HALTED}

    g = StateGraph(BlacksmithState)
    g.add_node("prepare_attempt", wrap("prepare_attempt", prepare))
    g.add_node("implement", wrap("implement", model))
    g.add_node("audit_commit", wrap("audit_commit", audit_and_commit))
    g.add_node("auto_fix", wrap("auto_fix", lambda s: gm.auto_fix(s, fix=deps.fix)))
    g.add_node("normalize_candidate", wrap("normalize_candidate", normalize_parallel_candidate))
    g.add_node("test_gate", wrap("test_gate", gate))
    g.add_node(
        "review",
        wrap(
            "review",
            lambda s: gm.review_node(s, executor=deps.executor, index_config=deps.index_config),
        ),
    )
    # Keep the preceding stage available to the single recovery decision.
    g.add_node("recovery_decision", recover)
    g.add_node("continue_implement", wrap("continue_implement", gm.prepare_implement_continuation))
    g.add_node("fix_retry", wrap("fix_retry", lambda s: gm.prepare_fix_retry(s, sbfl=deps.sbfl)))
    g.add_node("escalate", wrap("escalate", gm.prepare_escalation))
    g.add_node(
        "prepare_review_revision", wrap("prepare_review_revision", gm.prepare_review_revision)
    )
    g.add_node("finalize_review", wrap("finalize_review", gm.finalize_review))
    g.add_node("finalize_unit", wrap("finalize_unit", finalize))
    entries = ("prepare_attempt", "implement", "audit_commit", "auto_fix",
               "normalize_candidate", "test_gate", "review", "recovery_decision",
               "continue_implement", "fix_retry", "escalate", "prepare_review_revision")
    g.add_conditional_edges(START, lambda s: s.get("recovery_entry") or "prepare_attempt",
                            {name: name for name in entries})
    g.add_conditional_edges(
        "prepare_attempt", lambda s: "finalize_unit" if (
            s.get("unit_human") or s.get("status") == Status.HALTED) else "implement"
    )
    g.add_conditional_edges(
        "implement",
        lambda s: "recovery_decision" if s.get("status") == Status.HALTED else "audit_commit",
    )
    g.add_conditional_edges(
        "audit_commit",
        lambda s: "recovery_decision" if s.get("status") == Status.HALTED else "auto_fix",
    )
    g.add_conditional_edges(
        "auto_fix",
        lambda s: (
            "recovery_decision" if s.get("status") == Status.HALTED else "normalize_candidate"
        ),
    )
    g.add_edge("normalize_candidate", "test_gate")
    g.add_conditional_edges(
        "test_gate",
        lambda s: (
            "review"
            if s.get("status") != Status.HALTED
            and (s.get("test_results") or {}).get("passed")
            else "recovery_decision"
        ),
    )
    g.add_edge("review", "recovery_decision")
    g.add_conditional_edges("recovery_decision", lambda s: s["recovery_action"])
    for name in ("continue_implement", "fix_retry", "escalate", "prepare_review_revision"):
        g.add_edge(name, "prepare_attempt")
    g.add_edge("finalize_review", "finalize_unit")
    g.add_edge("finalize_unit", END)
    return g.compile()


def normalize_parallel_candidate(state):
    """Preserve the single-commit join API, before authoritative gate/review evidence."""
    if state.get("unit_mode") != "parallel":
        return {}
    from blacksmith import graph as gm

    path, base = state["worktree_path"], state.get("unit_base_commit")
    if not base:
        return {}
    context = call_context_for(state, unit_id=state["selected_unit"].id)
    journal = context.journal() if context else None
    operation = context.key("git.squash") if context else None
    existing = journal.get(operation) if journal else None
    staged_resume = False
    if existing:
        before, tree, dirty = candidate_identity(path)
        if existing.status == "completed":
            if (dirty or existing.outcome["candidate_commit"] != before
                    or existing.outcome["candidate_tree"] != tree):
                raise ReceiptError("squash outcome does not match candidate")
            return {"implementation": {**state["implementation"], "candidate_commit": before}}
        intended = existing.intent
        if intended["base_commit"] != base:
            raise ReceiptError("squash intent differs from recorded unit base")
        expected = intended["candidate_tree"]
        if (not dirty and tree == expected and before != intended["before_commit"]
                and gm._git_run(path, "rev-parse", "HEAD^").stdout.strip() == base
                and operation.trailer in gm._git_run(
                    path, "log", "-1", "--format=%B").stdout.splitlines()):
            outcome = {"candidate_commit": before, "candidate_tree": tree}
            proof = context.key("git.squash.reconciliation")
            journal.intent(proof, {"original_operation": operation.id, **outcome})
            journal.finish(proof, "completed", outcome)
            if existing.status == "intent":
                journal.finish(operation, "completed", outcome)
            return {"implementation": {**state["implementation"], "candidate_commit": before}}
        if existing.status != "intent":
            raise ReceiptError("failed squash requires explicit generation recovery")
        if before == base:
            # A lost process after soft reset may leave exactly the intended tree staged.
            if (gm._git_run(path, "write-tree").stdout.strip() != expected
                    or gm._git_run(path, "ls-files", "--others", "--exclude-standard").stdout):
                raise ReceiptError("unknown staged changes during squash")
            gm._git_run(path, "diff", "--quiet")
            staged_resume = True
        elif before != intended["before_commit"] or dirty or tree != expected:
            raise ReceiptError("unknown candidate during squash reconciliation")
        tree = expected
    else:
        count = gm._git_run(path, "rev-list", "--count", f"{base}..HEAD").stdout.strip()
        if int(count) <= 1:
            return {}
        before, tree, dirty = candidate_identity(path)
        if dirty:
            raise ValueError("cannot normalize a dirty unit candidate")
        if journal:
            journal.intent(operation, {
                "before_commit": before, "base_commit": base, "candidate_tree": tree})
    message = conventional_commit_message(state["selected_unit"])
    if operation:
        message += "\n\n" + operation.trailer
    if not staged_resume:
        gm._git_run(path, "reset", "--soft", base)
    gm._git_run(path, "commit", "-m", message)
    head, after_tree, dirty = candidate_identity(path)
    policy_for_state(state, path).audit(base)
    if dirty or tree != after_tree:
        raise ReceiptError("normalized candidate tree changed unexpectedly")
    if journal:
        journal.finish(operation, "completed", {"candidate_commit": head, "candidate_tree": tree})
    return {"implementation": {**state["implementation"], "candidate_commit": head}}


def unit_runner(unit_graph):
    """Project parent input/output; only keyed outcomes and ledgers cross fan-out."""

    def execute(state):
        selected = state.get("selected_unit")
        recovered = (state.get("recovery_units") or {}).get(selected.id if selected else "")
        parallel = (
            recovered.get("unit_mode") == "parallel"
            if recovered
            else bool(state.get("shared_clone_path"))
        )
        keep = {
            "prd",
            "state_version",
            "recovery_version",
            "run_id",
            "run_context",
            "selected_unit",
            "selected_plan",
            "plans",
            "plans_by_unit_id",
            "planning_version",
            "planning_prd_sha256",
            "plan_cursor",
            "planning_complete",
            "plan_approval",
            "approved_plans_sha256",
            "approvals",
            "unit_results",
            "planning_path",
            "worktree_path",
            "branch",
            "limits",
            "review_enabled",
            "review_version",
            "review_panel_size",
            "shared_clone_path",
            "level_cursor",
            "recovery_call_overrides",
        }
        seed = {key: state[key] for key in keep if key in state}
        seed.update({key: state[key] for key in UnitAttemptState.__annotations__ if key in state})
        seed.update(
            unit_mode="parallel" if parallel else "sequential",
            unit_initialized=False,
            cost_events=[],
            errors=[],
            review_findings=[],
            unresolved_review_findings=[],
            dependency_results=state.get("unit_results") or [],
            unit_results=[],
            unit_outcomes={},
            review_revisions_total=0,
            run_cost_base=float(state.get("level_cost_base") or 0)
            if parallel
            else sum(float(e.get("cost_usd") or 0) for e in state.get("cost_events") or []),
        )
        if recovered:
            seed = {**seed, **recovered, "limits": state["limits"],
                    "unit_initialized": recovered.get("unit_initialized", True)}
            # Include all run spend once, excluding the unit events already in this seed.
            known = {e.get("event_id") for e in seed.get("cost_events", [])}
            seed["run_cost_base"] = sum(
                float(e.get("cost_usd") or 0)
                for e in state.get("cost_events", [])
                if e.get("event_id") not in known
            )
        # Sync durability commits an attempt/cost checkpoint before the next effect.
        try:
            parent_writer = get_stream_writer()
            parent_config = get_config()
            inherited_checkpointer = (
                parent_config.get("configurable", {}).get(CONFIG_KEY_CHECKPOINTER)
            )
        except RuntimeError:
            parent_writer = None  # Standalone legacy helper, outside a parent task.
            inherited_checkpointer = None
            parent_config = {}
        token = _parent_writer.set(parent_writer)
        try:
            checkpointer = unit_graph.checkpointer
            if checkpointer is None:
                checkpointer = inherited_checkpointer
            from blacksmith.observability import parent_task_scope
            from blacksmith.scheduling import invocation_config
            parent_task = parent_config.get("configurable", {}).get("__pregel_task_id")
            with parent_task_scope(parent_task, parent_writer):
                result = unit_graph.invoke(
                    seed, config=invocation_config(seed, fallback_limits=
                        parent_config.get("configurable", {}).get("blacksmith_limits")),
                    durability="sync" if checkpointer else None,
                )
        finally:
            _parent_writer.reset(token)
        outcome = result["unit_outcome"]
        unit_id = selected.id if selected else "__none__"
        previous = (state.get("unit_outcomes") or {}).get(unit_id) or {}
        # A recovered child returns its cumulative count. Only the new delta belongs
        # in the parent's additive legacy display channel; keyed outcomes stay exact.
        revision_delta = max(0, outcome["review_revisions"]
                             - previous.get("review_revisions", 0))
        output = {
            "unit_outcomes": {unit_id: outcome},
            "cost_events": result.get("cost_events") or [],
            "errors": result.get("errors") or [],
            "review_findings": result.get("review_findings") or [],
            "unresolved_review_findings": result.get("unresolved_review_findings") or [],
            "review_revisions_total": revision_delta,
        }
        if recovered:
            output["recovery_queue"] = state["recovery_queue"][1:]
            output["status"] = Status.TESTING if outcome["ok"] else Status.HALTED
        if not parallel:
            output.update(
                {key: result[key] for key in UnitAttemptState.__annotations__ if key in result}
            )
            if outcome["ok"] and not outcome["human"]:
                output["unit_results"] = [_result_view(outcome)]
        return output

    return execute


def _result_view(outcome):
    return {
        key: outcome[key]
        for key in (
            "unit_id",
            "title",
            "files_touched",
            "diff_summary",
            "test_command",
            "gate_receipt",
            "review_receipt",
        )
    }
