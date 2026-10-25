"""Implement node — the executor writes the unit's code in the worktree (PRD §4 node 5).

The untouchables (PRD §7) are enforced two ways, per the chosen design:
1. **Constitution** — the untouchables are in the executor's system prompt.
2. **Hard pre-edit guard** — a ``can_use_tool`` callback BLOCKS any file write/edit
   whose target matches a protected path and records the attempt, so the agent
   literally cannot touch an untouchable path (the #1 failure to prevent, §7 / AC-7).
   Bash is disallowed for the implementer in v0, so the guard only needs to cover
   direct file writes; the behavioral untouchables ("no AI/cloud/subscription code",
   brand aesthetics) ride the constitution + the human PR gate.

After the agent runs, the node stages, captures, and commits the worktree diff into
``state["implementation"]`` so the PR node has something to push. Without an executor
wired (skeleton/tests), it is a status-only pass-through.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from blacksmith.commands import run_command
from blacksmith.config import IndexConfig
from blacksmith.context import render_context, requirements_prd
from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.index import (
    INDEX_MCP_SERVER_NAME,
    QUALIFIED_INDEX_TOOL_NAMES,
    build_repo_map,
    create_index_mcp_server,
    index_tools_prompt_section,
)
from blacksmith.nodes.plan import cost_event, usage_breakdown
from blacksmith.path_policy import (
    DEFAULT_PROTECTED_GLOBS,
    READ_TOOLS,
    WRITE_TARGETS,
    PathPolicy,
    PolicyViolation,
    policy_for_state,
)
from blacksmith.path_policy import (
    is_protected as is_protected,  # re-export the legacy helper
)
from blacksmith.plan_artifacts import PlanIntegrityError, verify_approved_plan
from blacksmith.receipts import CallContext, ReceiptError, call_context_for
from blacksmith.sandbox import SandboxManager
from blacksmith.state import BlacksmithState, Status

# Tools whose target file the guard gates. Bash is disallowed for the implementer.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_ALLOWED_TOOLS = ["Read", "Glob", "Grep"]  # auto-allowed read-only tools
# The implementer writes code; it must not reach for a shell or escape its tool surface.
# The guard allows only known file tools and explicitly wired MCP tools. Tools that
# could run commands or spawn a shell-capable helper are also denied here explicitly: Bash (+ its
# helpers), sub-agent spawning (Agent/Task), tool discovery (ToolSearch — it was observed
# searching for a Bash tool), and web tools. A run that exhausts its turns hunting for a way
# to run tests is exactly the failure this prevents — the test gate runs the suite, not the agent.
_DISALLOWED_TOOLS = [
    "Bash",
    "BashOutput",
    "KillShell",
    "Agent",
    "Task",
    "ToolSearch",
    "WebSearch",
    "WebFetch",
]
_IMPLEMENT_MAX_TURNS = 40

# S17: sandbox execution is disabled pending workspace ownership and real Docker
# timeout/restart evidence. Keep the injection signature for disabled callers;
# enabled managers reject before model/native work and never grant a tool.
_DEFAULT_SANDBOX_EXEC_TIMEOUT_S = 120

# Index tools (WU-SEARCH-TOOL / WU-STRUCT-WIRE): ADDITIVE and off by default, mirroring the
# sandbox tool wiring above. Only when an ``IndexConfig`` with ``enabled=True`` is passed in
# is the implementer wired the index MCP server (WU-CODE-INDEX, ``blacksmith.index``),
# read-only over the run's worktree, and granted its tools + told about them. With no
# ``index_config`` (or ``enabled=False``, the default) none of this is wired in and the tool
# surface + prompt are byte-for-byte unchanged -- raw Read/Glob/Grep stay available either way.
#
# The server name, the qualified tool-name list, and the advertisement text all come from
# ``blacksmith.index`` (single source of truth) so the plan tier and this tier can't drift.

# Conventional-Commits header so the unit's commit is never rejected by a target repo's
# commit-msg hook (commitlint / config-conventional) AFTER its expensive implementation
# already ran — the failure that discarded whole runs. The header is valid by construction
# for config-conventional (and a repo with no hook accepts it fine, so no detection needed):
#   <type>(<scope>): <subject>
# type is lower-case and in the default enum; scope is the lower-cased unit id (the
# scope-case rule requires lower-case); the subject's first letter is lower-cased so the
# whole subject is never classified sentence/start/pascal/upper-case (subject-case), any
# trailing period is stripped (subject-full-stop), and the header is truncated to the
# 100-char header-max-length default.
_COMMIT_TYPE = "feat"
_HEADER_MAX_LEN = 100
# Cap the gate output fed back into a retry prompt: the tail holds the actual failures, and
# an unbounded test log would balloon the retry's input tokens (the cost this feature saves).
_FEEDBACK_TAIL_CHARS = 3000


def conventional_commit_message(unit: WorkUnit, *, commit_type: str = _COMMIT_TYPE) -> str:
    """Build a Conventional-Commits header for a unit's commit (see _COMMIT_TYPE notes)."""
    scope = unit.id.lower()
    subject = unit.title.strip().rstrip(".").strip()
    subject = (subject[0].lower() + subject[1:]) if subject else f"implement {scope}"
    header = f"{commit_type}({scope}): {subject}"
    return header[:_HEADER_MAX_LEN].rstrip() if len(header) > _HEADER_MAX_LEN else header


# The target repo's CLAUDE.md (if committed) is injected into the implementer's system
# prompt as project context, so a repo with no claude.ai Project still carries its own
# conventions into the run. See _read_project_context for why we read it ourselves
# rather than enabling the SDK's setting_sources loader.
_PROJECT_CONTEXT_FILE = "CLAUDE.md"

# Repo map injection (WU-REPO-MAP-INJECT): ADDITIVE and OFF by default. Only when an
# ``IndexConfig`` with ``enabled=True`` is passed in does the implement node build the
# repo map (WU-CODE-INDEX, ``blacksmith.index.build_repo_map``) from the worktree and
# inject it into the implementer's SYSTEM prompt as static, clearly-labelled context --
# so it rides the prompt cache and gives the agent the codebase layout up front, cutting
# blind Read/Glob/Grep exploration. Bounded by ``[index].max_map_bytes``. With no
# ``index_config`` (or ``enabled=False``, the default), none of this runs and the system
# prompt is byte-for-byte unchanged from before this unit.


def make_pre_edit_guard(
    protected_globs: Sequence[str] = DEFAULT_PROTECTED_GLOBS,
    *,
    worktree_root: str | Path | None = None,
    allowed_mcp_tools: Sequence[str] = (),
):
    """Gate every write target; permit only known reads and explicitly wired MCP tools."""
    blocked: list[dict] = []
    policy = PathPolicy(Path(worktree_root or Path.cwd()), tuple(protected_globs))
    reads = READ_TOOLS | frozenset(allowed_mcp_tools)

    async def can_use_tool(tool_name, tool_input, context):
        if tool_name in reads:
            return PermissionResultAllow()
        fields = WRITE_TARGETS.get(tool_name)
        path = None
        if fields is None:
            reason = "unknown_tool"
        elif not isinstance(tool_input, dict):
            reason = "missing_or_malformed_target"
        else:
            values = [tool_input[f] for f in fields if f in tool_input]
            path = values[0] if len(values) == 1 else None
            reason = policy.violation(path)
        if reason:
            blocked.append({"tool": tool_name, "path": path, "reason": reason})
            return PermissionResultDeny(message=f"BLOCKED: {reason}: {path!r}")
        return PermissionResultAllow()

    can_use_tool.blocked = blocked
    return can_use_tool


def _within_worktree(root: Path, path: str) -> bool:
    """Compatibility helper; relative candidates also resolve against the clone root."""
    try:
        candidate = Path(path)
        (candidate if candidate.is_absolute() else root / candidate).resolve().relative_to(
            root.resolve()
        )
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def implement(
    state: BlacksmithState,
    *,
    executor: Executor | None = None,
    sandbox: SandboxManager | None = None,
    sandbox_exec_timeout_s: int = _DEFAULT_SANDBOX_EXEC_TIMEOUT_S,
    index_config: IndexConfig | None = None,
    defer_commit: bool = False,
) -> dict:
    from blacksmith.config import reject_enabled_sandbox
    reject_enabled_sandbox(sandbox is not None and sandbox.config.enabled)
    if executor is None:
        return {"status": Status.IMPLEMENTING}  # skeleton pass-through

    prd = requirements_prd(state)
    unit = state.get("selected_unit")
    worktree_path = state.get("worktree_path")
    if prd is None or unit is None or not worktree_path:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "implement", "message": "missing prd/selected_unit/worktree_path"}],
        }

    try:
        verify_approved_plan(state, unit)
    except PlanIntegrityError as exc:
        return {"status": Status.HALTED, "implement_error_kind": "plan_integrity",
                "errors": [{"node": "implement", "message": str(exc)}]}

    # A human-gated unit is verified by a human on the draft PR — not by the executor and not
    # by the automated gate (PRD §4 human-gated routing). It has nothing to implement: a
    # manual-QA unit produces no diff, and running the model here would be wasteful and could
    # re-edit the prior units' already-committed work. So skip the executor entirely and emit
    # an empty implementation; ``route_after_implement`` then sends this unit to
    # ``open_draft_pr``, which packages the prior auto units' commits already on the shared
    # branch. This mirrors the plan node, which likewise skips human-gated units (no plan).
    # Without this, a QA-only unit hit the empty-diff halt below and tore down the whole run.
    if prd.contract.gate_for(unit) == "human":
        return {
            "implementation": {
                "files_touched": [],
                "diff_summary": "",
                "blocked": [],
                "cost_usd": 0.0,
                "usage": {},
            },
            "status": Status.IMPLEMENTING,
        }

    try:
        policy = policy_for_state(state)
    except PolicyViolation as exc:
        return {
            "status": Status.HALTED,
            "implement_error_kind": "policy_violation",
            "errors": [{"node": "implement", "message": str(exc)}],
        }
    # Escalation (WU-ESCALATE-ON-FAIL): the first attempt runs the cheaper first-attempt
    # model (config.models.implement, default Sonnet). After a gate failure the run resets the
    # worktree and re-enters here with ``escalated`` set, so this attempt instead uses the
    # stronger model (config.models.implement_escalate). Capability is detected on the
    # executor — a plain test double without ``run_implement_escalate`` simply never escalates,
    # which preserves the prior "a gate failure halts" behaviour for those tests.
    escalating = bool(state.get("escalated"))
    can_escalate = hasattr(executor, "run_implement_escalate")
    # HEAD just before this unit's commit — the reset target a gate failure rewinds to, so the
    # failed attempt is discarded without touching prior units' committed work.
    pre_implement_ref = _git(worktree_path, "rev-parse", "HEAD").strip()
    run_attempt = executor.run_implement_escalate if escalating else executor.run_implement
    # The turn budget is configurable ([limits].max_implement_turns) so a large unit can be
    # given more room; falls back to the default when the graph is wired without limits.
    max_turns = int((state.get("limits") or {}).get("max_implement_turns") or _IMPLEMENT_MAX_TURNS)
    # A continuation (resume_partial_implement) keeps the prior capped attempt's partial work in
    # the worktree and asks the agent to FINISH it, rather than re-implementing from scratch.
    resuming = bool(state.get("resume_partial_implement"))
    sandbox_enabled = False  # S17: no sandbox tool is advertised until ownership is hardened.
    # Index tools (WU-SEARCH-TOOL / WU-STRUCT-WIRE): same additive gating as the sandbox tool
    # above -- only an explicitly enabled IndexConfig wires the server, grants the tools, and
    # advertises them. The grant, the server, and the prompt advertisement all key off this one
    # ``index_enabled`` flag so they can never diverge.
    index_enabled = bool(index_config is not None and index_config.enabled)
    allowed_tools = list(_ALLOWED_TOOLS)
    if index_enabled:
        allowed_tools.extend(QUALIFIED_INDEX_TOOL_NAMES)
    guard = make_pre_edit_guard(
        policy.rules,
        worktree_root=worktree_path,
        allowed_mcp_tools=[t for t in allowed_tools if t.startswith("mcp__")],
    )
    repo_map = _build_repo_map(worktree_path, index_config)
    call_kwargs: dict = {
        "system_prompt": _system_prompt(
            prd.contract,
            _read_project_context(worktree_path),
            repo_map,
            worktree_path,
            index_enabled=index_enabled,
        ),
        "cwd": worktree_path,
        # Read tools auto-approve; Write/Edit are NOT auto-approved, so under the
        # "default" permission mode they evaluate to "ask" and route through the
        # can_use_tool guard (which allows non-protected paths, blocks untouchables).
        "allowed_tools": allowed_tools,
        "tools": ["Read", "Glob", "Grep", "Write", "Edit", "NotebookEdit"],
        # Raw Bash (and every other shell-capable/escape tool) stays disallowed
        # UNCHANGED, sandboxed or not -- run_command is the only execution channel.
        "disallowed_tools": _DISALLOWED_TOOLS,
        "permission_mode": "default",
        "can_use_tool": guard,
        "max_turns": max_turns,
        "raise_on_error": False,  # surface failures into state, don't crash the graph
    }
    mcp_servers: dict = {}
    if index_enabled:
        mcp_servers[INDEX_MCP_SERVER_NAME] = create_index_mcp_server(
            worktree_path, exclude=index_config.exclude
        )
    if mcp_servers:
        call_kwargs["mcp_servers"] = mcp_servers
    call_context = call_context_for(state, unit_id=unit.id)
    if call_context is not None:
        call_kwargs["call_context"] = call_context
    result = run_attempt(
        _implement_prompt(
            unit,
            prior_failure=state.get("last_gate_output"),
            review_feedback=state.get("review_feedback"),
            resuming=resuming,
            continuation_feedback=state.get("continuation_feedback"),
            sandbox_enabled=sandbox_enabled,
            index_enabled=index_enabled,
            worktree_path=worktree_path,
        )
        + render_context(state, unit, include_plan=True),
        **call_kwargs,
    )
    # Ledger event for THIS attempt (WU-COST-EVENTS), built once and emitted on EVERY return
    # below — including the failure halts — so a run that halts inside implement still counts
    # this attempt's spend and the unit still appears in the per-unit metrics.
    event = cost_event("implement", unit.id, result)
    # Denial is terminal even if the model subsequently wrote unrelated valid files
    # or exhausted its turns. Audit before staging so disallowed changes stay recoverable.
    try:
        if guard.blocked:
            raise PolicyViolation(f"policy_violation: denied edit attempts: {guard.blocked}")
        policy.audit(pre_implement_ref)
    except PolicyViolation as exc:
        return {
            "status": Status.HALTED,
            "implement_error_kind": "policy_violation",
            "cost_events": [event],
            "implementation": {"blocked": list(guard.blocked)},
            "errors": [{"node": "implement", "message": str(exc)}],
        }
    if result.is_error:
        # A turn-cap is a RECOVERABLE, budget-shaped failure: report it legibly (never the
        # agent's reasoning dump) and tag ``implement_error_kind`` so routing can continue the
        # partial work instead of discarding the run. Any other error keeps the raw text.
        kind = result.error_kind or "other"
        if kind == "max_turns":
            message = (
                f"implement exceeded its turn budget ({max_turns} turns) on {unit.id} "
                f"without finishing (session {result.session_id or 'unknown'})"
            )
        else:
            message = f"implement call failed: {result.text}"
        return {
            "status": Status.HALTED,
            "cost_events": [event],
            "implement_error_kind": kind,
            "errors": [{"node": "implement", "message": message}],
        }

    pending = {
        "base_commit": pre_implement_ref,
        "can_escalate": can_escalate,
        "escalating": escalating,
        "blocked": list(guard.blocked),
        "cost_usd": result.cost_usd,
        "usage": usage_breakdown(result.usage),
    }
    update = {
        "pending_implementation": pending,
        "attempt_base_commit": pre_implement_ref,
        "cost_events": [event],
        "status": Status.IMPLEMENTING,
    }
    if can_escalate and not escalating:
        update["pre_implement_ref"] = pre_implement_ref
    if defer_commit:
        return update
    return {**update, **audit_and_commit({**state, **update})}


def audit_and_commit(state: BlacksmithState) -> dict:
    """Commit a checkpointed successful model result without invoking the model."""
    pending = state.get("pending_implementation")
    if pending is None:
        return {"status": Status.TESTING}  # dependency-free legacy graph
    worktree_path = state["worktree_path"]
    unit = state["selected_unit"]
    pre_implement_ref = pending["base_commit"]
    try:
        policy = policy_for_state(state)
        policy.audit(pre_implement_ref)
        files, diff_summary = _stage_and_commit(
            worktree_path,
            conventional_commit_message(unit),
            call_context=call_context_for(state, unit_id=unit.id),
        )
        policy.audit(pre_implement_ref)
    except PolicyViolation as exc:
        return {
            "status": Status.HALTED,
            "implement_error_kind": "policy_violation",
            "errors": [{"node": "implement", "message": str(exc)}],
        }
    except CommitError as exc:
        return {
            "status": Status.HALTED,
            "errors": [
                {"node": "implement", "message": f"failed to commit the unit's changes: {exc}"}
            ],
        }
    if not files:
        # The agent changed nothing — the unit wasn't implemented. Halt rather than
        # gate an unchanged tree and then fail on an empty-diff PR.
        return {
            "status": Status.HALTED,
            "errors": [
                {
                    "node": "implement",
                    "message": "implement produced no file changes; nothing to gate or open a PR",
                }
            ],
        }
    update: dict = {
        "attempt_base_commit": pre_implement_ref,
        "implementation": {
            "files_touched": files,
            "diff_summary": diff_summary,
            "candidate_commit": _git(worktree_path, "rev-parse", "HEAD").strip(),
            "blocked": pending["blocked"],
            "cost_usd": pending["cost_usd"],
            "usage": pending["usage"],
        },
        # Append-only ledger event for THIS attempt (WU-COST-EVENTS). The escalation retry
        # is a separate implement invocation that reaches here too, so each attempt
        # contributes exactly one event — the multi-unit/escalation spend is no longer lost
        # to the last-write-wins ``implementation`` slice.
        "pending_implementation": None,
        "status": Status.TESTING,
    }
    # Record the pre-attempt ref so a gate failure can reset to exactly here — discarding only
    # this attempt's commit, never prior units' work — and escalate once. Only on the first
    # attempt, and only when the executor can escalate; otherwise leave it unset so a gate
    # failure routes straight to human_halt as before.
    if pending["can_escalate"] and not pending["escalating"] and pre_implement_ref:
        update["pre_implement_ref"] = pre_implement_ref
    return update


class CommitError(Exception):
    """Raised when committing the unit's staged changes fails (e.g. the worktree has no git
    author identity). Surfacing it loudly stops a silent no-op from masquerading as a
    successful implement and only failing later as an empty-diff PR."""


def _stage_and_commit(
    worktree_path: str,
    message: str,
    *,
    call_context: CallContext | None = None,
) -> tuple[list[str], str]:
    """Stage all changes, capture the staged file list + diffstat, and commit if any.

    Raises ``CommitError`` if the commit fails. Previously the commit's exit code was
    ignored, so a failed commit (e.g. a fresh clone with no author identity) looked like
    success and surfaced only at PR time as "No commits between main and <branch>"."""
    journal = call_context.journal() if call_context else None
    operation = call_context.key("git.commit") if call_context else None
    if journal is not None:
        existing = journal.get(operation)
        base = (
            existing.intent["base_commit"]
            if existing
            else _git(worktree_path, "rev-parse", "HEAD").strip()
        )
        receipt = journal.intent(
            operation,
            {
                "path": str(Path(worktree_path).resolve()),
                "message": message,
                "base_commit": base,
            },
        )
        if not receipt.created:
            if receipt.status == "completed":
                head = _git(worktree_path, "rev-parse", "HEAD").strip()
                dirty = _git(worktree_path, "status", "--porcelain", "--untracked-files=all")
                commit_message = _git(worktree_path, "log", "-1", "--format=%B")
                if (
                    head != receipt.outcome["commit"]
                    or dirty.strip()
                    or operation.trailer not in commit_message
                ):
                    raise ReceiptError(
                        "committed operation has unknown extra changes; inspect workspace"
                    )
                return receipt.outcome["files"], receipt.outcome["diff_summary"]
            tree_key = call_context.key("git.commit.tree")
            tree_record = journal.get(tree_key)
            head = _git(worktree_path, "rev-parse", "HEAD").strip()
            dirty = _git(worktree_path, "status", "--porcelain", "--untracked-files=all")
            if tree_record and head != base and not dirty.strip():
                tree = _git(worktree_path, "rev-parse", "HEAD^{tree}").strip()
                parent = _git(worktree_path, "rev-parse", "HEAD^").strip()
                body = _git(worktree_path, "log", "-1", "--format=%B").splitlines()
                expected_tree = (
                    tree_record.outcome["observed_tree"] if tree_record.status == "completed"
                    else tree_record.intent["tree"]
                )
                if (tree == expected_tree and parent == base and operation.trailer in body):
                    outcome = {"commit": head, "files": tree_record.intent["files"],
                               "diff_summary": tree_record.intent["diff_summary"]}
                    proof_key = call_context.key("git.commit.reconciliation")
                    journal.intent(proof_key, {"original_operation": operation.id,
                                               "parent": base, "tree": tree, **outcome})
                    journal.finish(proof_key, "completed", outcome)
                    journal.finish(tree_key, "completed", {"commit": head, "observed_tree": tree})
                    if receipt.status == "intent":
                        journal.finish(operation, "completed", outcome)
                    # An original failed/interrupted receipt remains immutable. The
                    # separate proof records the observed Git effect, not a replay.
                    return outcome["files"], outcome["diff_summary"]
            if receipt.status == "intent" and tree_record and head == base:
                expected = tree_record.intent["tree"]
                staged = _git(worktree_path, "write-tree").strip()
                untracked = _git(worktree_path, "ls-files", "--others", "--exclude-standard")
                if staged != expected or untracked.strip():
                    raise ReceiptError("unknown staged changes before implementation commit")
                _git(worktree_path, "diff", "--quiet")
                # Exactly the original sealed tree is staged; finish that commit once.
            else:
                raise ReceiptError(f"interrupted_commit_requires_reconciliation: {operation.id}")
        message += "\n\n" + operation.trailer
    def before_commit(files, summary, tree):
        if journal:
            journal.intent(call_context.key("git.commit.tree"), {
                "tree": tree, "base_commit": base, "files": files, "diff_summary": summary,
            })

    try:
        files, summary = _commit_changes(worktree_path, message, before_commit=before_commit)
    except BaseException as exc:
        if journal is not None:
            journal.finish(
                operation,
                "failed" if isinstance(exc, CommitError) else "interrupted",
                {"error_type": type(exc).__name__},
            )
        raise
    if journal is not None:
        if not files:
            journal.finish(operation, "failed", {"error_kind": "empty_diff"})
            return files, summary
        head = _git(worktree_path, "rev-parse", "HEAD").strip()
        journal.finish(call_context.key("git.commit.tree"), "completed", {
            "commit": head,
            "observed_tree": _git(worktree_path, "rev-parse", "HEAD^{tree}").strip(),
        })
        journal.finish(
            operation,
            "completed",
            {
                "commit": head,
                "files": files,
                "diff_summary": summary,
            },
        )
    return files, summary


def _commit_changes(
    worktree_path: str, message: str, *, before_commit=None,
) -> tuple[list[str], str]:
    # All staging/diff failures are infrastructure failures, not an empty diff.
    _git(worktree_path, "add", "-A")
    files = [
        line for line in _git(worktree_path, "diff", "--cached", "--name-only").splitlines() if line
    ]
    diff_summary = _git(worktree_path, "diff", "--cached", "--stat")
    if files:
        if before_commit:
            before_commit(files, diff_summary, _git(worktree_path, "write-tree").strip())
        commit = run_command(
            ["git", "-C", str(worktree_path), "commit", "-m", message],
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
    return files, diff_summary


def _git(worktree_path: str, *args: str) -> str:
    result = run_command(["git", "-C", str(worktree_path), *args], capture_output=True, text=True)
    result.require_complete_output()
    if result.returncode:
        raise CommitError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout


# The default (no sandbox) execution note -- kept as its own constant, character-for-
# character identical to the original inline text, so the disabled path is byte-for-byte
# unchanged from before WU-SANDBOX-IMPLEMENT.
_NO_SANDBOX_EXECUTION_NOTE = (
    "You have NO shell and CANNOT run commands, tests, builds, or programs, and must not "
    "spawn sub-agents or search for tools to do so. Do NOT try to run the tests, the build, "
    "or the code to verify your work — an automated test gate runs the project's own "
    "test/lint commands after you finish. Make the code correct by reading and reasoning, "
    "and write any tests the unit requires, but never execute anything. Spend every turn "
    "implementing — do not waste turns looking for a way to run commands."
)


def _implement_prompt(
    unit: WorkUnit,
    *,
    prior_failure: str | None = None,
    review_feedback: str | None = None,
    resuming: bool = False,
    continuation_feedback: str | None = None,
    sandbox_enabled: bool = False,
    index_enabled: bool = False,
    worktree_path: str | Path | None = None,
) -> str:
    from blacksmith.config import reject_enabled_sandbox
    reject_enabled_sandbox(sandbox_enabled)
    execution_note = _NO_SANDBOX_EXECUTION_NOTE
    # When the index is enabled, the system prompt tells the agent this worktree's absolute
    # path and requires every Read/Edit to use an absolute path under it (killing observed
    # failed relative-path Read turns). This scope note MUST agree with that — an agent told
    # "never absolute" here and "always absolute" there gets contradictory instructions and
    # the absolute-path fix is defeated. With the index disabled, this stays the original
    # relative-path instruction, byte-for-byte.
    if index_enabled and worktree_path:
        worktree_abs = str(Path(worktree_path).resolve())
        scope_note = (
            f"Work ONLY within this worktree: {worktree_abs}\n"
            "Every Read and Edit (and Write/MultiEdit/NotebookEdit) tool call MUST use an "
            "absolute path under that worktree path — never a relative path. Never edit a "
            "file outside this worktree — it is an isolated clone, not the canonical repo."
        )
    else:
        scope_note = (
            "Work ONLY within the current working directory, using paths relative to it. Never "
            "edit a file by an absolute path or outside this directory — it is an isolated "
            "clone, not the canonical repo."
        )
    prompt = (
        "Implement this work unit fully inside the current working directory by creating or "
        "editing the target modules. Make the minimal changes needed to satisfy the test "
        "contract; do not add anything beyond what the unit asks for. Do NOT assume a target "
        "module already exists — verify with Read and create it if missing. You are done only "
        "once the target modules exist on disk with the required content.\n\n"
        f"{scope_note}\n\n"
        f"{execution_note}\n\n"
        f"Unit {unit.id}: {unit.title}\n"
        f"Layers: {', '.join(unit.layers)}\n"
        f"Target modules: {', '.join(unit.target_modules)}\n"
        f"Test contract (must be satisfied): {unit.test_contract}"
    )
    if resuming:
        # Continuation (WU turn-cap recovery): a prior attempt at THIS unit ran out of turns
        # with real partial work already saved in the working directory. Continue it — do not
        # start over — and spend the fresh budget finishing the remaining work.
        prompt += (
            "\n\nCONTINUATION: your PREVIOUS attempt at this unit ran out of turns before it "
            "finished. Your partial work is ALREADY SAVED in the working directory. First read "
            "the target modules to see what you have already done, then COMPLETE the remaining "
            "work — do NOT restart from scratch or rewrite what is already correct. Be efficient: "
            "you have a fresh turn budget but the unit must be finished within it."
        )
    if resuming and continuation_feedback:
        prompt += "\n\nCONTINUATION FEEDBACK:\n" + continuation_feedback
    if prior_failure and prior_failure.strip():
        # Self-heal retry (WU-GATE-SELF-HEAL): a prior attempt at THIS unit failed the gate.
        # Feed the gate's output back so the retry fixes the real error instead of re-running
        # blind. The honesty rule is explicit: fix the CODE, never weaken the gate — the human
        # PR review is the backstop, but a retry that games the tests defeats the gate's point.
        tail = prior_failure.strip()[-_FEEDBACK_TAIL_CHARS:]
        prompt += (
            "\n\nYOUR PREVIOUS ATTEMPT AT THIS UNIT FAILED THE TEST GATE. Read the gate output "
            "below, find the root cause, and FIX THE CODE so the project's own tests and lint "
            "pass. Do NOT delete, skip, weaken, or trivially rewrite tests/assertions to go "
            "green — fix the implementation. Gate output (tail):\n"
            f"{tail}"
        )
    if review_feedback:
        prompt += "\n\nREVIEW FEEDBACK (full reviewer request):\n" + review_feedback
    return prompt


def _read_project_context(worktree_path: str | Path) -> str | None:
    """Return the target repo's CLAUDE.md text (if committed), for use as agent context.

    blacksmith deliberately does NOT enable the SDK's ``setting_sources`` loader to pick
    this up: ``setting_sources=["project"]`` would also import the target repo's
    ``.claude/settings.json`` — its permissions (which could auto-allow ``Write`` and so
    bypass the pre-edit guard) and its hooks (arbitrary commands run from an arbitrary
    repo). Reading CLAUDE.md ourselves and injecting it as static system context gives the
    agent the repo's guidance while keeping the guard authoritative and executing nothing.
    """
    path = Path(worktree_path) / _PROJECT_CONTEXT_FILE
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _build_repo_map(worktree_path: str | Path, index_config: IndexConfig | None) -> str | None:
    """Build the repo map from the worktree if the index is enabled, else ``None``.

    ADDITIVE and off by default (see the ``index_config`` note above ``_PROJECT_CONTEXT_FILE``):
    with no ``index_config`` or ``enabled=False`` this never calls into ``blacksmith.index``
    at all, so the disabled path has zero behavioural change.
    """
    if index_config is None or not index_config.enabled:
        return None
    return (
        build_repo_map(
            worktree_path,
            max_bytes=index_config.max_map_bytes,
            exclude=index_config.exclude,
            rank_by_graph=index_config.graph_rank,
        )
        or None
    )


def _system_prompt(
    contract: PRDContract,
    project_context: str | None = None,
    repo_map: str | None = None,
    worktree_path: str | Path | None = None,
    *,
    index_enabled: bool = False,
) -> str:
    untouchables = "\n".join(f"- {item}" for item in contract.untouchables)
    prompt = (
        f"You are blacksmith's implementer for the {contract.component} project "
        f"({contract.primary_target_repo}). Implement precisely and minimally.\n\n"
        "CONSTITUTION — these are inviolable. Never create, edit, or delete anything that "
        "touches them; attempts to edit protected files are blocked and surfaced for human "
        f"sign-off:\n{untouchables}"
    )
    if project_context:
        prompt += (
            "\n\nPROJECT CONTEXT — the target repo's CLAUDE.md (this codebase's own "
            "conventions and guidance). Follow it, but the CONSTITUTION above wins on any "
            f"conflict:\n{project_context}"
        )
    # Advertise the index tools whenever the index is WIRED (index_enabled), independent of
    # whether a repo map built -- an empty/failed map must not silently un-advertise working
    # tools. The repo map, when present, is a separate block below.
    if index_enabled:
        prompt += (
            "\n\n"
            + index_tools_prompt_section()
            + " Reach for Read only for a file you are actually about to edit, or when the "
            "index cannot answer your question."
        )
        if worktree_path:
            worktree_abs = str(Path(worktree_path).resolve())
            prompt += (
                f"\n\nThis worktree's absolute path is: {worktree_abs}\n"
                "Every Read and Edit (and Write/MultiEdit/NotebookEdit) tool call MUST use an "
                "absolute path under that worktree path — never a relative path. A relative "
                "Read has been observed to fail against the wrong working directory."
            )
    if repo_map:
        prompt += (
            "\n\nREPO MAP — a structural outline of this repository (tracked files and their "
            "top-level symbols, WU-CODE-INDEX), given up front as static context so you can "
            "orient before exploring, rather than spending turns on blind Read/Glob/Grep:\n"
            f"{repo_map}"
        )
    return prompt
