"""Claude Agent SDK executor wrapper (PRD §3, §4 nodes 2 & 5, §8).

A thin wrapper around the Claude Agent SDK so each graph node gets a structured
return instead of parsing CLI stdout. It enforces the cost/auth model (PRD §8): every
call routes through the dedicated API key (``config.api.key_env_var``) and uses
per-node model tiering (cheaper on plan, stronger on implement). Static context
belongs in the system prompt, which the SDK/CLI caches across calls — keep it stable
to get cache hits (AC-8).

The SDK's ``query()`` is async; ``run()`` wraps it synchronously for the sync graph
nodes. The underlying Claude Code CLI must be installed for *live* calls; the mocked
tests inject a fake query function and need neither the CLI nor the network.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from langgraph.config import get_stream_writer

from blacksmith import transcript
from blacksmith.config import BlacksmithConfig

QueryFn = Callable[..., AsyncIterator[Any]]


class ExecutorError(Exception):
    """Raised when a Claude Agent SDK call fails or returns an error result."""


def _emit_activity(payload: dict[str, Any]) -> None:
    """Emit one intra-node activity event on LangGraph's custom stream (WU-LIVE-INTRA-NODE).

    Fail-open by construction: ``get_stream_writer()`` raises when called outside a
    LangGraph node/task runtime context (e.g. a unit test invoking the executor directly,
    or any caller that never runs inside a compiled graph), and the default writer is a
    silent no-op when the graph IS running but nobody is consuming ``stream_mode="custom"``.
    Either way this never raises and never affects the executor's return value -- a purely
    additive OBSERVATION channel, exactly like the metrics/live-run-event sinks. Every event
    carries ``"kind": "node_activity"`` so the drive loop's ``_step`` can forward it to the
    run-event sink unchanged.
    """
    try:
        writer = get_stream_writer()
        writer({"kind": "node_activity", **payload})
    except Exception:
        pass


# A single fenced ```json ... ``` (or bare) block containing a review verdict object. A
# small, independent mirror of ``nodes.review``'s own parsing (that module imports
# ``Executor``, so importing back here would be circular) -- used only to drive the live
# "finding" activity events; best-effort, never raises.
_FINDING_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_review_findings(text: str) -> list[dict[str, Any]]:
    """Best-effort parse of a review call's fenced JSON verdict into its findings list.

    Fail-open: any unparseable input (no fenced block, invalid JSON, wrong shape) returns
    an empty list rather than raising -- this only feeds the live activity stream, never
    the graph's actual review verdict (``nodes.review`` parses the real one independently).
    """
    if not text or not text.strip():
        return []
    match = _FINDING_FENCE_RE.search(text)
    if match:
        block = match.group(1)
    else:
        stripped = text.strip()
        block = stripped if stripped.startswith("{") and stripped.endswith("}") else None
    if block is None:
        return []
    try:
        parsed = json.loads(block)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    findings = parsed.get("findings")
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, dict)]


@dataclass(frozen=True)
class ExecutorResult:
    """Structured result of one executor call."""

    text: str
    model: str
    is_error: bool
    num_turns: int
    cost_usd: float | None
    usage: dict[str, Any] | None
    session_id: str | None
    # Why the call failed, when ``is_error`` (else None): ``"max_turns"`` when the agent hit
    # its turn budget mid-task (a RECOVERABLE, budget-shaped failure — the partial work is
    # real and worth continuing), ``"other"`` for any genuine error. The Agent SDK surfaces
    # the turn cap either as a ResultMessage with ``subtype == "error_max_turns"`` or, on some
    # paths, by raising "Reached maximum number of turns" during iteration — both map here.
    error_kind: str | None = None

    @property
    def hit_turn_limit(self) -> bool:
        """True when this call failed by exhausting its turn budget (recoverable)."""
        return self.error_kind == "max_turns"

    @property
    def cache_read_tokens(self) -> int:
        """Tokens served from cache — >0 means a cached prefix was reused (AC-8)."""
        return int((self.usage or {}).get("cache_read_input_tokens", 0) or 0)

    @property
    def cache_creation_tokens(self) -> int:
        """Tokens written to cache — >0 confirms static-context caching is engaged."""
        return int((self.usage or {}).get("cache_creation_input_tokens", 0) or 0)


class Executor:
    """Programmatic Claude execution for graph nodes (PRD §3)."""

    def __init__(self, config: BlacksmithConfig, *, query_fn: QueryFn = query) -> None:
        self._config = config
        self._query = query_fn

    def build_options(
        self,
        *,
        model: str,
        system_prompt: str | None = None,
        cwd: str | Path | None = None,
        max_turns: int = 1,
        allowed_tools: Sequence[str] | None = None,
        disallowed_tools: Sequence[str] | None = None,
        permission_mode: str | None = None,
        can_use_tool: Any = None,
        betas: Sequence[str] | None = None,
    ) -> ClaudeAgentOptions:
        # Dedicated key (PRD §8 / AC-8): resolve_api_key raises if it is unset, so an
        # accidental fall-back to other auth is impossible.
        env = {"ANTHROPIC_API_KEY": self._config.resolve_api_key()}
        return ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            cwd=str(cwd) if cwd is not None else None,
            max_turns=max_turns,
            allowed_tools=list(allowed_tools or []),
            disallowed_tools=list(disallowed_tools or []),
            permission_mode=permission_mode,
            can_use_tool=can_use_tool,
            betas=list(betas or []),
            env=env,
        )

    def run(
        self,
        prompt: str,
        *,
        model: str,
        raise_on_error: bool = True,
        emit_findings: bool = False,
        **option_kwargs: Any,
    ) -> ExecutorResult:
        """Run one prompt to completion and return a structured result.

        ``emit_findings`` (WU-LIVE-INTRA-NODE), set only by ``run_review``, additionally
        parses the call's final text as a review verdict and emits each finding as a
        "finding" activity event, in order, on the custom stream (best-effort; see
        ``_emit_activity``).
        """
        options = self.build_options(model=model, **option_kwargs)
        result = asyncio.run(self._collect(prompt, options, emit_findings=emit_findings))
        if raise_on_error and result.is_error:
            raise ExecutorError(f"executor call failed (model={result.model}): {result.text!r}")
        return result

    def run_plan(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run with the cheaper plan/triage model tier (PRD §8)."""
        return self.run(prompt, model=self._config.models.plan, **kwargs)

    def run_implement(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run with the first-attempt (cheaper) implement model tier (PRD §8)."""
        return self.run(prompt, model=self._config.models.implement, **kwargs)

    def run_implement_escalate(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run the single escalation retry with the stronger implement model (PRD §8).

        Used only after a gate failure has discarded the cheaper first attempt; the model is
        ``config.models.implement_escalate``. Escalation happens at most once per unit, so
        this is called at most once per unit.
        """
        return self.run(prompt, model=self._config.models.implement_escalate, **kwargs)

    def run_review(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run with the dedicated post-gate review model tier (WU-REVIEW-CONFIG/NODE).

        Uses ``config.models.review`` — a separate tier from plan/implement/triage, still
        routed through the same dedicated key (PRD §8). ``emit_findings=True`` so each
        parsed finding is streamed as a "finding" activity event (WU-LIVE-INTRA-NODE).
        """
        return self.run(prompt, model=self._config.models.review, emit_findings=True, **kwargs)

    async def _collect(
        self, prompt: str, options: ClaudeAgentOptions, *, emit_findings: bool = False
    ) -> ExecutorResult:
        default_model = options.model or self._config.models.implement
        # The session_id is surfaced mid-stream (on AssistantMessage/ResultMessage). Capture
        # it into this holder as _aggregate sees it so the error path below can link the
        # PARTIAL transcript already flushed for the call (instead of leaving session_id=None).
        captured: dict[str, str | None] = {"session_id": None}
        try:
            if options.can_use_tool is not None:
                # A can_use_tool guard needs a persistent bidirectional connection: the
                # one-shot query() closes its input stream before the permission round-trip
                # (the agent sees "Tool permission request failed: Stream closed"). The
                # ClaudeSDKClient keeps the connection open for the duration of the turn.
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(prompt)
                    return await self._aggregate(
                        client.receive_response(),
                        default_model,
                        captured,
                        emit_findings=emit_findings,
                    )
            return await self._aggregate(
                self._query(prompt=prompt, options=options),
                default_model,
                captured,
                emit_findings=emit_findings,
            )
        except Exception as exc:
            # The Agent SDK signals some failures — notably "Reached maximum number of turns",
            # but also CLI/stream errors — by RAISING during message iteration rather than
            # emitting a ResultMessage with is_error=True. Left unhandled, that bubbles up as a
            # raw traceback and kills the whole run. Convert it into a structured is_error
            # result instead: nodes that pass raise_on_error=False (implement) can HALT cleanly,
            # and raise_on_error=True callers still get a typed ExecutorError (not a bare
            # Exception). BaseException (KeyboardInterrupt, CancelledError) is deliberately not
            # caught, so Ctrl-C and task cancellation still propagate.
            kind = "max_turns" if "maximum number of turns" in str(exc).lower() else "other"
            return ExecutorResult(
                text=str(exc),
                model=default_model,
                is_error=True,
                num_turns=0,
                cost_usd=None,
                usage=None,
                session_id=captured["session_id"],
                error_kind=kind,
            )

    async def _aggregate(
        self,
        messages,
        default_model: str,
        captured: dict[str, str | None] | None = None,
        *,
        emit_findings: bool = False,
    ) -> ExecutorResult:
        texts: list[str] = []
        model = default_model
        result: ResultMessage | None = None
        # Transcript capture (WU-TRANSCRIPT-CAPTURE): buffer THIS call's events in memory
        # only — never graph state. Gated on the config so a disabled feature costs nothing
        # and changes nothing. The buffer is written once in the ``finally`` so the SDK-error/
        # max-turns path (iteration raises) still flushes the PARTIAL transcript captured so
        # far — the failure case is the most valuable to inspect.
        capture = self._config.transcripts.enabled
        events: list[dict] = []
        session_id: str | None = None
        turn = 0
        try:
            async for message in messages:
                if capture:
                    self._capture(events, message)
                if isinstance(message, AssistantMessage):
                    model = message.model or model
                    session_id = getattr(message, "session_id", None) or session_id
                    if captured is not None:
                        captured["session_id"] = session_id
                    texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
                    # Intra-node activity (WU-LIVE-INTRA-NODE): one "turn" event per assistant
                    # turn, plus one "tool_use" event per tool call within it -- best-effort,
                    # additive OBSERVATION only (see ``_emit_activity``).
                    turn += 1
                    _emit_activity({"activity": "turn", "turn": turn})
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            _emit_activity(
                                {"activity": "tool_use", "tool": block.name, "turn": turn}
                            )
                elif isinstance(message, ResultMessage):
                    result = message
                    session_id = message.session_id or session_id
                    if captured is not None:
                        captured["session_id"] = session_id
            text = result.result if result and result.result else "".join(texts)
            is_error = bool(result.is_error) if result else False
            # The SDK's ResultMessage carries WHY it ended in ``subtype`` (e.g. "success",
            # "error_max_turns", "error_during_execution"). Classify the turn cap distinctly so
            # a caller can recover from it rather than treating it like any other error.
            subtype = getattr(result, "subtype", None) if result else None
            error_kind = None
            if is_error:
                error_kind = "max_turns" if subtype == "error_max_turns" else "other"
            if emit_findings and not is_error:
                # Review call (WU-LIVE-INTRA-NODE): stream each parsed finding, in order, as
                # its own "finding" activity event -- fail-open (see _extract_review_findings).
                for finding in _extract_review_findings(text or ""):
                    _emit_activity({"activity": "finding", "finding": finding})
            return ExecutorResult(
                text=text or "",
                model=model,
                is_error=is_error,
                num_turns=result.num_turns if result else 0,
                cost_usd=result.total_cost_usd if result else None,
                usage=result.usage if result else None,
                session_id=result.session_id if result else None,
                error_kind=error_kind,
            )
        finally:
            if capture:
                self._write_transcript(session_id, events)

    @staticmethod
    def _capture(events: list[dict], message: Any) -> None:
        """Append a message's transcript events to the buffer — BEST-EFFORT.

        Wrapped so a malformed message can never turn a successful call into an error:
        capture is additive observability and must never affect the returned result.
        """
        try:
            events.extend(transcript.events_for_message(message))
        except Exception:
            pass

    def _write_transcript(self, session_id: str | None, events: list[dict]) -> None:
        """Flush the buffered events to ``<dir>/<session_id>.jsonl`` — BEST-EFFORT.

        Writes nothing when there is nothing to record. The write itself is best-effort
        (``transcript.write_transcript`` swallows errors), and this method also guards so
        an unexpected failure here never propagates into the run.
        """
        try:
            if not events:
                return
            cfg = self._config.transcripts
            name = f"{session_id or 'unknown-session'}.jsonl"
            transcript.write_transcript(cfg.dir / name, events)
        except Exception:
            pass
