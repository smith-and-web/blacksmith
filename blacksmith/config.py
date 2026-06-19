"""blacksmith runtime configuration.

This is blacksmith's *own* configuration — model tiering, target-repo location,
checkpointer database path, and API auth. It is distinct from the per-target-repo
``blacksmith.toml`` (which defines each target repo's toolchain ``test_cmd`` /
``lint_cmd`` and is read by the test gate, WU-06). Keeping the two separate is a
deliberate consequence of PRD §12 decision 4.

The loader fails fast with a clear, field-level message on a non-conforming file
(PRD acceptance criterion AC-1 in miniature: the same validate-or-reject discipline
the PRD contract validator will apply, WU-02).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Current model IDs only (PRD §8). The legacy ``claude-*-4-20250514`` IDs are
# retired and will error — do not reintroduce date-suffixed IDs here.
DEFAULT_PLAN_MODEL = "claude-sonnet-4-6"
DEFAULT_IMPLEMENT_MODEL = "claude-opus-4-8"
DEFAULT_TRIAGE_MODEL = "claude-haiku-4-5"


class ConfigError(Exception):
    """Raised when blacksmith's runtime config is missing, malformed, or invalid."""


class _Strict(BaseModel):
    """Base for config sections: reject unknown keys and freeze after load."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelTiers(_Strict):
    """Per-node model tiering (PRD §8): cheaper model on plan/triage, stronger on implement."""

    plan: str = DEFAULT_PLAN_MODEL
    implement: str = DEFAULT_IMPLEMENT_MODEL
    triage: str = DEFAULT_TRIAGE_MODEL


class TargetConfig(_Strict):
    """The repository blacksmith operates on (e.g. Kindling)."""

    repo_path: Path
    default_branch: str = "main"


class CheckpointerConfig(_Strict):
    """SQLite checkpointer settings (PRD §12 decision 1)."""

    db_path: Path = Path(".blacksmith/checkpoints.sqlite")


class ApiConfig(_Strict):
    """Anthropic auth + caching policy (PRD §8 / §12 decision 3)."""

    key_env_var: str = "BLACKSMITH_ANTHROPIC_API_KEY"
    prompt_caching: bool = True


class BlacksmithConfig(_Strict):
    """Top-level blacksmith runtime config, loaded from a TOML file."""

    target: TargetConfig
    models: ModelTiers = Field(default_factory=ModelTiers)
    checkpointer: CheckpointerConfig = Field(default_factory=CheckpointerConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)

    @classmethod
    def load(cls, path: str | Path) -> BlacksmithConfig:
        """Parse and validate a blacksmith config file.

        Raises ``ConfigError`` if the file is missing, is not valid TOML, or does
        not conform to the schema (missing required keys, wrong types, or unknown
        keys). The error message names the offending field(s).
        """
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"blacksmith config not found: {path}")
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(_format_validation_error(path, exc)) from exc

    def resolve_api_key(self) -> str:
        """Return the dedicated Anthropic API key from the configured env var.

        Raises ``ConfigError`` if the env var is unset — blacksmith requires a
        dedicated key (PRD §8) and must never silently fall back to other auth.
        """
        key = os.environ.get(self.api.key_env_var)
        if not key:
            raise ConfigError(
                f"API key env var {self.api.key_env_var!r} is unset; blacksmith "
                "requires a dedicated Anthropic API key (PRD §8). Set it in your "
                "environment or .env file."
            )
        return key


def _format_validation_error(path: Path, err: ValidationError) -> str:
    """Render a pydantic ValidationError as a readable, field-level message."""
    lines = [f"invalid blacksmith config in {path}:"]
    for detail in err.errors():
        loc = ".".join(str(part) for part in detail["loc"]) or "<root>"
        lines.append(f"  - {loc}: {detail['msg']}")
    return "\n".join(lines)
