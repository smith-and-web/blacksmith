"""Recoverable implement — continue on a turn cap instead of halting.

Test contract: an implement attempt that exhausts its turn budget is a RECOVERABLE,
budget-shaped failure — the partial work is kept (NOT reset like a gate failure) and the
attempt is CONTINUED with a fresh budget, bounded by ``limits.max_implement_continuations``
and the shared cost cap. A genuine (non-turn-cap) error still halts. The turn budget itself
is configurable via ``limits.max_implement_turns``. The loop is OFF unless the graph is wired
with ``limits`` and a non-zero continuation budget, so every other test keeps its behaviour.

The executor-classification and routing units are pinned directly; the recover / exhaust /
off-by-default flows are driven through the real CLI ``drive`` loop and real graph with a REAL
worktree manager (so "keep the partial work, don't reset" is exercised for real), the executor
and ``gh`` mocked.
"""

import re
import subprocess
from pathlib import Path

from claude_agent_sdk import ResultMessage

from blacksmith.cli import drive
from blacksmith.config import BlacksmithConfig, LimitsConfig
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.gate import GateResult
from blacksmith.graph import (
    build_checkpointer,
    compile_graph,
    prepare_implement_continuation,
    route_after_implement,
)
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


def _ok(text="done", cost=0.01):
    return ExecutorResult(
        text=text, model="claude-sonnet-5", is_error=False, num_turns=3,
        cost_usd=cost, usage={}, session_id="s",
    )


def _capped(session, cost=0.05):
    """A turn-cap failure: is_error with error_kind 'max_turns' and only reasoning text (the
    empty ``result`` an SDK max-turns ResultMessage produces)."""
    return ExecutorResult(
        text="...lots of agent reasoning that must NOT surface as the error...",
        model="claude-sonnet-5", is_error=True, num_turns=40, cost_usd=cost, usage={},
        session_id=session, error_kind="max_turns",
    )


class FakeTurnCapExecutor:
    """``run_implement`` hits the turn cap on the 1-indexed ``cap_calls`` and completes on the
    rest. A capped attempt still leaves PARTIAL work in the worktree (as a real capped agent
    would); a completing attempt writes the unit's real file. Records the prompt, the
    ``max_turns`` budget it was handed, and the files visible at entry (so a continuation can be
    shown to have KEPT the partial work rather than resetting it). Deliberately exposes NO
    ``run_implement_escalate`` — continuation is independent of the gate-failure escalation."""

    def __init__(self, cap_calls):
        self.cap_calls = set(cap_calls)
        self.calls = 0
        self.prompts: list[str] = []
        self.max_turns_seen: list[int] = []
        self.files_at_entry: dict[int, list[str]] = {}

    def run_plan(self, prompt, **kwargs):
        return _ok("1. plan")

    def run_implement(self, prompt, **kwargs):
        self.calls += 1
        n = self.calls
        cwd = Path(kwargs["cwd"])
        self.prompts.append(prompt)
        self.max_turns_seen.append(kwargs.get("max_turns"))
        self.files_at_entry[n] = sorted(p.name for p in cwd.glob("*.txt"))
        if n in self.cap_calls:
            (cwd / "partial.txt").write_text(f"partial from call {n}\n")  # real partial progress
            return _capped(session=f"cap{n}")
        unit_id = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
        (cwd / f"{unit_id.lower()}.txt").write_text(f"impl {unit_id}\n")
        return _ok()


class FakeGate:
    def __init__(self, fail_calls=()):
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


# -- executor classification -------------------------------------------------------------


class _FakeQuery:
    def __init__(self, messages):
        self.messages = messages

    def __call__(self, *, prompt, options, transport=None):
        return self._gen()

    async def _gen(self):
        for message in self.messages:
            yield message


class _RaisingQuery:
    def __init__(self, exc):
        self.exc = exc

    def __call__(self, *, prompt, options, transport=None):
        return self._gen()

    async def _gen(self):
        raise self.exc
        yield  # pragma: no cover - makes this an async generator


def _result_message(subtype, *, is_error, result=""):
    return ResultMessage(
        subtype=subtype, duration_ms=10, duration_api_ms=8, is_error=is_error, num_turns=40,
        session_id="s1", result=result, total_cost_usd=0.05, usage={},
    )


def test_executor_classifies_result_message_max_turns(monkeypatch):
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    msgs = [_result_message("error_max_turns", is_error=True)]
    executor = Executor(BlacksmithConfig(), query_fn=_FakeQuery(msgs))
    result = executor.run_implement("p", raise_on_error=False)
    assert result.is_error and result.error_kind == "max_turns" and result.hit_turn_limit


def test_executor_classifies_other_result_message_error(monkeypatch):
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    msgs = [_result_message("error_during_execution", is_error=True)]
    executor = Executor(BlacksmithConfig(), query_fn=_FakeQuery(msgs))
    result = executor.run_implement("p", raise_on_error=False)
    assert result.is_error and result.error_kind == "other" and not result.hit_turn_limit


def test_executor_success_has_no_error_kind(monkeypatch):
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    msgs = [_result_message("success", is_error=False, result="ok")]
    executor = Executor(BlacksmithConfig(), query_fn=_FakeQuery(msgs))
    result = executor.run_implement("p", raise_on_error=False)
    assert not result.is_error and result.error_kind is None and not result.hit_turn_limit


def test_executor_classifies_raised_max_turns(monkeypatch):
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    query = _RaisingQuery(RuntimeError("Reached maximum number of turns"))
    result = Executor(BlacksmithConfig(), query_fn=query).run_implement("p", raise_on_error=False)
    assert result.is_error and result.error_kind == "max_turns"


# -- routing + continuation prep ---------------------------------------------------------


def _halted(**extra):
    base = {"status": Status.HALTED, "limits": {"max_implement_continuations": 1}}
    return {**base, **extra}


def test_route_after_implement_continues_on_recoverable_turn_cap():
    state = _halted(implement_error_kind="max_turns", implement_continuations=0)
    assert route_after_implement(state) == "continue_implement"


def test_route_after_implement_halts_when_continuations_exhausted():
    state = _halted(implement_error_kind="max_turns", implement_continuations=1)
    assert route_after_implement(state) == "human_halt"


def test_route_after_implement_halts_on_non_turn_cap_error():
    state = _halted(implement_error_kind="other", implement_continuations=0)
    assert route_after_implement(state) == "human_halt"


def test_route_after_implement_halts_when_loop_disabled():
    # No limits wired (max_implement_continuations reads 0) -> a turn cap halts as before.
    state = {"status": Status.HALTED, "implement_error_kind": "max_turns"}
    assert route_after_implement(state) == "human_halt"


def test_prepare_continuation_keeps_partial_and_bumps_counter():
    out = prepare_implement_continuation({"implement_continuations": 2})
    assert out["implement_continuations"] == 3
    assert out["resume_partial_implement"] is True
    assert out["implement_error_kind"] == ""  # cleared so the next attempt is classified afresh
    assert out["status"] == Status.IMPLEMENTING
    # It returns no worktree-reset side effect — the partial work is kept, unlike fix_retry.


# -- end-to-end --------------------------------------------------------------------------


def test_turn_cap_continues_from_partial_work_then_passes(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeTurnCapExecutor(cap_calls={1})  # first attempt caps; the continuation finishes
    gh = _recording_gh("https://github.com/owner/demo/pull/11")
    limits = LimitsConfig(max_implement_continuations=1, max_implement_turns=25)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=FakeGate(), limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="cont")

    # Two implement calls: the capped base + one continuation that finished.
    assert executor.calls == 2
    # The continuation was told to CONTINUE and it SAW the partial work still on disk (not reset).
    assert "CONTINUATION" in executor.prompts[1]
    assert "partial.txt" in executor.files_at_entry[2]
    # The configurable turn budget was handed to the executor on both attempts.
    assert executor.max_turns_seen == [25, 25]
    assert final.values["status"] == Status.DONE
    assert len(_pr_creates(gh)) == 1
    saver.conn.close()


def test_turn_cap_exhausts_continuations_and_halts_legibly(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeTurnCapExecutor(cap_calls={1, 2})  # base caps, the one continuation caps too
    gh = _recording_gh("https://github.com/owner/demo/pull/12")
    limits = LimitsConfig(max_implement_continuations=1)
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=FakeGate(), limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="cont-exhaust")

    assert executor.calls == 2  # base + exactly one continuation, then halt
    assert final.values["status"] == Status.HALTED
    assert _pr_creates(gh) == []
    # The halt is LEGIBLE: it names the turn budget and the unit, not the agent's reasoning dump.
    message = final.values["errors"][-1]["message"]
    assert "turn budget" in message and "WU-S" in message
    assert "lots of agent reasoning" not in message
    saver.conn.close()


def test_turn_cap_halts_immediately_when_continuation_disabled(tmp_path):
    repo = _target_repo(tmp_path)
    executor = FakeTurnCapExecutor(cap_calls={1})
    gh = _recording_gh("https://github.com/owner/demo/pull/13")
    limits = LimitsConfig(max_implement_continuations=0)  # loop OFF -> prior behaviour
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=FakeGate(), limits=limits)

    final = drive(graph, _write_prd(tmp_path), approver=_approver(), thread_id="cont-off")

    assert executor.calls == 1  # no continuation attempted
    assert final.values["status"] == Status.HALTED
    assert _pr_creates(gh) == []
    saver.conn.close()
