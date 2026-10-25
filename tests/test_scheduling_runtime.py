"""Real graph/CLI/Git/checkpoint proofs for S16 wiring, scale and migration."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
from threading import Barrier, Lock

import pytest
from langgraph.config import get_config
from test_integration_integrity import configure
from test_publication_integrity import setup as install
from test_run_context import git, saved_values, write_prd
from test_run_context import project as project

from blacksmith import cli, unit_graph
from blacksmith.budget import BudgetLedger
from blacksmith.config import BlacksmithConfig
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.run_context import context_from_state
from blacksmith.scheduling import invocation_config
from blacksmith.state import Status


def setup_units(project, count, *, chain=False, overlap=False):
    configure(project)
    data = project[3]
    data["work_units"] = [
        {"id": f"U{i:03d}", "title": f"unit {i}", "layers": ["python"],
         "target_modules": ["shared.txt" if overlap else f"unit-{i:03d}.txt"],
         "test_contract": "real trusted gate passes",
         "depends_on": [f"U{i-1:03d}"] if chain and i else []}
        for i in range(count)
    ]
    write_prd(project[2], data)
    return deepcopy(data)


def observe_workers(monkeypatch, bound):
    original = unit_graph.unit_runner
    lock, rendezvous = Lock(), Barrier(min(bound, 2), timeout=20)
    seen = {"active": 0, "maximum": 0, "units": [], "configs": [], "child_configs": []}

    def factory(child):
        class ObservedChild:
            checkpointer = child.checkpointer

            def invoke(self, seed, config, *, durability=None):
                assert config == invocation_config(seed)
                with lock:
                    seen["child_configs"].append(config)
                return child.invoke(seed, config=config, durability=durability)

        execute = original(ObservedChild())

        def run(state):
            with lock:
                seen["active"] += 1
                seen["maximum"] = max(seen["maximum"], seen["active"])
                seen["units"].append(state["selected_unit"].id)
                seen["configs"].append(get_config()["max_concurrency"])
                first_pair = len(seen["units"]) <= min(bound, 2)
            try:
                if first_pair:
                    rendezvous.wait()
                return execute(state)
            finally:
                with lock:
                    seen["active"] -= 1
        return run
    monkeypatch.setattr(unit_graph, "unit_runner", factory)
    return seen


def execute(project, monkeypatch, count, *, chain=False, bound=2):
    original = setup_units(project, count, chain=chain)
    if bound != 2:
        project[1].write_text(project[1].read_text() + f"[limits]\nmax_parallel_units={bound}\n")
    calls, remote, _ = install(project, monkeypatch)
    seen = observe_workers(monkeypatch, 1 if chain else bound)
    assert cli.main([str(project[2]), "--config", str(project[1]), "--thread-id", "publication",
                     "--approve", "plan,pr"]) == 0
    state = saved_values(project[1], "publication")
    assert state["status"] == Status.DONE and state["scheduling_version"] == 1
    assert state["prd"].contract.model_dump() == original
    assert len(state["unit_outcomes"]) == count
    assert all(o["ok"] and o["gate_receipt"].outcome == "passed"
               and o["review_receipt"].status == "clean"
               for o in state["unit_outcomes"].values())
    context = context_from_state(state)
    balance = BudgetLedger(context).balance()
    assert not balance.accounting_incomplete
    assert balance.charged_usd == pytest.approx(.01*(3*count+1))
    assert len(calls) == 3*count+1 and len(remote.effects()) == 2
    assert state["publication_receipt"]["target"]["candidate_commit"] == git(
        Path(state["worktree_path"]), "rev-parse", "HEAD")
    assert seen["active"] == 0 and len(seen["units"]) == count
    assert seen["maximum"] == (1 if chain else min(bound, count))
    assert set(seen["configs"]) == {max(2, bound)}
    assert seen["child_configs"] == [invocation_config(state)]*count
    assert git(project[0], "status", "--porcelain") == ""
    return state, seen


def test_hundred_unit_chain_completes_actual_graph_without_recursion_failure(project, monkeypatch):
    state, seen = execute(project, monkeypatch, 100, chain=True)
    assert seen["units"] == [f"U{i:03d}" for i in range(100)]
    assert len(state["execution_levels"]) == 100
    assert invocation_config(state)["recursion_limit"] == 16100


def test_twenty_roots_default_bound_two_actual_workers(project, monkeypatch):
    state, seen = execute(project, monkeypatch, 20)
    assert len(state["execution_levels"]) == 1
    assert Counter(seen["units"]) == Counter(f"U{i:03d}" for i in range(20))


def test_nondefault_parallel_limit_reaches_actual_workers(project, monkeypatch):
    execute(project, monkeypatch, 6, bound=3)


def test_declared_overlap_runs_serially_with_prior_candidate_and_original_dependencies(
    project, monkeypatch,
):
    original = setup_units(project, 2, overlap=True)
    calls, remote, _ = install(project, monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]), "--thread-id", "publication",
                     "--approve", "plan,pr"]) == 0
    state = saved_values(project[1], "publication")
    assert [[u.id for u in batch] for batch in state["execution_levels"]] == [["U000"], ["U001"]]
    assert state["prd"].contract.model_dump() == original
    assert state["unit_outcomes"]["U001"]["base_commit"] == (
        state["unit_outcomes"]["U000"]["source_commit"])
    assert len({c[3].cwd for c in calls if c[0] == "implement"}) == 1
    assert (Path(state["worktree_path"])/"shared.txt").read_text() == "U001\n"
    assert all(o["ok"] for o in state["unit_outcomes"].values())
    assert len(remote.effects()) == 2 and len(calls) == 7


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_old_or_corrupt_execution_schedule_refuses_before_native_or_executor_and_keeps_evidence(
    project, monkeypatch, capsys, fault,
):
    setup_units(project, 2)
    calls, remote, _ = install(project, monkeypatch)
    remote.log.touch()
    args = ["--config", str(project[1]), "--thread-id", "publication", "--approve", "plan"]
    assert cli.main([str(project[2]), *args]) == 1
    before = saved_values(project[1], "publication")
    context = context_from_state(before)
    saver = build_checkpointer(BlacksmithConfig.load(project[1]).checkpointer.db_path)
    graph = compile_graph(saver)
    patch = {"scheduling_version": None} if fault == "missing" else {
        "execution_levels": before["execution_levels"] + [[before["selected_unit"]]]}
    graph.update_state({"configurable": {"thread_id": "publication"}}, patch)
    saver.conn.close()
    before = saved_values(project[1], "publication")
    operations, events = context.journal().operations(), context.journal().events()
    count = len(calls)
    monkeypatch.setattr(cli, "Executor", lambda *a, **k: pytest.fail("constructed executor"))
    monkeypatch.setattr("blacksmith.run_context.git_read", lambda *a, **k:
                        pytest.fail("native observation before scheduling refusal"))
    assert cli.main(["resume", *args]) == 1
    assert ("legacy_scheduling" if fault == "missing" else "execution batches differ") in (
        capsys.readouterr().err)
    assert saved_values(project[1], "publication") == before
    assert context.journal().operations() == operations and context.journal().events() == events
    assert len(calls) == count and remote.effects() == []


def test_internal_symlink_target_aliases_are_serialized_from_frozen_snapshot(project, monkeypatch):
    setup_units(project, 2)
    root = project[0]
    (root/"shared").mkdir()
    (root/"shared"/"keep.txt").write_text("base")
    for name in ("left", "right"):
        (root/name).symlink_to("shared", target_is_directory=True)
    git(root, "add", "-A")
    git(root, "commit", "-m", "internal target aliases")
    for unit, name in zip(project[3]["work_units"], ("left", "right"), strict=True):
        unit["target_modules"] = [name + "/result.txt"]
    original = deepcopy(project[3])
    write_prd(project[2], original)
    _, remote, _ = install(project, monkeypatch)
    assert cli.main([str(project[2]), "--config", str(project[1]), "--thread-id", "publication",
                     "--approve", "plan,pr"]) == 0
    state = saved_values(project[1], "publication")
    assert [[u.id for u in batch] for batch in state["execution_levels"]] == [["U000"], ["U001"]]
    assert state["prd"].contract.model_dump() == original
    assert (Path(state["worktree_path"])/"shared/result.txt").read_text() == "U001\n"
    assert all(o["ok"] for o in state["unit_outcomes"].values())
    assert len(remote.effects()) == 2


def test_resume_uses_saved_parallel_limit_when_local_config_changes(project, monkeypatch):
    setup_units(project, 2)
    project[1].write_text(project[1].read_text() + "[limits]\nmax_parallel_units=3\n")
    calls, remote, _ = install(project, monkeypatch)
    original_approver = cli._resolve_approver

    def stop_before_approval(args, renderer):
        def stop(payload, values):
            assert payload["gate"] == "plan"
            raise KeyboardInterrupt("retain completed plans before resume")
        return stop
    monkeypatch.setattr(cli, "_resolve_approver", stop_before_approval)
    args = ["--config", str(project[1]), "--thread-id", "publication", "--approve", "plan,pr"]
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(project[2]), *args])
    before = saved_values(project[1], "publication")
    assert len(calls) == 2 and not before.get("worktree_path")
    project[1].write_text(project[1].read_text().replace(
        "max_parallel_units=3", "max_parallel_units=1"))
    monkeypatch.setattr(cli, "_resolve_approver", original_approver)
    seen = observe_workers(monkeypatch, 3)
    assert cli.main(["resume", *args]) == 0
    state = saved_values(project[1], "publication")
    assert state["run_context"] == before["run_context"]
    assert state["limits"]["max_parallel_units"] == 3
    assert seen["maximum"] == 2 and set(seen["configs"]) == {3}
    assert seen["child_configs"] == [invocation_config(state)]*2
    assert len(calls) == 7 and len(remote.effects()) == 2


def test_single_worker_cli_completes_custom_stream_without_coordinator_starvation(
    project,
):
    import json
    import os
    import subprocess
    import sys

    data = project[1].parent / "single-worker-project.json"
    data.write_text(json.dumps(project[3]))
    script = r"""
import json,sys
from pathlib import Path
from pytest import MonkeyPatch
sys.path.insert(0,sys.argv[1])
from test_scheduling_runtime import execute
project=(Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]),
         json.loads(Path(sys.argv[5]).read_text()))
mp=MonkeyPatch()
try:
    state,seen=execute(project,mp,2,bound=1)
    assert seen['maximum']==1 and len(seen['units'])==2
    print('SINGLE_WORKER_CLI_COMPLETED')
finally:mp.undo()
"""
    source = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(source)}
    result = subprocess.run([sys.executable, "-c", script, str(Path(__file__).parent),
                             str(project[0]), str(project[1]), str(project[2]), str(data)],
                            cwd=source, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SINGLE_WORKER_CLI_COMPLETED" in result.stdout
