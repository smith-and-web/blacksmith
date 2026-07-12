"""SBFL fix-retry wiring (WU-SBFL-WIRE).

Test contract: when ``config.sbfl.enabled``, a gate failure that routes to a fix-retry (the
existing self-heal path — NOT a pass, NOT the first attempt) runs the fault-localization
collector against the FAILING worktree, before it is reset, and APPENDS
``format_suspicious_locations(...)`` to the same ``last_gate_output`` channel the raw gate
output already travels on, so the next implement attempt sees the ranked ``file:line``
locations alongside the failure text. With ``[sbfl].enabled=false`` the fix-retry feedback is
byte-for-byte unchanged: no collection runs, no block is appended, and the collector is never
called.

Driven through the real CLI ``drive`` loop and the real graph with a REAL worktree manager
(so the reset/re-implement is exercised for real), the executor and ``gh`` mocked, and the
SBFL collector stubbed by monkeypatch so the ranked locations are known without running the
target repo's coverage tooling. The fake executor records the prompt each attempt saw, so the
fed-back feedback is observable without a live model.
"""

import re
import subprocess

from blacksmith import graph
from blacksmith.cli import drive
from blacksmith.config import LimitsConfig, SBFLConfig
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
    """Minimal ExecutorResult-shaped object."""

    def __init__(self, cost_usd=0.01):
        self.text = "done"
        self.model = "claude-sonnet-4-6"
        self.is_error = False
        self.num_turns = 1
        self.cost_usd = cost_usd
        self.usage = {}
        self.session_id = "s"


class FakeExecutor:
    """Records the prompt each implement attempt saw."""

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


def _wire(tmp_path, repo, *, executor, gh, gate, limits, sbfl=None):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    compiled = compile_graph(
        saver,
        executor=executor,
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=gate,
        pr_runner=gh,
        limits=limits,
        sbfl=sbfl,
    )
    return compiled, saver


def _write_prd(tmp_path, units=_ONE_UNIT):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(units=units))
    return path


def test_sbfl_enabled_appends_suspicious_locations_to_fix_retry_feedback(tmp_path, monkeypatch):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/9")
    gate = FakeGate(fail_calls={1})  # first attempt fails, the fix retry passes
    limits = LimitsConfig(max_fix_attempts=1)
    sbfl = SBFLConfig(enabled=True, coverage_cmd="run-coverage")

    known = [{"file": "wu-s.txt", "line": 7, "score": 0.87, "failed": 3, "passed": 1}]
    calls: list = []

    def fake_collect(worktree_path, **kwargs):
        calls.append((worktree_path, kwargs))
        return known

    monkeypatch.setattr(graph, "collect_suspicious_locations", fake_collect)

    compiled, saver = _wire(
        tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits, sbfl=sbfl
    )
    final = drive(compiled, _write_prd(tmp_path), approver=_approver(), thread_id="sbfl-heal")

    # The collector ran exactly once — on the single fix-retry, against the failing worktree.
    assert len(calls) == 1
    assert calls[0][0]  # a real worktree path was passed
    retry_prompt = executor.implement_prompts[1]
    # The retry feedback carries BOTH the original gate output AND the ranked suspicious block.
    assert "BOOM-GATE-OUTPUT" in retry_prompt
    assert "SUSPICIOUS LOCATIONS" in retry_prompt
    assert "wu-s.txt:7" in retry_prompt
    # The first attempt ran blind — no gate output, no suspicious block.
    assert "BOOM-GATE-OUTPUT" not in executor.implement_prompts[0]
    assert "SUSPICIOUS LOCATIONS" not in executor.implement_prompts[0]
    assert final.values["status"] == Status.DONE
    saver.conn.close()


def test_sbfl_disabled_leaves_fix_retry_feedback_unchanged(tmp_path, monkeypatch):
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/10")
    gate = FakeGate(fail_calls={1})  # first attempt fails, the fix retry passes
    limits = LimitsConfig(max_fix_attempts=1)
    sbfl = SBFLConfig(enabled=False)

    calls: list = []

    def spy_collect(worktree_path, **kwargs):
        calls.append(worktree_path)
        return [{"file": "x", "line": 1, "score": 1.0, "failed": 1, "passed": 0}]

    monkeypatch.setattr(graph, "collect_suspicious_locations", spy_collect)

    compiled, saver = _wire(
        tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits, sbfl=sbfl
    )
    final = drive(compiled, _write_prd(tmp_path), approver=_approver(), thread_id="sbfl-off")

    # Disabled: the collector is never called and no suspicious block reaches the feedback.
    assert calls == []
    retry_prompt = executor.implement_prompts[1]
    assert "BOOM-GATE-OUTPUT" in retry_prompt  # the raw gate output still feeds back, as before
    assert "SUSPICIOUS LOCATIONS" not in retry_prompt
    assert final.values["status"] == Status.DONE
    saver.conn.close()
