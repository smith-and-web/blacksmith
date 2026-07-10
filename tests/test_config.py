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
    IndexConfig,
    LimitsConfig,
    ModelTiers,
    RespondConfig,
    ReviewConfig,
    SandboxConfig,
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
    # An explicit [models].implement still works — it just sets the first-attempt model.
    assert cfg.models.implement == "claude-opus-4-8"
    assert cfg.models.implement_escalate == "claude-opus-4-8"  # default escalation model
    assert cfg.models.plan == "claude-sonnet-4-6"
    assert cfg.checkpointer.db_path == Path(".blacksmith/checkpoints.sqlite")
    assert cfg.api.key_env_var == "BLACKSMITH_ANTHROPIC_API_KEY"


def test_implement_first_attempt_defaults_to_sonnet_with_opus_escalation():
    # The first implement attempt defaults to the cheaper Sonnet model; the escalation
    # retry defaults to the stronger Opus model (PRD §8, cheaper-first).
    tiers = ModelTiers()
    assert tiers.implement == "claude-sonnet-4-6"
    assert tiers.implement_escalate == "claude-opus-4-8"


def test_defaults_applied_when_optional_sections_omitted():
    # Only [target] is provided; everything else must default.
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config_minimal.toml")
    assert cfg.models == ModelTiers()
    assert cfg.checkpointer == CheckpointerConfig()
    assert cfg.api == ApiConfig()
    assert cfg.target.default_branch == "main"  # field default within [target]


def test_review_model_tier_defaults():
    # WU-REVIEW-CONFIG: config.models gains a `review` field, defaulting to Opus.
    tiers = ModelTiers()
    assert tiers.review == "claude-opus-4-8"


def test_max_review_revisions_defaults():
    # WU-REVIEW-CONFIG: config.limits gains `max_review_revisions`, an int >= 0
    # defaulting to 1, independent of the existing self-heal `max_fix_attempts`.
    limits = LimitsConfig()
    assert limits.max_review_revisions == 1
    assert isinstance(limits.max_review_revisions, int)


def test_max_review_revisions_rejects_negative():
    with pytest.raises(ValidationError):
        LimitsConfig(max_review_revisions=-1)


def test_review_section_enabled_defaults_true():
    # WU-REVIEW-CONFIG: a new [review] section gains `enabled`, defaulting to True,
    # surfaced as config.review.enabled.
    review = ReviewConfig()
    assert review.enabled is True


def test_review_panel_size_defaults_to_one():
    # WU-REVIEW-PANEL-CONFIG: [review] gains `panel_size`, an int >= 1 defaulting to
    # 1, surfaced as config.review.panel_size. The default of 1 must be
    # byte-for-byte the current single-reviewer behaviour, and `enabled` still
    # defaults to True alongside it.
    review = ReviewConfig()
    assert review.panel_size == 1
    assert isinstance(review.panel_size, int)
    assert review.enabled is True


def test_review_panel_size_rejects_less_than_one():
    with pytest.raises(ValidationError):
        ReviewConfig(panel_size=0)
    with pytest.raises(ValidationError):
        ReviewConfig(panel_size=-1)


def test_review_defaults_when_config_omits_new_keys():
    # An existing config that omits [review] and the new keys entirely still loads,
    # with the new fields defaulting.
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    assert cfg.models.review == "claude-opus-4-8"
    assert cfg.limits.max_review_revisions == 1
    assert cfg.review == ReviewConfig()
    assert cfg.review.enabled is True
    assert cfg.review.panel_size == 1


def test_review_defaults_when_optional_sections_omitted():
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config_minimal.toml")
    assert cfg.review == ReviewConfig()
    assert cfg.limits == LimitsConfig()
    assert cfg.review.panel_size == 1


def test_explicit_review_config_loads(tmp_path):
    cfg_file = tmp_path / "blacksmith.config.toml"
    cfg_file.write_text(
        "[target]\n"
        'repo_path = "/tmp/kindling"\n'
        "\n"
        "[models]\n"
        'review = "claude-sonnet-4-6"\n'
        "\n"
        "[limits]\n"
        "max_review_revisions = 3\n"
        "\n"
        "[review]\n"
        "enabled = false\n"
        "panel_size = 3\n"
    )
    cfg = BlacksmithConfig.load(cfg_file)
    assert cfg.models.review == "claude-sonnet-4-6"
    assert cfg.limits.max_review_revisions == 3
    assert cfg.review.enabled is False
    assert cfg.review.panel_size == 3


def test_sandbox_section_defaults():
    # WU-SANDBOX-CONFIG: [sandbox] is off by default, with sane defaults for the
    # other fields so enabling it later requires no other config changes.
    sandbox = SandboxConfig()
    assert sandbox.enabled is False
    assert isinstance(sandbox.image, str) and sandbox.image
    assert sandbox.setup_cmd is None
    assert sandbox.exec_timeout_s == 120
    assert isinstance(sandbox.exec_timeout_s, int)


def test_sandbox_exec_timeout_s_rejects_non_positive():
    with pytest.raises(ValidationError):
        SandboxConfig(exec_timeout_s=0)
    with pytest.raises(ValidationError):
        SandboxConfig(exec_timeout_s=-5)


def test_sandbox_defaults_when_config_omits_section():
    # A config that omits [sandbox] entirely still loads, with enabled=false and the
    # defaults — behaving exactly as today (backward compatible).
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    assert cfg.sandbox == SandboxConfig()
    assert cfg.sandbox.enabled is False


def test_sandbox_defaults_when_optional_sections_omitted():
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config_minimal.toml")
    assert cfg.sandbox == SandboxConfig()


def test_explicit_sandbox_config_loads(tmp_path):
    cfg_file = tmp_path / "blacksmith.config.toml"
    cfg_file.write_text(
        "[target]\n"
        'repo_path = "/tmp/kindling"\n'
        "\n"
        "[sandbox]\n"
        "enabled = true\n"
        'image = "kindling-sandbox:latest"\n'
        'setup_cmd = "pip install -e ."\n'
        "exec_timeout_s = 30\n"
    )
    cfg = BlacksmithConfig.load(cfg_file)
    assert cfg.sandbox.enabled is True
    assert cfg.sandbox.image == "kindling-sandbox:latest"
    assert cfg.sandbox.setup_cmd == "pip install -e ."
    assert cfg.sandbox.exec_timeout_s == 30


def test_index_section_defaults():
    # WU-INDEX-CONFIG: [index] is off by default, with sane defaults for the other
    # fields so enabling it later requires no other config changes.
    index = IndexConfig()
    assert index.enabled is False
    assert index.max_map_bytes == 65536
    assert isinstance(index.max_map_bytes, int)
    assert index.exclude == []


def test_index_max_map_bytes_rejects_non_positive():
    with pytest.raises(ValidationError):
        IndexConfig(max_map_bytes=0)
    with pytest.raises(ValidationError):
        IndexConfig(max_map_bytes=-1)


def test_index_defaults_when_config_omits_section():
    # A config that omits [index] entirely still loads, with enabled=false and the
    # defaults — behaving exactly as today (backward compatible).
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    assert cfg.index == IndexConfig()
    assert cfg.index.enabled is False


def test_index_defaults_when_optional_sections_omitted():
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config_minimal.toml")
    assert cfg.index == IndexConfig()


def test_explicit_index_config_loads(tmp_path):
    cfg_file = tmp_path / "blacksmith.config.toml"
    cfg_file.write_text(
        "[target]\n"
        'repo_path = "/tmp/kindling"\n'
        "\n"
        "[index]\n"
        "enabled = true\n"
        "max_map_bytes = 4000\n"
        'exclude = ["*.lock", "vendor/**"]\n'
    )
    cfg = BlacksmithConfig.load(cfg_file)
    assert cfg.index.enabled is True
    assert cfg.index.max_map_bytes == 4000
    assert cfg.index.exclude == ["*.lock", "vendor/**"]


def test_respond_section_max_attempts_defaults():
    # WU-RESPOND-CONFIG: a new [respond] section gains `max_attempts`, an int >= 1
    # defaulting to 1, surfaced as config.respond.max_attempts.
    respond = RespondConfig()
    assert respond.max_attempts == 1
    assert isinstance(respond.max_attempts, int)


def test_respond_max_attempts_rejects_less_than_one():
    with pytest.raises(ValidationError):
        RespondConfig(max_attempts=0)
    with pytest.raises(ValidationError):
        RespondConfig(max_attempts=-1)


def test_respond_defaults_when_config_omits_section():
    # A config that omits [respond] entirely still loads, with the default
    # max_attempts — behaving exactly as today (backward compatible).
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config.toml")
    assert cfg.respond == RespondConfig()
    assert cfg.respond.max_attempts == 1


def test_respond_defaults_when_optional_sections_omitted():
    cfg = BlacksmithConfig.load(FIXTURES / "valid_config_minimal.toml")
    assert cfg.respond == RespondConfig()


def test_explicit_respond_config_loads(tmp_path):
    cfg_file = tmp_path / "blacksmith.config.toml"
    cfg_file.write_text(
        "[target]\n"
        'repo_path = "/tmp/kindling"\n'
        "\n"
        "[respond]\n"
        "max_attempts = 3\n"
    )
    cfg = BlacksmithConfig.load(cfg_file)
    assert cfg.respond.max_attempts == 3


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
