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

import fnmatch
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from blacksmith.config import IndexConfig
from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.index import build_repo_map
from blacksmith.nodes.plan import cost_event, usage_breakdown
from blacksmith.sandbox import RUN_COMMAND_TOOL_NAME, SandboxManager, create_sandbox_mcp_server
from blacksmith.state import BlacksmithState, Status

# Tools whose target file the guard gates. Bash is disallowed for the implementer.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_ALLOWED_TOOLS = ["Read", "Glob", "Grep"]  # auto-allowed read-only tools
# The implementer writes code; it must not reach for a shell or escape its tool surface.
# The can_use_tool guard only gates Write/Edit and allows everything else, so any tool that
# could run commands or spawn a shell-capable helper is denied here explicitly: Bash (+ its
# helpers), sub-agent spawning (Agent/Task), tool discovery (ToolSearch — it was observed
# searching for a Bash tool), and web tools. A run that exhausts its turns hunting for a way
# to run tests is exactly the failure this prevents — the test gate runs the suite, not the agent.
_DISALLOWED_TOOLS = [
    "Bash", "BashOutput", "KillShell", "Agent", "Task", "ToolSearch", "WebSearch", "WebFetch",
]
_IMPLEMENT_MAX_TURNS = 40

# Sandbox self-verify tool (WU-SANDBOX-IMPLEMENT): ADDITIVE and off by default. When a
# started, enabled ``SandboxManager`` is passed in, the implementer is granted the
# ``run_command`` tool (WU-SANDBOX-TOOL) via an in-process MCP server bound to it -- every
# command it runs still executes ONLY inside the sandbox container over the mounted clone,
# never on the host, and raw Bash stays in ``_DISALLOWED_TOOLS`` regardless. With no sandbox
# (or ``sandbox.config.enabled`` False, the default), none of this is wired in and the call
# is byte-for-byte identical to today.
_SANDBOX_SERVER_NAME = "blacksmith-sandbox"  # must match blacksmith.sandbox's server name
_SANDBOX_TOOL_NAME = f"mcp__{_SANDBOX_SERVER_NAME}__{RUN_COMMAND_TOOL_NAME}"
# The SandboxManager this node receives carries only start/exec/stop settings (its own
# ``blacksmith.sandbox.SandboxConfig``), not the exec timeout -- that lives on blacksmith's
# own ``blacksmith.config.SandboxConfig`` ([sandbox].exec_timeout_s, default 120). Graph
# wiring may pass the configured value through explicitly; this is the fallback.
_DEFAULT_SANDBOX_EXEC_TIMEOUT_S = 120

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

# Path-like untouchables (PRD §7). Matched against the file's POSIX path with fnmatch
# (so `*` spans separators). The behavioral untouchables are NOT path-matchable.
DEFAULT_PROTECTED_GLOBS: tuple[str, ...] = (
    "*Cargo.lock",
    "*.kindling.yaml",
    "*/migrations/*",
    "*blacksmith/contract.py",
)


def is_protected(path: str, protected_globs: Sequence[str] = DEFAULT_PROTECTED_GLOBS) -> bool:
    """True if a (relative or absolute) path matches an untouchable glob."""
    posix = PurePosixPath(path).as_posix()
    return any(fnmatch.fnmatch(posix, glob) for glob in protected_globs)


def make_pre_edit_guard(
    protected_globs: Sequence[str] = DEFAULT_PROTECTED_GLOBS,
    *,
    worktree_root: str | Path | None = None,
):
    """Build a ``can_use_tool`` callback that denies dangerous writes.

    It blocks two things and records each in ``.blocked`` (so the node can surface them
    for human sign-off, AC-7): (1) writes whose path is outside ``worktree_root`` — the
    isolation boundary (§4/§5), so the agent can never edit the real checkout; and
    (2) writes to untouchable paths (§7).
    """
    blocked: list[dict] = []
    root = Path(worktree_root).resolve() if worktree_root is not None else None

    async def can_use_tool(tool_name, tool_input, context):
        if tool_name in _WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path") or ""
            if path and root is not None and not _within_worktree(root, path):
                blocked.append({"tool": tool_name, "path": path, "reason": "outside_worktree"})
                return PermissionResultDeny(
                    message=(
                        f"BLOCKED: {path} is outside the worktree ({root}). Every edit must "
                        "stay inside the worktree — use a path within it."
                    )
                )
            if path and is_protected(path, protected_globs):
                blocked.append({"tool": tool_name, "path": path, "reason": "untouchable"})
                return PermissionResultDeny(
                    message=(
                        f"BLOCKED: {path} is an untouchable path (PRD §7). "
                        "It may not be edited without explicit human sign-off."
                    )
                )
        return PermissionResultAllow()

    can_use_tool.blocked = blocked
    return can_use_tool


def _within_worktree(root: Path, path: str) -> bool:
    """A relative path resolves against the agent's cwd (the worktree); an absolute path
    must resolve to somewhere under the worktree root."""
    candidate = Path(path)
    if not candidate.is_absolute():
        return True
    try:
        candidate.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def implement(
    state: BlacksmithState,
    *,
    executor: Executor | None = None,
    sandbox: SandboxManager | None = None,
    sandbox_exec_timeout_s: int = _DEFAULT_SANDBOX_EXEC_TIMEOUT_S,
    index_config: IndexConfig | None = None,
) -> dict:
    if executor is None:
        return {"status": Status.IMPLEMENTING}  # skeleton pass-through

    prd = state.get("prd")
    unit = state.get("selected_unit")
    worktree_path = state.get("worktree_path")
    if prd is None or unit is None or not worktree_path:
        return {
            "status": Status.HALTED,
            "errors": [{"node": "implement", "message": "missing prd/selected_unit/worktree_path"}],
        }

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

    guard = make_pre_edit_guard(worktree_root=worktree_path)
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
    # Sandbox self-verify (WU-SANDBOX-IMPLEMENT): ADDITIVE and off by default. Only when an
    # enabled, started sandbox is injected does the tool surface or prompt change at all --
    # mirroring the same ``sandbox is not None and sandbox.config.enabled`` check used by the
    # run-level lifecycle wiring (WU-SANDBOX-LIFECYCLE) in graph.py.
    sandbox_enabled = bool(sandbox is not None and sandbox.config.enabled)
    allowed_tools = [*_ALLOWED_TOOLS, _SANDBOX_TOOL_NAME] if sandbox_enabled else _ALLOWED_TOOLS
    repo_map = _build_repo_map(worktree_path, index_config)
    call_kwargs: dict = {
        "system_prompt": _system_prompt(
            prd.contract, _read_project_context(worktree_path), repo_map
        ),
        "cwd": worktree_path,
        # Read tools auto-approve; Write/Edit are NOT auto-approved, so under the
        # "default" permission mode they evaluate to "ask" and route through the
        # can_use_tool guard (which allows non-protected paths, blocks untouchables).
        "allowed_tools": allowed_tools,
        # Raw Bash (and every other shell-capable/escape tool) stays disallowed
        # UNCHANGED, sandboxed or not -- run_command is the only execution channel.
        "disallowed_tools": _DISALLOWED_TOOLS,
        "permission_mode": "default",
        "can_use_tool": guard,
        "max_turns": max_turns,
        "raise_on_error": False,  # surface failures into state, don't crash the graph
    }
    if sandbox_enabled:
        call_kwargs["mcp_servers"] = {
            _SANDBOX_SERVER_NAME: create_sandbox_mcp_server(
                sandbox, exec_timeout_s=sandbox_exec_timeout_s
            )
        }
    result = run_attempt(
        _implement_prompt(
            unit,
            prior_failure=state.get("last_gate_output"),
            resuming=resuming,
            sandbox_enabled=sandbox_enabled,
        ),
        **call_kwargs,
    )
    # Ledger event for THIS attempt (WU-COST-EVENTS), built once and emitted on EVERY return
    # below — including the failure halts — so a run that halts inside implement still counts
    # this attempt's spend and the unit still appears in the per-unit metrics.
    event = cost_event("implement", unit.id, result)
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

    try:
        files, diff_summary = _stage_and_commit(
            worktree_path, conventional_commit_message(unit)
        )
    except CommitError as exc:
        return {
            "status": Status.HALTED,
            "cost_events": [event],
            "errors": [
                {"node": "implement", "message": f"failed to commit the unit's changes: {exc}"}
            ],
        }
    if not files and not guard.blocked:
        # The agent changed nothing — the unit wasn't implemented. Halt rather than
        # gate an unchanged tree and then fail on an empty-diff PR.
        return {
            "status": Status.HALTED,
            "cost_events": [event],
            "errors": [
                {"node": "implement", "message": "implement produced no file changes; "
                 "nothing to gate or open a PR"}
            ],
        }
    update: dict = {
        "implementation": {
            "files_touched": files,
            "diff_summary": diff_summary,
            "blocked": list(guard.blocked),
            "cost_usd": result.cost_usd,
            "usage": usage_breakdown(result.usage),
        },
        # Append-only ledger event for THIS attempt (WU-COST-EVENTS). The escalation retry
        # is a separate implement invocation that reaches here too, so each attempt
        # contributes exactly one event — the multi-unit/escalation spend is no longer lost
        # to the last-write-wins ``implementation`` slice.
        "cost_events": [event],
        "status": Status.TESTING,
    }
    # Record the pre-attempt ref so a gate failure can reset to exactly here — discarding only
    # this attempt's commit, never prior units' work — and escalate once. Only on the first
    # attempt, and only when the executor can escalate; otherwise leave it unset so a gate
    # failure routes straight to human_halt as before.
    if can_escalate and not escalating and pre_implement_ref:
        update["pre_implement_ref"] = pre_implement_ref
    # Only untouchable blocks (§7) are run errors that need human sign-off. Out-of-worktree
    # blocks are the isolation boundary doing its job — benign audit info, recorded under
    # implementation["blocked"] above but never surfaced as an implement error.
    untouchable = [b["path"] for b in guard.blocked if b.get("reason") == "untouchable"]
    if untouchable:
        update["errors"] = [
            {"node": "implement", "message": f"blocked untouchable edit(s): {untouchable}"}
        ]
    return update


class CommitError(Exception):
    """Raised when committing the unit's staged changes fails (e.g. the worktree has no git
    author identity). Surfacing it loudly stops a silent no-op from masquerading as a
    successful implement and only failing later as an empty-diff PR."""


def _stage_and_commit(worktree_path: str, message: str) -> tuple[list[str], str]:
    """Stage all changes, capture the staged file list + diffstat, and commit if any.

    Raises ``CommitError`` if the commit fails. Previously the commit's exit code was
    ignored, so a failed commit (e.g. a fresh clone with no author identity) looked like
    success and surfaced only at PR time as "No commits between main and <branch>"."""
    _git(worktree_path, "add", "-A")
    files = [
        line for line in _git(worktree_path, "diff", "--cached", "--name-only").splitlines() if line
    ]
    diff_summary = _git(worktree_path, "diff", "--cached", "--stat")
    if files:
        commit = subprocess.run(
            ["git", "-C", str(worktree_path), "commit", "-m", message],
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            raise CommitError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
    return files, diff_summary


def _git(worktree_path: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree_path), *args], capture_output=True, text=True
    )
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

# The sandboxed execution note (WU-SANDBOX-IMPLEMENT) REVERSES the instruction above: with a
# real self-verify channel available, the agent is told to use it and fix failures itself
# BEFORE finishing, rather than leaving all verification to the (still authoritative) gate.
_SANDBOX_EXECUTION_NOTE = (
    "You HAVE a sandbox: call the `run_command` tool to run commands INSIDE an isolated "
    "container over this working directory — never on the host, and never via a shell of "
    "your own (raw Bash stays disallowed; `run_command` is the only execution channel, and "
    "it is sandboxed). Before you finish, RUN the target project's own tests and build/lint "
    "commands in the sandbox and FIX any failures — do not finish while a test or the build "
    "is still failing. The test gate still runs afterward as the authoritative check, but "
    "your job is to make it pass on the first try, not to leave verification to it."
)


def _implement_prompt(
    unit: WorkUnit,
    *,
    prior_failure: str | None = None,
    resuming: bool = False,
    sandbox_enabled: bool = False,
) -> str:
    execution_note = _SANDBOX_EXECUTION_NOTE if sandbox_enabled else _NO_SANDBOX_EXECUTION_NOTE
    prompt = (
        "Implement this work unit fully inside the current working directory by creating or "
        "editing the target modules. Make the minimal changes needed to satisfy the test "
        "contract; do not add anything beyond what the unit asks for. Do NOT assume a target "
        "module already exists — verify with Read and create it if missing. You are done only "
        "once the target modules exist on disk with the required content.\n\n"
        "Work ONLY within the current working directory, using paths relative to it. Never "
        "edit a file by an absolute path or outside this directory — it is an isolated "
        "clone, not the canonical repo.\n\n"
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
            worktree_path, max_bytes=index_config.max_map_bytes, exclude=index_config.exclude
        )
        or None
    )


def _system_prompt(
    contract: PRDContract,
    project_context: str | None = None,
    repo_map: str | None = None,
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
    if repo_map:
        prompt += (
            "\n\nREPO MAP — a structural outline of this repository (tracked files and their "
            "top-level symbols, WU-CODE-INDEX), given up front as static context so you can "
            "orient before exploring, rather than spending turns on blind Read/Glob/Grep:\n"
            f"{repo_map}"
        )
    return prompt
