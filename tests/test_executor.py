"""Tests for the Claude Agent SDK executor wrapper (WU-04).

Test contract (PRD §6, WU-04): mocked unit test for wrapper logic (the live smoke
call + caching verification is the manual `scripts/smoke.py`). A fake query function
stands in for the SDK, so these run with no CLI and no network.
"""

from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from blacksmith.config import BlacksmithConfig, ConfigError
from blacksmith.executor import Executor, ExecutorError

FIXTURES = Path(__file__).parent / "fixtures"


class FakeQuery:
    """Stand-in for claude_agent_sdk.query: records calls, yields canned messages."""

    def __init__(self, messages):
        self.messages = messages
        self.calls: list[dict] = []

    def __call__(self, *, prompt, options, transport=None):
        self.calls.append({"prompt": prompt, "options": options})
        return self._gen()

    async def _gen(self):
        for message in self.messages:
            yield message


def _config(monkeypatch) -> BlacksmithConfig:
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "sk-ant-test")
    return BlacksmithConfig.load(FIXTURES / "valid_config.toml")


def _assistant(text: str, model: str = "claude-opus-4-8") -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model=model)


def _result(*, result="ok", is_error=False, cost=0.001, usage=None) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=1,
        session_id="s1",
        result=result,
        total_cost_usd=cost,
        usage=usage if usage is not None else {"cache_read_input_tokens": 100},
    )


def test_run_aggregates_result_message(monkeypatch):
    fake = FakeQuery([_assistant("partial "), _result(result="final answer", cost=0.002,
                                                       usage={"cache_read_input_tokens": 1234})])
    ex = Executor(_config(monkeypatch), query_fn=fake)
    r = ex.run("do the thing", model="claude-opus-4-8")
    assert r.text == "final answer"  # ResultMessage.result wins over streamed text
    assert r.cost_usd == 0.002
    assert r.cache_read_tokens == 1234
    assert r.is_error is False


def test_cache_token_properties(monkeypatch):
    usage = {"cache_read_input_tokens": 12, "cache_creation_input_tokens": 345}
    fake = FakeQuery([_result(usage=usage)])
    ex = Executor(_config(monkeypatch), query_fn=fake)
    r = ex.run("p", model="claude-opus-4-8")
    assert r.cache_read_tokens == 12
    assert r.cache_creation_tokens == 345


def test_run_falls_back_to_assistant_text(monkeypatch):
    fake = FakeQuery([_assistant("hello "), _assistant("world"), _result(result=None)])
    ex = Executor(_config(monkeypatch), query_fn=fake)
    assert ex.run("p", model="claude-opus-4-8").text == "hello world"


def test_build_options_injects_dedicated_key_and_model(monkeypatch):
    ex = Executor(_config(monkeypatch), query_fn=FakeQuery([]))
    options = ex.build_options(model="claude-opus-4-8", system_prompt="CONSTITUTION")
    assert options.model == "claude-opus-4-8"
    assert options.system_prompt == "CONSTITUTION"
    assert options.env["ANTHROPIC_API_KEY"] == "sk-ant-test"


def test_run_plan_and_implement_use_tiered_models(monkeypatch):
    config = _config(monkeypatch)
    fake = FakeQuery([_result()])
    ex = Executor(config, query_fn=fake)

    ex.run_plan("p")
    assert fake.calls[-1]["options"].model == config.models.plan  # claude-sonnet-4-6

    ex.run_implement("p")
    assert fake.calls[-1]["options"].model == config.models.implement  # claude-opus-4-8


def test_run_raises_on_error_result(monkeypatch):
    fake = FakeQuery([_result(result="boom", is_error=True)])
    ex = Executor(_config(monkeypatch), query_fn=fake)
    with pytest.raises(ExecutorError):
        ex.run("p", model="claude-opus-4-8")


def test_run_can_suppress_error_raising(monkeypatch):
    fake = FakeQuery([_result(result="boom", is_error=True)])
    ex = Executor(_config(monkeypatch), query_fn=fake)
    r = ex.run("p", model="claude-opus-4-8", raise_on_error=False)
    assert r.is_error is True
    assert r.text == "boom"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("BLACKSMITH_ANTHROPIC_API_KEY", raising=False)
    config = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    ex = Executor(config, query_fn=FakeQuery([]))
    with pytest.raises(ConfigError):
        ex.build_options(model="claude-opus-4-8")
