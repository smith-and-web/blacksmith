"""Sandbox manager: start / exec / stop a docker container over a mounted clone.

This is an ADDITIVE, opt-in self-verify channel for the agent — never the host. It
is off by default and, when unused, touches nothing: no HOST command execution is
ever added by this module. Every command issued here runs *inside* a docker
container, over a bind-mounted clone directory, via the ``docker`` CLI (a system
dependency the operator installs — no new third-party Python dependency, mirroring
how blacksmith already shells out to ``git`` and ``gh``).

This module never runs against the real ``docker`` binary in its own test suite —
:class:`SandboxManager` takes an injectable ``runner`` callable so tests exercise the
full start/exec/stop contract with a FAKE command runner. The default runner (used in
real operation) simply shells out to ``docker`` via :mod:`subprocess`.

Pure infra: this module does not wire into the graph/executor/implement nodes and
does not touch the test gate (:mod:`blacksmith.gate`), which remains the sole
authoritative pass/fail backstop, run on the host exactly as today.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# A "timed out" exec still needs a concrete, non-zero exit code so callers can treat
# it like any other failing command rather than special-casing it.
TIMEOUT_EXIT_CODE = 124

DEFAULT_IMAGE = "python:3-slim"
DEFAULT_CONTAINER_NAME = "blacksmith-sandbox"


class SandboxError(Exception):
    """Raised when a docker operation fails: missing binary, launch failure, etc.

    Never a raw traceback bubbles out of :class:`SandboxManager` — every underlying
    ``OSError``/``FileNotFoundError`` from the command runner is caught and re-raised
    as this typed, best-effort error instead.
    """


@dataclass(frozen=True)
class ExecResult:
    """Structured result of a command run inside the sandbox container."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class _CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


# The command runner is injectable so tests can supply a FAKE that never actually
# invokes the ``docker`` CLI. It receives the full argv and the timeout (seconds, or
# None for no timeout) and returns a ``subprocess.CompletedProcess``-shaped object, or
# raises ``FileNotFoundError``/``OSError`` (docker missing/unusable) or
# ``subprocess.TimeoutExpired`` (command ran past its timeout).
CommandRunner = Callable[[list[str], float | None], _CompletedProcessLike]


def _default_runner(argv: list[str], timeout: float | None) -> subprocess.CompletedProcess:
    # `timeout` kills (SIGKILL, after a terminate) the local `docker` client process
    # once it elapses, so a hung/slow command inside the container can never hang
    # blacksmith itself — subprocess.run raises TimeoutExpired, handled below.
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


@dataclass(frozen=True)
class SandboxConfig:
    """Sandbox settings (future ``[sandbox]`` section of blacksmith's own config).

    Deliberately self-contained here — this unit is pure infra and does not wire this
    config into :mod:`blacksmith.config`, the graph, the executor, or implement.
    ``enabled=False`` (the default) means nothing in the rest of blacksmith changes:
    the sandbox is opt-in and additive, never a replacement for the test gate.
    """

    enabled: bool = False
    image: str = DEFAULT_IMAGE
    setup_cmd: str | None = None
    container_name: str = DEFAULT_CONTAINER_NAME
    docker_bin: str = "docker"


@dataclass
class SandboxManager:
    """Starts, execs into, and stops a single docker container over a mounted clone.

    ``start`` launches the container with the given path bind-mounted (and set as the
    working directory), then, if configured, runs ``config.setup_cmd`` once via
    :meth:`exec`. ``exec`` runs a command inside the running container via
    ``docker exec`` and always returns a structured :class:`ExecResult` — a command
    that exceeds its timeout is killed and comes back as a non-zero timeout result,
    never a hang. ``stop`` removes the container and is idempotent: calling it
    multiple times, or before ``start``, never raises.
    """

    config: SandboxConfig = field(default_factory=SandboxConfig)
    runner: CommandRunner = field(default=_default_runner)
    _started: bool = field(default=False, init=False, repr=False)
    _mount_path: Path | None = field(default=None, init=False, repr=False)

    def start(self, path: str | Path) -> None:
        """Launch the sandbox container with ``path`` bind-mounted as its workdir."""
        mount_path = Path(path).resolve()
        argv = [
            self.config.docker_bin,
            "run",
            "-d",
            "--rm",
            "--name",
            self.config.container_name,
            "-v",
            f"{mount_path}:{mount_path}",
            "-w",
            str(mount_path),
            self.config.image,
            "sleep",
            "infinity",
        ]
        result = self._run(argv, timeout=None)
        if result.exit_code != 0:
            raise SandboxError(
                f"docker run failed (exit {result.exit_code}): {result.stderr.strip()}"
            )
        self._started = True
        self._mount_path = mount_path
        if self.config.setup_cmd:
            setup = self.exec(self.config.setup_cmd)
            if not setup.ok:
                raise SandboxError(
                    f"sandbox setup_cmd failed (exit {setup.exit_code}): {setup.stderr.strip()}"
                )

    def exec(self, command: str, timeout: float | None = None) -> ExecResult:
        """Run ``command`` inside the sandbox container via ``docker exec``.

        Always returns a structured :class:`ExecResult`, even on timeout: a command
        that runs past ``timeout`` seconds is killed and comes back with a non-zero
        exit code rather than hanging or raising.
        """
        if not self._started:
            raise SandboxError("sandbox has not been started")
        argv = [self.config.docker_bin, "exec", self.config.container_name, "sh", "-c", command]
        return self._run(argv, timeout=timeout)

    def stop(self) -> None:
        """Remove the sandbox container. Idempotent — safe to call any number of times."""
        if not self._started:
            return
        try:
            self._run(
                [self.config.docker_bin, "rm", "-f", self.config.container_name], timeout=None
            )
        except SandboxError:
            pass  # best-effort teardown: never let a failed rm surface
        finally:
            self._started = False
            self._mount_path = None

    def _run(self, argv: list[str], *, timeout: float | None) -> ExecResult:
        try:
            proc = self.runner(argv, timeout)
        except subprocess.TimeoutExpired as exc:
            stderr = f"command timed out after {timeout}s: {exc}"
            return ExecResult(exit_code=TIMEOUT_EXIT_CODE, stdout="", stderr=stderr)
        except (FileNotFoundError, OSError) as exc:
            raise SandboxError(f"failed to run {self.config.docker_bin}: {exc}") from exc
        return ExecResult(
            exit_code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or ""
        )
