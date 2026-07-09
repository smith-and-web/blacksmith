"""Read-only repo map + symbol/text search over the worktree (git-backed, stdlib only).

Additive and OFF by default (PRD, index feature): this module exists purely as a
library of pure functions. Nothing here is wired into the graph/executor/prompt — a
separate, later unit does that behind a config flag. Importing this module has zero
effect on today's behaviour.

Structural/lexical only, on purpose: file enumeration goes through ``git ls-files``,
text search through ``git grep -n`` (git is already a hard dependency), and symbol
extraction is done with a handful of stdlib ``re`` patterns per file extension. No
embeddings, no vector store, no new third-party dependency — that stays out of scope.

Both public functions are:

* **read-only** — they only ever enumerate/read files via git plumbing or plain
  filesystem reads; nothing here writes to, stages, or mutates the target repo.
* **best-effort** — a git failure (not a repo, no git on PATH, timeout, etc.) is
  swallowed and yields an empty map/list rather than raising, so a broken index never
  takes down a run.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

_TRUNCATION_MARKER = "…(truncated)"

# Which "family" of symbol patterns applies to a given file extension.
_EXT_FAMILY = {
    ".py": "python",
    ".rs": "rust",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "js",
    ".tsx": "js",
}

# Lightweight, best-effort per-family (kind, regex) pairs. Each pattern is anchored at
# the start of a line (re.MULTILINE `^`) so only top-level (unindented) declarations
# match — nested/indented members are deliberately skipped to keep the map compact.
_FAMILY_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "python": [
        ("class", re.compile(r"^class\s+(?P<name>\w+)\b.*$", re.MULTILINE)),
        ("function", re.compile(r"^def\s+(?P<name>\w+)\s*\(.*$", re.MULTILINE)),
    ],
    "rust": [
        (
            "struct",
            re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?struct\s+(?P<name>\w+)\b.*$", re.MULTILINE),
        ),
        (
            "trait",
            re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?trait\s+(?P<name>\w+)\b.*$", re.MULTILINE),
        ),
        (
            "impl",
            re.compile(
                r"^impl(?:<[^>]*>)?\s+(?:\S+\s+for\s+)?(?P<name>\w+)\b.*$", re.MULTILINE
            ),
        ),
        (
            "function",
            re.compile(
                r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>\w+)\b.*$", re.MULTILINE
            ),
        ),
    ],
    "js": [
        (
            "class",
            re.compile(
                r"^(?:export\s+(?:default\s+)?)?class\s+(?P<name>\w+)\b.*$", re.MULTILINE
            ),
        ),
        (
            "function",
            re.compile(
                r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s+"
                r"(?P<name>\w+)\s*\(.*$",
                re.MULTILINE,
            ),
        ),
        (
            "const",
            re.compile(r"^(?:export\s+)?const\s+(?P<name>\w+)\s*=.*$", re.MULTILINE),
        ),
    ],
}


def build_repo_map(repo_path: str | Path, *, max_bytes: int, exclude: Iterable[str] = ()) -> str:
    """Build a compact outline of the repo: tracked files + their top-level symbols.

    Enumerates files with ``git ls-files`` and, for recognized extensions, lists each
    file's top-level ``def``/``class``/``fn``/``struct``/etc. signatures underneath it.
    The result is truncated to ``max_bytes`` (UTF-8 encoded) with an explicit
    ``"…(truncated)"`` marker appended when a cut was made. Read-only and best-effort:
    any git failure yields ``""`` rather than raising.
    """
    repo_path = Path(repo_path)
    exclude = tuple(exclude)
    files = _list_files(repo_path, exclude)
    if not files:
        return ""

    sections: list[str] = []
    for rel_path in files:
        ext = Path(rel_path).suffix
        family = _EXT_FAMILY.get(ext)
        symbols = []
        if family:
            content = _read_file(repo_path, rel_path)
            if content is not None:
                symbols = _extract_symbols(content, family)
        lines = [rel_path]
        lines.extend(f"  {symbol['signature']}" for symbol in symbols)
        sections.append("\n".join(lines))

    full = "\n".join(sections)
    encoded = full.encode("utf-8")
    if len(encoded) <= max_bytes:
        return full

    marker_bytes = _TRUNCATION_MARKER.encode("utf-8")
    budget = max(max_bytes - len(marker_bytes), 0)
    # Decode with errors="ignore" so a hard byte-budget cut can't land mid-codepoint.
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + _TRUNCATION_MARKER


def search_code(
    repo_path: str | Path, query: str, *, limit: int = 20, exclude: Iterable[str] = ()
) -> list[dict]:
    """Rank search results for ``query`` across the repo's tracked files.

    Symbol *definitions* (extracted the same way as :func:`build_repo_map`) are ranked
    first, followed by plain-text ``git grep -n`` hits — so a function's definition
    outranks an incidental mention of its name elsewhere. Results are deduped by
    ``(file, line)`` and capped at ``limit``. Read-only and best-effort: an empty/blank
    query, an absent repo, or any git failure yields ``[]`` rather than raising.
    """
    query = (query or "").strip()
    if not query:
        return []

    repo_path = Path(repo_path)
    exclude = tuple(exclude)
    files = _list_files(repo_path, exclude)
    if not files:
        return []

    results: list[dict] = []
    seen: set[tuple[str, int]] = set()
    needle = query.lower()

    for rel_path in files:
        if len(results) >= limit:
            return results[:limit]
        ext = Path(rel_path).suffix
        family = _EXT_FAMILY.get(ext)
        if not family:
            continue
        content = _read_file(repo_path, rel_path)
        if content is None:
            continue
        for symbol in _extract_symbols(content, family):
            if needle not in symbol["name"].lower():
                continue
            key = (rel_path, symbol["line"])
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "file": rel_path,
                    "line": symbol["line"],
                    "kind": symbol["kind"],
                    "snippet": symbol["signature"],
                }
            )
            if len(results) >= limit:
                break

    if len(results) >= limit:
        return results[:limit]

    grep_output = _git(repo_path, "grep", "-n", "-I", "--", query)
    if grep_output:
        for line in grep_output.splitlines():
            parsed = _parse_grep_line(line)
            if parsed is None:
                continue
            rel_path, line_no, snippet = parsed
            if exclude and _is_excluded(rel_path, exclude):
                continue
            key = (rel_path, line_no)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {"file": rel_path, "line": line_no, "kind": "text", "snippet": snippet.strip()}
            )
            if len(results) >= limit:
                break

    return results[:limit]


def _extract_symbols(content: str, family: str) -> list[dict]:
    symbols: list[dict] = []
    for kind, pattern in _FAMILY_PATTERNS.get(family, []):
        for match in pattern.finditer(content):
            line_no = content.count("\n", 0, match.start()) + 1
            symbols.append(
                {
                    "kind": kind,
                    "name": match.group("name"),
                    "signature": match.group(0).strip(),
                    "line": line_no,
                }
            )
    symbols.sort(key=lambda symbol: symbol["line"])
    return symbols


def _list_files(repo_path: Path, exclude: tuple[str, ...]) -> list[str]:
    out = _git(repo_path, "ls-files")
    if out is None:
        return []
    files = [line for line in out.splitlines() if line]
    if exclude:
        files = [f for f in files if not _is_excluded(f, exclude)]
    return files


def _read_file(repo_path: Path, rel_path: str) -> str | None:
    try:
        return (repo_path / rel_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _parse_grep_line(line: str) -> tuple[str, int, str] | None:
    parts = line.split(":", 2)
    if len(parts) != 3:
        return None
    rel_path, line_part, snippet = parts
    try:
        line_no = int(line_part)
    except ValueError:
        return None
    return rel_path, line_no, snippet


def _is_excluded(rel_path: str, exclude: tuple[str, ...]) -> bool:
    for pattern in exclude:
        if not pattern:
            continue
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if fnmatch.fnmatch(rel_path, f"{pattern.rstrip('/')}/*"):
            return True
    return False


def _git(repo_path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


# --- search_code tool (WU-SEARCH-TOOL) --------------------------------------------------
#
# Grants the implementer a controlled way to ask "where is X defined/mentioned?" over the
# run's own worktree, instead of guessing paths or paying for blind Read/Glob/Grep turns.
# Built with the SAME in-process ``create_sdk_mcp_server``/``tool`` pattern
# :mod:`blacksmith.sandbox` uses for its ``run_command`` tool -- no subprocess, no IPC, no
# new dependency. The handler routes straight into :func:`search_code` above, which stays
# read-only and best-effort exactly as documented there; this tool adds no new execution
# path or write capability. ADDITIVE and wired only when ``[index].enabled`` — see
# ``blacksmith.nodes.implement`` for the enabled/disabled gating.

SEARCH_CODE_TOOL_NAME = "search_code"

DEFAULT_SEARCH_LIMIT = 20


def format_search_results(results: list[dict]) -> str:
    """Render :func:`search_code`'s ranked matches as the compact text handed to the agent.

    One match per line, ``path:line: snippet`` — cheap for the agent to scan and cheap on
    tokens. An empty result list renders as an explicit "no matches" note rather than
    blank text, so the agent doesn't mistake silence for a tool failure.
    """
    if not results:
        return "no matches"
    return "\n".join(f"{r['file']}:{r['line']}: {r['snippet']}" for r in results)


def make_search_code_tool(
    repo_path: str | Path,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude: Iterable[str] = (),
) -> SdkMcpTool[Any]:
    """Build the ``search_code`` SDK tool bound to ``repo_path`` (the run's worktree).

    The handler NEVER does anything but call :func:`search_code` against ``repo_path`` --
    read-only, git-backed structural/lexical search over the same worktree the implementer
    is already editing. Results are rendered with :func:`format_search_results`.
    """
    exclude = tuple(exclude)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        results = search_code(repo_path, args.get("query", ""), limit=limit, exclude=exclude)
        text = format_search_results(results)
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        SEARCH_CODE_TOOL_NAME,
        "Search this repository's tracked files for a symbol name or text query. Returns "
        "ranked matches (symbol definitions first, then plain-text hits) as `file:line: "
        "snippet` lines -- use it to find where something is defined or mentioned instead "
        "of guessing paths or reading files blind.",
        {"query": str},
    )(handler)


def create_index_mcp_server(
    repo_path: str | Path,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude: Iterable[str] = (),
) -> McpSdkServerConfig:
    """Build the in-process MCP server exposing ``search_code`` for a call's
    ``ClaudeAgentOptions.mcp_servers`` -- the same in-process (no subprocess, no IPC)
    ``create_sdk_mcp_server``/``tool`` pattern :mod:`blacksmith.sandbox` uses for
    ``run_command``."""
    return create_sdk_mcp_server(
        name="blacksmith-index",
        tools=[make_search_code_tool(repo_path, limit=limit, exclude=exclude)],
    )
