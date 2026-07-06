"""Wiring the post-gate review loop into the graph (WU-REVIEW-LOOP).

Test contract: on a ``test_gate`` PASS, when ``config.review.enabled`` and the current
unit is not yet review-clean, the run routes to the ``review`` node instead of straight to
next_unit/approve_pr. A clean verdict proceeds via the unchanged next_unit/approve_pr
decision. A blocking finding revises the unit (feeding the findings back into the next
implement prompt exactly as a fix-retry feeds back the gate output), bumps
``review_revisions``, and re-gates/re-reviews -- bounded by ``limits.max_review_revisions``
and the cost cap. Once revisions are exhausted (or the run is over budget) the unit still
proceeds to next_unit/approve_pr, carrying its unresolved findings forward, rather than
halting. The gate FAILURE branch and the escalation/self-heal path are untouched.

Driven through the real CLI ``drive`` loop and the real graph with a REAL worktree manager
(so the "revise in place, on top of the passing commit" behaviour is exercised for real);
the executor and ``gh`` are mocked. The fake executor exposes ``run_plan``/``run_implement``/
``run_review`` and records which unit each call targeted, so the review-then-revise sequence
is observable without a live model.
"""

import re
import subprocess

from blacksmith.cli import drive
from blacksmith.config import LimitsConfig, ReviewConfig
from blacksmith.executor import ExecutorResult
from blacksmith.gate import GateResult
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

_PRD_TEMPLATE = """\
---
contract_version: 1
component: demo
version: v0
primary_target_repo: owner/demo
layers:
  py-logic: auto
untouchables:
  - "do not touch the brand files"
work_units:
{units}
---
# Demo PRD

## 1. Purpose
demo.

## 2. Scope fences
demo.

## 7. Untouchables
none.

## 10. Acceptance criteria
done.
"""

_ONE_UNIT = """\
  - id: WU-S
    title: "solo unit"
    layers: [py-logic]
    target_modules: ["wu-s.txt"]
    test_contract: "the gate command passes"
    depends_on: []"""

CLEAN_VERDICT = '```json\n{"verdict": "clean", "findings": []}\n```'
NEEDS_CHANGES_VERDICT = (
    '```json\n{"verdict": "needs_changes", "findings": '
    '[{"severity": "blocking", "file": "wu-s.txt", '
    '"detail": "off-by-one in the loop bound"}]}\n```'
)


def _review_result(text, *, cost=0.02):
    return ExecutorResult(
        text=text, model="claude-opus-4-8", is_error=False, num_turns=2,
        cost_usd=cost, usage={}, session_id="s",
    )


class FakeExecutor:
    """Records which unit each implement/review call targeted, and the prompt each
    implement call saw -- so the review-then-revise sequence and the revision feedback
    are observable without a live model."""

    def __init__(self, review_verdicts, cost_usd=0.01):
        self.implement_calls: list[str] = []
        self.implement_prompts: list[str] = []
        self.review_calls: list[str] = []
        self._review_verdicts = list(review_verdicts)
        self._cost = cost_usd

    def run_plan(self, prompt, **kwargs):
        return ExecutorResult(
            text="1. do the thing", model="claude-sonnet-4-6", is_error=False, num_turns=1,
            cost_usd=self._cost, usage={}, session_id="s",
        )

    @staticmethod
    def _unit_id(prompt):
        return re.search(r"^Unit (\S+):", prompt, re.M).group(1)

    def run_implement(self, prompt, **kwargs):
        from pathlib import Path

        unit_id = self._unit_id(prompt)
        # Distinguish a fresh (blind) attempt from a review-driven revision: the revision
        # prompt carries the review's feedback fed back via last_gate_output.
        marker = "revised" if "REVIEWER flagged" in prompt else "base"
        (Path(kwargs["cwd"]) / f"{unit_id.lower()}.txt").write_text(f"{marker} {unit_id}\n")
        self.implement_calls.append(unit_id)
        self.implement_prompts.append(prompt)
        return ExecutorResult(
            text="done", model="claude-sonnet-4-6", is_error=False, num_turns=1,
            cost_usd=self._cost, usage={}, session_id="s",
        )

    def run_review(self, prompt, **kwargs):
        unit_id = self._unit_id(prompt)
        self.review_calls.append(unit_id)
        verdict = self._review_verdicts[len(self.review_calls) - 1]
        return _review_result(verdict, cost=self._cost)


class FakeGate:
    """Fails the 1-indexed calls in ``fail_calls`` with a recognizable output, passes the
    rest -- same shape as the self-heal/escalation suites' FakeGate."""

    def __init__(self, fail_calls=()):
        self.fail_calls = set(fail_calls)
        self.calls = 0

    def __call__(self, worktree_path, layer):
        self.calls += 1
        passed = self.calls not in self.fail_calls
        return GateResult(
            passed=passed, output="ok" if passed else "BOOM-GATE-OUTPUT", command="pytest"
        )


def _recording_gh(url):
    def run(argv, cwd=None):
        run.calls.append(list(argv))
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")

    run.calls = []
    return run


def _pr_creates(gh):
    return [c for c in gh.calls if c[:3] == ["gh", "pr", "create"]]


def _approver(decision=True):
    def approve(payload, values):
        return decision

    return approve


def _target_repo(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    (repo / "blacksmith.toml").write_text('test_cmd = "true"\n')  # unused: a fake gate is injected
    g("add", "-A")
    g("commit", "-m", "init")
    return repo


def _wire(tmp_path, repo, *, executor, gh, gate, limits=None, review=None):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor,
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=gate,
        pr_runner=gh,
        limits=limits,
        review=review,
    )
    return graph, saver


def _write_prd(tmp_path, units=_ONE_UNIT):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(units=units))
    return path


def _git_show(repo, ref):
    return subprocess.run(
        ["git", "-C", str(repo), *ref], capture_output=True, text=True
    ).stdout


# --- (a) clean-first: exactly one review call, no revision, proceeds -----------------


def test_review_clean_first_pass_proceeds_with_one_review_call(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(review_verdicts=[CLEAN_VERDICT])
    gh = _recording_gh("https://github.com/owner/demo/pull/1")
    gate = FakeGate()  # every gate call passes
    graph, saver = _wire(
        tmp_path, repo, executor=executor, gh=gh, gate=gate,
        limits=LimitsConfig(), review=ReviewConfig(enabled=True),
    )

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="clean")

    assert executor.implement_calls == ["WU-S"]  # no revision attempt
    assert executor.review_calls == ["WU-S"]  # exactly one review call
    assert gate.calls == 1
    assert final.values["status"] == Status.DONE
    assert final.values.get("unresolved_review_findings", []) == []
    assert len(_pr_creates(gh)) == 1
    saver.conn.close()


# --- (b) blocking-then-clean: revises once, re-gates, re-reviews clean, proceeds -----


def test_review_blocking_then_clean_revises_once_and_proceeds(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(review_verdicts=[NEEDS_CHANGES_VERDICT, CLEAN_VERDICT])
    gh = _recording_gh("https://github.com/owner/demo/pull/2")
    gate = FakeGate()  # both the base gate call and the re-gate pass
    graph, saver = _wire(
        tmp_path, repo, executor=executor, gh=gh, gate=gate,
        limits=LimitsConfig(max_review_revisions=1), review=ReviewConfig(enabled=True),
    )

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="revise")

    # Base attempt + exactly one revision; the revision prompt carried the review feedback.
    assert executor.implement_calls == ["WU-S", "WU-S"]
    assert "off-by-one" in executor.implement_prompts[1]
    assert "REVIEWER flagged" in executor.implement_prompts[1]
    assert "REVIEWER flagged" not in executor.implement_prompts[0]  # first attempt ran blind
    # Reviewed twice: once flags blocking, the re-review after the revision is clean.
    assert executor.review_calls == ["WU-S", "WU-S"]
    assert gate.calls == 2  # base gate pass + re-gate pass after the revision

    assert final.values["status"] == Status.DONE
    assert final.values.get("unresolved_review_findings", []) == []
    assert len(_pr_creates(gh)) == 1

    # The final diff reflects the revision (not the discarded/superseded base content).
    branch = final.values["branch"]
    assert _git_show(repo, ["show", f"{branch}:wu-s.txt"]) == "revised WU-S\n"
    # Both commits landed (the revision commits ON TOP of the passing base commit, unlike a
    # fix-retry/escalation which discards the failed attempt): two commits touch wu-s.txt.
    log = _git_show(repo, ["log", "--oneline", branch])
    assert log.count("wu-s") == 2
    saver.conn.close()


# --- (c) revisions exhausted: proceeds to approve_pr, carrying unresolved findings ---


def test_review_revisions_exhausted_proceeds_without_halting(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(review_verdicts=[NEEDS_CHANGES_VERDICT])
    gh = _recording_gh("https://github.com/owner/demo/pull/3")
    gate = FakeGate()
    graph, saver = _wire(
        tmp_path, repo, executor=executor, gh=gh, gate=gate,
        # No revisions allowed at all: the first blocking finding is immediately exhausted.
        limits=LimitsConfig(max_review_revisions=0), review=ReviewConfig(enabled=True),
    )

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="exhausted")

    assert executor.implement_calls == ["WU-S"]  # no revision attempt
    assert executor.review_calls == ["WU-S"]  # reviewed exactly once
    assert gate.calls == 1

    # Proceeds all the way through -- never halts on an exhausted review loop.
    assert final.values["status"] == Status.DONE
    assert len(_pr_creates(gh)) == 1

    unresolved = final.values.get("unresolved_review_findings") or []
    assert len(unresolved) == 1
    assert unresolved[0]["severity"] == "blocking"
    assert unresolved[0]["file"] == "wu-s.txt"
    assert "off-by-one" in unresolved[0]["detail"]
    saver.conn.close()


# --- (d) config.review.enabled false: the review node is never entered --------------


def test_review_disabled_never_enters_review_node(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(review_verdicts=[])  # never consumed
    gh = _recording_gh("https://github.com/owner/demo/pull/4")
    gate = FakeGate()
    graph, saver = _wire(
        tmp_path, repo, executor=executor, gh=gh, gate=gate,
        limits=LimitsConfig(), review=ReviewConfig(enabled=False),
    )

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="disabled")

    assert executor.review_calls == []  # review is never entered
    assert executor.implement_calls == ["WU-S"]
    assert gate.calls == 1
    assert final.values["status"] == Status.DONE
    assert final.values.get("unresolved_review_findings", []) == []
    assert len(_pr_creates(gh)) == 1
    saver.conn.close()


def test_review_never_wired_preserves_prior_behaviour(tmp_path):
    """A graph compiled without a ``review`` config at all (every pre-existing caller)
    behaves exactly as before: the review node is unreachable regardless of the gate
    outcome, matching current behaviour prior to this unit."""
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(review_verdicts=[])
    gh = _recording_gh("https://github.com/owner/demo/pull/5")
    gate = FakeGate()
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="unwired")

    assert executor.review_calls == []
    assert final.values["status"] == Status.DONE
    assert len(_pr_creates(gh)) == 1
    saver.conn.close()
