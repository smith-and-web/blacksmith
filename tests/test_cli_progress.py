"""Per-node progress stream (WU-PROGRESS).

Test contract: drive the real graph (mocked executor / gh, real worktree + gate, the
existing e2e pattern) through an auto-approver while capturing output, and assert that
(1) a concise progress line naming each node is emitted to STDERR in execution order,
(2) stdout still carries the final report unchanged, (3) ``--quiet`` (a ``None`` emitter)
suppresses the progress stream while leaving the report intact, and (4) the happy path
still ends DONE and a denied gate still routes to human_halt.
"""

import subprocess
from pathlib import Path

from blacksmith.cli import _progress_emitter, _report, drive
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

# The worker nodes that should appear, in order, on the happy path.
HAPPY_PATH_NODES = ["ingest_prd", "plan", "implement", "test_gate", "open_pr", "cleanup_worktree"]


class FakeExecutor:
    def run_plan(self, prompt, **kwargs):
        return _result("1. write out.txt")

    def run_implement(self, prompt, **kwargs):
        Path(kwargs["cwd"], "out.txt").write_text("implemented\n")
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
        return CommandResult(0, "", "")

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


def _emitted_nodes(stderr: str) -> list[str]:
    """Parse the node names out of the STDERR progress lines (exact, not substring)."""
    prefix = "blacksmith: "
    return [
        line[len(prefix):].strip()
        for line in stderr.splitlines()
        if line.strip().startswith(prefix)
    ]


def _is_ordered_subsequence(haystack: list[str], needles: list[str]) -> bool:
    it = iter(haystack)
    return all(needle in it for needle in needles)


def test_progress_streams_each_node_to_stderr_in_order(tmp_path, capsys):
    repo = _target_repo(tmp_path, gate_cmd="true")  # gate passes
    graph, saver = _wire(tmp_path, repo)
    approver = _recording_approver(decision=True)

    final = drive(
        graph, _write_prd(tmp_path), approver=approver,
        thread_id="pass", on_node=_progress_emitter(quiet=False),
    )
    _report(final)
    captured = capsys.readouterr()
    saver.conn.close()

    # (4) happy path: still ends DONE, gates flow through plan then pr.
    assert final.values["status"] == Status.DONE
    assert approver.gates == ["plan", "pr"]

    # (1) each node named on STDERR, in execution order.
    nodes = _emitted_nodes(captured.err)
    assert _is_ordered_subsequence(nodes, HAPPY_PATH_NODES), nodes

    # (2) stdout still carries the final report unchanged; no progress leaks to stdout.
    assert "status: done" in captured.out
    assert "PR: https://github.com/owner/demo/pull/1" in captured.out
    assert "blacksmith:" not in captured.out


def test_quiet_suppresses_progress_but_keeps_report(tmp_path, capsys):
    repo = _target_repo(tmp_path, gate_cmd="true")
    graph, saver = _wire(tmp_path, repo)
    approver = _recording_approver(decision=True)

    final = drive(
        graph, _write_prd(tmp_path), approver=approver,
        thread_id="quiet", on_node=_progress_emitter(quiet=True),
    )
    _report(final)
    captured = capsys.readouterr()
    saver.conn.close()

    # (3) no progress stream on STDERR, but the run and report are intact.
    assert _emitted_nodes(captured.err) == []
    assert "blacksmith:" not in captured.err
    assert final.values["status"] == Status.DONE
    assert "PR: https://github.com/owner/demo/pull/1" in captured.out


def test_denied_gate_still_routes_to_human_halt_with_progress(tmp_path, capsys):
    repo = _target_repo(tmp_path, gate_cmd="true")
    graph, saver = _wire(tmp_path, repo)
    approver = _recording_approver(decision=False)  # deny the plan gate

    final = drive(
        graph, _write_prd(tmp_path), approver=approver,
        thread_id="deny", on_node=_progress_emitter(quiet=False),
    )
    capsys.readouterr()
    saver.conn.close()

    # Control flow unchanged: a denied gate halts and never reaches the PR gate.
    assert approver.gates == ["plan"]
    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
