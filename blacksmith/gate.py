"""Toolchain-aware test gate (PRD §4 node 6, §5).

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
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class TargetToolchain(_Strict):
    """The target repo's `blacksmith.toml`: default commands + optional per-layer overrides."""

    setup_cmd: str | None = None
    test_cmd: str
    lint_cmd: str | None = None
    layers: dict[str, LayerOverride] = Field(default_factory=dict)

    def commands_for(
        self, layer: str | None = None
    ) -> tuple[str | None, str, str | None]:
        override = self.layers.get(layer) if layer else None
        setup_cmd = override.setup_cmd if override and override.setup_cmd else self.setup_cmd
        test_cmd = override.test_cmd if override and override.test_cmd else self.test_cmd
        lint_cmd = override.lint_cmd if override and override.lint_cmd else self.lint_cmd
        return setup_cmd, test_cmd, lint_cmd


@dataclass(frozen=True)
class GateResult:
    passed: bool
    output: str
    command: str

    def as_test_results(self) -> TestResults:
        """Project into the state's test_results shape (PRD §4)."""
        return {"passed": self.passed, "output": self.output, "command": self.command}


def load_toolchain(repo_path: str | Path) -> TargetToolchain:
    """Load and validate the target repo's ``blacksmith.toml``."""
    path = Path(repo_path) / CONFIG_FILENAME
    if not path.is_file():
        raise GateError(f"no {CONFIG_FILENAME} found in target repo: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
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
    setup_cmd, test_cmd, lint_cmd = toolchain.commands_for(layer)

    ran: list[str] = []
    sections: list[str] = []

    # setup_cmd provisions the fresh worktree (e.g. `npm ci`) before the gate proper.
    # A setup failure is an environmental fail, surfaced like any other gate failure.
    if setup_cmd:
        ran.append(setup_cmd)
        setup_ok, setup_output = _run(setup_cmd, path)
        sections.append(f"$ {setup_cmd}\n{setup_output}")
        if not setup_ok:
            return GateResult(
                passed=False, output="\n".join(sections), command=" && ".join(ran)
            )

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


def _run(command: str, cwd: Path) -> tuple[bool, str]:
    if not command.strip():
        raise GateError("empty command in toolchain config")
    # shell=True so chains/pipes (`npm ci && npm test`) work without an `sh -c` wrapper.
    # Commands come from the target repo's committed blacksmith.toml — trusted config,
    # not untrusted input. A missing binary now surfaces as a failing gate (exit 127 +
    # captured stderr) rather than a hard GateError, which is the right signal to route on.
    proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, shell=True)
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def _format_validation_error(path: Path, err: ValidationError) -> str:
    lines = [f"invalid {CONFIG_FILENAME} in {path}:"]
    for detail in err.errors():
        loc = ".".join(str(part) for part in detail["loc"]) or "<root>"
        lines.append(f"  - {loc}: {detail['msg']}")
    return "\n".join(lines)
