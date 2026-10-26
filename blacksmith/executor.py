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
import math
import warnings
from collections.abc import AsyncIterator, Callable, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
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
from blacksmith.receipts import CallContext, ReceiptError, digest

# The SDK (>=0.2.115) warns that listing a tool in ``allowed_tools`` auto-approves it before
# the ``can_use_tool`` guard is consulted. That is intentional here: the implementer's guard
# (blacksmith.nodes.implement) only ever gates the WRITE tools (Write/Edit/MultiEdit/
# NotebookEdit), and those are deliberately NOT in ``allowed_tools`` — so they still fall
# through to the guard. The tools the warning names (Read/Glob/Grep/search_code) are read-only
# and the guard allows them unconditionally anyway, so auto-approving them changes nothing.
# The advisory would otherwise print once per implement/build call and drown real warnings.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)

QueryFn = Callable[..., AsyncIterator[Any]]


class ExecutorError(Exception):
    """Raised when a Claude Agent SDK call fails or returns an error result."""


class ModelCallStopped(ReceiptError):
    """A started model call stopped on budget/deadline; never an advisory fallback."""

    def __init__(self, reason, result):
        self.reason, self.result = reason, result
        self.call_id, self.started = result.call_id, True
        super().__init__(f"model_call_stopped:{reason}: {result.call_id}")


_CALL = ContextVar("blacksmith_model_call", default=None)


def _cost_record(key, operation_kind, result):
    event = {"call_id": key.id, "unit_id": key.unit_id, "attempt_id": key.attempt_id,
             "node": operation_kind, "model": result.model,
             "cost_usd": result.cost_usd, "usage": result.usage,
             "num_turns": result.num_turns, "session_id": result.session_id}
    if result.transcript_path:
        event["transcript_path"] = result.transcript_path
    return [(key.id + ":cost", "model_cost", event)]


def _partial_result(model, call, *, kind, text):
    saved = call.get("captured", {})
    terminal = saved.get("terminal")
    return ExecutorResult(text=text, model=saved.get("model") or model, is_error=True,
        num_turns=terminal.num_turns if terminal else saved.get("num_turns", 0),
        cost_usd=terminal.total_cost_usd if terminal else None,
        usage=terminal.usage if terminal else None, session_id=saved.get("session_id"),
        error_kind=kind, terminal_received=False, transcript_path=call.get("transcript_path"),
        call_id=call.get("call_id"))


def _emit_activity(payload: dict[str, Any]) -> None:
    """Emit one intra-node activity event on LangGraph's custom stream (WU-LIVE-INTRA-NODE).

    Fail-open by construction: ``get_stream_writer()`` raises when called outside a
    LangGraph node/task runtime context (e.g. a unit test invoking the executor directly,
    or any caller that never runs inside a compiled graph), and the default writer is a
    silent no-op when the graph IS running but nobody is consuming ``stream_mode="custom"``.
    Either way this never raises and never affects the executor's return value -- a purely
    additive OBSERVATION channel, exactly like the metrics/live-run-event sinks. Every event
    carries ``"kind": "node_activity"`` so the drive loop's ``_step`` can forward it to the
    run-event sink after validation and bounded preview. Actual task identity comes
    from LangGraph; model-call identity comes from the immutable call context.
    """
    from blacksmith.observability import parent_activity_writer, runtime_identity, warn_optional
    try:
        writer = parent_activity_writer() or get_stream_writer()
    except RuntimeError:
        return  # No runnable stream is an expected direct-library path.
    try:
        writer({"kind": "node_activity", **payload,
                **runtime_identity(call=_CALL.get())})
    except Exception as exc:
        warn_optional("activity stream", exc)


def _extract_review_findings(text: str) -> list[dict[str, Any]]:
    """Preview only the same strict, complete verdict accepted by critical review.

    Invalid text emits no findings. This optional channel never supplies a verdict
    to routing; the full typed receipt remains authoritative.
    """
    from blacksmith.review_receipts import parse_verdict
    try:
        return [finding.model_dump() for finding in parse_verdict(text).findings]
    except (ValueError, TypeError, AttributeError):
        return []


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
    call_id: str | None = None
    terminal_received: bool = True
    transcript_path: str | None = field(default=None, compare=False)

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
        config.require_supported_execution()
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
        mcp_servers: dict[str, Any] | None = None,
        tools: Sequence[str] | None = None,
        max_budget_usd: float | None = None,
    ) -> ClaudeAgentOptions:
        # Dedicated key (PRD §8 / AC-8): resolve_api_key raises if it is unset, so an
        # accidental fall-back to other auth is impossible.
        env = {"ANTHROPIC_API_KEY": self._config.resolve_api_key()}
        budget = self._config.limits.max_call_cost_usd
        if max_budget_usd is not None:
            if (type(max_budget_usd) not in {int, float}
                    or not math.isfinite(max_budget_usd) or max_budget_usd <= 0):
                raise ValueError("SDK budget must be positive and finite")
            budget = min(budget, max_budget_usd)
        return ClaudeAgentOptions(
            model=model,
            tools=list(tools) if tools is not None else None,
            system_prompt=system_prompt,
            cwd=str(cwd) if cwd is not None else None,
            max_turns=max_turns,
            max_budget_usd=budget,
            allowed_tools=list(allowed_tools or []),
            disallowed_tools=list(disallowed_tools or []),
            permission_mode=permission_mode,
            can_use_tool=can_use_tool,
            betas=list(betas or []),
            # In-process MCP tool servers (the index search_code tool, the sandbox run_command
            # tool). The nodes pass these through run()'s **option_kwargs; build_options must
            # accept and forward them or a live tool-enabled call raises TypeError here — the
            # gap caught by the reviewer that FakeExecutor's **kwargs swallow hid from tests.
            mcp_servers=dict(mcp_servers or {}),
            # Never inherit permission auto-allow rules, hooks or unowned MCP servers.
            setting_sources=[],
            strict_mcp_config=True,
            env=env,
        )

    def run(
        self, prompt: str, *, model: str, raise_on_error: bool = True,
        emit_findings: bool = False, call_context: CallContext | None = None,
        operation_kind: str = "model.call", **option_kwargs: Any,
    ) -> ExecutorResult:
        from blacksmith.budget import BudgetLedger
        from blacksmith.receipts import validate_model_terminal_policy
        from blacksmith.run_clock import execution_scope
        if call_context is not None:
            validate_model_terminal_policy(call_context.journal())
        context = BudgetLedger.for_call(call_context).context if call_context else None
        with execution_scope(context):
            return self._run(prompt, model=model, raise_on_error=raise_on_error,
                emit_findings=emit_findings, call_context=call_context,
                operation_kind=operation_kind, **option_kwargs)

    def _run(
        self,
        prompt: str,
        *,
        model: str,
        raise_on_error: bool = True,
        emit_findings: bool = False,
        call_context: CallContext | None = None,
        operation_kind: str = "model.call",
        **option_kwargs: Any,
    ) -> ExecutorResult:
        """Run one prompt to completion and return a structured result.

        ``emit_findings`` (WU-LIVE-INTRA-NODE), set only by ``run_review``, additionally
        parses the call's final text as a review verdict and emits each finding as a
        "finding" activity event, in order, on the custom stream (best-effort; see
        ``_emit_activity``).
        """
        if call_context is None and self._query is query:
            raise ReceiptError(
                "run_admission_context_required: default SDK execution needs an immutable "
                "run/call context before options or transport")
        options = self.build_options(model=model, **option_kwargs)
        journal = call_context.journal() if call_context else None
        key = call_context.key(operation_kind) if call_context else None
        budget_ledger = reservation = None
        if journal is not None:
            from blacksmith.budget import BudgetLedger
            budget_ledger = BudgetLedger.for_call(call_context)
            reservation = (budget_ledger.reservation_for(key) if journal.get(key)
                           else budget_ledger.reserve(key))
            options = replace(options, max_budget_usd=reservation.amount_usd)
        cached = None
        if journal is not None:
            from blacksmith.receipts import MODEL_TERMINAL_POLICY_VERSION
            receipt = journal.intent(key, {
                "terminal_policy_version": MODEL_TERMINAL_POLICY_VERSION,
                "prompt_sha256": digest(prompt),
                "system_prompt_sha256": digest(str(options.system_prompt or "")),
                "model": options.model, "cwd": str(options.cwd) if options.cwd else None,
                "max_turns": options.max_turns, "max_budget_usd": options.max_budget_usd,
                "admission_id": reservation.operation_id,
                "allowed_tools": options.allowed_tools,
                "disallowed_tools": options.disallowed_tools,
                "tools": options.tools,
                "permission_mode": options.permission_mode,
                "mcp_servers": sorted(options.mcp_servers),
            })
            if not receipt.created:
                if receipt.status not in {"completed", "failed"}:
                    raise ReceiptError(f"interrupted_attempt_requires_resume: {key.id}")
                try:
                    cached = ExecutorResult(**receipt.outcome["result"])
                except (KeyError, TypeError) as exc:
                    raise ReceiptError("invalid retained SDK result") from exc
                if (cached.call_id != key.id or not cached.terminal_received
                        or cached.is_error != (receipt.status == "failed")):
                    raise ReceiptError("invalid retained SDK result identity/status")
        if cached is not None:
            result = cached
            if budget_ledger is not None:
                budget_ledger.settle(reservation)
        else:
            call = {"call_id": key.id if key else str(uuid4()), "role": operation_kind}
            if key:
                call.update(run_id=key.run_id, unit_id=key.unit_id, attempt_id=key.attempt_id)
            try:
                token = _CALL.set(call)
                try:
                    result = asyncio.run(self._bounded_collect(
                        prompt, options, emit_findings=emit_findings))
                    result = replace(result, transcript_path=_CALL.get().get("transcript_path"))
                finally:
                    _CALL.reset(token)
            except BaseException as exc:
                if journal is not None:
                    partial = _partial_result(options.model, call, kind="interrupted",
                                              text=f"call interrupted: {type(exc).__name__}")
                    journal.finish(key, "interrupted", {"error_type": type(exc).__name__,
                        "result": asdict(partial)},
                        events=_cost_record(key, operation_kind, partial))
                    budget_ledger.settle(reservation)
                raise
            if journal is not None:
                result = replace(result, call_id=key.id)
                status = ("unknown" if not result.terminal_received
                          else "failed" if result.is_error else "completed")
                journal.finish(key, status,
                               {"result": asdict(result)},
                               events=_cost_record(key, operation_kind, result))
                budget_ledger.settle(reservation)
                if not result.terminal_received:
                    if result.error_kind in {"timeout", "run_deadline"}:
                        raise ModelCallStopped(result.error_kind, result)
                    raise ReceiptError(f"interrupted_attempt_requires_resume: {key.id}")
        if result.error_kind == "budget" or (budget_ledger is not None and (
                budget_ledger.balance().unacknowledged_overrun)):
            raise ModelCallStopped("budget", result)
        if raise_on_error and result.is_error:
            raise ExecutorError(f"executor call failed (model={result.model}): {result.text!r}")
        return result

    async def _bounded_collect(self, prompt, options, *, emit_findings=False):
        from blacksmith.run_clock import current
        from blacksmith.sdk_transport import finish_cleanup
        clock = current()
        timeout = self._config.limits.model_timeout_s
        reason = "timeout"
        if clock is not None and clock.check() < timeout:
            timeout, reason = clock.remaining, "run_deadline"

        async def watch():
            while True:
                clock.check()
                await asyncio.sleep(.05)

        async def drain(task):
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            async with asyncio.timeout(timeout):
                if clock is None:
                    return await self._collect(prompt, options, emit_findings=emit_findings)
                task = asyncio.create_task(self._collect(prompt, options,
                                                       emit_findings=emit_findings))
                monitor = asyncio.create_task(watch())
                try:
                    done, _ = await asyncio.wait((task, monitor),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if monitor in done:
                        monitor.result()
                    return task.result()
                finally:
                    monitor.cancel()
                    task.cancel()
                    await finish_cleanup(drain(task))
                    await finish_cleanup(drain(monitor))
        except TimeoutError:
            return _partial_result(options.model, _CALL.get() or {},
                                   kind=reason, text="model call timed out")

    def run_plan(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run with the cheaper plan model tier (PRD §8)."""
        return self.run(prompt, model=self._config.models.plan,
                        operation_kind="model.plan", **kwargs)

    def run_summary(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run with the cheap plan model tier, for PR title/body narration (WU-PR-SUMMARY-TIER).

        Reuses ``config.models.plan``. Ordinary synthesis errors may use deterministic
        text; admission, budget-stop, deadline and critical receipt failures halt.
        """
        # Narration uses only the supplied immutable text, with no built-in or MCP tools.
        kwargs.update(tools=[], allowed_tools=[], mcp_servers={})
        return self.run(prompt, model=self._config.models.plan,
                        operation_kind="model.summary", **kwargs)

    def run_plan_critique(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run the (advisory, OFF by default) plan critic on the existing plan model tier
        (WU-PLAN-CRITIC-CORE).

        Reuses ``config.models.plan`` rather than a dedicated tier -- like ``run_summary``,
        this adds no provider/tier. Admission, budget-stop, deadline and critical
        receipt failures propagate; only ordinary advisory failures can fall back.
        """
        return self.run(prompt, model=self._config.models.plan,
                        operation_kind="model.critic", **kwargs)

    def run_implement(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run with the first-attempt (cheaper) implement model tier (PRD §8)."""
        return self.run(prompt, model=self._config.models.implement,
                        operation_kind="model.implement", **kwargs)

    def run_implement_escalate(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run the single escalation retry with the stronger implement model (PRD §8).

        Used only after a gate failure has discarded the cheaper first attempt; the model is
        ``config.models.implement_escalate``. Escalation happens at most once per unit, so
        this is called at most once per unit.
        """
        return self.run(prompt, model=self._config.models.implement_escalate,
                        operation_kind="model.implement", **kwargs)

    def run_review(self, prompt: str, **kwargs: Any) -> ExecutorResult:
        """Run with the dedicated post-gate review model tier (WU-REVIEW-CONFIG/NODE).

        Uses ``config.models.review`` — a separate tier from plan/implement, still
        routed through the same dedicated key (PRD §8). ``emit_findings=True`` so each
        parsed finding is streamed as a "finding" activity event (WU-LIVE-INTRA-NODE).
        """
        return self.run(prompt, model=self._config.models.review, emit_findings=True,
                        operation_kind="model.review", **kwargs)

    async def _collect(
        self, prompt: str, options: ClaudeAgentOptions, *, emit_findings: bool = False
    ) -> ExecutorResult:
        default_model = options.model or self._config.models.implement
        # The session_id is surfaced mid-stream (on AssistantMessage/ResultMessage). Capture
        # it into this holder as _aggregate sees it so the error path below can link the
        # PARTIAL transcript already flushed for the call (instead of leaving session_id=None).
        captured: dict[str, Any] = {"session_id": None, "num_turns": 0}
        if _CALL.get() is not None:
            _CALL.get()["captured"] = captured
        try:
            if options.can_use_tool is not None:
                # A can_use_tool guard needs a persistent bidirectional connection: the
                # one-shot query() closes its input stream before the permission round-trip
                # (the agent sees "Tool permission request failed: Stream closed"). The
                # ClaudeSDKClient keeps the connection open for the duration of the turn.
                from blacksmith.sdk_transport import OwnedCLITransport
                transport = OwnedCLITransport(prompt="", options=options)
                async with ClaudeSDKClient(options=options, transport=transport) as client:
                    await client.query(prompt)
                    return await self._aggregate(
                        client.receive_response(),
                        default_model,
                        captured,
                        emit_findings=emit_findings,
                    )
            kwargs = {"prompt": prompt, "options": options}
            if self._query is query:
                from blacksmith.sdk_transport import OwnedCLITransport
                kwargs["transport"] = OwnedCLITransport(prompt=prompt, options=options)
            return await self._aggregate(
                self._query(**kwargs),
                default_model,
                captured,
                emit_findings=emit_findings,
            )
        except ReceiptError:
            raise
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
            terminal = captured.get("terminal")
            return ExecutorResult(
                text=str(exc),
                model=default_model,
                is_error=True,
                num_turns=terminal.num_turns if terminal else captured["num_turns"],
                cost_usd=terminal.total_cost_usd if terminal else None,
                usage=terminal.usage if terminal else None,
                session_id=captured["session_id"],
                error_kind=kind,
                terminal_received=terminal is not None,
            )

    async def _aggregate(
        self,
        messages,
        default_model: str,
        captured: dict[str, Any] | None = None,
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
                        captured["model"] = model
                    texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
                    # Intra-node activity (WU-LIVE-INTRA-NODE): one "turn" event per assistant
                    # turn, plus one "tool_use" event per tool call within it -- best-effort,
                    # additive OBSERVATION only (see ``_emit_activity``).
                    turn += 1
                    if captured is not None:
                        captured["num_turns"] = turn
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
                        captured["terminal"] = message
            text = result.result if result and result.result else "".join(texts)
            is_error = bool(result.is_error) if result else True
            # The SDK's ResultMessage carries WHY it ended in ``subtype`` (e.g. "success",
            # "error_max_turns", "error_during_execution"). Classify the turn cap distinctly so
            # a caller can recover from it rather than treating it like any other error.
            subtype = getattr(result, "subtype", None) if result else None
            # Both indicators must substantiate success. Error or unknown terminal
            # subtypes cannot become clean evidence when the SDK error flag is false.
            is_error = is_error or subtype != "success"
            error_kind = None
            if is_error:
                error_kind = ("max_turns" if subtype == "error_max_turns" else
                              "budget" if subtype == "error_max_budget_usd" else "other")
            if result is None:
                error_kind = "missing_terminal"
            if emit_findings and result is not None and not is_error:
                # Review call (WU-LIVE-INTRA-NODE): stream each parsed finding, in order, as
                # its own "finding" activity event -- fail-open (see _extract_review_findings).
                from blacksmith.observability import preview_rows
                findings, omitted = preview_rows(_extract_review_findings(text or ""))
                for finding in findings:
                    _emit_activity({"activity": "finding", "finding": finding,
                                    "terminal_received": True})
                if omitted:
                    _emit_activity({"activity": "findings_omitted", "count": omitted,
                                    "terminal_received": True})
            return ExecutorResult(
                text=text or "",
                model=model,
                is_error=is_error,
                num_turns=result.num_turns if result else turn,
                cost_usd=result.total_cost_usd if result else None,
                usage=result.usage if result else None,
                session_id=result.session_id if result else None,
                error_kind=error_kind,
                terminal_received=result is not None,
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
            call = _CALL.get()
            name = f"{call['call_id'] if call else uuid4()}.jsonl"
            path = cfg.dir / name
            if transcript.write_transcript(path, events) and call is not None:
                call["transcript_path"] = str(path)
        except Exception:
            pass
