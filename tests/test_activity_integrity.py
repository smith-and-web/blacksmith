"""Optional activity uses real graph tasks and complete strict terminal verdicts."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from langgraph.config import get_stream_writer
from test_live_intra_node import (
    FakeQuery,
    _config,
    _result_message,
    _run_single_node_graph,
)

from blacksmith.executor import Executor


def verdict(count=1, *, detail="required behavior", file="result.py"):
    return json.dumps({"verdict": "needs_changes", "findings": [
        {"severity": "blocking", "file": file, "detail": detail + str(i)}
        for i in range(count)]})


def drive(tmp_path, monkeypatch, messages):
    executor = Executor(_config(monkeypatch), query_fn=FakeQuery(messages))
    results = []

    def node(state):
        results.append(executor.run_review("review actual changes", cwd=str(tmp_path),
                                           raise_on_error=False))
        return {"result": results[-1].text}

    events = _run_single_node_graph(tmp_path, node, thread_id="activity-integrity")
    return results[0], events


@pytest.mark.parametrize("text", [
    '{"findings":[{"severity":"blocking","file":"x","detail":"bug"}]}',
    '{"verdict":"clean","verdict":"needs_changes","findings":[]}',
    '{"verdict":"needs_changes","findings":[1]}',
    '{"verdict":"needs_changes","findings":[{"severity":"fatal","file":"x",'
    '"detail":"bug"}]}',
    '{"verdict":"needs_changes","findings":[],"ignored":"field"}',
    'prefix {"verdict":"needs_changes","findings":[]} suffix',
])
def test_invalid_complete_verdict_never_becomes_a_displayed_finding(
    tmp_path, monkeypatch, text,
):
    result, events = drive(tmp_path, monkeypatch, [_result_message(text)])
    assert result.text == text and not result.is_error
    assert not any(payload.get("activity") == "finding" for _, payload in events)
    assert {kind for kind, _ in events} >= {"node_start", "node_end"}


@pytest.mark.parametrize("terminal", [False, True])
def test_missing_or_error_terminal_cannot_emit_a_plausible_finding(
    tmp_path, monkeypatch, terminal,
):
    text = verdict()
    messages = [AssistantMessage(content=[TextBlock(text=text)], model="offline")]
    if terminal:
        messages.append(replace(_result_message(text), is_error=True))
    result, events = drive(tmp_path, monkeypatch, messages)
    assert result.is_error and result.text == text
    assert result.terminal_received is terminal
    assert not any(payload.get("activity") == "finding" for _, payload in events)


def test_finding_preview_is_bounded_with_real_task_identity_and_full_result_retained(
    tmp_path, monkeypatch,
):
    text = verdict(50, detail="D" * 5000, file="F" * 1000)
    result, events = drive(tmp_path, monkeypatch, [_result_message(text)])
    assert not result.is_error and result.terminal_received and result.text == text
    start = next(payload for kind, payload in events if kind == "node_start")
    end = next(payload for kind, payload in events if kind == "node_end")
    findings = [payload for _, payload in events if payload.get("activity") == "finding"]
    counts = [payload for _, payload in events
              if payload.get("activity") == "findings_omitted"]
    assert len(findings) == 20 and len(counts) == 1 and counts[0]["count"] == 30
    assert start["task_id"] == end["task_id"] and start["task_id"]
    assert len({item["call_id"] for item in findings + counts}) == 1
    # Context-free injected calls keep the legacy ExecutorResult equality contract;
    # their unique activity ID still binds to the actual transcript file.
    assert result.call_id is None
    call_id = Path(result.transcript_path).stem
    assert findings[0]["call_id"] == call_id
    for item in findings + counts:
        assert item["run_id"] == "activity-integrity" and item["unit_id"] == "__run__"
        assert item["attempt_id"] == call_id and item["task_id"] == start["task_id"]
        assert item["role"] == "model.review" and item["node"] == "work"
        assert item["terminal_received"] is True
    for item in findings:
        assert "characters omitted" in item["finding"]["file"]
        assert "characters omitted" in item["finding"]["detail"]
        assert len(item["finding"]["file"]) < 650
        assert len(item["finding"]["detail"]) < 2200


def test_unowned_custom_activity_is_dropped_without_changing_node_result(tmp_path, capsys):
    def node(state):
        get_stream_writer()({"kind": "node_activity", "activity": "finding",
                             "finding": {"severity": "blocking", "file": "x", "detail": "bug"}})
        return {"result": "completed"}

    events = _run_single_node_graph(tmp_path, node)
    assert [kind for kind, _ in events] == ["node_start", "node_end"]
    assert "optional live events unavailable (ValueError)" in capsys.readouterr().err
