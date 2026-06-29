"""Gate self-heal INSIDE the parallel fan-out path (WU-GATE-SELF-HEAL + WU-PARALLEL-FANOUT).

The sequential implement->test_gate path recovers a gate failure (base -> fix_retry x N on the
cheap model with the gate output fed back -> escalate -> halt). This pins down the SAME bounded
same-model fix retry inside the fan-out worker ``build_unit``: a multi-unit level builds each unit
in its OWN clone, and a unit whose gate fails is reset to its base tip and re-implemented with the
gate output fed back, BEFORE the level's outcome is recorded — bounded by ``max_fix_attempts`` and
the optional ``max_run_cost_usd`` ceiling. The fan-out worker does NOT escalate (the sequential
path owns the single stronger-model retry).

Driven hermetically through the real CLI ``drive`` loop and the real graph against a REAL local
source repo with a REAL ``CloneManager`` (so the per-unit clone + reset/re-implement is exercised
for real), the executor and ``gh`` mocked. The fake executor records which tier built each unit and
the prompt it saw, so the fed-back gate output and the same-model-vs-escalate choice are observable.
"""

import re
import subprocess
from collections import Counter
from pathlib import Path

from blacksmith.cli import drive
from blacksmith.config import LimitsConfig
from blacksmith.gate import GateResult
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status
from blacksmith.worktree import CloneManager

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


def _unit(uid, title, target, deps):
    dep = "[" + ", ".join(deps) + "]"
    return (
        f"  - id: {uid}\n"
        f'    title: "{title}"\n'
        f"    layers: [py-logic]\n"
        f'    target_modules: ["{target}"]\n'
        f'    test_contract: "the gate command passes"\n'
        f"    depends_on: {dep}"
    )


# Two roots, one level, disjoint files -> a parallel fan-out level.
_TWO_INDEPENDENT = "\n".join(
    [_unit("WU-X", "first", "wu-x.txt", []), _unit("WU-Y", "second", "wu-y.txt", [])]
)


class _Result:
    """Minimal ExecutorResult-shaped object (the cost drives the budget test)."""

    def __init__(self, cost_usd=0.01):
        self.text = "done"
        self.model = "claude-sonnet-4-6"
        self.is_error = False
        self.num_turns = 1
        self.cost_usd = cost_usd
        self.usage = {}
        self.session_id = "s"


class FakeExecutor:
    """Records which tier built each unit and the prompt each implement attempt saw, and
    writes each unit's target module so the gate/cherry-pick has a real diff to work on."""

    def __init__(self, cost_usd=0.01):
        self.implement_calls: list[str] = []
        self.escalate_calls: list[str] = []
        self.prompts_by_unit: dict[str, list[str]] = {}
        self._cost = cost_usd

    def run_plan(self, prompt, **kwargs):
        return _Result(self._cost)

    @staticmethod
    def _unit_id(prompt):
        return re.search(r"^Unit (\S+):", prompt, re.M).group(1)

    @staticmethod
    def _target(prompt):
        return re.search(r"^Target modules: (.+)$", prompt, re.M).group(1).split(",")[0].strip()

    def run_implement(self, prompt, **kwargs):
        unit_id = self._unit_id(prompt)
        (Path(kwargs["cwd"]) / self._target(prompt)).write_text(f"content from {unit_id}\n")
        self.implement_calls.append(unit_id)
        self.prompts_by_unit.setdefault(unit_id, []).append(prompt)
        return _Result(self._cost)

    def run_implement_escalate(self, prompt, **kwargs):
        unit_id = self._unit_id(prompt)
        (Path(kwargs["cwd"]) / self._target(prompt)).write_text(f"escalated {unit_id}\n")
        self.escalate_calls.append(unit_id)
        return _Result(self._cost)


class FlakyUnitGate:
    """Fails the first ``fail_times`` gate calls whose worktree path contains ``unit_substr``
    (the per-unit build clone is named after the unit), then passes that unit; passes everyone
    else immediately. A per-unit call counter, so a unit that is reset + re-implemented can pass
    on a later attempt against the SAME clone path."""

    def __init__(self, unit_substr: str, fail_times: int):
        self.unit_substr = unit_substr
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, worktree_path, layer):
        if self.unit_substr in str(worktree_path):
            self.calls += 1
            if self.calls <= self.fail_times:
                return GateResult(passed=False, output="BOOM-FANOUT-GATE", command="pytest")
        return GateResult(passed=True, output="ok", command="pytest")


def _recording_gh(url):
    def run(argv, cwd=None):
        run.calls.append(list(argv))
        if argv[:2] == ["git", "push"] and cwd is not None:
            log = subprocess.run(
                ["git", "-C", str(cwd), "log", "--format=%s"], capture_output=True, text=True
            ).stdout
            run.push_logs.append(log)
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")

    run.calls = []
    run.push_logs = []
    return run


def _pr_creates(gh):
    return [c for c in gh.calls if c[:3] == ["gh", "pr", "create"]]


def _git_pushes(gh):
    return [c for c in gh.calls if c[:2] == ["git", "push"]]


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
        worktree_manager=CloneManager(repo, base_dir=tmp_path / "clones"),
        gate=gate,
        pr_runner=gh,
        limits=limits,
    )
    return graph, saver


def _write_prd(tmp_path, units=_TWO_INDEPENDENT):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(units=units))
    return path


def test_fanout_unit_gate_fails_once_then_passes_on_same_model_retry(tmp_path):
    # A fan-out level where WU-Y's gate fails once, then passes on the same-model fix retry;
    # WU-X passes first try. Both units land on one combined branch -> one combined PR.
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    gate = FlakyUnitGate(unit_substr="wu-y", fail_times=1)  # WU-Y fails its first gate only
    limits = LimitsConfig(max_fix_attempts=1)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="fan-heal")

    # WU-Y built TWICE on the cheap model (base + fix retry); WU-X once. NEVER escalated.
    assert executor.implement_calls.count("WU-Y") == 2
    assert executor.implement_calls.count("WU-X") == 1
    assert executor.escalate_calls == []
    # The retry prompt fed back the failing gate output AND carried the honesty rule; the first
    # WU-Y attempt ran blind.
    y_prompts = executor.prompts_by_unit["WU-Y"]
    assert "BOOM-FANOUT-GATE" not in y_prompts[0]
    assert "BOOM-FANOUT-GATE" in y_prompts[1]
    assert "weaken" in y_prompts[1].lower()

    # Both units recovered onto the one combined branch -> exactly one combined PR.
    assert len(_pr_creates(gh)) == 1
    assert len(gh.push_logs) == 1
    assert "wu-x" in gh.push_logs[0] and "wu-y" in gh.push_logs[0]
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/7")
    saver.conn.close()


def test_fanout_unit_exhausts_retries_then_level_halts(tmp_path):
    # A fan-out level where WU-Y's gate fails on EVERY attempt: base + one same-model retry both
    # fail, retries are spent, and the whole level halts with nothing cherry-picked and no PR.
    repo = _target_repo(tmp_path)
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/8")
    gate = FlakyUnitGate(unit_substr="wu-y", fail_times=99)  # WU-Y never passes
    limits = LimitsConfig(max_fix_attempts=1)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="fan-exhaust")

    # WU-Y built base + exactly one same-model retry, then halts. No escalation in the fan-out.
    assert executor.implement_calls.count("WU-Y") == 2
    assert executor.escalate_calls == []
    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    assert _pr_creates(gh) == []  # no PR
    assert _git_pushes(gh) == []  # nothing cherry-picked nor pushed
    assert any("WU-Y" in e["message"] for e in final.values["errors"])  # the failed unit named
    saver.conn.close()


def test_fanout_cost_events_capture_each_unit_and_retry_spend(tmp_path):
    # The run ledger (cost_events) must include EVERY fan-out implement attempt's spend, so the
    # cost report/metrics don't under-count a fan-out level. WU-Y fails once then passes on the
    # same-model retry (-> two implement events); WU-X passes first try (-> one).
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(cost_usd=0.02)
    gh = _recording_gh("https://github.com/owner/demo/pull/12")
    gate = FlakyUnitGate(unit_substr="wu-y", fail_times=1)
    limits = LimitsConfig(max_fix_attempts=1)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="fan-ledger")
    assert final.values["status"] == Status.DONE

    events = final.values["cost_events"]
    implement_events = [e for e in events if e["node"] == "implement"]
    by_unit = Counter(e["unit_id"] for e in implement_events)
    # Each unit's spend landed in the ledger: WU-X built once, WU-Y twice (base + retry). The
    # retry's extra spend — previously dropped on the fan-out path — is now counted.
    assert by_unit == {"WU-X": 1, "WU-Y": 2}
    # Exactly one plan event, and nothing double-counted: 3 implement events for 3 attempts (no
    # re-adding of the pre-level baseline threaded in for the budget check).
    assert sum(1 for e in events if e["node"] == "plan") == 1
    assert len(implement_events) == 3
    assert all(e["cost_usd"] == 0.02 for e in implement_events)
    saver.conn.close()


def test_fanout_cost_cap_blocks_retry_and_level_halts(tmp_path):
    # The cost cap is below even one attempt's spend, so WU-Y's gate failure halts WITHOUT a
    # same-model retry — the budget is honoured inside the fan-out worker, not just sequentially.
    repo = _target_repo(tmp_path)
    executor = FakeExecutor(cost_usd=0.01)
    gh = _recording_gh("https://github.com/owner/demo/pull/9")
    gate = FlakyUnitGate(unit_substr="wu-y", fail_times=99)  # WU-Y fails; the cap blocks recovery
    # Retries allowed in principle, but the ceiling is below the plan + base attempt's spend.
    limits = LimitsConfig(max_fix_attempts=3, max_run_cost_usd=0.005)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate, limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="fan-capped")

    # The cap blocks recovery: WU-Y's base attempt only, no fix retry, no escalation.
    assert executor.implement_calls.count("WU-Y") == 1
    assert executor.escalate_calls == []
    assert final.values["status"] == Status.HALTED
    # The halt is labelled a budget halt, not a plain repeated failure.
    messages = " ".join(e["message"] for e in final.values.get("errors", []))
    assert "cost cap" in messages
    assert _pr_creates(gh) == []
    saver.conn.close()
