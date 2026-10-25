"""Bounded optional observations; critical state and receipts remain untouched."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

DISPLAY_ROWS = 20
DISPLAY_TEXT_CHARS = 2048


def display_text(value, limit=DISPLAY_TEXT_CHARS):
    """Escape terminal controls and show an explicit bounded preview."""
    value = "" if value is None else str(value)
    text = "".join(char if char in "\n\t" or char.isprintable()
                   else (f"\\x{ord(char):02x}" if ord(char) < 256 else f"\\u{ord(char):04x}")
                   for char in value)
    if len(text) <= limit:
        return text
    return (text[:limit] + f"\n[{len(text) - limit} more characters omitted; "
            "full details retained in run artifacts]")


def preview_rows(rows, limit=DISPLAY_ROWS):
    rows = rows if isinstance(rows, (list, tuple)) else list(rows or [])
    return rows[:limit], max(0, len(rows) - limit)


def omitted_rows(count, label):
    return f"[{count} more {label} omitted; full details retained in run artifacts]"


def review_revision_count(state):
    """Prefer current keyed unit counters over a legacy additive display total."""
    outcomes = state.get("unit_outcomes") or {}
    if outcomes:
        if not isinstance(outcomes, dict):
            return None
        counts = {}
        for unit_id, row in outcomes.items():
            if not isinstance(row, dict):
                return None
            value = row.get("review_revisions")
            if type(value) is not int or value < 0:
                return None  # Missing historical counts are unknown, not zero.
            counts[unit_id] = value
        selected = state.get("selected_unit")
        active = state.get("current_unit_id") or getattr(selected, "id", None)
        value = state.get("review_revisions")
        if active and type(value) is int and value >= 0:
            counts[active] = max(counts.get(active, 0), value)
        return sum(counts.values())
    value = state.get("review_revisions_total", state.get("review_revisions", 0))
    return value if type(value) is int and value >= 0 else None


class _ObservationScope:
    def __init__(self, state, config):
        self.identity = {"run_id": state.get("run_id")
                         or config.get("configurable", {}).get("thread_id")}
        self.seen = set()
        self.lock = Lock()

    def warn(self, feature, exc):
        with self.lock:
            if feature in self.seen:
                return
            self.seen.add(feature)
        _warning(feature, exc)


_SCOPE = ContextVar("blacksmith_observation_scope", default=None)
_PARENT_TASK = ContextVar("blacksmith_observation_parent_task", default=None)
_PARENT_WRITER = ContextVar("blacksmith_observation_parent_writer", default=None)


@contextmanager
def observation_scope(state=None, config=None):
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set(_ObservationScope(state or {}, config or {}))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _warning(feature, exc):
    # Exception messages may contain credentials/paths. Report only the class.
    try:
        print(f"optional {feature} unavailable ({type(exc).__name__}); execution continues",
              file=sys.stderr)
    except Exception:
        pass  # A failed warning stream must not turn an optional sink into a gate.


def warn_optional(feature, exc):
    owner = _SCOPE.get()
    if owner is None:
        _warning(feature, exc)
    else:
        owner.warn(feature, exc)


def warning_once(feature):
    """A sink also has its own warning budget when used outside a graph scope."""
    lock, seen = Lock(), False

    def warn(exc):
        nonlocal seen
        with lock:
            if seen:
                return
            seen = True
        warn_optional(feature, exc)
    return warn


def parent_activity_writer():
    """A synchronous child invoke does not itself forward custom events to its parent."""
    return _PARENT_WRITER.get()


@contextmanager
def parent_task_scope(task_id, writer=None):
    token = _PARENT_TASK.set(task_id)
    writer_token = _PARENT_WRITER.set(writer)
    try:
        yield
    finally:
        _PARENT_WRITER.reset(writer_token)
        _PARENT_TASK.reset(token)


def runtime_identity(state=None, call=None, *, task_id=None, node=None):
    """Use the actual LangGraph task and immutable model-call identity when present."""
    from langgraph.config import get_config

    try:
        config = get_config()
    except RuntimeError:
        config = {}
    owner = _SCOPE.get()
    state = state if isinstance(state, dict) else {}
    call = call or {}
    task_id = task_id or config.get("configurable", {}).get("__pregel_task_id")
    selected = state.get("selected_unit")
    result = {
        "run_id": state.get("run_id") or (owner.identity.get("run_id") if owner else None)
                  or config.get("configurable", {}).get("thread_id"),
        "unit_id": state.get("current_unit_id") or getattr(selected, "id", None) or "__run__",
        "attempt_id": state.get("attempt_id") or task_id,
        "task_id": task_id,
        "parent_task_id": _PARENT_TASK.get(),
        "node": node or config.get("metadata", {}).get("langgraph_node"),
        "identity_source": "graph_task",
    }
    for key in ("run_id", "unit_id", "attempt_id", "call_id", "role"):
        if call.get(key) is not None:
            result[key] = call[key]
    if call.get("call_id"):
        result["identity_source"] = "model_call" if call.get("run_id") else "injected_call"
        result["attempt_id"] = call.get("attempt_id") or call["call_id"]
    return result


def activity_preview(chunk):
    """Validate optional activity and bound its displayed fields without changing receipts."""
    from blacksmith.review_receipts import ValidatedFinding

    if chunk.get("kind") not in {"unit_stage", "node_activity"}:
        raise ValueError("unsupported custom observation kind")
    identities = ("run_id", "unit_id", "attempt_id", "task_id")
    if not all(isinstance(chunk.get(key), str) and chunk[key] for key in identities):
        raise ValueError("activity lacks task/unit/attempt ownership")
    result = {key: chunk.get(key) for key in (*identities, "parent_task_id", "call_id",
                                             "role", "node", "identity_source")}
    for key, limit in (("node", 128), ("role", 128)):
        result[key] = display_text(result[key], limit) if isinstance(result[key], str) else None
    if chunk.get("kind") == "unit_stage":
        if not isinstance(chunk.get("phase"), str) or not chunk["phase"]:
            raise ValueError("unit stage lacks phase")
        result["phase"] = display_text(chunk["phase"], 128)
        return "unit_stage", result
    activity = chunk.get("activity")
    result["activity"] = activity
    if activity == "finding":
        if (chunk.get("terminal_received") is not True
                or not isinstance(chunk.get("call_id"), str) or not chunk["call_id"]):
            raise ValueError("finding lacks a successful terminal call")
        finding = ValidatedFinding.model_validate(chunk.get("finding"))
        result["finding"] = {"severity": finding.severity,
                             "file": display_text(finding.file, 512),
                             "detail": display_text(finding.detail)}
        result["terminal_received"] = True
    elif activity == "findings_omitted":
        count = chunk.get("count")
        if (type(count) is not int or not 0 < count < 2**63
                or chunk.get("terminal_received") is not True):
            raise ValueError("invalid finding preview count")
        result.update(count=count, terminal_received=True)
    elif activity in {"turn", "tool_use"}:
        turn = chunk.get("turn")
        if type(turn) is not int or not 0 < turn < 2**63:
            raise ValueError("invalid turn observation")
        result["turn"] = turn
        if activity == "tool_use":
            if not isinstance(chunk.get("tool"), str) or not chunk["tool"]:
                raise ValueError("tool observation lacks name")
            result["tool"] = display_text(chunk["tool"], 256)
    else:
        raise ValueError("unsupported activity observation")
    return "node_activity", result
