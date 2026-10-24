"""Immutable version-2 run inputs, pre-spend preflight, paths and execution locks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from blacksmith.config import BlacksmithConfig
from blacksmith.contract import PRD, ContractError, parse_prd_text, validate_execution_contract
from blacksmith.gate import GateError, TargetToolchain, parse_toolchain
from blacksmith.receipts import OperationJournal, OperationKey, ReceiptError

STATE_VERSION = 2


class RunContextError(Exception):
    """Preflight, integrity or lock failure; no new paid work may be admitted."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def origin_identity(url: str, *, allow_slug: bool = False) -> str:
    """Canonical host + full repository path; never persist embedded credentials."""
    url = url.strip()
    if "://" in url:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"https", "http", "ssh", "git"} or not parsed.hostname
            or parsed.password or parsed.query or parsed.fragment
            or (parsed.username and not (parsed.scheme == "ssh" and parsed.username == "git"))
        ):
            raise RunContextError(
                "unsupported or credential-bearing origin; use a credential helper"
            )
        host = parsed.hostname.lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise RunContextError("invalid origin port") from exc
        if port and port != {"https": 443, "http": 80, "ssh": 22, "git": 9418}[parsed.scheme]:
            host += f":{port}"
        path = parsed.path.strip("/")
    elif match := re.fullmatch(r"(?:git@)?([^/:@]+):([^:]+)", url):
        host, path = match.groups()
    elif allow_slug and re.fullmatch(r"[^/\s]+/[^/\s]+", url):
        host, path = "github.com", url
    else:
        raise RunContextError("origin must identify a remote host and owner/repository")
    path = path.rstrip("/").removesuffix(".git")
    if len(path.split("/")) < 2 or any(p in {"", ".", ".."} for p in path.split("/")):
        raise RunContextError("origin must identify a remote host and owner/repository")
    return f"{host}/{path}".lower()


def git_read(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunContextError(f"Git preflight failed ({args[0]}): {type(exc).__name__}") from exc
    if proc.returncode:
        # Do not echo credential-bearing origin or configuration output.
        raise RunContextError(f"Git preflight failed ({args[0]}), exit {proc.returncode}")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise RunContextError(f"Git preflight returned non-UTF-8 output ({args[0]})") from exc


def atomic_write(path: Path, text: str) -> None:
    """Replace a critical artifact atomically and fsync both the file and directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


class RunLock:
    """Nonblocking POSIX lock held for the entire drive, including approval waits."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RunContextError(f"run_already_locked: {self.path}") from exc
        self._fd = fd
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def thread_lock(config: BlacksmithConfig, thread_id: str) -> RunLock:
    return RunLock(config.config_dir / ".blacksmith" / "locks" / f"{sha256(thread_id)}.lock")


def unit_hash(unit_id: str) -> str:
    return sha256(unit_id)


def run_branch(run_id: str, unit_id: str) -> str:
    UUID(run_id)
    slug = re.sub(r"[^a-z0-9]+", "-", unit_id.lower()).strip("-")[:48] or "unit"
    return f"blacksmith/{run_id}/{slug}-{unit_hash(unit_id)[:16]}"


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_version: Literal[2] = STATE_VERSION
    run_id: str
    thread_id: str
    repo_path: str
    origin: str
    origin_url: str
    base_commit: str
    default_branch: str
    prd_path: str
    prd_text: str
    prd_sha256: str
    runtime_config_json: str
    runtime_config_sha256: str
    runtime_config_path: str | None
    toolchain_text: str
    toolchain_sha256: str
    run_dir: str

    @model_validator(mode="after")
    def _integrity(self):
        UUID(self.run_id)
        if not self.thread_id.strip() or any(ord(c) < 32 for c in self.thread_id):
            raise ValueError("invalid thread ID")
        if not re.fullmatch(r"[0-9a-f]{40,64}", self.base_commit):
            raise ValueError("invalid base commit")
        for value, digest in (
            (self.prd_text, self.prd_sha256),
            (self.runtime_config_json, self.runtime_config_sha256),
            (self.toolchain_text, self.toolchain_sha256),
        ):
            if sha256(value) != digest:
                raise ValueError("snapshot hash mismatch")
        if origin_identity(self.origin_url) != self.origin:
            raise ValueError("origin identity mismatch")
        for path in (self.repo_path, self.run_dir, self.prd_path):
            if str(Path(path).resolve()) != path:
                raise ValueError("noncanonical persisted path")
        config = self.execution_config()
        expected_dir = config.config_dir / ".blacksmith" / "runs" / self.run_id
        if self.directory != expected_dir:
            raise ValueError("run directory does not belong to this run")
        parse_toolchain(self.toolchain_text)
        validate_execution_contract(self.prd().contract)
        return self

    @property
    def directory(self) -> Path:
        return Path(self.run_dir)

    @property
    def planning_path(self) -> Path:
        return self.directory / "planning"

    @property
    def workspace_path(self) -> Path:
        return self.directory / "workspace"

    def unit_path(self, unit_id: str) -> Path:
        return self.directory / "units" / unit_hash(unit_id) / "workspace"

    def lock(self) -> RunLock:
        return RunLock(self.directory / "execution.lock")

    def journal(self) -> OperationJournal:
        return OperationJournal.for_context(self)

    def prd(self) -> PRD:
        return parse_prd_text(self.prd_text, path=self.prd_path)

    def toolchain(self) -> TargetToolchain:
        return parse_toolchain(self.toolchain_text)

    def execution_config(self) -> BlacksmithConfig:
        config = BlacksmithConfig.model_validate_json(self.runtime_config_json)
        if config.target.repo_path != Path(self.repo_path):
            raise ValueError("runtime target differs from snapshot identity")
        config._source_path = (
            Path(self.runtime_config_path) if self.runtime_config_path
            else self.directory.parents[2] / "blacksmith.config.toml"
        )
        return config

    def initial_state(self) -> dict:
        return {
            "state_version": STATE_VERSION, "run_id": self.run_id,
            "run_context": self.model_dump(), "planning_path": str(self.planning_path),
            "prd": self.prd(), "prd_path": self.prd_path,
        }

    def save(self) -> None:
        data = self.model_dump()
        atomic_write(self.directory / "manifest.json", canonical_json({
            "context": data, "sha256": sha256(canonical_json(data)),
        }) + "\n")
        atomic_write(self.directory / "input.prd.md", self.prd_text)
        atomic_write(self.directory / "runtime-config.json", self.runtime_config_json)
        atomic_write(self.directory / "trusted-blacksmith.toml", self.toolchain_text)

    def verify(self, *, selected_repo: Path, require_workspace: bool = False) -> None:
        """Read-only reconciliation of immutable input files and source identity."""
        if selected_repo.resolve() != Path(self.repo_path):
            raise RunContextError("selected repository differs from recorded run repository")
        try:
            manifest = json.loads((self.directory / "manifest.json").read_text())
            if manifest != {"context": self.model_dump(),
                            "sha256": sha256(canonical_json(self.model_dump()))}:
                raise ValueError("manifest mismatch")
            for name, digest in (
                ("input.prd.md", self.prd_sha256),
                ("runtime-config.json", self.runtime_config_sha256),
                ("trusted-blacksmith.toml", self.toolchain_sha256),
            ):
                if hashlib.sha256((self.directory / name).read_bytes()).hexdigest() != digest:
                    raise ValueError("artifact mismatch")
        except (OSError, ValueError, KeyError) as exc:
            raise RunContextError(f"missing/corrupt run artifacts: {self.directory}") from exc
        self.journal().verify()  # Never replace a missing/corrupt critical recovery journal.
        origin = git_read(Path(self.repo_path), "remote", "get-url", "origin").strip()
        if origin_identity(origin) != self.origin:
            raise RunContextError("source origin differs from recorded run identity")
        if not self.planning_path.is_dir():
            raise RunContextError(f"missing planning snapshot: {self.planning_path}")
        if git_read(self.planning_path, "rev-parse", "HEAD").strip() != self.base_commit:
            raise RunContextError("planning snapshot HEAD differs from recorded base commit")
        if git_read(self.planning_path, "status", "--porcelain", "--untracked-files=all").strip():
            raise RunContextError("planning snapshot has unexpected changes")
        if require_workspace and not self.workspace_path.is_dir():
            raise RunContextError(f"missing run workspace: {self.workspace_path}")


def context_from_state(state: dict) -> RunContext:
    if state.get("state_version") != STATE_VERSION:
        raise RunContextError("legacy_checkpoint_requires_manual_recovery")
    try:
        context = RunContext.model_validate(state["run_context"])
        if state.get("run_id") != context.run_id:
            raise ValueError("run ID mismatch")
        return context
    except (KeyError, ValueError, ValidationError, GateError, ContractError) as exc:
        raise RunContextError("missing/corrupt version-2 run context") from exc


def create_run_context(
    config: BlacksmithConfig, prd: PRD, thread_id: str,
    *, required_tools: tuple[str, ...] = ("git", "gh", "claude"),
) -> RunContext:
    """Preflight once; materialize immutable artifacts before the first paid call."""
    if not thread_id.strip() or any(ord(c) < 32 for c in thread_id):
        raise RunContextError("thread-id must be nonempty and contain no control characters")
    config = config.resolved()
    validate_execution_contract(prd.contract)
    if not prd.text or len(prd.text.encode("utf-8")) > config.limits.max_prd_bytes:
        raise RunContextError("PRD exceeds max_prd_bytes or lacks full input; split the PRD")
    for tool in required_tools:
        if shutil.which(tool) is None:
            raise RunContextError(f"required host tool is unavailable: {tool}")
    for name in (config.api.key_env_var, config.api.admin_key_env_var):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise RunContextError(
                "API settings must contain environment variable names, not values"
            )
    repo = config.resolve_repo_path().resolve()
    git_read(repo, "check-ref-format", "--branch", config.target.default_branch)
    base = git_read(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if git_read(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise RunContextError("tracked checkout is dirty; commit or stash changes before running")
    origin_url = git_read(repo, "remote", "get-url", "origin").strip()
    origin = origin_identity(origin_url)
    expected = origin_identity(prd.contract.primary_target_repo, allow_slug=True)
    if expected != origin:
        raise RunContextError(f"target repo mismatch: {origin!r}; PRD expects {expected!r}")
    identity = git_read(repo, "var", "GIT_AUTHOR_IDENT").strip()
    git_read(repo, "var", "GIT_COMMITTER_IDENT")
    match = re.fullmatch(r"(.*) <([^<>]+)> \d+ [+-]\d{4}", identity)
    if not match:
        raise RunContextError("Git author identity is unavailable")
    try:
        toolchain_text = git_read(repo, "show", f"{base}:blacksmith.toml")
    except RunContextError as exc:
        raise RunContextError("source commit must contain a committed blacksmith.toml") from exc
    toolchain = parse_toolchain(toolchain_text)
    for unit in prd.contract.work_units:
        for layer in unit.layers:
            if prd.contract.layers[layer] == "auto":
                commands = toolchain.commands_for(layer)
                if any(cmd is not None and not cmd.strip() for cmd in commands):
                    raise RunContextError(f"empty required toolchain command for layer {layer}")
    config = config.model_copy(update={
        "target": config.target.model_copy(update={"repo_path": repo}),
    })
    snapshot = canonical_json(config.model_dump(mode="json"))
    run_id = str(uuid4())
    directory = config.config_dir / ".blacksmith" / "runs" / run_id
    context = RunContext(
        run_id=run_id, thread_id=thread_id, repo_path=str(repo), origin=origin,
        origin_url=origin_url, base_commit=base, default_branch=config.target.default_branch,
        prd_path=str(prd.path.resolve()), prd_text=prd.text, prd_sha256=sha256(prd.text),
        runtime_config_json=snapshot, runtime_config_sha256=sha256(snapshot),
        runtime_config_path=str(config.config_path) if config.config_path else None,
        toolchain_text=toolchain_text, toolchain_sha256=sha256(toolchain_text),
        run_dir=str(directory),
    )
    directory.mkdir(parents=True, exist_ok=False)
    try:
        context.save()  # Critical input persistence fails closed before planning.
        journal = OperationJournal.create(context)
        operation = OperationKey(context.run_id, "__run__", "bootstrap", "git.planning_snapshot")
        journal.intent(operation, {"source": str(repo), "base_commit": base,
                                   "destination": str(context.planning_path)})
        git_read(repo.parent, "clone", "--local", "--no-hardlinks", "--no-checkout",
                 str(repo), str(context.planning_path))
        git_read(context.planning_path, "checkout", "--detach", base)
        git_read(context.planning_path, "remote", "set-url", "origin", origin_url)
        for key, value in zip(("user.name", "user.email"), match.groups(), strict=True):
            git_read(context.planning_path, "config", key, value)
        # No hard links and no symlink following: chmod must never alter source files.
        paths = sorted(context.planning_path.rglob("*"), key=lambda p: len(p.parts), reverse=True)
        for path in paths:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
        context.planning_path.chmod(context.planning_path.stat().st_mode & ~0o222)
        context.verify(selected_repo=repo)
        journal.finish(operation, "completed", {"base_commit": base,
                                                "planning_path": str(context.planning_path)})
    except (OSError, RunContextError, ReceiptError) as exc:
        raise RunContextError(
            f"preflight snapshot failed; retained artifacts: {directory}"
        ) from exc
    return context
