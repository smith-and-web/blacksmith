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
    query,
)

from blacksmith.config import BlacksmithConfig

QueryFn = Callable[..., AsyncIterator[Any]]


class ExecutorError(Exception):
    """Raised when a Claude Agent SDK call fails or returns an error result."""


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
        **option_kwargs: Any,
    ) -> ExecutorResult:
        """Run one prompt to completion and return a structured result."""
        options = self.build_options(model=model, **option_kwargs)
        result = asyncio.run(self._collect(prompt, options))
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

    async def _collect(self, prompt: str, options: ClaudeAgentOptions) -> ExecutorResult:
        default_model = options.model or self._config.models.implement
        if options.can_use_tool is not None:
            # A can_use_tool guard needs a persistent bidirectional connection: the
            # one-shot query() closes its input stream before the permission round-trip
            # (the agent sees "Tool permission request failed: Stream closed"). The
            # ClaudeSDKClient keeps the connection open for the duration of the turn.
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                return await self._aggregate(client.receive_response(), default_model)
        return await self._aggregate(self._query(prompt=prompt, options=options), default_model)

    async def _aggregate(self, messages, default_model: str) -> ExecutorResult:
        texts: list[str] = []
        model = default_model
        result: ResultMessage | None = None
        async for message in messages:
            if isinstance(message, AssistantMessage):
                model = message.model or model
                texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
            elif isinstance(message, ResultMessage):
                result = message
        text = result.result if result and result.result else "".join(texts)
        return ExecutorResult(
            text=text or "",
            model=model,
            is_error=bool(result.is_error) if result else False,
            num_turns=result.num_turns if result else 0,
            cost_usd=result.total_cost_usd if result else None,
            usage=result.usage if result else None,
            session_id=result.session_id if result else None,
        )
