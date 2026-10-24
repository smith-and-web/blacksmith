"""Low-level single-layer toolchain helpers (PRD §4 node 6, §5).

Version-2 production execution uses ``verification.py`` with the immutable trusted
toolchain and typed exact-candidate receipts. These standalone helpers preserve the
legacy API, including use in directories without Git metadata.

Reads the target repo's own ``blacksmith.toml`` — the per-repo toolchain config,
distinct from blacksmith's runtime ``blacksmith.config.toml`` — and runs the optional
one-off ``setup_cmd`` (e.g. ``npm ci``) followed by the configured test (then optional
lint) command inside a worktree, recording a deterministic pass/fail. That result is
what the graph routes on: a graph edge, not a model decision.

Commands run through the shell, so chains and pipes work directly
(``test_cmd = "npm ci && npm test"``) with no ``sh -c`` wrapper. ``setup_cmd`` is the
cleaner fix for the fresh-worktree dependency problem: a worktree is cut from ``HEAD``
with no installed deps (``node_modules`` is gitignored), so ``test_cmd = "npm test"``
alone fails with ``command not found`` — set ``setup_cmd = "npm ci"`` to provision the
worktree once before the gate proper. cargo hides this (it fetches its own deps); npm /
pnpm / yarn / pip-without-venv do not.

Commands may be overridden per layer under ``[layers.<name>]``; otherwise the
top-level defaults apply, matching the PRD's flat examples (Kindling: cargo test /
cargo clippy; blacksmith self-target: pytest / ruff check).

An optional ``fix_cmd`` (``run_fix``) runs the target's own deterministic formatter/
auto-fixer (e.g. ``cargo fmt --all``) BEFORE the gate and folds the result into the unit's
commit. It exists because mechanical formatting failures (``cargo fmt --check``,
``prettier --check``) are 100% auto-fixable yet the agent is worst at reproducing them by
hand — so blacksmith fixes them itself, for free, instead of burning a model retry. The gate
stays verify-only; ``fix_cmd`` is a separate step.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from blacksmith.commands import run_command
from blacksmith.gate_receipts import GateReceipt
from blacksmith.state import TestResults

CONFIG_FILENAME = "blacksmith.toml"


class GateError(Exception):
    """Raised when the toolchain config is missing/invalid or a command can't run."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LayerOverride(_Strict):
    setup_cmd: str | None = None
    test_cmd: str | None = None
    lint_cmd: str | None = None
    fix_cmd: str | None = None


class TargetToolchain(_Strict):
    """The target repo's `blacksmith.toml`: default commands + optional per-layer overrides."""

    setup_cmd: str | None = None
    test_cmd: str
    lint_cmd: str | None = None
    # Optional deterministic auto-fixer (e.g. `cargo fmt --all`). Run by `run_fix` after the
    # implement commit and BEFORE the gate, so mechanical formatting/lint failures are fixed
    # without a model retry. Self-contained: if it needs deps, chain them in (`npm ci && ...`).
    fix_cmd: str | None = None
    layers: dict[str, LayerOverride] = Field(default_factory=dict)

    def commands_for(
        self, layer: str | None = None
    ) -> tuple[str | None, str, str | None, str | None]:
        override = self.layers.get(layer) if layer else None
        setup_cmd = override.setup_cmd if override and override.setup_cmd else self.setup_cmd
        test_cmd = override.test_cmd if override and override.test_cmd else self.test_cmd
        lint_cmd = override.lint_cmd if override and override.lint_cmd else self.lint_cmd
        fix_cmd = override.fix_cmd if override and override.fix_cmd else self.fix_cmd
        return setup_cmd, test_cmd, lint_cmd, fix_cmd


@dataclass(frozen=True)
class GateResult:
    passed: bool
    output: str
    command: str
    receipt: GateReceipt | None = None

    def as_test_results(self) -> TestResults:
        """Project into the state's test_results shape (PRD §4)."""
        return {"passed": self.passed, "output": self.output, "command": self.command}


def load_toolchain(repo_path: str | Path) -> TargetToolchain:
    """Load and validate the target repo's ``blacksmith.toml``."""
    path = Path(repo_path) / CONFIG_FILENAME
    if not path.is_file():
        raise GateError(f"no {CONFIG_FILENAME} found in target repo: {path}")
    return parse_toolchain(path.read_text(encoding="utf-8"), source=path)


def parse_toolchain(text: str, *, source: str | Path = CONFIG_FILENAME) -> TargetToolchain:
    """Validate committed toolchain text without reloading an agent-editable clone."""
    path = Path(source)
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise GateError(f"invalid TOML in {path}: {exc}") from exc
    try:
        return TargetToolchain.model_validate(raw)
    except ValidationError as exc:
        raise GateError(_format_validation_error(path, exc)) from exc


def run_gate(
    worktree_path: str | Path,
    layer: str | None = None,
    *,
    toolchain: TargetToolchain | None = None,
) -> GateResult:
    """Run the configured test (then lint) command in the worktree; record pass/fail.

    Lint runs only if the test passed and a lint command is configured — a failed
    test short-circuits, mirroring how a developer would stop at a red test.
    """
    path = Path(worktree_path)
    toolchain = toolchain or load_toolchain(path)
    setup_cmd, test_cmd, lint_cmd, _fix_cmd = toolchain.commands_for(layer)

    ran: list[str] = []
    sections: list[str] = []

    # setup_cmd provisions the fresh worktree (e.g. `npm ci`) before the gate proper.
    # A setup failure is an environmental fail, surfaced like any other gate failure.
    if setup_cmd:
        ran.append(setup_cmd)
        setup_ok, setup_output = _run(setup_cmd, path)
        sections.append(f"$ {setup_cmd}\n{setup_output}")
        if not setup_ok:
            return GateResult(passed=False, output="\n".join(sections), command=" && ".join(ran))

    ran.append(test_cmd)
    test_ok, output = _run(test_cmd, path)
    sections.append(f"$ {test_cmd}\n{output}")
    passed = test_ok
    if test_ok and lint_cmd:
        ran.append(lint_cmd)
        lint_ok, lint_output = _run(lint_cmd, path)
        sections.append(f"$ {lint_cmd}\n{lint_output}")
        passed = lint_ok
    return GateResult(passed=passed, output="\n".join(sections), command=" && ".join(ran))


@dataclass(frozen=True)
class FixResult:
    """Low-level formatter outcome: configured, committed changes, command exit status.

    A nonzero exit may proceed only when source remains clean; partial failed edits
    raise GateError and are left uncommitted. Production adds journal ownership and
    complete layer coverage through ``verification.fix_candidate``.
    """

    applied: bool
    changed: bool
    ok: bool
    output: str
    command: str


def run_fix(
    worktree_path: str | Path,
    layer: str | None = None,
    *,
    toolchain: TargetToolchain | None = None,
) -> FixResult:
    """Run an explicitly supplied/configured formatter and check staging/amend.

    Successful edits are amended. A failed clean formatter is reported in ``ok``;
    a failed formatter leaving source changes raises and retains those edits. The
    version-2 graph uses its journal-owned formatter service instead of this helper.
    """
    path = Path(worktree_path)
    toolchain = toolchain or load_toolchain(path)
    _setup_cmd, _test_cmd, _lint_cmd, fix_cmd = toolchain.commands_for(layer)
    if not fix_cmd:
        return FixResult(applied=False, changed=False, ok=True, output="", command="")
    ok, output = _run(fix_cmd, path)
    if (
        not ok
        and run_command(["git", "-C", str(path), "status", "--porcelain", "-z"], check=True).stdout
    ):
        raise GateError("failed formatter left source changes; retained without staging/amend")
    changed = _amend_if_changed(path) if ok else False
    return FixResult(applied=True, changed=changed, ok=ok, output=output, command=fix_cmd)


def _amend_if_changed(cwd: Path) -> bool:
    """Stage everything and, only if that produced a staged diff, amend the unit's commit.

    Returns whether an amend happened. The ``--cached --quiet`` check is what keeps an
    already-formatted tree (the common case) from amending — and stops a degenerate
    no-commit implement path from rewriting an unrelated commit."""
    run_command(["git", "-C", str(cwd), "add", "-A"], check=True)
    staged = run_command(
        ["git", "-C", str(cwd), "diff", "--cached", "--quiet"], check=True, allowed=(0, 1)
    )
    if staged.returncode == 0:  # nothing staged -> the fixer changed nothing
        return False
    amend = run_command(["git", "-C", str(cwd), "commit", "--amend", "--no-edit"], check=True)
    return amend.returncode == 0


def _run(command: str, cwd: Path) -> tuple[bool, str]:
    if not command.strip():
        raise GateError("empty command in toolchain config")
    # shell=True so chains/pipes (`npm ci && npm test`) work without an `sh -c` wrapper.
    # Commands come from the target repo's committed blacksmith.toml — trusted config,
    # not untrusted input. A missing binary now surfaces as a failing gate (exit 127 +
    # captured stderr) rather than a hard GateError, which is the right signal to route on.
    proc = run_command(
        command, cwd=str(cwd), capture_output=True, text=True, shell=True, toolchain=True
    )
    return proc.returncode == 0, proc.stdout_tail + proc.stderr_tail


def _format_validation_error(path: Path, err: ValidationError) -> str:
    lines = [f"invalid {CONFIG_FILENAME} in {path}:"]
    for detail in err.errors():
        loc = ".".join(str(part) for part in detail["loc"]) or "<root>"
        lines.append(f"  - {loc}: {detail['msg']}")
    return "\n".join(lines)
