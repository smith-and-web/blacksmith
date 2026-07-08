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
# Review tier (WU-REVIEW-CONFIG): a single dedicated model used for the additive
# post-gate review loop. Reuses the same dedicated key as every other tier (PRD §8).
DEFAULT_REVIEW_MODEL = "claude-opus-4-8"

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
    ``review`` is the dedicated model for the additive post-gate review loop
    (WU-REVIEW-CONFIG); it is a separate tier and does not affect plan/implement/triage.
    """

    plan: str = DEFAULT_PLAN_MODEL
    implement: str = DEFAULT_IMPLEMENT_MODEL
    implement_escalate: str = DEFAULT_IMPLEMENT_ESCALATE_MODEL
    triage: str = DEFAULT_TRIAGE_MODEL
    review: str = DEFAULT_REVIEW_MODEL


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


class StoreConfig(_Strict):
    """Persistent long-term memory Store settings (WU-STORE-WIRING).

    A SEPARATE, additive persistence channel from the per-thread ``[checkpointer]``:
    its own ``db_path`` backs the cross-thread memory Store and never shares a file
    with the checkpointer.
    """

    db_path: Path = Path(".blacksmith/store.sqlite")


class MetricsConfig(_Strict):
    """Local metrics SQLite sink settings (WU-METRICS-RECORD).

    A SEPARATE, additive OUTPUT channel from the ``[checkpointer]`` and ``[store]``: its
    own ``db_path`` backs a write-only metrics database that records one row per run (and
    per unit) for later reporting. It is never read back into the graph and never shares a
    file with the checkpointer or the long-term Store, so a run with metrics disabled or a
    failed metrics write behaves exactly as today.
    """

    db_path: Path = Path(".blacksmith/metrics.sqlite")


class LiveConfig(_Strict):
    """Live run-event sink settings (WU-RUN-EVENTS).

    A SEPARATE, additive OBSERVATION channel from ``[checkpointer]`` / ``[store]`` /
    ``[metrics]``: its own append-only ``db_path`` records a durable, thread-keyed stream
    of structured run events (node_start/node_end, unit_result, run_status) for live
    viewing. It is never read back into the graph and, exactly like the metrics sink, is
    best-effort — with ``enabled=false`` or on any write error a run is byte-for-byte
    unaffected.
    """

    enabled: bool = True
    db_path: Path = Path(".blacksmith/live.sqlite")


class TranscriptsConfig(_Strict):
    """Per-call transcript capture settings (WU-TRANSCRIPT-CAPTURE).

    A purely ADDITIVE observability channel: when ``enabled`` (the default), the
    executor writes one JSONL file per model call under ``dir``, keyed by the call's
    session id. It is its OWN directory, never the checkpointer/state — disabling it,
    or an unwritable ``dir``, writes nothing and the run behaves exactly as today.
    """

    dir: Path = Path(".blacksmith/transcripts")
    enabled: bool = True


class LimitsConfig(_Strict):
    """Recovery limits for the gate self-heal loop (WU-GATE-SELF-HEAL).

    When a unit's test gate fails, blacksmith can re-implement the unit with the gate's
    output fed back in, rather than discarding the (expensive) run on a fixable error.
    These knobs bound that recovery (and the separate post-gate review loop) so neither
    can ever spiral — the whole point of the feature is to recover cheaply, not to keep
    paying:

    * ``max_fix_attempts`` — same-model retries (cheap first-attempt model) WITH the gate
      error fed back, attempted BEFORE the single stronger-model escalation. ``0`` disables
      the self-heal loop entirely (escalate-then-halt, the prior behaviour). The bounded
      count is the primary anti-runaway guard: worst case per unit is
      ``1 + max_fix_attempts + 1`` implement attempts.
    * ``max_run_cost_usd`` — an OPTIONAL hard ceiling on total run spend. Once the run's
      summed ``cost_events`` cross it, no further retry OR escalation fires and the run
      halts with a "cost cap reached" error. ``None`` (the default) means no cap; it is
      left off by default because a hard whole-run cap can strand a multi-unit run's
      already-committed units, so the value is best chosen per project.
    * ``max_review_revisions`` — bounds the SEPARATE post-gate review loop
      (WU-REVIEW-CONFIG): the maximum number of review-driven revision attempts on a
      unit that already PASSED the test gate. It is independent of ``max_fix_attempts``
      and never alters the gate's FAILURE-branch counters or semantics. ``0`` disables
      review-driven revision (review may still run and report, but never triggers a
      revision retry).
    * ``max_implement_turns`` — the per-attempt turn budget handed to the implementer.
      A larger unit needs more turns; too small a budget makes implement hit the cap and
      fail before finishing. This is the ceiling each attempt runs under (sequential AND
      fan-out).
    * ``max_implement_continuations`` — how many times a turn-capped implement attempt may
      CONTINUE (keeping its partial work, fresh turn budget) before the run halts. A
      turn-cap is a budget-shaped failure, not a broken one, so — unlike a gate failure —
      the partial work is kept and finished rather than discarded. ``0`` disables the loop
      (a turn cap halts immediately, the prior behaviour). Sequential path only, mirroring
      escalation; a fan-out worker still records the cap and lets the join halt the level.
    """

    max_fix_attempts: int = Field(default=1, ge=0)
    max_run_cost_usd: float | None = Field(default=None, gt=0)
    max_review_revisions: int = Field(default=1, ge=0)
    max_implement_turns: int = Field(default=40, ge=1)
    max_implement_continuations: int = Field(default=1, ge=0)


class ReviewConfig(_Strict):
    """Additive post-gate review loop settings (WU-REVIEW-CONFIG).

    A SEPARATE, bounded loop that runs only AFTER the test gate PASSES; it never
    alters how the gate itself decides pass/fail. ``enabled`` (default ``True``) is a
    plain on/off toggle — disabling it makes a run behave exactly as it does today,
    with no review step attempted regardless of ``[limits].max_review_revisions``.
    """

    enabled: bool = True


class SandboxConfig(_Strict):
    """Additive, opt-in sandbox self-verify channel settings (WU-SANDBOX-CONFIG).

    OFF by default (``enabled=false``): with the default config, a run's tool surface,
    prompt, and behaviour are byte-for-byte unchanged from today — no HOST command
    execution is ever added. When explicitly enabled, this section only configures
    where an agent-run command is allowed to execute: inside a container built from
    ``image``, over the mounted clone, never on the host. It never alters the test
    gate's (``blacksmith/gate.py``) authoritative pass/fail semantics.

    * ``enabled`` — plain on/off toggle, default ``False``.
    * ``image`` — the container image name to run the sandbox in.
    * ``setup_cmd`` — optional command run once in the container after it starts (e.g.
      installing the target toolchain). ``None`` (the default) runs no setup step.
    * ``exec_timeout_s`` — the per-command wall-clock ceiling inside the sandbox,
      an int > 0, default 120.
    """

    enabled: bool = False
    image: str = "python:3.12-slim"
    setup_cmd: str | None = None
    exec_timeout_s: int = Field(default=120, gt=0)


class ApiConfig(_Strict):
    """Anthropic auth + caching policy (PRD §8 / §12 decision 3).

    ``admin_key_env_var`` names a SEPARATE credential used only by the read-only
    ``blacksmith costs`` reporter (org-scoped Admin API). It is distinct from the
    dedicated run key (``key_env_var``) — never reuse one for the other, and the admin
    key is never persisted.
    """

    key_env_var: str = "BLACKSMITH_ANTHROPIC_API_KEY"
    admin_key_env_var: str = "BLACKSMITH_ANTHROPIC_ADMIN_KEY"
    prompt_caching: bool = True


class BlacksmithConfig(_Strict):
    """Top-level blacksmith runtime config, loaded from a TOML file."""

    target: TargetConfig = Field(default_factory=TargetConfig)
    models: ModelTiers = Field(default_factory=ModelTiers)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    checkpointer: CheckpointerConfig = Field(default_factory=CheckpointerConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    transcripts: TranscriptsConfig = Field(default_factory=TranscriptsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)

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

    def resolve_admin_key(self) -> str:
        """Return the org-scoped Anthropic Admin API key from its own configured env var.

        Used solely by the read-only ``blacksmith costs`` reporter. Raises
        ``ConfigError`` naming the env var if it is unset — the admin key is a SEPARATE
        credential from blacksmith's dedicated run key and is never reused or persisted.
        """
        key = os.environ.get(self.api.admin_key_env_var)
        if not key:
            raise ConfigError(
                f"Admin API key env var {self.api.admin_key_env_var!r} is unset; "
                "`blacksmith costs` requires a SEPARATE Anthropic Admin API key, "
                "distinct from the dedicated run key. Set it in your environment "
                "or .env file."
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
