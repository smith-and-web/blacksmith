"""Gate-failure lessons recorded per repo and surfaced to the planner (WU-MEMORY-LESSONS).

Test contract (driven at the COMPILED-GRAPH level with a real tmp ``SqliteStore``,
mirroring ``test_plan_node_wired_into_graph``):

1. Drive a run to a gate failure (the path that halts) under repo R and assert a lesson
   record exists in the Store under R's namespace.
2. Compile a fresh graph for repo R with the same Store and assert the next plan's system
   prompt contains the stored lesson text.
3. A repo with no stored lessons, and a store-less compile, produce the SAME plan system
   prompt as before this unit (memory is purely additive — empty/absent memory is a no-op).
"""

import subprocess
from pathlib import Path

from blacksmith.cli import drive
from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.gate import GateResult
from blacksmith.graph import _will_escalate, build_checkpointer, compile_graph
from blacksmith.memory import build_store, recent_lessons, record_lesson, repo_namespace
from blacksmith.nodes.plan import _system_prompt
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
  - id: WU-S
    title: "solo unit"
    layers: [py-logic]
    target_modules: ["wu-s.txt"]
    test_contract: "the gate command passes"
    depends_on: []
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


# A SECOND, DIFFERENT PRD that targets the SAME repo (owner/demo) and reuses unit-id
# WU-S, but declares an extra unit (WU-T) — so its set of unit-ids differs. This is the
# cross-PRD case the per-PRD discriminator must keep from clobbering.
_PRD_VARIANT = """\
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
  - id: WU-S
    title: "solo unit"
    layers: [py-logic]
    target_modules: ["wu-s.txt"]
    test_contract: "the gate command passes"
    depends_on: []
  - id: WU-T
    title: "second unit"
    layers: [py-logic]
    target_modules: ["wu-t.txt"]
    test_contract: "the gate command passes"
    depends_on: []
---
# Demo PRD (variant)

## 1. Purpose
demo.

## 2. Scope fences
demo.

## 7. Untouchables
none.

## 10. Acceptance criteria
done.
"""


def _write_prd(tmp_path) -> Path:
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE)
    return path


def _lesson(reason: str) -> dict:
    return {
        "unit_id": "WU-S",
        "title": "solo unit",
        "reason": reason,
        "files_touched": [],
    }


def _result(text="done"):
    return ExecutorResult(
        text=text, model="claude-sonnet-4-6", is_error=False, num_turns=1,
        cost_usd=0.01, usage={}, session_id="s",
    )


class RecordingExecutor:
    """Fake executor that records the system prompt each ``run_plan`` saw, and writes the
    unit's file on implement (so the real implement node can commit it)."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_plan(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return _result("1. do the thing")

    def run_implement(self, prompt, **kwargs):
        cwd = Path(kwargs["cwd"])
        (cwd / "wu-s.txt").write_text("work\n")
        return _result()

    def run_implement_escalate(self, prompt, **kwargs):
        cwd = Path(kwargs["cwd"])
        (cwd / "wu-s.txt").write_text("escalated\n")
        return _result()


class FailingGate:
    """A gate that fails every call — so the unit fails, escalates once, then halts."""

    def __call__(self, worktree_path, layer):
        return GateResult(
            passed=False,
            output="boom: the gate command exited non-zero",
            command="pytest",
        )


def _recording_gh(url="https://github.com/owner/demo/pull/1"):
    def run(argv, cwd=None):
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")

    return run


def _approver(decision=True):
    def approve(payload, values):
        return decision

    return approve


def _target_repo(tmp_path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    g("add", "-A")
    g("commit", "-m", "init")
    return repo


def _contract(prd_path):
    return parse_prd(prd_path).contract


# --- (1) a halting gate failure writes a lesson under the repo namespace ------


def test_gate_failure_records_lesson_under_repo_namespace(tmp_path):
    repo = _target_repo(tmp_path)
    prd = _write_prd(tmp_path)
    store = build_store(tmp_path / "store.sqlite")
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=RecordingExecutor(),
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=FailingGate(),
        pr_runner=_recording_gh(),
        store=store,
    )

    final = drive(graph, prd, approver=_approver(True), thread_id="fail")
    assert final.values["status"] == Status.HALTED  # both attempts failed -> halt, no PR

    lessons = recent_lessons(store, repo_namespace(_contract(prd)), limit=10)
    assert any(lesson["unit_id"] == "WU-S" for lesson in lessons)
    lesson = next(lesson for lesson in lessons if lesson["unit_id"] == "WU-S")
    assert lesson["title"] == "solo unit"
    assert "boom" in lesson["reason"]
    assert "files_touched" in lesson
    saver.conn.close()
    store.conn.close()


# --- (2) the next plan surfaces the stored lesson in its system prompt --------


def _run_plan_capturing_system_prompt(tmp_path, prd, store, *, thread_id):
    saver = build_checkpointer(tmp_path / f"ckpt-{thread_id}.sqlite")
    executor = RecordingExecutor()
    graph = compile_graph(saver, executor=executor, store=store)
    cfg = {"configurable": {"thread_id": thread_id}}
    graph.invoke({"prd": parse_prd(prd)}, cfg)  # runs plan, then pauses at approve_plan
    assert graph.get_state(cfg).next == ("approve_plan",)
    saver.conn.close()
    return executor.calls[0]["system_prompt"]


def test_plan_surfaces_stored_lesson(tmp_path):
    prd = _write_prd(tmp_path)
    store = build_store(tmp_path / "store.sqlite")
    record_lesson(
        store,
        _contract(prd),
        {
            "unit_id": "WU-S",
            "title": "solo unit",
            "reason": "boom: the gate command exited non-zero",
            "files_touched": ["wu-s.txt"],
        },
    )

    system_prompt = _run_plan_capturing_system_prompt(tmp_path, prd, store, thread_id="surface")
    assert "PRIOR LESSONS ON THIS REPO" in system_prompt
    assert "WU-S" in system_prompt
    assert "boom: the gate command exited non-zero" in system_prompt
    store.conn.close()


# --- (3) no lessons / no store -> identical to the pre-unit system prompt -----


def test_empty_store_and_no_store_match_pre_unit_prompt(tmp_path):
    prd = _write_prd(tmp_path)
    baseline = _system_prompt(_contract(prd))  # the system prompt as it was before this unit

    empty_store = build_store(tmp_path / "store.sqlite")
    with_empty = _run_plan_capturing_system_prompt(
        tmp_path, prd, empty_store, thread_id="empty"
    )
    without = _run_plan_capturing_system_prompt(tmp_path, prd, None, thread_id="none")

    assert with_empty == baseline  # empty store -> no lessons section
    assert without == baseline  # store-less compile -> no lessons section
    assert "PRIOR LESSONS" not in with_empty
    empty_store.conn.close()


# --- (4) lessons are scoped per PRD: no cross-PRD clobber on a shared repo -----


def test_distinct_prds_sharing_unit_id_do_not_clobber(tmp_path):
    store = build_store(tmp_path / "store.sqlite")
    prd_a = tmp_path / "a.md"
    prd_a.write_text(_PRD_TEMPLATE)
    prd_b = tmp_path / "b.md"
    prd_b.write_text(_PRD_VARIANT)
    contract_a = _contract(prd_a)
    contract_b = _contract(prd_b)

    # Same target repo (same namespace), and both reuse unit-id WU-S.
    assert repo_namespace(contract_a) == repo_namespace(contract_b)

    record_lesson(store, contract_a, _lesson("boom A"))
    record_lesson(store, contract_b, _lesson("boom B"))

    lessons = recent_lessons(store, repo_namespace(contract_a), limit=10)
    wu_s = [lesson for lesson in lessons if lesson["unit_id"] == "WU-S"]
    assert len(wu_s) == 2  # the second PRD did NOT overwrite the first
    assert {lesson["reason"] for lesson in wu_s} == {"boom A", "boom B"}
    store.conn.close()


def test_same_prd_rerun_overwrites_by_unit_id(tmp_path):
    store = build_store(tmp_path / "store.sqlite")
    prd = _write_prd(tmp_path)
    contract = _contract(prd)

    record_lesson(store, contract, _lesson("first attempt"))
    record_lesson(store, contract, _lesson("second attempt"))  # same PRD + unit -> overwrite

    lessons = recent_lessons(store, repo_namespace(contract), limit=10)
    wu_s = [lesson for lesson in lessons if lesson["unit_id"] == "WU-S"]
    assert len(wu_s) == 1  # de-duped by unit-id as today
    assert wu_s[0]["reason"] == "second attempt"  # latest failure wins
    store.conn.close()


# --- (5) the shared escalation helper is a pure refactor of the inline checks --


def test_will_escalate_matches_the_inline_condition():
    # ``pre_implement_ref and not escalated`` — the exact boolean both call sites used.
    assert _will_escalate({"pre_implement_ref": "abc123"}) is True
    assert _will_escalate({"pre_implement_ref": "abc123", "escalated": False}) is True
    assert _will_escalate({"pre_implement_ref": "abc123", "escalated": True}) is False
    assert _will_escalate({"escalated": False}) is False  # no ref -> cannot escalate
    assert _will_escalate({}) is False
