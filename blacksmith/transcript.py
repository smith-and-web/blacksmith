"""Per-call transcript capture (WU-TRANSCRIPT-CAPTURE).

ADDITIVE OBSERVABILITY ONLY. This module turns one executor call's streamed
Agent-SDK messages into a flat list of small event dicts and writes them to a
per-call JSONL file. It must NEVER change the ``ExecutorResult`` a call returns,
the graph control flow, gate decisions, or model behaviour — capture is purely a
side channel.

Transcripts are FILE-BASED, never graph state: the executor buffers ONE call's
events in memory and writes them once at end-of-call, so a 40-turn implement
transcript never bloats the checkpointer. Writing is BEST-EFFORT — a disabled
feature or an unwritable directory writes nothing and falls back to exactly the
prior behaviour (``write_transcript`` swallows every error and returns ``False``).

Only stdlib ``json`` is used; ``default=str`` keeps a non-JSON value (e.g. a
``Path`` in a tool input) from ever raising mid-write.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# Event ``type`` discriminators — the on-disk schema for one captured call.
EVENT_ASSISTANT_TEXT = "assistant_text"
EVENT_TOOL_USE = "tool_use"
EVENT_TOOL_RESULT = "tool_result"
EVENT_RESULT = "result"


def assistant_text_event(text: str) -> dict[str, Any]:
    """An assistant text block the model emitted this turn."""
    return {"type": EVENT_ASSISTANT_TEXT, "text": text}


def tool_use_event(block: ToolUseBlock) -> dict[str, Any]:
    """A tool-use block: which tool the agent invoked and its input."""
    return {
        "type": EVENT_TOOL_USE,
        "id": block.id,
        "name": block.name,
        "input": block.input,
    }


def tool_result_event(block: ToolResultBlock) -> dict[str, Any]:
    """A tool-result block returned to the agent (where the SDK provides one)."""
    return {
        "type": EVENT_TOOL_RESULT,
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


def result_event(result: ResultMessage) -> dict[str, Any]:
    """The terminal result + usage for the call (the final, summarising event)."""
    return {
        "type": EVENT_RESULT,
        "subtype": getattr(result, "subtype", None),
        "is_error": bool(result.is_error),
        "num_turns": result.num_turns,
        "session_id": result.session_id,
        "cost_usd": result.total_cost_usd,
        "usage": result.usage,
    }


def events_for_message(message: Any) -> list[dict[str, Any]]:
    """Translate one streamed SDK message into zero or more transcript events.

    Handles, at minimum, assistant text, tool-use and tool-result blocks, and the
    final ``ResultMessage``. Unknown message/block kinds are ignored — capture is
    additive and never blocks on a shape it does not recognise.
    """
    events: list[dict[str, Any]] = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                events.append(assistant_text_event(block.text))
            elif isinstance(block, ToolUseBlock):
                events.append(tool_use_event(block))
            elif isinstance(block, ToolResultBlock):
                events.append(tool_result_event(block))
    elif isinstance(message, UserMessage):
        # Tool results normally come back on a UserMessage's content blocks.
        content = message.content
        if not isinstance(content, str):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    events.append(tool_result_event(block))
    elif isinstance(message, ResultMessage):
        events.append(result_event(message))
    return events


def write_transcript(path: str | Path, events: Iterable[dict[str, Any]]) -> bool:
    """Serialize ``events`` to a JSONL file at ``path``. BEST-EFFORT.

    Creates the parent directory if needed and writes one JSON object per line.
    Returns ``True`` on success, ``False`` if anything goes wrong (an unwritable
    directory, a serialization error, …) — it NEVER raises, so a failed capture
    leaves the caller's behaviour byte-for-byte unchanged.
    """
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, default=str) + "\n")
        return True
    except Exception:
        return False
