"""Tests for per-call transcript capture (WU-TRANSCRIPT-CAPTURE).

ADDITIVE OBSERVABILITY: capturing the streamed Agent-SDK messages of one call to a
per-call JSONL file must NEVER change the ``ExecutorResult`` a caller gets, and must be
best-effort (a disabled feature or an unwritable dir writes nothing and changes nothing).
A fake message stream stands in for the SDK, so these run with no CLI and no network.
"""

import json
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from blacksmith.config import BlacksmithConfig, TranscriptsConfig
from blacksmith.contract import parse_prd
from blacksmith.executor import Executor
from blacksmith.nodes.plan import cost_event, plan
from blacksmith.transcript import events_for_message, write_transcript

FIXTURES = Path(__file__).parent / "fixtures"
VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"


class FakeQuery:
    """Stand-in for claude_agent_sdk.query: yields a canned message stream."""

    def __init__(self, messages):
        self.messages = messages

    def __call__(self, *, prompt, options, transport=None):
        return self._gen()

    async def _gen(self):
        for message in self.messages:
            yield message


class RaisingAfterQuery:
    """Yields some messages, THEN raises mid-stream — how the SDK signals max-turns."""

    def __init__(self, messages, message="Reached maximum number of turns (40)"):
        self.messages = messages
        self.message = message

    def __call__(self, *, prompt, options, transport=None):
        return self._gen()

    async def _gen(self):
        for message in self.messages:
            yield message
        raise Exception(self.message)


def _config(monkeypatch, *, transcripts_dir, enabled=True):
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    base = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    return base.model_copy(
        update={"transcripts": TranscriptsConfig(dir=transcripts_dir, enabled=enabled)}
    )


def _assistant(*, text=None, tool=None, session_id="sess-1"):
    content = []
    if text is not None:
        content.append(TextBlock(text=text))
    if tool is not None:
        content.append(ToolUseBlock(id="t1", name=tool[0], input=tool[1]))
    return AssistantMessage(content=content, model="claude-opus-4-8", session_id=session_id)


def _result(*, session_id="sess-1", result="ok", is_error=False):
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=2,
        session_id=session_id,
        result=result,
        total_cost_usd=0.01,
        usage={"input_tokens": 5},
    )


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the module's writer + schema --------------------------------------------


def test_write_transcript_serializes_events_to_jsonl(tmp_path):
    events = [{"type": "assistant_text", "text": "hi"}, {"type": "result", "is_error": False}]
    path = tmp_path / "nested" / "sess.jsonl"
    assert write_transcript(path, events) is True
    assert _read_events(path) == events


def test_write_transcript_is_best_effort_on_unwritable_path(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")  # a FILE where a directory is needed -> mkdir fails
    # Never raises; reports failure instead.
    assert write_transcript(blocker / "nested" / "s.jsonl", [{"type": "result"}]) is False


def test_events_for_message_covers_text_tool_use_and_result():
    text_events = events_for_message(_assistant(text="thinking"))
    assert text_events == [{"type": "assistant_text", "text": "thinking"}]

    tool_events = events_for_message(_assistant(tool=("Read", {"file_path": "a.py"})))
    assert tool_events == [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}
    ]

    result_events = events_for_message(_result(session_id="sess-9"))
    assert len(result_events) == 1
    assert result_events[0]["type"] == "result"
    assert result_events[0]["session_id"] == "sess-9"


# --- the executor captures a call to <dir>/<session_id>.jsonl ----------------


def test_capture_writes_jsonl_with_event_types_keyed_by_session_id(monkeypatch, tmp_path):
    out = tmp_path / "transcripts"
    cfg = _config(monkeypatch, transcripts_dir=out)
    stream = [
        _assistant(text="let me look", session_id="sess-42"),
        _assistant(tool=("Read", {"file_path": "a.py"}), session_id="sess-42"),
        _result(session_id="sess-42"),
    ]
    result = Executor(cfg, query_fn=FakeQuery(stream)).run("p", model="claude-opus-4-8")

    path = out / "sess-42.jsonl"  # keyed by the call's session_id
    assert path.is_file()
    events = _read_events(path)
    types = [e["type"] for e in events]
    assert "assistant_text" in types
    assert "tool_use" in types
    assert "result" in types
    tool_use = next(e for e in events if e["type"] == "tool_use")
    assert tool_use["name"] == "Read"
    assert tool_use["input"] == {"file_path": "a.py"}
    # The returned result is unaffected by capture.
    assert result.text == "ok"
    assert result.session_id == "sess-42"


def test_disabled_writes_no_file_and_returns_identical_result(monkeypatch, tmp_path):
    stream = [_assistant(text="hi", session_id="s1"), _result(session_id="s1", result="final")]
    on = _config(monkeypatch, transcripts_dir=tmp_path / "on", enabled=True)
    off = _config(monkeypatch, transcripts_dir=tmp_path / "off", enabled=False)

    result_on = Executor(on, query_fn=FakeQuery(stream)).run("p", model="claude-opus-4-8")
    result_off = Executor(off, query_fn=FakeQuery(stream)).run("p", model="claude-opus-4-8")

    assert not (tmp_path / "off").exists()  # disabled: nothing written
    assert result_off == result_on  # byte-for-byte identical ExecutorResult


def test_unwritable_dir_leaves_result_unchanged(monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")  # transcripts dir lives under a FILE -> mkdir fails
    cfg = _config(monkeypatch, transcripts_dir=blocker / "transcripts")
    stream = [_assistant(text="hi", session_id="s1"), _result(session_id="s1", result="final")]

    result = Executor(cfg, query_fn=FakeQuery(stream)).run("p", model="claude-opus-4-8")

    assert result.text == "final"  # unchanged; no crash
    assert not (blocker / "transcripts").exists()


def test_sdk_error_stream_still_writes_partial_transcript(monkeypatch, tmp_path):
    out = tmp_path / "transcripts"
    cfg = _config(monkeypatch, transcripts_dir=out)
    # The agent does work, then the SDK raises (max-turns) before a ResultMessage.
    stream = [
        _assistant(text="partial work", session_id="sess-err"),
        _assistant(tool=("Edit", {"file_path": "x.py"}), session_id="sess-err"),
    ]
    result = Executor(cfg, query_fn=RaisingAfterQuery(stream)).run(
        "p", model="claude-sonnet-4-6", raise_on_error=False
    )

    assert result.is_error is True  # error path still produces a structured result
    path = out / "sess-err.jsonl"
    assert path.is_file()  # the PARTIAL transcript is still written
    types = [e["type"] for e in _read_events(path)]
    assert "assistant_text" in types
    assert "tool_use" in types
    assert "result" not in types  # call errored mid-stream — no terminal result event


def test_sdk_error_carries_session_id_so_partial_transcript_links(monkeypatch, tmp_path):
    out = tmp_path / "transcripts"
    cfg = _config(monkeypatch, transcripts_dir=out)
    # A session_id was surfaced before the SDK raised (max-turns) mid-stream.
    stream = [
        _assistant(text="partial work", session_id="sess-err"),
        _assistant(tool=("Edit", {"file_path": "x.py"}), session_id="sess-err"),
    ]
    result = Executor(cfg, query_fn=RaisingAfterQuery(stream)).run(
        "p", model="claude-sonnet-4-6", raise_on_error=False
    )

    assert result.is_error is True  # still a structured result, not a crash
    # The is_error result carries the session_id captured from the stream, so the failed
    # call's cost_event links the PARTIAL transcript already on disk (instead of None).
    assert result.session_id == "sess-err"
    assert (out / "sess-err.jsonl").is_file()
    event = cost_event("implement", "WU-01", result)
    assert event["session_id"] == "sess-err"


def test_sdk_error_without_surfaced_session_id_degrades_cleanly(monkeypatch, tmp_path):
    out = tmp_path / "transcripts"
    cfg = _config(monkeypatch, transcripts_dir=out)
    # The SDK raises before surfacing any session_id at all.
    result = Executor(cfg, query_fn=RaisingAfterQuery([])).run(
        "p", model="claude-sonnet-4-6", raise_on_error=False
    )

    assert result.is_error is True  # no crash
    assert result.session_id is None  # nothing surfaced -> degrades cleanly


# --- the ref lives in cost_events; transcript content never enters state -----


def test_cost_event_carries_session_id_reference():
    from blacksmith.executor import ExecutorResult

    result = ExecutorResult(
        text="plan", model="m", is_error=False, num_turns=1,
        cost_usd=0.01, usage=None, session_id="sess-xyz",
    )
    event = cost_event("plan", "WU-01", result)
    assert event["session_id"] == "sess-xyz"


def test_transcript_content_never_appears_in_returned_state(monkeypatch, tmp_path):
    out = tmp_path / "transcripts"
    cfg = _config(monkeypatch, transcripts_dir=out)
    sentinel = "TOOLINPUT_SENTINEL_XYZ"  # appears ONLY in a captured tool input
    stream = [
        _assistant(tool=("Read", {"file_path": sentinel}), session_id="sess-plan"),
        _result(session_id="sess-plan", result="here is the plan"),
    ]
    executor = Executor(cfg, query_fn=FakeQuery(stream))

    out_state = plan({"prd": parse_prd(VENDORED_PRD)}, executor=executor)

    # The tool-input sentinel is captured to the transcript file on disk...
    transcript_text = (out / "sess-plan.jsonl").read_text(encoding="utf-8")
    assert sentinel in transcript_text
    # ...but never leaks into the returned graph state (transcripts are file-based only).
    assert sentinel not in repr(out_state)
    # The session_id reference DOES live in the ledger so the file can be found later.
    assert out_state["cost_events"][0]["session_id"] == "sess-plan"
