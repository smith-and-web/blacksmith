"""Escalate-on-failure (WU-ESCALATE-ON-FAIL).

Test contract: on a gate FAILURE the run discards the failed attempt, resets the shared
worktree to the state recorded just before this unit's implement attempt (so prior units'
committed work is NOT discarded), re-implements the SAME unit exactly once with the stronger
``config.models.implement_escalate`` model, and re-gates. A second failure routes to
``human_halt`` with no PR; a first-attempt PASS never escalates (the escalate model is never
invoked). Escalation happens at most once per unit.

Driven through the real CLI ``drive`` loop and the real graph, with the executor and ``gh``
mocked but a REAL worktree manager, so the worktree reset/re-implement is exercised for real.
The fake executor exposes ``run_implement`` AND ``run_implement_escalate`` — the two model
tiers — and records which it was called with, so the model selection is observable without a
live model. A separate unit test pins ``run_implement_escalate`` to the escalate model tier.
"""

import re
import subprocess
from pathlib import Path

from claude_agent_sdk import ResultMessage

from blacksmith.cli import drive
from blacksmith.config import BlacksmithConfig
from blacksmith.executor import Executor, ExecutorResult
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

_TWO_UNITS = """\
  - id: WU-A
    title: "first unit"
    layers: [py-logic]
    target_modules: ["wu-a.txt"]
    test_contract: "the gate command passes"
    depends_on: []
  - id: WU-B
    title: "second unit"
    layers: [py-logic]
    target_modules: ["wu-b.txt"]
    test_contract: "the gate command passes"
    depends_on: [WU-A]"""

_ONE_UNIT = """\
  - id: WU-S
    title: "solo unit"
    layers: [py-logic]
    target_modules: ["wu-s.txt"]
    test_contract: "the gate command passes"
    depends_on: []"""


def _result(text):
    return ExecutorResult(
        text=text, model="claude-opus-4-8", is_error=False, num_turns=1,
        cost_usd=0.01, usage={}, session_id="s",
    )


class FakeExecutor:
    """Records which implement tier built each attempt and what the escalation attempt saw.

    ``run_implement`` is the cheaper first attempt; ``run_implement_escalate`` is the stronger
    retry. Each writes the unit's file (committed by the real implement node) with attempt-
    distinct content, so a later assertion can tell which attempt's work landed. The escalation
    attempt also snapshots the files visible in its cwd — proving the failed first attempt was
    reset away while prior units' committed files survived.
    """

    def __init__(self):
        self.implement_calls: list[str] = []
        self.escalate_calls: list[str] = []
        self.seen_at_escalation: dict[str, list[str]] = {}

    def run_plan(self, prompt, **kwargs):
        return _result("1. do the thing")

    @staticmethod
    def _unit_id(prompt):
        return re.search(r"^Unit (\S+):", prompt, re.M).group(1)

    def run_implement(self, prompt, **kwargs):
        unit_id = self._unit_id(prompt)
        cwd = Path(kwargs["cwd"])
        (cwd / f"{unit_id.lower()}.txt").write_text(f"first {unit_id}\n")
        self.implement_calls.append(unit_id)
        return _result("done")

    def run_implement_escalate(self, prompt, **kwargs):
        unit_id = self._unit_id(prompt)
        cwd = Path(kwargs["cwd"])
        self.seen_at_escalation[unit_id] = sorted(p.name for p in cwd.glob("*.txt"))
        (cwd / f"{unit_id.lower()}.txt").write_text(f"escalated {unit_id}\n")
        self.escalate_calls.append(unit_id)
        return _result("done")


class FakeGate:
    """Gate that fails the 1-indexed calls listed in ``fail_calls`` and passes the rest."""

    def __init__(self, fail_calls):
        self.fail_calls = set(fail_calls)
        self.calls = 0

    def __call__(self, worktree_path, layer):
        self.calls += 1
        passed = self.calls not in self.fail_calls
        return GateResult(passed=passed, output="ok" if passed else "boom", command="pytest")


def _recording_gh(url):
    def run(argv, cwd=None):
        run.calls.append(list(argv))
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")  # git push etc. succeed without a real remote

    run.calls = []
    return run


def _pr_creates(gh):
    return [c for c in gh.calls if c[:3] == ["gh", "pr", "create"]]


def _recording_approver(decision=True):
    def approve(payload, values):
        approve.gates.append(payload.get("gate"))
        return decision

    approve.gates = []
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


def _wire(tmp_path, repo, *, executor, gh, gate):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor,
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=gate,
        pr_runner=gh,
    )
    return graph, saver


def _write_prd(tmp_path, units):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(units=units))
    return path


def _git_show(repo, ref):
    return subprocess.run(
        ["git", "-C", str(repo), *ref], capture_output=True, text=True
    ).stdout


def test_gate_failure_escalates_then_passes_and_opens_one_pr(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    # WU-A passes (call 1); WU-B's first attempt fails (call 2); WU-B's escalation passes (3).
    gate = FakeGate(fail_calls={2})
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _TWO_UNITS), approver=approver, thread_id="esc")

    # The cheaper first-attempt model built WU-A and WU-B's first (failing) attempt...
    assert executor.implement_calls == ["WU-A", "WU-B"]
    # ...and the stronger model re-implemented WU-B exactly once (escalation, at most once).
    assert executor.escalate_calls == ["WU-B"]
    assert gate.calls == 3  # WU-A, WU-B (fail), WU-B re-gate (pass) — no extra escalation

    # When WU-B re-implemented, the worktree had been reset to just before WU-B's attempt:
    # WU-A's committed file SURVIVED (prior units' work not discarded) and WU-B's failed
    # first attempt was gone (only the escalation attempt lands).
    assert executor.seen_at_escalation["WU-B"] == ["wu-a.txt"]

    # Exactly ONE PR, and the run completed.
    assert len(_pr_creates(gh)) == 1
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/7")
    assert approver.gates == ["plan", "pr"]

    # The branch's diff reflects ONLY the escalation attempt: WU-A's file is its own work and
    # WU-B's file holds the escalation content (the discarded first attempt left no trace).
    branch = final.values["branch"]
    assert _git_show(repo, ["show", f"{branch}:wu-a.txt"]) == "first WU-A\n"
    assert _git_show(repo, ["show", f"{branch}:wu-b.txt"]) == "escalated WU-B\n"
    # ...and the branch carries exactly one WU-B commit (the failed attempt's commit was reset
    # away, so it is unreachable from the branch — the diff is not "both attempts"). The unit id
    # rides the Conventional-Commits scope, lower-cased: "feat(wu-b): ...".
    log = _git_show(repo, ["log", "--oneline", branch])
    assert log.count("wu-b") == 1
    saver.conn.close()


def test_both_attempts_fail_routes_to_human_halt_and_opens_no_pr(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/9")
    gate = FakeGate(fail_calls={1, 2})  # the first attempt AND the escalation both fail
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _ONE_UNIT), approver=approver, thread_id="halt")

    # Escalated exactly once before halting — never twice (at most once per unit).
    assert executor.implement_calls == ["WU-S"]
    assert executor.escalate_calls == ["WU-S"]
    assert gate.calls == 2  # one first attempt + one escalation, then it halts

    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    assert _pr_creates(gh) == []  # a second failure opens no PR
    assert approver.gates == ["plan"]  # never reached the PR-approval gate
    saver.conn.close()


def test_first_attempt_pass_never_escalates(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/1")
    gate = FakeGate(fail_calls=set())  # the first attempt passes
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _ONE_UNIT), approver=approver, thread_id="pass")

    # A passing first attempt never invokes the stronger escalate model.
    assert executor.implement_calls == ["WU-S"]
    assert executor.escalate_calls == []
    assert gate.calls == 1
    assert len(_pr_creates(gh)) == 1
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/1")
    saver.conn.close()


class _FakeQuery:
    """Stand-in for claude_agent_sdk.query: records the options it was called with."""

    def __init__(self, messages):
        self.messages = messages
        self.calls: list[dict] = []

    def __call__(self, *, prompt, options, transport=None):
        self.calls.append({"prompt": prompt, "options": options})
        return self._gen()

    async def _gen(self):
        for message in self.messages:
            yield message


def _result_message():
    return ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1,
        session_id="s1", result="ok", total_cost_usd=0.001, usage={},
    )


def test_run_implement_escalate_uses_the_escalate_model_tier(monkeypatch):
    # The escalation retry must run config.models.implement_escalate — distinct from the
    # cheaper first-attempt config.models.implement (PRD §8).
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    config = BlacksmithConfig()  # default model tiers
    fake = _FakeQuery([_result_message()])
    executor = Executor(config, query_fn=fake)

    executor.run_implement_escalate("p")
    assert fake.calls[-1]["options"].model == config.models.implement_escalate

    executor.run_implement("p")
    assert fake.calls[-1]["options"].model == config.models.implement
    # The two tiers are genuinely different models — escalation is a real model change.
    assert config.models.implement_escalate != config.models.implement
