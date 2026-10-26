"""Respond execution is disabled until it uses the common integrity/recovery services.

The command and library signatures remain recognizable. Historical result models,
formatting and branch helpers are retained for compatibility/inspection; they do
not enable the deferred revision workflow.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from blacksmith.commands import run_command
from blacksmith.config import BlacksmithConfig
from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.gate import FixResult, GateResult, TargetToolchain
from blacksmith.nodes.pr import Runner, subprocess_runner
from blacksmith.worktree import Clone, CloneManager

GateFn = Callable[[str | Path, str | None], GateResult]
FixFn = Callable[[str | Path, str | None], FixResult]

# The ad hoc "unit" a revision runs under: respond has no PRD of its own, so a minimal,
# always-auto-gated layer/unit stands in — just enough for `implement()` to build its
# system prompt (untouchables) and run the guard; it names no real target modules
# because the revision touches whatever the PR's review comments call out.
_RESPOND_LAYER = "respond"
_DEFAULT_UNTOUCHABLES = ["No PRD contract is associated with this PR revision."]


class RespondError(Exception):
    """A respond operation is unavailable or failed."""


RESPOND_UNSUPPORTED = (
    "respond_unsupported_until_hardened: respond is disabled until its shared "
    "integrity, execution and recovery services are hardened."
)


class RespondUnsupportedError(RespondError):
    """The revision workflow cannot execute under the current readiness policy."""


@dataclass(frozen=True)
class RespondResult:
    """Outcome of one :func:`respond_to_pr` call."""

    pr_number: int
    branch: str
    comment_count: int
    attempts: int
    pushed: bool
    reason: str  # "no_comments" | "pushed" | "gate_failed"
    gate_output: str | None = None


class PRBranchCloneManager(CloneManager):
    """A :class:`CloneManager` that checks out an EXISTING remote branch — a PR's
    already-pushed branch — instead of creating a fresh one off HEAD.

    ``CloneManager.create()`` always makes a brand-new branch (``checkout -b``): right
    for a fresh unit, wrong here, since the PR's branch already exists on the real
    remote and a revision must land ON it, never diverge onto a new one. Everything
    else — its own ``.git``, origin repointed at the source's real remote, identity
    propagation — is inherited from :class:`CloneManager` unchanged.
    """

    def create(self, branch: str) -> Clone:  # type: ignore[override]
        path = self.base_dir / branch.replace("/", "-")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._git(self.repo_path.parent, "clone", "--local", str(self.repo_path), str(path))
        # Repoint origin at the source's REAL remote BEFORE fetching the PR branch. The
        # `--local` clone's origin is the local source checkout, which usually does NOT have
        # the PR's branch — blacksmith pushes each PR branch to the remote from a throwaway
        # clone, so the operator's source checkout only tracks `main`. Fetching the branch from
        # the local source therefore fails ("couldn't find remote ref"); the branch lives on the
        # remote. (When the source has no remote, origin stays the local clone as a fallback.)
        source_origin = self._source_origin_url()
        if source_origin is not None:
            self._git(path, "remote", "set-url", "origin", source_origin)
        self._git(path, "fetch", "origin", branch)
        self._git(path, "checkout", "-B", branch, f"origin/{branch}")
        self._propagate_identity(path)
        return Clone(path=path, branch=branch, repo_path=self.repo_path)


def _respond_unit() -> WorkUnit:
    return WorkUnit(
        id="RESPOND",
        title="Address PR review feedback",
        layers=[_RESPOND_LAYER],
        target_modules=["(the PR's existing changes)"],
        test_contract="The project's test gate must pass after addressing the feedback.",
        depends_on=[],
    )


def _default_contract() -> PRDContract:
    """A minimal, always-auto-gated contract used when the caller has none to pass in.

    Its ``untouchables`` still ride the implementer's system prompt (the constitution),
    and its lone layer is ``"auto"`` so ``implement()`` never treats a revision as
    human-gated (which would skip the executor entirely).
    """
    return PRDContract(
        contract_version=1,
        component="respond",
        version="0",
        primary_target_repo="",
        layers={_RESPOND_LAYER: "auto"},
        untouchables=list(_DEFAULT_UNTOUCHABLES),
        work_units=[_respond_unit()],
    )


def format_review_feedback(comments: list[dict]) -> str:
    """Render fetched review comments (WU-PR-COMMENTS) as the revision instruction fed
    to the implementer via the EXISTING feedback channel (``last_gate_output``)."""
    lines = ["A human reviewer left the following feedback on this open PR. Address it:"]
    for comment in comments:
        where = f"{comment.get('path')}:{comment.get('line')}" if comment.get("path") else "general"
        author = comment.get("author") or "reviewer"
        lines.append(f"- [{where}] {author}: {comment.get('body', '')}")
    return "\n".join(lines)


def _implement_failure_message(update: dict) -> str:
    errors = update.get("errors") or []
    if errors:
        return "; ".join(e.get("message", "") for e in errors)
    return "the revision attempt made no changes"


def _git(path: Path, *args: str) -> str:
    result = run_command(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )
    return result.require_complete_output().stdout


def _reset_hard(path: Path, ref: str) -> None:
    """Discard a failed attempt's commit(s), back to the clean pre-attempt state —
    exactly mirroring the self-heal loop's ``prepare_fix_retry`` reset."""
    run_command(
        ["git", "-C", str(path), "reset", "--hard", ref], capture_output=True, text=True, check=True
    )
    run_command(
        ["git", "-C", str(path), "clean", "-fd"], capture_output=True, text=True, check=True
    )


def _push_revision(clone: Clone, branch: str, *, remote: str, runner: Runner) -> None:
    """Append the revision's commit(s) to the PR's EXISTING branch — via the existing
    ``Runner`` machinery, never ``gh pr create`` and never a force-push."""
    result = runner(["git", "push", remote, branch], clone.path)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RespondError(f"git push failed: {detail}")


def respond_to_pr(
    *,
    pr_number: int,
    branch: str,
    repo_path: str | Path,
    config: BlacksmithConfig,
    executor: Executor,
    gate: GateFn | None = None,
    fix: FixFn | None = None,
    clone_manager: CloneManager | None = None,
    pr_runner: Runner = subprocess_runner,
    toolchain: TargetToolchain | None = None,
    layer: str | None = None,
    repo: str | None = None,
    own_login: str | None = "blacksmith",
    contract: PRDContract | None = None,
    remote: str = "origin",
) -> RespondResult:
    """Refuse the deferred revision workflow before comment fetch or any effect."""
    raise RespondUnsupportedError(RESPOND_UNSUPPORTED)
