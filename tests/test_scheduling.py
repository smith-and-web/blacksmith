"""Bounded invocation and disjoint batches without changing PRD dependencies."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_planner import _contract, _unit

from blacksmith.config import LimitsConfig
from blacksmith.planner import execution_levels
from blacksmith.scheduling import execution_batches, invocation_config, targets_overlap


def contract(targets, dependencies=None):
    values = _contract([_unit("template")]).model_dump()
    template = values["work_units"][0]
    values["work_units"] = [dict(template, id=str(i), target_modules=paths,
                               depends_on=(dependencies or {}).get(str(i), []))
                            for i, paths in enumerate(targets)]
    return type(_contract([_unit("template")])).model_validate(values)


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2"])
def test_parallel_bound_rejects_nonpositive_or_noninteger_values(value):
    with pytest.raises(ValidationError):
        LimitsConfig(max_parallel_units=value)


def test_default_bound_and_exact_recursion_formula():
    prd = type("PRD", (), {"contract": contract([["a"], ["b"], ["c"]])})()
    assert invocation_config({"prd": prd}) == {"max_concurrency": 2, "recursion_limit": 580}
    assert invocation_config({"prd": prd, "limits": {
        "max_parallel_units": 3, "max_fix_attempts": 2,
        "max_review_revisions": 4, "max_implement_continuations": 3,
    }}) == {"max_concurrency": 3, "recursion_limit": 2020}


@pytest.mark.parametrize("left,right,overlap", [
    ("src", "src/module.py", True), ("./src/", "src", True),
    ("src", "src_other", False), ("src/a.py", "src/b.py", False),
    (".", "anything", True), ("src/*.py", "src/nested/file.py", True),
    ("src/[ab].py", "docs/file.md", False), ("*.py", "docs", True),
    ("SRC", "src/file.py", True),
])
def test_target_overlap_is_conservative_for_paths_patterns_and_case(left, right, overlap):
    units = contract([[left], [right]]).work_units
    assert targets_overlap(*units) is overlap
    assert targets_overlap(*reversed(units)) is overlap


def test_conflicting_order_and_dependency_barriers_preserve_original_contract():
    prd = contract([["src"], ["src/a.py"], ["docs"], ["src/b.py"], ["final"]],
                   {"4": ["0", "1", "2", "3"]})
    original = deepcopy(prd.model_dump())
    assert [[u.id for u in level] for level in execution_levels(prd)] == [
        ["0", "1", "2", "3"], ["4"]]
    assert [[u.id for u in batch] for batch in execution_batches(prd)] == [
        ["0", "2"], ["1", "3"], ["4"]]
    assert prd.model_dump() == original


def test_multitarget_conflicts_propagate_to_later_batch():
    prd = contract([["src/a.py"], ["src/a.py", "docs"], ["docs/readme.md"], ["other"]])
    assert [[u.id for u in batch] for batch in execution_batches(prd)] == [
        ["0", "3"], ["1"], ["2"]]


def test_invalid_library_input_still_reaches_ingest_halt(tmp_path):
    invalid = tmp_path / "invalid.md"
    invalid.write_text("malformed PRD")
    assert invocation_config({"prd_path": str(invalid)})["recursion_limit"] == 260


def test_compiled_default_limits_are_used_until_state_is_seeded():
    from blacksmith.graph import build_checkpointer, compile_graph
    saver = build_checkpointer(":memory:")
    try:
        graph = compile_graph(saver, limits=LimitsConfig(max_parallel_units=3, max_fix_attempts=2))
        config = invocation_config(
            {}, fallback_limits=graph.config["configurable"]["blacksmith_limits"])
        assert graph.config["max_concurrency"] == config["max_concurrency"] == 3
        assert config["recursion_limit"] == 300
    finally:
        saver.conn.close()


def test_owned_node_pool_cancels_queued_work_and_drains_active_work_on_error():
    from threading import Event, Timer

    from langgraph._internal._constants import CONFIG_KEY_RUNNER_SUBMIT

    from blacksmith.scheduling import node_execution

    started, release, completed = Event(), Event(), Event()
    queued_calls = []

    def active():
        started.set()
        assert release.wait(2)
        completed.set()

    timer = Timer(.1, release.set)
    try:
        with pytest.raises(RuntimeError, match="cancel node scope"):
            with node_execution({"max_concurrency": 1}) as config:
                submit = config["configurable"][CONFIG_KEY_RUNNER_SUBMIT]()
                first = submit(active)
                assert started.wait(1)
                pending = submit(lambda: queued_calls.append("forbidden"))
                timer.start()
                raise RuntimeError("cancel node scope")
    finally:
        release.set()
        timer.join()
    assert first.done() and completed.is_set()
    assert pending.cancelled() and queued_calls == []


def test_single_worker_custom_stream_gets_events_before_node_completion(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = r"""
import operator,threading,sys
from typing import Annotated,TypedDict
from langgraph.graph import StateGraph,START,END
from langgraph.config import get_stream_writer
from blacksmith.scheduling import node_execution
from blacksmith.graph import build_checkpointer
class S(TypedDict):
    results: Annotated[list[int],operator.add]
seen=threading.Event()
def work(state):
    get_stream_writer()({'kind':'progress'})
    assert seen.wait(1), 'custom event not delivered while node active'
    return {'results':[1]}
g=StateGraph(S);g.add_node('work',work);g.add_edge(START,'work');g.add_edge('work',END)
saver=build_checkpointer(sys.argv[1])
with node_execution({'max_concurrency':1,'configurable':{'thread_id':'single'}}) as config:
    for mode,chunk in g.compile(checkpointer=saver).stream(
        {'results':[]},config,stream_mode=['updates','custom'],durability='sync'):

        if mode=='custom':
            assert chunk=={'kind':'progress'}
            seen.set()
assert seen.is_set()
saver.conn.close()
print('CUSTOM_PROGRESS_BEFORE_COMPLETION')
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "stream.sqlite")],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CUSTOM_PROGRESS_BEFORE_COMPLETION" in result.stdout
