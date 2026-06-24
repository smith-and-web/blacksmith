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
# Implement runs cheaper-first: Sonnet on the first attempt, escalating to the stronger
# model only on a gate failure (PRD §8). ``implement`` is the first-attempt model.
DEFAULT_IMPLEMENT_MODEL = "claude-sonnet-4-6"
DEFAULT_IMPLEMENT_ESCALATE_MODEL = "claude-opus-4-8"
DEFAULT_TRIAGE_MODEL = "claude-haiku-4-5"

# Default name for blacksmith's runtime config, discovered by walking up to the
# git root (WU-INSTALL) so a global install can be run from anywhere inside a repo.
CONFIG_FILENAME = "blacksmith.config.toml"


class ConfigError(Exception):
    """Raised when blacksmith's runtime config is missing, malformed, or invalid."""


def find_git_root(start: str | Path | None = None) -> Path | None:
    """Return the git repository root containing ``start`` (default: cwd), or ``None``.

    Walks up from ``start`` looking for a ``.git`` entry (a directory in a normal
    clone, a file inside a linked worktree). Returns the first directory that has one.
    """
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / ".git").exists():
            return directory
    return None


def find_config(
    start: str | Path | None = None, *, filename: str = CONFIG_FILENAME
) -> Path | None:
    """Discover a blacksmith config by walking up from ``start`` to the git root.

    Enables running a globally-installed ``blacksmith`` from any nested path inside a
    repo: the config lives at the repo root and is found by climbing the directory
    tree (stopping at the git root). Returns the config path, or ``None`` if none is
    found at or below the git root.
    """
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    root = find_git_root(here)
    for directory in (here, *here.parents):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
        if root is not None and directory == root:
            break
    return None


class _Strict(BaseModel):
    """Base for config sections: reject unknown keys and freeze after load."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelTiers(_Strict):
    """Per-node model tiering (PRD §8): cheaper model on plan/triage and on the first
    implement attempt, escalating to a stronger model only on a gate failure.

    ``implement`` is the FIRST-attempt implement model (defaults to Sonnet);
    ``implement_escalate`` is the stronger model used for the single escalation retry.
    """

    plan: str = DEFAULT_PLAN_MODEL
    implement: str = DEFAULT_IMPLEMENT_MODEL
    implement_escalate: str = DEFAULT_IMPLEMENT_ESCALATE_MODEL
    triage: str = DEFAULT_TRIAGE_MODEL


class TargetConfig(_Strict):
    """The repository blacksmith operates on (e.g. Kindling).

    ``repo_path`` is optional: when omitted, the effective target repo is resolved at
    runtime to the git root of the current working directory (WU-INSTALL), so a
    globally-installed blacksmith can be run from inside the repo it should operate on.
    An explicit absolute ``repo_path`` is still honoured unchanged (backward compatible).
    """

    repo_path: Path | None = None
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

    target: TargetConfig = Field(default_factory=TargetConfig)
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

    def resolve_repo_path(self, start: str | Path | None = None) -> Path:
        """Return the effective target repo path (WU-INSTALL).

        Uses an explicit ``[target].repo_path`` unchanged when set (backward
        compatible). Otherwise defaults to the git root of ``start`` (the current
        working directory), so a globally-installed blacksmith run from inside a repo
        operates on that repo. Raises ``ConfigError`` if no path is configured and
        ``start`` is not inside a git repository.
        """
        if self.target.repo_path is not None:
            return self.target.repo_path
        root = find_git_root(start)
        if root is None:
            raise ConfigError(
                "[target].repo_path is not set and the current directory is not "
                "inside a git repository, so the target repo cannot be determined. "
                "Run blacksmith from inside the target repo, or set "
                "[target].repo_path in blacksmith.config.toml."
            )
        return root


def _format_validation_error(path: Path, err: ValidationError) -> str:
    """Render a pydantic ValidationError as a readable, field-level message."""
    lines = [f"invalid blacksmith config in {path}:"]
    for detail in err.errors():
        loc = ".".join(str(part) for part in detail["loc"]) or "<root>"
        lines.append(f"  - {loc}: {detail['msg']}")
    return "\n".join(lines)
