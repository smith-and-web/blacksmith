"""Parallel fan-out of a multi-unit level (WU-PARALLEL-FANOUT).

A level with >1 unit is built concurrently: the engine fans out one ``build_unit`` per
unit via LangGraph ``Send``, each in its OWN clone taken from the combined-branch tip
(``CloneManager.create_from``), and gates it there; a ``join_level`` barrier then
CHERRY-PICKS each passing unit's commit onto the one shared combined branch in
declaration order, so the run still opens exactly ONE combined PR.

Driven hermetically through the real CLI ``drive`` loop and the real graph against a
REAL local source repo, with the executor and ``gh`` mocked but a REAL ``CloneManager``
providing per-unit clone isolation and the REAL ``run_gate`` (over a ``true``/``false``
stand-in command) unless a unit-failing fake gate is injected.

Covers:
(1) two INDEPENDENT disjoint-file units in one level build in DISTINCT clones and BOTH
    land on one combined branch via cherry-pick -> one combined PR;
(2) two same-level units writing CONFLICTING content to the SAME file -> the run HALTS
    naming BOTH units AND the file, with no PR;
(3) a level unit whose test gate FAILS -> the run HALTS, nothing is cherry-picked, no PR;
(4) a dependent level's units build from the prior level's MERGED combined tip;
(5) regression: a single-unit run and an all-size-1-level chain still use the run's ONE
    shared clone (no per-unit clone, no cherry-pick) and behave as before.
"""

import json
import re
import subprocess
from pathlib import Path

from blacksmith import graph as bsgraph
from blacksmith.cli import drive
from blacksmith.config import IndexConfig, LimitsConfig, ReviewConfig
from blacksmith.executor import ExecutorResult
from blacksmith.gate import GateResult, run_gate
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.pr import CommandResult
from blacksmith.sandbox import SandboxConfig, SandboxManager
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


# (1) Two roots, one level, disjoint files -> a parallel fan-out level.
_TWO_INDEPENDENT = "\n".join(
    [_unit("WU-X", "first", "wu-x.txt", []), _unit("WU-Y", "second", "wu-y.txt", [])]
)

# (2) Two roots in one level both writing the SAME file -> a cherry-pick conflict.
_TWO_CONFLICTING = "\n".join(
    [_unit("WU-X", "first", "out.txt", []), _unit("WU-Y", "second", "out.txt", [])]
)

# (4) A size-1 root level, then a size-2 dependent level: A and B must see ROOT's file.
_ROOT_THEN_FANOUT = "\n".join(
    [
        _unit("WU-ROOT", "root", "root.txt", []),
        _unit("WU-A", "left", "a.txt", ["WU-ROOT"]),
        _unit("WU-B", "right", "b.txt", ["WU-ROOT"]),
    ]
)

# (5) An all-size-1-level chain: every level has one unit, so nothing ever fans out.
_CHAIN = "\n".join(
    [
        _unit("WU-1", "one", "wu-1.txt", []),
        _unit("WU-2", "two", "wu-2.txt", ["WU-1"]),
        _unit("WU-3", "three", "wu-3.txt", ["WU-2"]),
    ]
)

_ONE_UNIT = _unit("WU-S", "solo", "wu-s.txt", [])


def _result(text):
    return ExecutorResult(
        text=text, model="claude-opus-4-8", is_error=False, num_turns=1,
        cost_usd=0.01, usage={}, session_id="s",
    )


class FakeExecutor:
    """Records the cwd each unit built in and the files visible at implement time, and
    writes each unit's target module so the gate/cherry-pick has a real diff to work on."""

    def __init__(self):
        self.cwds: dict[str, str] = {}
        self.seen: dict[str, list[str]] = {}
        self.reviewed: list[str] = []  # unit ids the reviewer was run on

    def run_plan(self, prompt, **kwargs):
        return _result("1. do the thing")

    def run_implement(self, prompt, **kwargs):
        cwd = Path(kwargs["cwd"])
        unit_id = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
        target = re.search(r"^Target modules: (.+)$", prompt, re.M).group(1).split(",")[0].strip()
        self.cwds[unit_id] = str(cwd)
        self.seen[unit_id] = sorted(p.name for p in cwd.glob("*.txt"))
        (cwd / target).write_text(f"content from {unit_id}\n")
        return _result("done")

    def run_review(self, prompt, **kwargs):
        # Record which unit's diff was reviewed and return a BLOCKING finding tagged with it,
        # so a test can assert the reviewer actually ran on the fan-out unit and its finding
        # reaches the PR body.
        unit_id = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
        self.reviewed.append(unit_id)
        verdict = {
            "verdict": "needs_changes",
            "findings": [
                {"severity": "blocking", "file": f"{unit_id}.txt",
                 "detail": f"review flagged {unit_id}"}
            ],
        }
        return _result("```json\n" + json.dumps(verdict) + "\n```")


class FailUnitGate:
    """Real-shaped gate that fails for any unit whose clone path contains ``fail_substr``
    (the per-unit build clone is named after the unit), and passes everyone else."""

    def __init__(self, fail_substr: str):
        self.fail_substr = fail_substr

    def __call__(self, worktree_path, layer):
        passed = self.fail_substr not in str(worktree_path)
        return GateResult(passed=passed, output="ok" if passed else "boom", command="pytest")


def _recording_gh(url):
    def run(argv, cwd=None):
        run.calls.append(list(argv))
        if argv[:2] == ["git", "push"] and cwd is not None:
            # Capture the combined branch's history at push time, proving which units'
            # commits actually landed on it (the cherry-pick result).
            log = subprocess.run(
                ["git", "-C", str(cwd), "log", "--format=%s"], capture_output=True, text=True
            ).stdout
            run.push_logs.append(log)
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")  # git push etc. succeed without a real remote

    run.calls = []
    run.push_logs = []
    return run


def _pr_creates(gh):
    return [c for c in gh.calls if c[:3] == ["gh", "pr", "create"]]


def _git_pushes(gh):
    return [c for c in gh.calls if c[:2] == ["git", "push"]]


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
        worktree_manager=CloneManager(repo, base_dir=tmp_path / "clones"),
        gate=gate,
        pr_runner=gh,
    )
    return graph, saver


def _write_prd(tmp_path, units):
    path = tmp_path / "prd.md"
    path.write_text(_PRD_TEMPLATE.format(units=units))
    return path


def _build_clone_dirs(tmp_path):
    base = tmp_path / "clones"
    return sorted(p.name for p in base.glob("*-build")) if base.exists() else []


def test_independent_level_builds_in_distinct_clones_to_one_pr(tmp_path):
    # (1) Two independent units in one level fan out into DISTINCT clones and BOTH land on
    # one combined branch via cherry-pick -> exactly one combined PR.
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _TWO_INDEPENDENT), approver=approver, thread_id="ind")

    # Both units built, each in its OWN per-unit build clone (distinct paths).
    assert set(executor.cwds) == {"WU-X", "WU-Y"}
    assert executor.cwds["WU-X"] != executor.cwds["WU-Y"]
    assert executor.cwds["WU-X"].endswith("-build")
    assert executor.cwds["WU-Y"].endswith("-build")

    # Exactly ONE combined PR, and at push time the combined branch carried BOTH commits
    # (the cherry-picks landed both units on the one branch).
    assert len(_pr_creates(gh)) == 1
    assert len(gh.push_logs) == 1
    # Commit subjects carry the unit id in the Conventional-Commits scope, lower-cased.
    assert "wu-x" in gh.push_logs[0] and "wu-y" in gh.push_logs[0]
    body = _pr_body(gh)
    assert "WU-X" in body and "WU-Y" in body
    assert "wu-x.txt" in body and "wu-y.txt" in body

    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/7")
    assert approver.gates == ["plan", "pr"]  # one plan + one PR approval, not per-unit
    saver.conn.close()


def test_conflicting_level_halts_naming_both_units_and_file(tmp_path):
    # (2) Two same-level units writing the SAME file conflict at cherry-pick -> HALT.
    repo = _target_repo(tmp_path, gate_cmd="true")  # both gates pass; the clash is at join
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/9")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _TWO_CONFLICTING), approver=approver, thread_id="cf")

    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    assert _pr_creates(gh) == []  # no PR
    assert _git_pushes(gh) == []  # nothing was pushed
    # The halt error names BOTH clashing units AND the file they fought over.
    messages = " ".join(e["message"] for e in final.values["errors"])
    assert "WU-X" in messages and "WU-Y" in messages
    assert "out.txt" in messages
    assert approver.gates == ["plan"]  # never reached the PR-approval gate
    saver.conn.close()


def test_level_gate_failure_halts_with_no_cherry_pick_and_no_pr(tmp_path):
    # (3) One unit's gate fails inside its own clone -> the whole level halts with nothing
    # cherry-picked onto the combined branch and no PR.
    repo = _target_repo(tmp_path, gate_cmd="true")  # unused: a unit-failing gate is injected
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/11")
    gate = FailUnitGate(fail_substr="wu-y")  # WU-Y's build clone fails its gate
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh, gate=gate)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _TWO_INDEPENDENT), approver=approver, thread_id="gf")

    assert final.values["status"] == Status.HALTED
    assert final.values.get("pr_url") is None
    assert _pr_creates(gh) == []  # no PR
    assert _git_pushes(gh) == []  # nothing cherry-picked nor pushed
    assert any("WU-Y" in e["message"] for e in final.values["errors"])  # the failed unit named
    assert approver.gates == ["plan"]
    saver.conn.close()


def test_dependent_level_builds_from_prior_levels_merged_tip(tmp_path):
    # (4) A size-1 root level, then a size-2 fan-out level: A and B both build from the
    # combined tip that already carries ROOT's commit, so each sees root.txt.
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/13")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(
        graph, _write_prd(tmp_path, _ROOT_THEN_FANOUT), approver=approver, thread_id="dep"
    )

    # ROOT built sequentially on the shared clone (no -build suffix); A and B fanned out.
    assert not executor.cwds["WU-ROOT"].endswith("-build")
    assert executor.cwds["WU-A"].endswith("-build")
    assert executor.cwds["WU-B"].endswith("-build")
    assert executor.cwds["WU-A"] != executor.cwds["WU-B"]
    # The dependent level's units see the prior level's MERGED change (root.txt).
    assert "root.txt" in executor.seen["WU-A"]
    assert "root.txt" in executor.seen["WU-B"]
    assert executor.seen["WU-ROOT"] == []  # ROOT saw nothing before it

    # One combined PR naming every unit across both levels; the combined branch carried all.
    assert len(_pr_creates(gh)) == 1
    # Commit subjects carry the unit id in the Conventional-Commits scope, lower-cased.
    assert all(uid in gh.push_logs[0] for uid in ["wu-root", "wu-a", "wu-b"])
    body = _pr_body(gh)
    assert all(uid in body for uid in ["WU-ROOT", "WU-A", "WU-B"])
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/13")
    assert approver.gates == ["plan", "pr"]
    saver.conn.close()


def test_single_unit_uses_shared_clone_without_fanout(tmp_path):
    # (5a) A single-unit run uses the run's ONE shared clone — no per-unit build clone.
    repo = _target_repo(tmp_path, gate_cmd="true")
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/1")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _ONE_UNIT), approver=approver, thread_id="solo")

    assert set(executor.cwds) == {"WU-S"}
    assert not executor.cwds["WU-S"].endswith("-build")  # the shared clone, not a build clone
    assert _build_clone_dirs(tmp_path) == []  # no per-unit clone was ever created
    assert len(_pr_creates(gh)) == 1
    assert final.values["status"] == Status.DONE
    assert final.values["pr_url"].endswith("/pull/1")
    assert approver.gates == ["plan", "pr"]
    saver.conn.close()


def test_all_size_one_level_chain_stays_on_shared_clone(tmp_path):
    # (5b) An all-size-1-level chain builds every unit sequentially on the ONE shared clone:
    # no fan-out, no per-unit clone, no cherry-pick — exactly the pre-fan-out behaviour.
    repo = _target_repo(tmp_path, gate_cmd="true")
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/3")
    graph, saver = _wire(tmp_path, repo, executor=executor, gh=gh)
    approver = _recording_approver(decision=True)

    final = drive(graph, _write_prd(tmp_path, _CHAIN), approver=approver, thread_id="chain")

    assert set(executor.cwds) == {"WU-1", "WU-2", "WU-3"}
    # Every unit built in the SAME shared clone directory, none a -build clone.
    assert len(set(executor.cwds.values())) == 1
    assert not any(cwd.endswith("-build") for cwd in executor.cwds.values())
    assert _build_clone_dirs(tmp_path) == []
    # Later units saw earlier units' committed work on the shared branch (sequential).
    assert "wu-1.txt" in executor.seen["WU-2"]
    assert {"wu-1.txt", "wu-2.txt"} <= set(executor.seen["WU-3"])
    assert len(_pr_creates(gh)) == 1
    assert final.values["status"] == Status.DONE
    assert approver.gates == ["plan", "pr"]
    saver.conn.close()


def test_fanout_build_unit_implement_receives_index_and_sandbox(tmp_path, monkeypatch):
    """Fan-out feature parity (review finding #2 / #5, Workstream A1).

    A unit that lands in a multi-unit level is built by the ``build_unit`` worker, not the
    sequential ``implement`` node. Before the ``UnitDeps`` bundle, the worker called
    ``implement(sub, executor=executor)`` and silently dropped index/sandbox — so an operator
    with ``[index]``/``[sandbox]`` enabled got them on single-chain PRDs but NOT on parallel
    ones. This pins that the worker's inner implement call now receives the SAME index/sandbox
    deps the sequential node is bound with. (Would fail on ``main``: pre-A1 the worker passed
    only ``executor``.)"""
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/7")

    index_cfg = IndexConfig(enabled=True)
    # A wired-but-disabled sandbox is fully inert (no docker started) yet still proves the
    # OBJECT is threaded into the worker's implement call — which is exactly what was missing.
    sandbox_mgr = SandboxManager(config=SandboxConfig(enabled=False))

    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor,
        worktree_manager=CloneManager(repo, base_dir=tmp_path / "clones"),
        gate=run_gate,
        pr_runner=gh,
        index=index_cfg,
        sandbox=sandbox_mgr,
    )

    # Spy on the ``implement`` the fan-out worker calls (build_unit looks it up as a module
    # global at call time), recording the kwargs each call receives, then delegating so the
    # real implement still commits and the level joins normally.
    real_implement = bsgraph.implement
    captured: list[dict] = []

    def spy_implement(state, **kwargs):
        captured.append(kwargs)
        return real_implement(state, **kwargs)

    monkeypatch.setattr(bsgraph, "implement", spy_implement)

    final = drive(
        graph,
        _write_prd(tmp_path, _TWO_INDEPENDENT),
        approver=_recording_approver(),
        thread_id="deps",
    )

    # Both units built via fan-out (their own -build clones), so every implement call captured
    # here came from build_unit — none from the sequential node.
    assert set(executor.cwds) == {"WU-X", "WU-Y"}
    assert all(cwd.endswith("-build") for cwd in executor.cwds.values())
    assert len(captured) == 2  # one per fan-out unit
    for kwargs in captured:
        assert kwargs.get("index_config") is index_cfg
        assert kwargs["index_config"].enabled is True
        assert kwargs.get("sandbox") is sandbox_mgr
        assert "sandbox_exec_timeout_s" in kwargs
    assert final.values["status"] == Status.DONE
    saver.conn.close()


def test_fanout_units_are_reviewed_and_findings_reach_pr(tmp_path):
    """Review on the fan-out path (review finding #1, Workstream A2).

    The default-ON post-gate reviewer only ran on the sequential path, so a unit that landed
    in a parallel level was NEVER reviewed — a regression the unit tests missed shipped
    unreviewed, depending only on the PRD's dependency shape. This pins that BOTH units of a
    parallel level are reviewed on their own build clones and their blocking findings reach the
    ONE combined PR body. (Would fail before A2: the worker never called run_review.)"""
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/7")

    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor,
        worktree_manager=CloneManager(repo, base_dir=tmp_path / "clones"),
        gate=run_gate,
        pr_runner=gh,
        review=ReviewConfig(enabled=True),
    )

    final = drive(
        graph,
        _write_prd(tmp_path, _TWO_INDEPENDENT),
        approver=_recording_approver(),
        thread_id="rev",
    )

    # Both fan-out units were reviewed, each on its own build clone.
    assert set(executor.reviewed) == {"WU-X", "WU-Y"}
    # Both units' blocking findings reach the single combined PR body (surfaced, not dropped).
    body = _pr_body(gh)
    assert "review flagged WU-X" in body
    assert "review flagged WU-Y" in body
    assert len(_pr_creates(gh)) == 1
    assert final.values["status"] == Status.DONE
    saver.conn.close()


def test_fanout_node_seam_every_feature_reaches_the_worker(tmp_path, monkeypatch):
    """Node-seam exhaustiveness guard (review finding #4, Workstream A3).

    The forwarding guard (test_reviewer_wiring.py, seam 1) stops at ``compile_graph``'s kwargs —
    it never checks a feature reaches the NODE that reads it, and never touches ``build_unit``,
    which is exactly how the reviewer (#1) and index/sandbox (#2) went dark on parallel units
    while it stayed green. This drives the REAL graph with a ``CloneManager`` + a 2-unit fan-out
    level + index/sandbox/review ALL wired on, and asserts NODE BEHAVIOUR on the parallel path:
    each worker's inner implement call receives the index + sandbox deps AND the reviewer runs on
    both units. Green now; red on the pre-fix main (the worker passed only ``executor`` and never
    reviewed)."""
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = FakeExecutor()
    gh = _recording_gh("https://github.com/owner/demo/pull/7")

    index_cfg = IndexConfig(enabled=True)
    # Wired-but-disabled sandbox: threaded into the graph (so we can assert it reaches the
    # worker's implement) without requiring docker in the test.
    sandbox_mgr = SandboxManager(config=SandboxConfig(enabled=False))

    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor,
        worktree_manager=CloneManager(repo, base_dir=tmp_path / "clones"),
        gate=run_gate,
        pr_runner=gh,
        index=index_cfg,
        sandbox=sandbox_mgr,
        review=ReviewConfig(enabled=True),
    )

    # Spy on the implement the fan-out worker calls (looked up as a module global at call time).
    real_implement = bsgraph.implement
    captured: list[dict] = []

    def spy_implement(state, **kwargs):
        captured.append(kwargs)
        return real_implement(state, **kwargs)

    monkeypatch.setattr(bsgraph, "implement", spy_implement)

    final = drive(
        graph,
        _write_prd(tmp_path, _TWO_INDEPENDENT),
        approver=_recording_approver(),
        thread_id="seam",
    )

    # Both units built via fan-out (own -build clones) -> every implement call came from build_unit.
    assert set(executor.cwds) == {"WU-X", "WU-Y"}
    assert all(cwd.endswith("-build") for cwd in executor.cwds.values())
    # (a) index + sandbox reached each worker's implement call.
    assert len(captured) == 2
    for kwargs in captured:
        assert kwargs.get("index_config") is index_cfg
        assert kwargs.get("sandbox") is sandbox_mgr
        assert "sandbox_exec_timeout_s" in kwargs
    # (b) the reviewer ran on BOTH fan-out units.
    assert set(executor.reviewed) == {"WU-X", "WU-Y"}
    assert final.values["status"] == Status.DONE
    saver.conn.close()


class ReviseExecutor(FakeExecutor):
    """Fan-out executor for the in-worker review-revise loop (A-i).

    ``run_review`` flags a BLOCKING finding for a unit's first ``clears_at`` reviews, then
    reports clean — so with ``clears_at=1`` the loop revises once and converges, and with a
    large ``clears_at`` the reviewer never clears (the bound stops it and the finding surfaces).
    ``run_implement`` stamps version-numbered content so each revision produces a REAL diff to
    commit (the base FakeExecutor writes constant content, which a revision couldn't re-commit)."""

    def __init__(self, clears_at: int = 1):
        super().__init__()
        self.clears_at = clears_at
        self.impl_calls: dict[str, int] = {}
        self.review_calls: dict[str, int] = {}

    def run_implement(self, prompt, **kwargs):
        cwd = Path(kwargs["cwd"])
        unit_id = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
        target = re.search(r"^Target modules: (.+)$", prompt, re.M).group(1).split(",")[0].strip()
        n = self.impl_calls.get(unit_id, 0)
        self.impl_calls[unit_id] = n + 1
        self.cwds[unit_id] = str(cwd)
        (cwd / target).write_text(f"content from {unit_id} v{n}\n")
        return _result("done")

    def run_review(self, prompt, **kwargs):
        unit_id = re.search(r"^Unit (\S+):", prompt, re.M).group(1)
        n = self.review_calls.get(unit_id, 0)
        self.review_calls[unit_id] = n + 1
        if n >= self.clears_at:
            verdict = {"verdict": "clean", "findings": []}
        else:
            verdict = {
                "verdict": "needs_changes",
                "findings": [{"severity": "blocking", "file": f"{unit_id}.txt",
                              "detail": f"fix {unit_id}"}],
            }
        return _result("```json\n" + json.dumps(verdict) + "\n```")


def _wire_review_revise(tmp_path, repo, *, executor, gh):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    graph = compile_graph(
        saver,
        executor=executor,
        worktree_manager=CloneManager(repo, base_dir=tmp_path / "clones"),
        gate=run_gate,
        pr_runner=gh,
        review=ReviewConfig(enabled=True),
        limits=LimitsConfig(),  # max_review_revisions defaults to 1
    )
    return graph, saver


def test_fanout_blocking_review_revises_in_worker_then_lands_clean(tmp_path):
    """In-worker review revision on the fan-out path (A-i, follow-up to finding #1).

    A blocking review finding on a parallel unit now RE-IMPLEMENTS the unit in place on its own
    build clone (findings fed back), re-gates, and re-reviews — instead of merely surfacing the
    finding. With a reviewer that flags once then clears, both units of a parallel level revise
    once and land CLEAN: no unresolved note in the PR, and the revise commits squashed so each
    unit is still one cherry-pick onto the combined branch."""
    repo = _target_repo(tmp_path, gate_cmd="true")  # every gate passes
    executor = ReviseExecutor(clears_at=1)  # blocking first review, clean after the revision
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    graph, saver = _wire_review_revise(tmp_path, repo, executor=executor, gh=gh)

    final = drive(
        graph,
        _write_prd(tmp_path, _TWO_INDEPENDENT),
        approver=_recording_approver(),
        thread_id="revise",
    )

    # Each unit implemented TWICE (original + one revision) and reviewed TWICE (blocking, clean).
    assert executor.impl_calls == {"WU-X": 2, "WU-Y": 2}
    assert executor.review_calls == {"WU-X": 2, "WU-Y": 2}
    # The revision RESOLVED the findings, so the PR carries no unresolved-blocking note.
    body = _pr_body(gh)
    assert "unresolved (blocking)" not in body
    # Still exactly one combined PR, both units landed, and the run completed.
    assert len(_pr_creates(gh)) == 1
    assert final.values["status"] == Status.DONE
    saver.conn.close()


def test_fanout_blocking_review_exhausts_revisions_then_surfaces(tmp_path):
    """The revise loop is BOUNDED (A-i): a reviewer that never clears revises up to
    ``max_review_revisions`` (default 1) then SURFACES the outstanding blocking finding on the
    PR and proceeds — it never halts the run on it, mirroring the sequential finalize_review."""
    repo = _target_repo(tmp_path, gate_cmd="true")
    executor = ReviseExecutor(clears_at=99)  # reviewer never clears
    gh = _recording_gh("https://github.com/owner/demo/pull/7")
    graph, saver = _wire_review_revise(tmp_path, repo, executor=executor, gh=gh)

    final = drive(
        graph,
        _write_prd(tmp_path, _TWO_INDEPENDENT),
        approver=_recording_approver(),
        thread_id="exhaust",
    )

    # Bounded to one revision: original + one revision implement, reviewed after each.
    assert executor.impl_calls == {"WU-X": 2, "WU-Y": 2}
    assert executor.review_calls == {"WU-X": 2, "WU-Y": 2}
    # The still-blocking findings SURFACE on the PR (not resolved), and the run still completes.
    body = _pr_body(gh)
    assert "unresolved (blocking): WU-X.txt" in body
    assert "unresolved (blocking): WU-Y.txt" in body
    # The PR reports the fan-out revision count: two units each revised once (reducer total),
    # which the last-write-wins review_revisions could never carry off concurrent workers.
    assert "resolved via revision: 2" in body
    assert final.values["review_revisions_total"] == 2
    assert final.values["status"] == Status.DONE
    saver.conn.close()
