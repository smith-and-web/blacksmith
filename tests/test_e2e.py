"""End-to-end wiring tests (WU-11).

Test contract (PRD §6, WU-11): e2e run on a trivial unit — pass -> PR-approval halt;
fail -> human_halt. Driven through the real CLI drive loop with the model + gh mocked
but a REAL worktree, REAL test gate (true/false stand-in commands), and the real
graph. Also covers AC-1 (a non-conforming PRD halts at ingest with a field-level error).
"""

import subprocess
from pathlib import Path

from blacksmith.cli import drive
from blacksmith.executor import ExecutorResult
from blacksmith.gate import run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

PRD = """\
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
  - id: WU-E1
    title: "trivial unit"
    layers: [py-logic]
    target_modules: ["out.txt"]
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


class FakeExecutor:
    def run_plan(self, prompt, **kwargs):
        return _result("1. write out.txt")

    def run_implement(self, prompt, **kwargs):
        Path(kwargs["cwd"], "out.txt").write_text("implemented\n")  # simulate the edit
        return _result("done")


def _result(text):
    return ExecutorResult(
        text=text, model="claude-opus-4-8", is_error=False, num_turns=1,
        cost_usd=0.01, usage={}, session_id="s",
    )


def _fake_gh(url):
    def run(argv, cwd=None):
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")  # git push etc. succeed without a real remote

    return run


def _recording_approver(decision=True):
    gates: list[str] = []

    def approve(payload, values):
        gates.append(payload.get("gate"))
        return decision

    approve.gates = gates
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


def _wire(tmp_path, repo, pr_url="https://github.com/owner/demo/pull/1"):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=FakeExecutor(),
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=run_gate,
        pr_runner=_fake_gh(pr_url),
    )
    return graph, saver


def _write_prd(tmp_path, text=PRD):
    path = tmp_path / "prd.md"
    path.write_text(text)
    return path


def test_e2e_pass_halts_at_pr_then_opens_pr(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="true")  # gate passes
    graph, saver = _wire(tmp_path, repo)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path), approver=approver, thread_id="pass")

    assert approver.gates == ["plan", "pr"]  # AC-5: pass routed through the PR-approval gate
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"] == "https://github.com/owner/demo/pull/1"
    saver.conn.close()


def test_e2e_fail_routes_to_human_halt(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="false")  # gate fails
    graph, saver = _wire(tmp_path, repo)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path), approver=approver, thread_id="fail")

    assert approver.gates == ["plan"]  # AC-6: never reaches the PR gate
    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    saver.conn.close()


def test_e2e_nonconforming_prd_halts_at_ingest(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="true")
    graph, saver = _wire(tmp_path, repo)
    approver = _recording_approver(decision=True)
    bad_prd = _write_prd(tmp_path, text=PRD.replace('  - id: WU-E1', '  - id: WU-E1\n    bogus: 1'))

    final = drive(graph, bad_prd, approver=approver, thread_id="bad")

    assert approver.gates == []  # AC-1: rejected before any gate
    assert final.values["status"] == Status.HALTED
    assert any(e["node"] == "ingest_prd" for e in final.values["errors"])
    saver.conn.close()
