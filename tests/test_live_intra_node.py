"""Tests for WU-LIVE-INTRA-NODE: intra-node agent activity on LangGraph's custom stream.

Test contract: (a) a fake executor run, driven inside a real (tiny) compiled graph node,
puts ``node_activity`` events into the run-event sink carrying per-turn / tool_use labels;
(b) a review call's findings are emitted as ``node_activity`` events, in order; (c) calling
the executor directly -- no graph, no stream writer bound -- is completely unchanged: no
exception, no events. Everything here rides LangGraph's own ``get_stream_writer`` / custom
stream (``blacksmith.executor``) and the drive loop's ``_step`` (``blacksmith.cli``); no new
dependency, no gate/control-flow change.
"""

from pathlib import Path
from typing import TypedDict

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from langgraph.graph import END, START, StateGraph

from blacksmith.cli import _step
from blacksmith.config import BlacksmithConfig
from blacksmith.executor import Executor
from blacksmith.graph import build_checkpointer

FIXTURES = Path(__file__).parent / "fixtures"


class FakeQuery:
    """Stand-in for claude_agent_sdk.query: yields canned messages (mirrors test_executor.py)."""

    def __init__(self, messages):
        self.messages = messages

    def __call__(self, *, prompt, options, transport=None):
        return self._gen()

    async def _gen(self):
        for message in self.messages:
            yield message


def _config(monkeypatch) -> BlacksmithConfig:
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    return BlacksmithConfig.load(FIXTURES / "valid_config.toml")


def _result_message(text: str) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result=text,
        total_cost_usd=0.01,
        usage={},
    )


class DemoState(TypedDict, total=False):
    result: str


def _recorder():
    events: list[tuple[str, dict]] = []

    def on_event(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    on_event.events = events
    return on_event


def _run_single_node_graph(tmp_path, node_fn, *, thread_id: str = "t1") -> list[tuple[str, dict]]:
    """Compile a minimal one-node graph, drive it once through ``_step``, and return
    whatever the ``on_event`` recorder captured -- the same mechanism the real CLI drive
    loop uses (WU-RUN-EVENTS / WU-LIVE-INTRA-NODE)."""
    graph = StateGraph(DemoState)
    graph.add_node("work", node_fn)
    graph.add_edge(START, "work")
    graph.add_edge("work", END)
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    compiled = graph.compile(checkpointer=saver)
    recorder = _recorder()
    config = {"configurable": {"thread_id": thread_id}}
    _step(compiled, {}, config, on_node=lambda _n: None, on_event=recorder)
    saver.conn.close()
    return recorder.events


def _node_activity(events: list[tuple[str, dict]]) -> list[dict]:
    return [payload for kind, payload in events if kind == "node_activity"]


# (a) --------------------------------------------------------------------------------


def test_turn_and_tool_use_activity_land_in_the_sink(tmp_path, monkeypatch):
    fake = FakeQuery(
        [
            AssistantMessage(
                content=[TextBlock(text="working"), ToolUseBlock(id="1", name="Write", input={})],
                model="claude-opus-4-8",
            ),
            _result_message("done"),
        ]
    )
    executor = Executor(_config(monkeypatch), query_fn=fake)

    def node(state: DemoState) -> dict:
        result = executor.run_implement("do the thing", cwd=str(tmp_path))
        return {"result": result.text}

    activity = _node_activity(_run_single_node_graph(tmp_path, node))

    turns = [a for a in activity if a.get("activity") == "turn"]
    tools = [a for a in activity if a.get("activity") == "tool_use"]
    assert turns, activity
    assert turns[0]["turn"] == 1
    assert turns[0]["node"] == "work"
    assert tools, activity
    assert tools[0]["tool"] == "Write"
    assert tools[0]["turn"] == 1
    assert tools[0]["node"] == "work"


# (b) --------------------------------------------------------------------------------


def test_review_findings_emitted_as_node_activity_in_order(tmp_path, monkeypatch):
    verdict = (
        '```json\n{"verdict": "needs_changes", "findings": ['
        '{"severity": "blocking", "file": "a.py", "detail": "bug A"}, '
        '{"severity": "advisory", "file": "b.py", "detail": "note B"}]}\n```'
    )
    fake = FakeQuery([_result_message(verdict)])
    executor = Executor(_config(monkeypatch), query_fn=fake)

    def node(state: DemoState) -> dict:
        result = executor.run_review("review this", cwd=str(tmp_path))
        return {"result": result.text}

    activity = _node_activity(_run_single_node_graph(tmp_path, node))
    findings = [a for a in activity if a.get("activity") == "finding"]

    assert len(findings) == 2
    assert findings[0]["finding"]["file"] == "a.py"
    assert findings[1]["finding"]["file"] == "b.py"
    assert findings[0]["node"] == "work"
    assert findings[1]["node"] == "work"


# (c) --------------------------------------------------------------------------------


def test_executor_direct_call_with_no_writer_in_context_is_unchanged(monkeypatch):
    """No graph, no stream writer bound: emission is a no-op -- no error, no behavior
    change (the fail-open path ``_emit_activity`` swallows the RuntimeError
    ``get_stream_writer()`` raises outside a LangGraph node/task runtime)."""
    fake = FakeQuery(
        [
            AssistantMessage(
                content=[ToolUseBlock(id="1", name="Read", input={})], model="claude-opus-4-8"
            ),
            _result_message("ok"),
        ]
    )
    executor = Executor(_config(monkeypatch), query_fn=fake)
    result = executor.run_implement("do it")
    assert result.is_error is False
    assert result.text == "ok"

    # Same for a review call (the findings-parsing path), also outside any graph context.
    fake_review = FakeQuery(
        [_result_message('```json\n{"verdict": "clean", "findings": []}\n```')]
    )
    review_executor = Executor(_config(monkeypatch), query_fn=fake_review)
    review_result = review_executor.run_review("review it")
    assert review_result.is_error is False
