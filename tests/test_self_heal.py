"""Gate self-heal loop (WU-GATE-SELF-HEAL).

Test contract: when a unit's test gate fails, blacksmith re-implements the SAME unit on the
cheap first-attempt model WITH the gate output fed back, BEFORE the single stronger-model
escalation — bounded by ``limits.max_fix_attempts`` and an optional ``limits.max_run_cost_usd``
ceiling. Order per unit is base -> fix_retry x N -> escalate -> halt. The loop is OFF unless the
graph is wired with ``limits`` (so every other test keeps its prior behaviour); these tests opt
in explicitly.

Driven through the real CLI ``drive`` loop and the real graph with a REAL worktree manager (the
reset/re-implement is exercised for real), the executor and ``gh`` mocked. The fake executor
records which tier built each attempt and the prompt it saw, so the fed-back gate output and the
same-model-vs-escalate choice are observable without a live model.
"""

import re
import subprocess

from blacksmith.cli import drive
from blacksmith.config import LimitsConfig
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


class _Result:
    """Minimal ExecutorResult-shaped object (the cost drives the budget tests)."""

    def __init__(self, cost_usd=0.01):
        self.text = "done"
        self.model = "claude-sonnet-4-6"
        self.is_error = False
        self.num_turns = 1
        self.cost_usd = cost_usd
        self.usage = {}
        self.session_id = "s"


class FakeExecutor:
    """Records which tier built each attempt and the prompt it saw."""

    def __init__(self, cost_usd=0.01):
        self.implement_calls: list[str] = []
        self.escalate_calls: list[str] = []
        self.implement_prompts: list[str] = []
        self._cost = cost_usd

    def run_plan(self, prompt, **kwargs):
        return _Result(self._cost)

    @staticmethod
    def _unit_id(prompt):
        return re.search(r"^Unit (\S+):", prompt, re.M).group(1)

    def run_implement(self, prompt, **kwargs):
        from pathlib import Path

        unit_id = self._unit_id(prompt)
        (Path(kwargs["cwd"]) / f"{unit_id.lower()}.txt").write_text(f"impl {unit_id}\n")
        self.implement_calls.append(unit_id)
        self.implement_prompts.append(prompt)
        return _Result(self._cost)

    def run_implement_escalate(self, prompt, **kwargs):
        from pathlib import Path

        unit_id = self._unit_id(prompt)
        (Path(kwargs["cwd"]) / f"{unit_id.lower()}.txt").write_text(f"escalated {unit_id}\n")
        self.escalate_calls.append(unit_id)
        return _Result(self._cost)


class FakeGate:
    """Fails the 1-indexed calls in ``fail_calls`` with a recognizable output, passes the rest."""

    def __init__(self, fail_calls):
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


def _wire(tmp_path, repo, *, executor, gh, gate, limits):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor,
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=gate,
        pr_runner=gh,
        limits=limits,
    )
    return graph, saver


def _write_prd(tmp_path, units=_ONE_UNIT):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(units=units))
    return path


def test_fix_retry_same_model_with_feedback_then_passes(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/5")
    gate = FakeGate(fail_calls={1})  # first attempt fails, the fix retry passes
    limits = LimitsConfig(max_fix_attempts=1)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="heal")

    # The cheap model built the unit TWICE (base + fix retry) — the stronger model was untouched.
    assert executor.implement_calls == ["WU-S", "WU-S"]
    assert executor.escalate_calls == []
    assert gate.calls == 2
    # The retry prompt fed back the failing gate output AND carried the honesty rule.
    assert "BOOM-GATE-OUTPUT" in executor.implement_prompts[1]
    assert "weaken" in executor.implement_prompts[1].lower()
    assert "BOOM-GATE-OUTPUT" not in executor.implement_prompts[0]  # first attempt ran blind

    assert final.values["status"] == Status.DONE
    assert len(_pr_creates(gh)) == 1
    saver.conn.close()


def test_fix_retry_exhausts_then_escalates_then_halts(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/6")
    gate = FakeGate(fail_calls={1, 2, 3})  # base, fix retry, AND escalation all fail
    limits = LimitsConfig(max_fix_attempts=1)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="exhaust")

    # base + one fix retry on the cheap model, then exactly one escalation, then halt.
    assert executor.implement_calls == ["WU-S", "WU-S"]
    assert executor.escalate_calls == ["WU-S"]
    assert gate.calls == 3
    assert final.values["status"] == Status.HALTED
    assert _pr_creates(gh) == []
    saver.conn.close()


def test_cost_cap_halts_without_retry(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(cost_usd=0.01)
    gh = _recording_gh("https://github.com/owner/demo/pull/8")
    gate = FakeGate(fail_calls={1})  # the first attempt fails
    # Retries are allowed in principle, but the ceiling is below even one attempt's spend.
    limits = LimitsConfig(max_fix_attempts=3, max_run_cost_usd=0.005)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="capped")

    # The cap blocks recovery: no fix retry, no escalation, just the base attempt then halt.
    assert executor.implement_calls == ["WU-S"]
    assert executor.escalate_calls == []
    assert gate.calls == 1
    assert final.values["status"] == Status.HALTED
    # The halt is labelled a budget halt, not a plain repeated failure.
    messages = " ".join(e["message"] for e in final.values.get("errors", []))
    assert "cost cap" in messages
    assert _pr_creates(gh) == []
    saver.conn.close()
