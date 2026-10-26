"""One fail-closed policy for agent writes and changed-tree audits.

Contract rules prefixed ``path:``/``glob:`` are hard rules. A whitespace-free
bare entry containing a slash, dot or glob metacharacter is a legacy path rule;
other prose remains prompt guidance. Explicit directory paths cover descendants.
"""

from __future__ import annotations

import fnmatch
import glob
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from blacksmith.commands import CommandError, run_command

DEFAULT_PROTECTED_GLOBS = (
    "*Cargo.lock",
    "*.kindling.yaml",
    "*/migrations/*",
    "*blacksmith/contract.py",
)
READ_TOOLS = frozenset({"Read", "Glob", "Grep"})
WRITE_TARGETS = {
    "Write": ("file_path", "path"),
    "Edit": ("file_path", "path"),
    "MultiEdit": ("file_path", "path"),
    "NotebookEdit": ("notebook_path",),
}


class PolicyViolation(ValueError):
    """A boundary violation or an audit that could not establish safety."""


def _valid_glob(value: str) -> bool:
    i = 0
    while i < len(value):
        if value[i] == "[":
            end = i + 1
            if end < len(value) and value[end] == "!":
                end += 1
            if end < len(value) and value[end] == "]":
                end += 1
            end = value.find("]", end)
            if end == -1:
                return False
            i = end
        i += 1
    return True


def compile_path_rules(untouchables: Iterable[str]) -> tuple[str, ...]:
    rules = list(DEFAULT_PROTECTED_GLOBS)
    for entry in untouchables:
        explicit = entry.startswith(("path:", "glob:"))
        value = entry.split(":", 1)[1].strip() if explicit else entry
        bare_path = not any(c.isspace() for c in value) and any(c in value for c in "/.*?[")
        if not explicit and not bare_path:
            continue
        if (
            not value
            or value.startswith(("/", "\\"))
            or "\\" in value
            or ".." in PurePosixPath(value).parts
            or any(ord(c) < 32 for c in value)
            or not PurePosixPath(value).parts
            or (not entry.startswith("path:") and not _valid_glob(value))
        ):
            raise PolicyViolation(f"malformed path untouchable: {entry!r}")
        value = value.removeprefix("./").rstrip("/")
        if entry.startswith("path:"):
            # Explicit paths are literal, even if a filename contains '[' or '*'.
            rules.extend((glob.escape(value), glob.escape(value) + "/*"))
        else:
            rules.append(value)
    return tuple(dict.fromkeys(rules))


def is_protected(path: str, protected_globs: Iterable[str] = DEFAULT_PROTECTED_GLOBS) -> bool:
    posix = PurePosixPath(path).as_posix()
    parts = PurePosixPath(posix).parts
    # These protections cannot be removed by supplying an empty custom rule list.
    if any(
        p in {".git", ".gitmodules", ".mcp.json", "blacksmith.toml", "blacksmith.config.toml"}
        for p in parts
    ):
        return True
    if any(
        parts[i] == ".claude" and fnmatch.fnmatchcase(parts[i + 1], "settings*.json")
        for i in range(len(parts) - 1)
    ):
        return True
    for rule in protected_globs:
        if fnmatch.fnmatchcase(posix, rule) or fnmatch.fnmatchcase("/" + posix.lstrip("/"), rule):
            return True
        if not any(c in rule for c in "*?[") and posix.startswith(rule.rstrip("/") + "/"):
            return True
    return False


@dataclass(frozen=True)
class PathPolicy:
    root: Path
    rules: tuple[str, ...] = DEFAULT_PROTECTED_GLOBS
    _lexical_root: Path = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_lexical_root", Path(os.path.abspath(self.root)))
        object.__setattr__(self, "root", Path(self.root).resolve())

    def violation(self, value: object) -> str | None:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            return "missing_or_malformed_target"
        candidate = Path(value)
        if candidate.is_absolute():
            # Accept the caller's root spelling (e.g. macOS /tmp -> /private/tmp)
            # without accepting a link introduced *inside* the workspace.
            try:
                lexical = self.root / candidate.relative_to(self._lexical_root)
            except ValueError:
                lexical = candidate
        else:
            lexical = self.root / candidate
        try:
            resolved = lexical.resolve()
            relative = resolved.relative_to(self.root)
            lexical_relative = lexical.relative_to(self.root)
        except (ValueError, OSError, RuntimeError):
            return "outside_worktree"
        if is_protected(lexical_relative.as_posix(), self.rules) or is_protected(
            relative.as_posix(), self.rules
        ):
            return "untouchable"
        if relative == Path("."):
            return "missing_or_malformed_target"
        # Inspect the lexical chain, not just resolve()'s final destination. Even an
        # in-root link is denied: a different process could retarget it before Write.
        current = self.root
        for part in lexical_relative.parts:
            current /= part
            if current.is_symlink():
                return "symlink_target"
        return None

    def audit(self, base_commit: str) -> list[str]:
        """Check tracked/index/worktree changes, both rename sides, and new source.

        --no-renames deliberately represents renames as deletion+addition. NUL
        delimiters preserve filenames containing whitespace/newlines. Git failures
        are infrastructure errors, never an empty successful audit.
        """
        paths: set[str] = set()
        for args in (
            ("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", base_commit, "--"),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ):
            try:
                proc = run_command(
                    ["git", "-C", os.fspath(self.root), *args],
                    capture_output=True,
                    text=False,
                    sensitive=True,
                )
                proc.require_complete_output()
            except (OSError, CommandError) as exc:
                raise PolicyViolation(f"path audit failed: {exc}") from exc
            if proc.returncode:
                raise PolicyViolation(f"path audit failed: {proc.stderr.decode(errors='replace')}")
            paths.update(os.fsdecode(p) for p in proc.stdout.split(b"\0") if p)
        for path in sorted(paths):
            if reason := self.violation(path):
                raise PolicyViolation(f"policy_violation: {reason}: {path}")
        return sorted(paths)


def policy_for_state(state: dict, root: str | Path | None = None) -> PathPolicy:
    from blacksmith.context import requirements_prd

    prd = requirements_prd(state)
    rules = compile_path_rules(prd.contract.untouchables if prd else ())
    if state.get("run_context"):
        from blacksmith.run_context import context_from_state

        context = context_from_state(state)
        if context.runtime_config_path:
            try:
                relative = Path(context.runtime_config_path).relative_to(context.repo_path)
            except ValueError:
                pass  # Config outside the source checkout is outside every clone already.
            else:
                rules += compile_path_rules(["path: " + relative.as_posix()])
    return PathPolicy(Path(root or state["worktree_path"]), rules)
