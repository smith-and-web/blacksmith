"""Tests for the blacksmith runtime config loader (WU-01, WU-INSTALL).

Test contract (PRD §6, WU-01): config parses; unknown keys raise. WU-INSTALL:
``[target].repo_path`` is optional and defaults to the git root of the cwd; config is
discovered by walking up to the git root.
"""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from blacksmith.config import (
    ApiConfig,
    BlacksmithConfig,
    CheckpointerConfig,
    ConfigError,
    ModelTiers,
    find_config,
    find_git_root,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _git_init(path: Path) -> None:
    """Initialise a minimal git repo at ``path`` (enough for git-root discovery)."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


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


def test_omitted_repo_path_loads_and_resolves_to_git_root(tmp_path, monkeypatch):
    # (1) A config that omits [target].repo_path loads; the effective target repo
    # resolves to the git root of the current working directory (WU-INSTALL).
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git_init(repo)
    cfg_file = repo / "blacksmith.config.toml"
    cfg_file.write_text("[target]\ndefault_branch = \"main\"\n")

    cfg = BlacksmithConfig.load(cfg_file)
    assert cfg.target.repo_path is None  # not configured

    monkeypatch.chdir(repo)
    assert cfg.resolve_repo_path() == repo.resolve()


def test_omitted_target_section_entirely_loads(tmp_path, monkeypatch):
    # (1) Omitting the [target] section altogether still loads and resolves to the
    # git root of the cwd.
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git_init(repo)
    cfg_file = repo / "blacksmith.config.toml"
    cfg_file.write_text("[models]\nplan = \"claude-sonnet-4-6\"\n")

    cfg = BlacksmithConfig.load(cfg_file)
    assert cfg.target.repo_path is None
    assert cfg.target.default_branch == "main"

    monkeypatch.chdir(repo)
    assert cfg.resolve_repo_path() == repo.resolve()


def test_explicit_repo_path_used_unchanged():
    # (2) An explicit [target].repo_path still loads and is used unchanged.
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    assert cfg.target.repo_path == Path("/tmp/kindling")
    assert cfg.resolve_repo_path() == Path("/tmp/kindling")


def test_resolve_repo_path_raises_outside_git_repo(tmp_path, monkeypatch):
    # No explicit repo_path and not inside a git repo → clear ConfigError.
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config_minimal_no_repo_path.toml")
    monkeypatch.chdir(tmp_path)  # tmp_path is not a git repo
    with pytest.raises(ConfigError):
        cfg.resolve_repo_path(tmp_path)


def test_find_config_walks_up_to_git_root(tmp_path):
    # (3) Config is discovered by walking up from a nested subdirectory to the git
    # root, so `blacksmith <prd>` works when invoked from a nested path.
    repo = tmp_path / "proj"
    repo.mkdir()
    _git_init(repo)
    cfg_file = repo / "blacksmith.config.toml"
    cfg_file.write_text("[target]\n")
    nested = repo / "a" / "b" / "c"
    nested.mkdir(parents=True)

    found = find_config(nested)
    assert found == cfg_file
    assert find_git_root(nested) == repo.resolve()


def test_find_config_returns_none_outside_git_repo(tmp_path):
    assert find_config(tmp_path) is None
    assert find_git_root(tmp_path) is None


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
