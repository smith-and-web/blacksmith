"""Sequential multi-unit execution (WU-MULTI-SEQ).

Test contract: a multi-unit PRD runs every unit in topological order
(``blacksmith.planner.execution_order``) on ONE shared worktree/branch, gates each,
and on all-pass opens exactly ONE combined PR naming every unit built. A failed gate
halts the run (status HALTED, naming the failed unit) and opens NO PR. A single-unit
PRD is unchanged: plan -> implement -> gate -> one PR.

Driven through the real CLI ``drive`` loop and the real graph, with the executor and
``gh`` mocked but a REAL worktree manager. The gate is either the real ``run_gate``
(over a ``true``/``false`` stand-in command) or an injected fake that fails a chosen
unit, so we can exercise a pass-then-fail sequence deterministically.
"""

import re
import subprocess
from pathlib import Path

from blacksmith.cli import drive
from blacksmith.executor import ExecutorResult
from blacksmith.gate import GateResult, run_gate
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
    """Records implement order, the cwd used, and the files visible at implement time.

    Writing each unit's file (committed by the real implement node) lets a later unit's
    implement step observe an earlier unit's committed work — proving the shared worktree.
    """

    def __init__(self):
        self.implemented: list[str] = []
        self.cwds: list[str] = []
        self.seen: dict[str, list[str]] = {}

    def run_plan(self, prompt, **kwargs):
        return _result("1. do the thing")

    def run_implement(self, prompt, **kwargs):
        cwd = Path(kwargs["cwd"])
        unit_id = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
        self.cwds.append(str(cwd))
        self.seen[unit_id] = sorted(p.name for p in cwd.glob("*.txt"))
        (cwd / f"{unit_id.lower()}.txt").write_text(f"impl {unit_id}\n")
        self.implemented.append(unit_id)
        return _result("done")


class FakeGate:
    """Gate that passes every unit except the ``fail_on``-th call (1-indexed)."""

    def __init__(self, fail_on: int):
        self.fail_on = fail_on
        self.calls = 0

    def __call__(self, worktree_path, layer):
        self.calls += 1
        passed = self.calls != self.fail_on
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


def _pr_body(gh):
    create = _pr_creates(gh)[0]
    return create[create.index("--body") + 1]


def _recording_approver(decision=True):
    def approve(payload, values):
        approve.gates.append(payload.get("gate"))
        return decision

    approve.gates = []
    return approve


def _target_repo(tmp_path, gate_cmd):
    repo = tmp_path / "target"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "README.md").write_text("x\n")
    (repo / "blacksmith.toml").write_text(f'test_cmd = "{gate_cmd}"\n')
    g("add", "-A")
    g("commit", "-m", "init")
    return repo


def _wire(tmp_path, repo, *, executor, gh, gate=run_gate):
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


def test_two_units_run_in_topo_order_on_one_branch_and_open_one_pr(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _TWO_UNITS), approver=approver, thread_id="multi")

    # (1) BOTH units built, in topological order (A before B, since B depends_on A).
    assert executor.implemented == ["WU-A", "WU-B"]
    # ONE shared worktree/branch: both implement steps ran in the same directory...
    assert len(set(executor.cwds)) == 1
    # ...and B's implement step saw A's committed file (A ran & committed first).
    assert executor.seen["WU-A"] == []
    assert "wu-a.txt" in executor.seen["WU-B"]

    # (2) exactly ONE combined PR, whose body names every unit built.
    assert len(_pr_creates(gh)) == 1
    body = _pr_body(gh)
    assert "WU-A" in body and "WU-B" in body
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/7")
    # One plan approval and one PR approval — the run is gated, not the per-unit loop.
    assert approver.gates == ["plan", "pr"]
    saver.conn.close()


def test_gate_failure_halts_naming_the_unit_and_opens_no_pr(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="true")  # unused: a fake gate is injected
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/9")
    gate = FakeGate(fail_on=2)  # WU-A passes, WU-B fails
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _TWO_UNITS), approver=approver, thread_id="halt")

    # (3) the failed unit halts the run and is named; no PR is opened.
    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    assert _pr_creates(gh) == []
    assert any("WU-B" in e["message"] for e in final.values["errors"])
    # Both units were built (A then B) before B's gate failed — proving the sequence ran.
    assert executor.implemented == ["WU-A", "WU-B"]
    assert approver.gates == ["plan"]  # never reached the PR-approval gate
    saver.conn.close()


def test_single_unit_prd_is_unchanged_and_opens_one_pr(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="true")
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/1")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _ONE_UNIT), approver=approver, thread_id="single")

    # (4) no regression: plan -> implement -> gate -> exactly one PR.
    assert executor.implemented == ["WU-S"]
    assert len(_pr_creates(gh)) == 1
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/1")
    assert approver.gates == ["plan", "pr"]
    saver.conn.close()


def _chain_units(n: int) -> str:
    """Build YAML for ``n`` units forming a dependency chain WU-01 -> WU-02 -> ...

    Each unit depends on its predecessor, so ``execution_order`` is fully determined and
    the run exercises ``n`` sequential implement->gate super-steps on one branch.
    """
    ids = [f"WU-{i:02d}" for i in range(1, n + 1)]
    blocks = []
    for idx, uid in enumerate(ids):
        dep = f"[{ids[idx - 1]}]" if idx else "[]"
        blocks.append(
            f'  - id: {uid}\n'
            f'    title: "unit {idx + 1}"\n'
            f'    layers: [py-logic]\n'
            f'    target_modules: ["{uid.lower()}.txt"]\n'
            f'    test_contract: "the gate command passes"\n'
            f'    depends_on: {dep}'
        )
    return "\n".join(blocks)


def test_twelve_units_run_to_one_pr_without_recursion_error(tmp_path):
    # A 12-unit chain costs ~36 LangGraph super-steps — well past the default-25 ceiling
    # that would raise GraphRecursionError. The raised recursion_limit lets it complete.
    ids = [f"WU-{i:02d}" for i in range(1, 13)]
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/42")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(
        graph, _write_prd(tmp_path, _chain_units(12)), approver=approver, thread_id="twelve"
    )

    # All 12 units built, in dependency order, on one shared branch to a single PR.
    assert executor.implemented == ids
    assert len(set(executor.cwds)) == 1
    assert len(_pr_creates(gh)) == 1
    body = _pr_body(gh)
    assert all(uid in body for uid in ids)
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/42")
    assert approver.gates == ["plan", "pr"]
    saver.conn.close()
