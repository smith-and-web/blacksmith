"""End-to-end wiring tests (WU-11, updated to the clone model in WU-CLONE-WIRING).

Test contract (PRD §6, WU-11): e2e run on a trivial unit — pass -> PR-approval halt;
fail -> human_halt. Driven through the real CLI drive loop with the model + gh mocked
but a REAL local source repo, an isolated CloneManager run, the REAL test gate (true/
false stand-in commands), and the real graph. Also covers AC-1 (a non-conforming PRD
halts at ingest with a field-level error) and the clone isolation guarantee: the run
executes inside a clone whose .git is a real local directory and the SOURCE repo's
working tree is untouched after the run (WU-CLONE-WIRING).
"""

import subprocess
from pathlib import Path

from blacksmith.cli import drive
from blacksmith.executor import ExecutorResult
from blacksmith.gate import run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status
from blacksmith.worktree import CloneManager

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
    """Stand-in implementer that records where it ran, so a test can prove the run
    happened inside the clone (own .git dir) and not the source checkout."""

    def __init__(self):
        self.implement_cwd: Path | None = None
        self.git_is_dir: bool | None = None

    def run_plan(self, prompt, **kwargs):
        return _result("1. write out.txt")

    def run_implement(self, prompt, **kwargs):
        cwd = Path(kwargs["cwd"])
        self.implement_cwd = cwd
        # A clone owns a real .git directory; a linked worktree's .git is a file.
        self.git_is_dir = (cwd / ".git").is_dir()
        (cwd / "out.txt").write_text("implemented\n")  # simulate the edit
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


def _wire(tmp_path, repo, pr_url="https://github.com/owner/demo/pull/1", executor=None):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor or FakeExecutor(),
        # The run is isolated in a throwaway local clone, NOT a linked worktree.
        worktree_manager=CloneManager(repo, base_dir=tmp_path / "clones"),
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


def test_e2e_runs_in_clone_and_leaves_source_untouched(tmp_path):
    """The run executes inside a clone (own local .git) and never touches the source repo
    (WU-CLONE-WIRING): the self-targeting hazard is dead."""
    repo = _target_repo(tmp_path, gate_cmd="true")  # gate passes
    source_head_before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    executor = FakeExecutor()
    graph, saver = _wire(tmp_path, repo, executor=executor)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path), approver=approver, thread_id="clone")

    assert final.values["status"] == Status.DONE
    # The implement step ran inside a clone whose .git is a real local directory...
    assert executor.git_is_dir is True
    # ...located under the clone base dir, NOT in the source repo's working tree.
    assert executor.implement_cwd is not None
    assert executor.implement_cwd.resolve() != repo.resolve()
    assert (tmp_path / "clones").resolve() in executor.implement_cwd.resolve().parents

    # The SOURCE repo's working tree is untouched: the edit never leaked into it, and its
    # HEAD/history did not move (no blacksmith commit landed in the source).
    assert not (repo / "out.txt").exists()
    source_head_after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert source_head_after == source_head_before
    source_log = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "--oneline"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "WU-E1" not in source_log
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
