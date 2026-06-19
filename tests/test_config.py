"""Tests for the blacksmith runtime config loader (WU-01).

Test contract (PRD §6, WU-01): config parses; missing keys raise.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from blacksmith.config import (
    ApiConfig,
    BlacksmithConfig,
    CheckpointerConfig,
    ConfigError,
    ModelTiers,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_config_parses():
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    assert cfg.target.repo_path == Path("/tmp/kindling")
    assert cfg.target.default_branch == "main"
    assert cfg.models.implement == "claude-opus-4-8"
    assert cfg.models.plan == "claude-sonnet-4-6"
    assert cfg.models.triage == "claude-haiku-4-5"
    assert cfg.checkpointer.db_path == Path(".blacksmith/checkpoints.sqlite")
    assert cfg.api.key_env_var == "BLACKSMITH_ANTHROPIC_API_KEY"
    assert cfg.api.prompt_caching is True


def test_defaults_applied_when_optional_sections_omitted():
    # Only [target] is provided; everything else must default.
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config_minimal.toml")
    assert cfg.models == ModelTiers()
    assert cfg.checkpointer == CheckpointerConfig()
    assert cfg.api == ApiConfig()
    assert cfg.target.default_branch == "main"  # field default within [target]


def test_missing_required_key_raises():
    with pytest.raises(ConfigError) as exc:
        BlacksmithConfig.load(FIXTURES / "invalid_missing_repo_path.toml")
    # Error is field-level and names the offending key.
    assert "repo_path" in str(exc.value)


def test_unknown_key_rejected():
    with pytest.raises(ConfigError) as exc:
        BlacksmithConfig.load(FIXTURES / "invalid_unknown_key.toml")
    assert "wat" in str(exc.value)


def test_missing_file_raises():
    with pytest.raises(ConfigError):
        BlacksmithConfig.load(FIXTURES / "does_not_exist.toml")


def test_invalid_toml_raises(tmp_path):
    bad = tmp_path / "broken.toml"
    bad.write_text("this is = = not toml")
    with pytest.raises(ConfigError):
        BlacksmithConfig.load(bad)


def test_config_is_frozen():
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    with pytest.raises(ValidationError):
        cfg.target.default_branch = "other"  # type: ignore[misc]


def test_resolve_api_key_reads_env(monkeypatch):
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    monkeypatch.setenv(cfg.api.key_env_var, "sk-ant-test")
    assert cfg.resolve_api_key() == "sk-ant-test"


def test_resolve_api_key_raises_when_unset(monkeypatch):
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    monkeypatch.delenv(cfg.api.key_env_var, raising=False)
    with pytest.raises(ConfigError):
        cfg.resolve_api_key()
