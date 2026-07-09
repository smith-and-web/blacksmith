"""``blacksmith respond`` — revise an already-open PR from its review comments
(WU-RESPOND-FLOW).

ADDITIVE, PRD §7-compliant entry point: a NEW code path that never touches the normal
ingest→plan→implement→gate→PR graph run. A repo that never calls :func:`respond_to_pr`
behaves exactly as today.

Given a PR blacksmith itself opened, this drives a BOUNDED revise loop:

1. Fetch the PR's human review feedback (``blacksmith.pr_comments``, WU-PR-COMMENTS).
   Empty feedback is a no-op — nothing is cloned, revised, or pushed.
2. Clone the PR's branch in an isolated :class:`CloneManager`-based clone (never the
   canonical target repo's working tree).
3. Run the implementer with the review comments fed in as the revision instruction —
   by calling the EXISTING ``blacksmith.nodes.implement.implement`` node itself, so the
   revision reuses, unchanged, the untouchable pre-edit guard (PRD §7) and the same
   feedback channel (``last_gate_output``) a gate-failure retry already uses.
4. Run the deterministic auto-fixer, then the authoritative test gate. The gate's
   pass/fail semantics are NEVER altered here.
5. On PASS, push the appended commit to the PR's EXISTING branch via the existing
   ``Runner`` machinery (``blacksmith.nodes.pr``) — one more commit on the open PR,
   never a new PR, never a force-push, never rewritten history.
6. On FAIL, discard the failed attempt and retry — feeding the gate's own output back
   in, exactly like the self-heal loop — up to ``config.respond.max_attempts``. If every
   attempt fails, ``respond_to_pr`` stops WITHOUT pushing anything: the gate stays
   authoritative for a revision too.
"""

from __future__ import annotations

import functools
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from blacksmith.config import BlacksmithConfig
from blacksmith.contract import PRD, PRDContract, WorkUnit
from blacksmith.executor import Executor
from blacksmith.gate import FixResult, GateResult, TargetToolchain, run_fix, run_gate
from blacksmith.nodes.implement import implement
from blacksmith.nodes.pr import Runner, subprocess_runner
from blacksmith.pr_comments import fetch_pr_review_comments
from blacksmith.state import Status
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
    """Raised when pushing a passing revision to the PR's branch fails."""


@dataclass(frozen=True)
class RespondResult:
    """Outcome of one :func:`respond_to_pr` call."""

    pr_number: int
    branch: str
    comment_count: int
    attempts: int
    pushed: bool
    reason: str  # "no_comments" | "pushed" | "gate_failed"


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
        where = (
            f"{comment.get('path')}:{comment.get('line')}" if comment.get("path") else "general"
        )
        author = comment.get("author") or "reviewer"
        lines.append(f"- [{where}] {author}: {comment.get('body', '')}")
    return "\n".join(lines)


def _implement_failure_message(update: dict) -> str:
    errors = update.get("errors") or []
    if errors:
        return "; ".join(e.get("message", "") for e in errors)
    return "the revision attempt made no changes"


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    return result.stdout


def _reset_hard(path: Path, ref: str) -> None:
    """Discard a failed attempt's commit(s), back to the clean pre-attempt state —
    exactly mirroring the self-heal loop's ``prepare_fix_retry`` reset."""
    subprocess.run(
        ["git", "-C", str(path), "reset", "--hard", ref], capture_output=True, text=True
    )
    subprocess.run(["git", "-C", str(path), "clean", "-fd"], capture_output=True, text=True)


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
    """Revise ``pr_number`` (whose branch is ``branch``) from its human review comments,
    gate the revision, and — only on a PASS — push the appended commit to the PR's
    existing branch.

    Bounded by ``config.respond.max_attempts``: each failing gate discards the attempt
    and retries with the gate's own output fed back in, mirroring the self-heal loop.
    If every attempt fails, this returns WITHOUT pushing anything — the gate stays
    authoritative for a revision exactly as it is for a fresh unit. Empty review
    comments are a no-op: nothing is cloned, revised, or pushed.
    """
    repo_path = Path(repo_path)
    comments = fetch_pr_review_comments(
        pr_number, repo=repo, own_login=own_login, runner=pr_runner, cwd=repo_path
    )
    if not comments:
        return RespondResult(
            pr_number=pr_number,
            branch=branch,
            comment_count=0,
            attempts=0,
            pushed=False,
            reason="no_comments",
        )

    gate = gate or functools.partial(run_gate, toolchain=toolchain)
    fix = fix or functools.partial(run_fix, toolchain=toolchain)
    contract = contract or _default_contract()
    unit = contract.work_units[0]
    manager = clone_manager or PRBranchCloneManager(repo_path)

    clone = manager.create(branch)
    try:
        feedback = format_review_feedback(comments)
        max_attempts = max(1, config.respond.max_attempts)
        pre_attempt_ref = _git(clone.path, "rev-parse", "HEAD").strip()
        for attempt in range(1, max_attempts + 1):
            state = {
                "prd": PRD(path=Path("<respond>"), contract=contract, body=""),
                "selected_unit": unit,
                "worktree_path": str(clone.path),
                "last_gate_output": feedback,
            }
            impl_update = implement(state, executor=executor)
            if impl_update.get("status") != Status.TESTING or impl_update.get("errors"):
                # No usable revision this attempt (nothing changed, a commit failure, or
                # the guard blocked an untouchable edit) — discard and retry.
                _reset_hard(clone.path, pre_attempt_ref)
                feedback = _implement_failure_message(impl_update)
                continue

            fix(clone.path, layer)
            gate_result = gate(clone.path, layer)
            if gate_result.passed:
                _push_revision(clone, branch, remote=remote, runner=pr_runner)
                return RespondResult(
                    pr_number=pr_number,
                    branch=branch,
                    comment_count=len(comments),
                    attempts=attempt,
                    pushed=True,
                    reason="pushed",
                )
            _reset_hard(clone.path, pre_attempt_ref)
            feedback = gate_result.output
        return RespondResult(
            pr_number=pr_number,
            branch=branch,
            comment_count=len(comments),
            attempts=max_attempts,
            pushed=False,
            reason="gate_failed",
        )
    finally:
        manager.remove(clone)
