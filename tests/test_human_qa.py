"""Tests for the human-gated QA path (WU-QA-STATUS + WU-QA-WIRING).

A human-GATED unit that implemented successfully parks its work behind a draft PR for
manual QA. That outcome needs its own terminal status, distinct from HALTED
(failure/rejection, work discarded) and DONE (fully approved); the status must also
survive checkpoint persistence, i.e. round-trip through ``blacksmith_serde``
(WU-QA-STATUS).

WU-QA-WIRING then wires that into the real graph: a PRD whose unit declares a ``human``
layer, run end-to-end with a mocked executor/``gh`` but a REAL worktree manager,
(1) opens a DRAFT PR for the shared branch (``gh`` argv contains ``--draft``),
(2) ends with status AWAITING_QA (not HALTED),
(3) preserves the branch (cleanup does NOT delete it), and
(4) never runs the automated ``test_gate`` for that unit.

Regression-guarded: a genuine gate FAILURE or a REJECTED approval STILL routes to
``human_halt`` -> HALTED with the branch deleted (work discarded) — only a human-gated
unit that implemented successfully gets the new draft-PR / AWAITING_QA treatment.
"""

import logging
import subprocess
from pathlib import Path

from blacksmith.cli import drive
from blacksmith.executor import ExecutorResult
from blacksmith.gate import GateResult, run_gate
from blacksmith.graph import blacksmith_serde, build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status
from blacksmith.worktree import WorktreeManager

SERDE_LOGGER = "langgraph.checkpoint.serde.jsonplus"


def _serde_warnings(caplog):
    return [
        r for r in caplog.records if "unregistered" in r.message or "will be blocked" in r.message
    ]


def test_awaiting_qa_is_a_distinct_terminal_status():
    # Exists as a member...
    assert hasattr(Status, "AWAITING_QA")
    # ...and is a genuinely distinct terminal value, not an alias of the other terminals.
    assert Status.AWAITING_QA is not Status.HALTED
    assert Status.AWAITING_QA is not Status.DONE
    assert Status.AWAITING_QA != Status.HALTED
    assert Status.AWAITING_QA != Status.DONE
    assert Status.AWAITING_QA.value == "awaiting_qa"


def test_awaiting_qa_round_trips_through_checkpointer_serde(caplog):
    state = {"status": Status.AWAITING_QA}
    serde = blacksmith_serde()
    with caplog.at_level(logging.WARNING, logger=SERDE_LOGGER):
        restored = serde.loads_typed(serde.dumps_typed(state))

    assert restored["status"] == Status.AWAITING_QA
    assert restored["status"] is Status.AWAITING_QA
    assert _serde_warnings(caplog) == []


# --- WU-QA-WIRING: end-to-end through the real graph -------------------------

_PRD_TEMPLATE = """\
---
contract_version: 1
component: demo
version: v0
primary_target_repo: owner/demo
layers:
{layers}
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

_HUMAN = {
    "layers": "  qa: human",
    "units": """\
  - id: WU-QA
    title: "human-gated unit"
    layers: [qa]
    target_modules: ["out.txt"]
    test_contract: "a human QAs the draft PR"
    depends_on: []""",
}

_AUTO = {
    "layers": "  py-logic: auto",
    "units": """\
  - id: WU-AUTO
    title: "auto-gated unit"
    layers: [py-logic]
    target_modules: ["out.txt"]
    test_contract: "the gate command passes"
    depends_on: []""",
}


# A 3-unit chain: WU-A (auto) -> WU-B (human) -> WU-C (auto, depends on WU-B). The run
# builds WU-A, then WU-B, and opens a DRAFT PR at WU-B — WU-C is never reached.
_THREE = {
    "layers": "  py-logic: auto\n  qa: human",
    "units": """\
  - id: WU-A
    title: "auto unit A"
    layers: [py-logic]
    target_modules: ["out.txt"]
    test_contract: "the gate command passes"
    depends_on: []
  - id: WU-B
    title: "human-gated unit B"
    layers: [qa]
    target_modules: ["out.txt"]
    test_contract: "a human QAs the draft PR"
    depends_on: [WU-A]
  - id: WU-C
    title: "auto unit C"
    layers: [py-logic]
    target_modules: ["out.txt"]
    test_contract: "the gate command passes"
    depends_on: [WU-B]""",
}


def _result(text):
    return ExecutorResult(
        text=text, model="claude-opus-4-8", is_error=False, num_turns=1,
        cost_usd=0.01, usage={}, session_id="s",
    )


class FakeExecutor:
    def run_plan(self, prompt, **kwargs):
        return _result("1. write out.txt")

    def run_implement(self, prompt, **kwargs):
        Path(kwargs["cwd"], "out.txt").write_text("implemented\n")  # simulate the edit
        return _result("done")


class MultiExecutor:
    """Like ``FakeExecutor`` but writes a DISTINCT file per implement call, so each unit in
    a multi-unit chain produces a real (non-empty) diff to commit."""

    def __init__(self):
        self.n = 0

    def run_plan(self, prompt, **kwargs):
        return _result("1. write files")

    def run_implement(self, prompt, **kwargs):
        self.n += 1
        Path(kwargs["cwd"], f"out{self.n}.txt").write_text("implemented\n")
        return _result("done")


class CodeThenQAExecutor:
    """Realistic split: writes code for an auto unit but NOTHING for a manual-QA unit (a
    'verify it on a real machine' unit has no diff to produce). Records every implement
    prompt so a test can assert the human-gated unit's implement was skipped entirely —
    the regression guard for the empty-diff halt that used to tear the whole run down."""

    def __init__(self):
        self.implement_prompts = []

    def run_plan(self, prompt, **kwargs):
        return _result("1. write out.txt")

    def run_implement(self, prompt, **kwargs):
        self.implement_prompts.append(prompt)
        if "Layers: qa" in prompt:  # a real implementer produces no diff for manual QA
            return _result("nothing to implement — this is a manual QA step")
        Path(kwargs["cwd"], "out.txt").write_text("implemented\n")  # the auto unit's code
        return _result("done")


class RecordingGate:
    """Gate stand-in that records its calls — the human path must never invoke it."""

    def __init__(self):
        self.calls = 0

    def __call__(self, worktree_path, layer):
        self.calls += 1
        return GateResult(passed=True, output="ok", command="pytest")


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


def _approver(*, reject=()):
    """Approve every gate except those named in ``reject``; record the gate order."""
    def approve(payload, values):
        gate = payload.get("gate")
        approve.gates.append(gate)
        return gate not in reject

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


def _branches(repo, branch):
    out = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", branch],
        capture_output=True, text=True,
    ).stdout
    return out.strip()


def _wire(tmp_path, repo, *, gh, gate=run_gate, executor=None):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor or FakeExecutor(),
        worktree_manager=WorktreeManager(repo, base_dir=tmp_path / "wt"),
        gate=gate,
        pr_runner=gh,
    )
    return graph, saver


def _write_prd(tmp_path, spec):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(**spec))
    return path


def test_human_gated_unit_opens_draft_pr_and_awaits_qa(tmp_path):
    # gate_cmd="false" would FAIL if the automated gate ever ran — it must not.
    repo = _target_repo(tmp_path, gate_cmd="false")
    gh = _recording_gh("https://github.com/owner/demo/pull/5")
    gate = RecordingGate()
    graph, saver = _wire(tmp_path, repo, gh=gh, gate=gate)
    approver = _approver()

    final = drive(graph, _write_prd(tmp_path, _HUMAN), approver=approver, thread_id="qa")

    # (1) a DRAFT PR was opened for the shared branch.
    creates = _pr_creates(gh)
    assert len(creates) == 1
    assert "--draft" in creates[0]
    # (2) the run ends AWAITING_QA, not HALTED.
    assert final.values["status"] == Status.AWAITING_QA
    assert final.values["pr_url"].endswith("/pull/5")
    # (3) the branch is preserved — the draft PR needs it.
    branch = final.values["branch"]
    assert branch and branch in _branches(repo, branch)
    # (4) the automated test_gate never ran for the human-gated unit.
    assert gate.calls == 0
    # Only the plan gate is consulted — the QA happens on the draft PR itself.
    assert approver.gates == ["plan"]
    saver.conn.close()


def _create_arg(create, flag):
    return create[create.index(flag) + 1]


def test_mid_dag_draft_pr_describes_only_units_built(tmp_path):
    # gate_cmd="true" so WU-A's auto gate passes; WU-B (human) never runs the gate.
    repo = _target_repo(tmp_path, gate_cmd="true")
    gh = _recording_gh("https://github.com/owner/demo/pull/8")
    graph, saver = _wire(tmp_path, repo, gh=gh, executor=MultiExecutor())
    approver = _approver()

    final = drive(graph, _write_prd(tmp_path, _THREE), approver=approver, thread_id="mid")

    # The run parked WU-B behind a draft PR; WU-C was never built.
    assert final.values["status"] == Status.AWAITING_QA
    creates = _pr_creates(gh)
    assert len(creates) == 1
    assert "--draft" in creates[0]

    title = _create_arg(creates[0], "--title")
    body = _create_arg(creates[0], "--body")
    # The PR names only the units actually built/committed (WU-A and WU-B)...
    assert "WU-A" in title and "WU-A" in body
    assert "WU-B" in title and "WU-B" in body
    # ...and never the never-built WU-C.
    assert "WU-C" not in title
    assert "WU-C" not in body
    saver.conn.close()


def test_gate_failure_still_halts_and_deletes_branch(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="false")  # genuine gate failure
    gh = _recording_gh("https://github.com/owner/demo/pull/6")
    graph, saver = _wire(tmp_path, repo, gh=gh)  # real run_gate over "false"
    approver = _approver()

    final = drive(graph, _write_prd(tmp_path, _AUTO), approver=approver, thread_id="fail")

    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    assert _pr_creates(gh) == []  # no PR — not even a draft
    # the work is discarded: the shared branch is deleted by cleanup.
    branch = final.values["branch"]
    assert branch and _branches(repo, branch) == ""
    assert approver.gates == ["plan"]  # never reached the PR-approval gate
    saver.conn.close()


def test_qa_only_unit_with_no_diff_still_opens_draft_pr(tmp_path):
    """The real-world failure that tore runs down: an auto unit builds the code, then a
    dependent manual-QA unit produces NO diff (there is nothing to implement). The run must
    still open a draft PR for the auto unit's work and end AWAITING_QA — not halt on
    "implement produced no file changes". The QA unit's implement is skipped entirely
    (no executor call, no wasted spend, no risk of re-editing the committed code)."""
    # gate_cmd="false" would FAIL if the auto gate ever ran for the QA unit — it must not.
    # WU-CODE's own gate runs on the real run_gate, so use a passing command for it.
    repo = _target_repo(tmp_path, gate_cmd="true")
    gh = _recording_gh("https://github.com/owner/demo/pull/9")
    executor = CodeThenQAExecutor()
    graph, saver = _wire(tmp_path, repo, gh=gh, executor=executor)
    approver = _approver()

    final = drive(graph, _write_prd(tmp_path, _THREE), approver=approver, thread_id="qaonly")

    # The run parked the human-gated WU-B behind a draft PR with WU-A's committed code.
    assert final.values["status"] == Status.AWAITING_QA, final.values.get("errors")
    creates = _pr_creates(gh)
    assert len(creates) == 1 and "--draft" in creates[0]
    body = _create_arg(creates[0], "--body")
    assert "WU-A" in body and "WU-B" in body  # auto code + the QA unit being verified
    # The human-gated unit never reached the executor: no implement prompt for a qa layer.
    assert executor.implement_prompts  # the auto unit WAS implemented
    assert not any("Layers: qa" in p for p in executor.implement_prompts)
    # The branch is preserved for the draft PR.
    branch = final.values["branch"]
    assert branch and branch in _branches(repo, branch)
    assert approver.gates == ["plan"]  # QA happens on the draft PR, not via an approval gate
    saver.conn.close()


def test_rejected_pr_approval_still_halts_and_deletes_branch(tmp_path):
    repo = _target_repo(tmp_path, gate_cmd="true")  # gate passes -> reaches the PR gate
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    graph, saver = _wire(tmp_path, repo, gh=gh)
    approver = _approver(reject=("pr",))  # approve plan, REJECT the PR

    final = drive(graph, _write_prd(tmp_path, _AUTO), approver=approver, thread_id="reject")

    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    assert _pr_creates(gh) == []  # rejection opens no PR
    branch = final.values["branch"]
    assert branch and _branches(repo, branch) == ""  # branch deleted, work discarded
    assert approver.gates == ["plan", "pr"]
    saver.conn.close()
