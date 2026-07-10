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

    Spends the ``max_bytes`` budget in priority order:

    1. Every tracked file's path is ALWAYS listed, one per line -- paths alone are
       cheap, so the map never silently omits a file, regardless of budget.
    2. Remaining budget goes to symbol outlines (each file's top-level
       ``def``/``class``/``fn``/``struct``/etc. signatures, listed under its path).
       When the full outline doesn't fit, whole per-file symbol blocks are dropped --
       never a mid-file byte cut -- lowest priority first: files outside ``tests/``
       and ``docs/`` with a recognized extension are kept longest, then files under
       ``tests/``, then everything else (docs/config and unrecognized extensions).

    If any file's symbols were dropped, the map ends with an explicit marker naming
    how many files that happened to. A map that fits under budget untouched is
    returned unchanged, with no marker. Read-only and best-effort: any git failure
    yields ``""`` rather than raising.
    """
    repo_path = Path(repo_path)
    exclude = tuple(exclude)
    files = _list_files(repo_path, exclude)
    if not files:
        return ""

    entries: list[dict[str, Any]] = []
    for rel_path in files:
        ext = Path(rel_path).suffix
        family = _EXT_FAMILY.get(ext)
        symbol_lines: list[str] = []
        if family:
            content = _read_file(repo_path, rel_path)
            if content is not None:
                symbol_lines = [
                    f"  {symbol['signature']}" for symbol in _extract_symbols(content, family)
                ]
        entries.append(
            {
                "path": rel_path,
                "symbol_lines": symbol_lines,
                "priority": _map_priority(rel_path, recognized=family is not None),
            }
        )

    def render(omitted: set[int]) -> str:
        sections = []
        for i, entry in enumerate(entries):
            lines = [entry["path"]]
            if i not in omitted:
                lines.extend(entry["symbol_lines"])
            sections.append("\n".join(lines))
        return "\n".join(sections)

    full = render(set())
    if len(full.encode("utf-8")) <= max_bytes:
        return full

    # Over budget: drop whole per-file symbol blocks, lowest priority first. Paths
    # are never dropped or cut mid-file -- see the priority order in the docstring.
    droppable = sorted(
        (i for i, entry in enumerate(entries) if entry["symbol_lines"]),
        key=lambda i: (-entries[i]["priority"], i),
    )
    if not droppable:
        return full  # nothing droppable; paths alone already exceed max_bytes

    # Reserve worst-case marker space up front so the final (content + marker) fits
    # max_bytes whenever the content alone can be brought under budget.
    marker_reserve = len(_omitted_symbols_marker(len(droppable)).encode("utf-8"))
    content_budget = max(max_bytes - marker_reserve, 0)

    omitted: set[int] = set()
    current = full
    for i in droppable:
        if len(current.encode("utf-8")) <= content_budget:
            break
        omitted.add(i)
        current = render(omitted)

    return current + _omitted_symbols_marker(len(omitted))


def _map_priority(rel_path: str, *, recognized: bool) -> int:
    """Symbol-drop priority for :func:`build_repo_map` -- higher drops first.

    1 (kept longest) = recognized-code extensions outside ``tests/`` and ``docs/``.
    2 = files under a ``tests/`` directory.
    3 (dropped first) = everything else -- docs/config and unrecognized extensions.
    """
    if not recognized:
        return 3
    dirs = Path(rel_path).parts[:-1]
    if "tests" in dirs:
        return 2
    if "docs" in dirs:
        return 3
    return 1


def _omitted_symbols_marker(count: int) -> str:
    noun = "file's" if count == 1 else "files'"
    return f"\n…({count} {noun} symbols omitted)…"


def search_code(
    repo_path: str | Path, query: str, *, limit: int = 20, exclude: Iterable[str] = ()
) -> list[dict]:
    """Rank search results for ``query`` across the repo's tracked files.

    ``query`` is split on whitespace into terms and matched with OR semantics: a
    symbol whose name contains ANY term (case-insensitive) is a symbol hit, and the
    text pass runs ``git grep`` with one ``-e <term>`` per term joined by ``--or``
    (``-F`` for literal, fixed-string terms; ``-i`` for case-insensitivity) instead of
    treating the raw query as a single pattern -- so terms with regex metacharacters
    (``(``, ``.``, ``*``, ...) are matched literally rather than as a broken/surprising
    regex. A single-term query behaves as before, modulo case-insensitivity.

    Symbol *definitions* (extracted the same way as :func:`build_repo_map`) are ranked
    first, followed by plain-text hits -- so a function's definition outranks an
    incidental mention of its name elsewhere. Within each of those two tiers, results
    matching more terms rank above results matching fewer. Results are deduped by
    ``(file, line)`` and capped at ``limit``. Read-only and best-effort: an empty/blank
    query, an absent repo, or any git failure yields ``[]`` rather than raising.
    """
    query = (query or "").strip()
    if not query:
        return []
    terms = query.split()
    if not terms:
        return []
    terms_lower = [term.lower() for term in terms]

    repo_path = Path(repo_path)
    exclude = tuple(exclude)
    files = _list_files(repo_path, exclude)
    if not files:
        return []

    seen: set[tuple[str, int]] = set()
    symbol_hits: list[tuple[int, dict]] = []

    for rel_path in files:
        ext = Path(rel_path).suffix
        family = _EXT_FAMILY.get(ext)
        if not family:
            continue
        content = _read_file(repo_path, rel_path)
        if content is None:
            continue
        for symbol in _extract_symbols(content, family):
            name_lower = symbol["name"].lower()
            match_count = sum(1 for term in terms_lower if term in name_lower)
            if match_count == 0:
                continue
            key = (rel_path, symbol["line"])
            if key in seen:
                continue
            seen.add(key)
            symbol_hits.append(
                (
                    match_count,
                    {
                        "file": rel_path,
                        "line": symbol["line"],
                        "kind": symbol["kind"],
                        "snippet": symbol["signature"],
                    },
                )
            )

    symbol_hits.sort(key=lambda hit: -hit[0])
    results: list[dict] = [hit[1] for hit in symbol_hits[:limit]]

    if len(results) >= limit:
        return results[:limit]

    grep_args = ["grep", "-n", "-I", "-i", "-F"]
    for i, term in enumerate(terms):
        if i:
            grep_args.append("--or")
        grep_args.extend(["-e", term])

    grep_output = _git(repo_path, *grep_args)
    if grep_output:
        text_hits: list[tuple[int, dict]] = []
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
            snippet_lower = snippet.lower()
            match_count = sum(1 for term in terms_lower if term in snippet_lower)
            text_hits.append(
                (
                    match_count,
                    {
                        "file": rel_path,
                        "line": line_no,
                        "kind": "text",
                        "snippet": snippet.strip(),
                    },
                )
            )
        text_hits.sort(key=lambda hit: -hit[0])
        remaining = limit - len(results)
        results.extend(hit[1] for hit in text_hits[:remaining])

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
