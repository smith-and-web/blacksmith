"""Checked, bounded native commands; trusted toolchains are not an OS sandbox.

Each command owns a POSIX process group and capped on-disk output. Critical
receipts fail closed. This service does not retry effects or reuse observations;
the calling stage owns semantic restart reconciliation.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import math
import os
import selectors
import signal
import subprocess
import tempfile
import time
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from uuid import uuid4

from blacksmith.receipts import OperationJournal, attempt_identity

TAIL_BYTES = 16384
GIT_TIMEOUT_S = 120


class CommandError(RuntimeError):
    """A native command failed; its bounded logs are retained."""

    def __init__(self, message, *, result=None):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class CommandScope:
    timeout_s: float = 1800
    max_output_bytes: int = 1048576
    log_dir: Path | None = None
    journal: OperationJournal | None = None
    unit_id: str = "__run__"
    attempt_id: str = "native"
    stage: str = "native"
    credential_vars: tuple[str, ...] = ()
    context: object = None


_scope = contextvars.ContextVar("blacksmith_command_scope", default=None)


@contextmanager
def command_scope(
    context=None, *, stage="native", unit_id="__run__", attempt_id="native", limits=None
):
    previous = _scope.get() or CommandScope()
    if context is not None:
        config = context.execution_config()
        config.require_supported_execution()
        limits = config.limits
        scope = CommandScope(
            timeout_s=limits.command_timeout_s,
            max_output_bytes=limits.max_command_output_bytes,
            log_dir=context.directory / "commands",
            journal=context.journal(),
            unit_id=unit_id,
            attempt_id=attempt_id,
            stage=stage,
            credential_vars=(config.api.key_env_var, config.api.admin_key_env_var),
            context=context,
        )
    elif limits is not None:
        scope = CommandScope(
            timeout_s=limits.command_timeout_s,
            max_output_bytes=limits.max_command_output_bytes,
            stage=stage,
        )
    else:
        from dataclasses import replace

        scope = replace(previous, stage=stage, unit_id=unit_id, attempt_id=attempt_id)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


@contextmanager
def command_scope_for_state(state, stage):
    from blacksmith.run_context import context_from_state

    context = context_from_state(state) if state.get("run_context") else None
    selected = state.get("selected_unit")
    with command_scope(
        context,
        stage=stage,
        unit_id=state.get("current_unit_id") or (selected.id if selected else "__run__"),
        attempt_id=attempt_identity(state),
    ) as scope:
        yield scope


def toolchain_environment(credential_vars=()):
    """Copy the host environment without model/admin/GitHub credentials."""
    denied = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_ADMIN_API_KEY",
        "BLACKSMITH_ANTHROPIC_API_KEY",
        "BLACKSMITH_ANTHROPIC_ADMIN_API_KEY",
        "BLACKSMITH_ANTHROPIC_ADMIN_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GITHUB_PAT",
        "ANTHROPIC_CUSTOM_HEADERS",
        *credential_vars,
    }
    return {key: value for key, value in os.environ.items() if key not in denied}


@dataclass(frozen=True)
class CommandResult:
    args: str | Sequence[str]
    returncode: int
    stdout: str | bytes
    stderr: str | bytes
    stdout_tail: str
    stderr_tail: str
    log_dir: Path
    stdout_bytes: int
    stderr_bytes: int
    truncated: bool
    timed_out: bool
    cancelled: bool

    def check_returncode(self, allowed=(0,)):
        if self.timed_out or self.cancelled or self.returncode not in allowed:
            reason = (
                "timeout"
                if self.timed_out
                else "cancelled"
                if self.cancelled
                else str(self.returncode)
            )
            raise CommandError(f"command failed ({reason}); logs: {self.log_dir}", result=self)
        return self

    def require_complete_output(self):
        if self.truncated:
            raise CommandError(f"command output truncated; logs: {self.log_dir}")
        return self


class _Capture:
    def __init__(self, path, budget, *, sensitive):
        self.file = path.open("xb")
        self.budget = budget
        self.sensitive = sensitive
        self.raw = bytearray()
        self.tail = bytearray()
        self.total = self.kept = 0

    def add(self, data):
        self.total += len(data)
        self.tail = (self.tail + data)[-TAIL_BYTES:]
        keep = data[: self.budget[0]]
        self.budget[0] -= len(keep)
        self.kept += len(keep)
        if self.sensitive:
            self.raw.extend(keep)
        else:
            self.file.write(keep)

    def finish(self):
        if self.sensitive:
            self.file.write(b"[sensitive output redacted]\n"[: self.kept])
        elif self.total > self.kept:
            marker = f"\n[output truncated; emitted={self.total} bytes]\n".encode()
            # Replace retained bytes, rather than exceed the shared output cap.
            marker = marker[-self.kept :] if self.kept else b""
            self.file.seek(max(0, self.kept - len(marker)))
            self.file.write(marker)
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()

    def output(self):
        return bytes(self.raw) if self.sensitive else Path(self.file.name).read_bytes()


def _group_signal(pid, sig):
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False


def run_command(
    argv: str | Sequence[str],
    *,
    cwd=None,
    shell=False,
    text=True,
    timeout=None,
    check=False,
    allowed=(0,),
    toolchain=False,
    sensitive=False,
    capture_output=True,
    cancel: Event | None = None,
    terminate_grace_s=5,
    kind=None,
) -> CommandResult:
    if not capture_output:
        raise ValueError("native commands require captured output")
    scope = _scope.get() or CommandScope()
    executable = Path(argv[0]).name if not isinstance(argv, str) and argv else "shell"
    timeout = (
        timeout
        if timeout is not None
        else (GIT_TIMEOUT_S if executable in {"git", "gh"} else scope.timeout_s)
    )
    from blacksmith.run_clock import current
    clock = current()
    if clock is not None:
        timeout = min(timeout, clock.check())
    if not math.isfinite(timeout) or timeout <= 0 or scope.max_output_bytes < 128:
        raise ValueError("command bounds must be positive")
    if not math.isfinite(terminate_grace_s) or not 0 <= terminate_grace_s <= 5:
        raise ValueError("termination grace must be between zero and five seconds")
    kind = kind or executable
    if executable == "git" and not isinstance(argv, str):
        sensitive = sensitive or any(part in {"remote", "config", "var"} for part in argv[1:])
    intent = {
        "argv": "[redacted]" if sensitive else argv,
        "cwd": str(cwd) if cwd else None,
        "shell": shell,
        "timeout_s": timeout,
        "max_output_bytes": scope.max_output_bytes,
        "toolchain": toolchain,
        "stage": scope.stage,
    }
    receipt = (
        scope.journal.command_intent(
            scope.unit_id, scope.attempt_id, f"{scope.stage}:{kind}", intent
        )
        if scope.journal
        else None
    )
    operation_id = receipt.key.id if receipt else str(uuid4())
    root = scope.log_dir or Path(tempfile.mkdtemp(prefix="blacksmith-commands-"))
    log_dir = root / operation_id
    # No command may run until its artifacts can be created.
    log_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    budget = [scope.max_output_bytes]
    captures = [
        _Capture(log_dir / name, budget, sensitive=sensitive)
        for name in ("stdout.log", "stderr.log")
    ]
    from blacksmith.run_context import atomic_write

    atomic_write(log_dir / "intent.json", json.dumps(intent, sort_keys=True))
    proc = None
    error = None
    timed_out = cancelled = False
    started = time.monotonic()
    deadline = started + timeout
    stopping = None
    try:
        if cancel is not None and cancel.is_set():
            cancelled = True
            raise CommandError(f"command cancelled before start; logs: {log_dir}")
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            shell=shell,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=toolchain_environment(scope.credential_vars) if toolchain else None,
        )
        with selectors.DefaultSelector() as selector:
            for stream, capture in zip((proc.stdout, proc.stderr), captures, strict=True):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, capture)
            while selector.get_map() or proc.poll() is None:
                now = time.monotonic()
                if stopping is None and (
                    now >= deadline or (cancel is not None and cancel.is_set())
                    or (clock is not None and clock.cancelled.is_set())
                ):
                    timed_out = now >= deadline
                    cancelled = not timed_out
                    _group_signal(proc.pid, signal.SIGTERM)
                    stopping = now + terminate_grace_s
                # A shell can exit while grandchildren keep its output pipes open.
                if stopping is None and proc.poll() is not None and selector.get_map():
                    _group_signal(proc.pid, signal.SIGTERM)
                    stopping = now + terminate_grace_s
                if stopping is not None and now >= stopping:
                    _group_signal(proc.pid, signal.SIGKILL)
                    # A process that deliberately left the group cannot hold the
                    # controller open forever through inherited pipes.
                    if now >= stopping + 1:
                        for selected in list(selector.get_map().values()):
                            selector.unregister(selected.fileobj)
                            selected.fileobj.close()
                for selected, _ in selector.select(0.05):
                    data = os.read(selected.fileobj.fileno(), 65536)
                    if data:
                        selected.data.add(data)
                    else:
                        selector.unregister(selected.fileobj)
                        selected.fileobj.close()
            proc.wait()
        # Also terminate descendants which redirected/closed inherited output.
        if _group_signal(proc.pid, signal.SIGTERM):
            until = stopping if stopping is not None else time.monotonic() + terminate_grace_s
            while time.monotonic() < until and _group_signal(proc.pid, 0):
                time.sleep(0.05)
            _group_signal(proc.pid, signal.SIGKILL)
    except BaseException as exc:
        error = exc
        if proc is not None:
            until = time.monotonic() + terminate_grace_s
            _group_signal(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=terminate_grace_s)
            except subprocess.TimeoutExpired:
                pass
            while time.monotonic() < until and _group_signal(proc.pid, 0):
                time.sleep(0.05)
            _group_signal(proc.pid, signal.SIGKILL)
            proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream and not stream.closed:
                    stream.close()
    finally:
        for capture in captures:
            capture.finish()
    outcome = {
        "returncode": proc.returncode if proc else None,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "error_type": type(error).__name__ if error else None,
        "duration_s": time.monotonic() - started,
        "log_dir": str(log_dir),
        "stdout_bytes": captures[0].total,
        "stderr_bytes": captures[1].total,
        "stdout_retained_bytes": captures[0].kept,
        "stderr_retained_bytes": captures[1].kept,
        "truncated": any(c.total > c.kept for c in captures),
        "stdout_sha256": hashlib.sha256(captures[0].output()).hexdigest(),
        "stderr_sha256": hashlib.sha256(captures[1].output()).hexdigest(),
    }
    atomic_write(log_dir / "outcome.json", json.dumps(outcome, sort_keys=True))
    if scope.journal:
        status = (
            "interrupted"
            if error or cancelled
            else "failed"
            if timed_out or proc.returncode
            else "completed"
        )
        scope.journal.finish(receipt.key, status, outcome)
    if error:
        error.command_log_dir = log_dir
        raise error
    if clock is not None and clock.error:
        raise clock.error
    outputs = [c.output() for c in captures]
    tails = [bytes(c.tail).decode("utf-8", errors="replace") for c in captures]
    for index, capture in enumerate(captures):
        if capture.total > len(capture.tail) or capture.total > capture.kept:
            tails[index] = (
                f"[output truncated; emitted={capture.total} bytes; logs: {log_dir}]\n"
                + tails[index]
            )
    result = CommandResult(
        argv,
        proc.returncode,
        *(s.decode("utf-8", errors="replace") if text else s for s in outputs),
        *tails,
        log_dir,
        captures[0].total,
        captures[1].total,
        outcome["truncated"],
        timed_out,
        cancelled,
    )
    # Timeout/cancellation can never be mistaken for a passing shell exit.
    if timed_out or cancelled or check:
        result.check_returncode(allowed)
    return result


_native_command = run_command


@functools.wraps(_native_command)
def run_command(*args, **kwargs):
    from blacksmith.run_clock import execution_scope, retention_scope
    scope = _scope.get()
    if scope is not None and scope.stage in {"recovery_capture", "sdk_cleanup"}:
        with retention_scope():
            return _native_command(*args, **kwargs)
    with execution_scope(scope.context if scope else None):
        return _native_command(*args, **kwargs)
