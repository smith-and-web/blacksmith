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

import ast
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


def build_repo_map(
    repo_path: str | Path,
    *,
    max_bytes: int,
    exclude: Iterable[str] = (),
    rank_by_graph: bool = False,
) -> str:
    """Build a compact outline of the repo: tracked files + their top-level symbols.

    Spends the ``max_bytes`` budget in priority order:

    1. Every tracked file's path is ALWAYS listed, one per line -- paths alone are
       cheap, so the map never silently omits a file, regardless of budget.
    2. Remaining budget goes to symbol outlines (each file's top-level
       ``def``/``class``/``fn``/``struct``/etc. signatures, listed under its path).
       When the full outline doesn't fit, whole per-file symbol blocks are dropped --
       never a mid-file byte cut. By default (``rank_by_graph=False``) the drop order
       is the directory heuristic: files outside ``tests/`` and ``docs/`` with a
       recognized extension are kept longest, then files under ``tests/``, then
       everything else (docs/config and unrecognized extensions) -- see
       :func:`_map_priority`.

       When ``rank_by_graph=True``, the drop order instead follows graph centrality
       (:func:`build_reference_graph` + :func:`rank_files`, WU-DEP-GRAPH), ascending:
       the least-central (least-referenced) files' symbols are dropped first, so the
       byte budget is spent on the most-referenced files. Best-effort -- an empty or
       unrankable graph falls back to the directory heuristic above rather than
       raising.

    If any file's symbols were dropped, the map ends with an explicit marker naming
    how many files that happened to. A map that fits under budget untouched is
    returned unchanged, with no marker, regardless of ``rank_by_graph``. Read-only and
    best-effort: any git failure yields ``""`` rather than raising.
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

    # Over budget: drop whole per-file symbol blocks. Paths are never dropped or cut
    # mid-file -- see the priority order in the docstring.
    ranks: dict[str, float] | None = None
    if rank_by_graph:
        try:
            graph = build_reference_graph(repo_path, exclude=exclude)
            computed = rank_files(graph) if graph else {}
        except Exception:
            computed = {}
        ranks = computed or None

    if ranks is not None:
        # Ascending centrality: least-referenced files' symbols dropped first.
        droppable = sorted(
            (i for i, entry in enumerate(entries) if entry["symbol_lines"]),
            key=lambda i: (ranks.get(entries[i]["path"], 0.0), i),
        )
    else:
        # Fallback / default: directory heuristic, lowest priority first.
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
    repo_path: str | Path,
    query: str,
    *,
    limit: int = 20,
    exclude: Iterable[str] = (),
    path: str | None = None,
) -> list[dict]:
    """Rank search results for ``query`` across the repo's tracked files.

    ``path`` (optional) scopes the search to files matching a path or glob (fnmatch
    against the repo-relative path, or a directory prefix -- the same two forms
    ``exclude`` accepts). This exists because "which lines in THIS file mention X?" is
    a real, observed query shape (a reviewer/planner narrowing into one test file) and
    without it agents fall back to path-scoped ``Grep`` calls the index can't answer.

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

    Each result dict also carries ``context``: up to 2 lines immediately following the
    matched line, read straight from the file on disk (best-effort -- a missing or
    unreadable file yields ``context: []``, leaving the hit's ``snippet`` as the only
    content).
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
    if path:
        files = [f for f in files if _matches_path(f, path)]
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
                        "context": _context_after(content, symbol["line"]),
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
        content_cache: dict[str, str | None] = {}
        for line in grep_output.splitlines():
            parsed = _parse_grep_line(line)
            if parsed is None:
                continue
            rel_path, line_no, snippet = parsed
            if exclude and _is_excluded(rel_path, exclude):
                continue
            if path and not _matches_path(rel_path, path):
                continue
            key = (rel_path, line_no)
            if key in seen:
                continue
            seen.add(key)
            snippet_lower = snippet.lower()
            match_count = sum(1 for term in terms_lower if term in snippet_lower)
            if rel_path not in content_cache:
                content_cache[rel_path] = _read_file(repo_path, rel_path)
            text_hits.append(
                (
                    match_count,
                    {
                        "file": rel_path,
                        "line": line_no,
                        "kind": "text",
                        "snippet": snippet.strip(),
                        "context": _context_after(content_cache[rel_path], line_no),
                    },
                )
            )
        text_hits.sort(key=lambda hit: -hit[0])
        remaining = limit - len(results)
        results.extend(hit[1] for hit in text_hits[:remaining])

    return results[:limit]


READ_SYMBOL_MAX_LINES = 150

READ_SYMBOL_TRUNCATION_MARKER = f"…(truncated at {READ_SYMBOL_MAX_LINES} lines)…"


def read_symbol(repo_path: str | Path, file: str, name: str) -> str:
    """Return the source block of the top-level symbol ``name`` in tracked ``file``.

    The block runs from the symbol's own definition line (``def``/``class``/etc., same
    extraction as :func:`build_repo_map`/:func:`search_code`) up to -- but not
    including -- the next top-level symbol's definition line, or end of file if it's
    the last one. A next symbol's leading decorator lines (``@...`` directly above its
    definition, no blank line between them) belong to *that* symbol, not the one being
    read, so the boundary is walked back above them.

    The output's first line is a ``file:start-end`` header (1-indexed, inclusive) naming
    the block's line span, so a caller can follow up with an offset-scoped ``Read`` (or
    anchor an edit) without grepping for the line number. The header always spans the
    FULL block, even when the body below is truncated.

    Capped at :data:`READ_SYMBOL_MAX_LINES` lines; a block longer than that is cut and
    an explicit :data:`READ_SYMBOL_TRUNCATION_MARKER` line is appended so the agent
    knows the block was cut rather than mistaking it for the whole thing.

    An unknown/untracked ``file``, an unreadable file, or an unknown ``name`` returns a
    clear "not found" message -- naming the file's known top-level symbols when the
    file exists and is readable -- instead of raising, so the agent can self-correct in
    one turn. Read-only and best-effort, same as every other lookup in this module.
    """
    repo_path = Path(repo_path)
    tracked = set(_list_files(repo_path, ()))
    if file not in tracked:
        return f"not found: '{file}' is not a tracked file in this repo"

    content = _read_file(repo_path, file)
    if content is None:
        return f"not found: '{file}' could not be read"

    family = _EXT_FAMILY.get(Path(file).suffix)
    symbols = _extract_symbols(content, family) if family else []

    match = next((symbol for symbol in symbols if symbol["name"] == name), None)
    if match is None:
        return _symbol_not_found_message(file, name, symbols)

    lines = content.splitlines()
    start_line = match["line"]
    later_lines = [symbol["line"] for symbol in symbols if symbol["line"] > start_line]
    if later_lines:
        end_line = _decorator_adjusted_line(lines, min(later_lines))
    else:
        end_line = len(lines) + 1

    block = lines[start_line - 1 : end_line - 1]
    # First line is a `file:start-end` header (1-indexed, inclusive, the FULL block's
    # span even when the body below is truncated) so the agent can follow up with an
    # offset-scoped Read or anchor an Edit without a Grep just to learn the line number
    # -- an observed wasted turn.
    header = f"{file}:{start_line}-{end_line - 1}"
    truncated = len(block) > READ_SYMBOL_MAX_LINES
    if truncated:
        block = block[:READ_SYMBOL_MAX_LINES]
    text = header + "\n" + "\n".join(block)
    if truncated:
        text += f"\n{READ_SYMBOL_TRUNCATION_MARKER}"
    return text


def _decorator_adjusted_line(lines: list[str], line_no: int) -> int:
    """Walk ``line_no`` (1-indexed) back over contiguous decorator lines directly above.

    A decorator (``@foo``) immediately preceding a definition is part of that
    definition, not of whatever precedes it -- so the caller, which uses this only as
    the *end* boundary for the previous symbol's block, stops before the decorator
    rather than swallowing it into the wrong symbol.
    """
    idx = line_no - 1
    while idx > 0 and lines[idx - 1].strip().startswith("@"):
        idx -= 1
    return idx + 1


def _symbol_not_found_message(file: str, name: str, symbols: list[dict]) -> str:
    if not symbols:
        return (
            f"not found: no symbol '{name}' in '{file}' -- "
            "file has no known top-level symbols"
        )
    known = ", ".join(sorted({symbol["name"] for symbol in symbols}))
    return f"not found: no symbol '{name}' in '{file}' -- known top-level symbols: {known}"


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


def _context_after(content: str | None, line_no: int, *, count: int = 2) -> list[str]:
    """Up to ``count`` lines immediately following 1-indexed ``line_no`` in ``content``.

    Best-effort: ``content is None`` (file missing/unreadable) yields ``[]`` so callers
    fall back to the snippet alone, matching every other read path in this module.
    """
    if content is None:
        return []
    lines = content.splitlines()
    return lines[line_no : line_no + count]


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


def _matches_path(rel_path: str, pattern: str) -> bool:
    """Inclusion twin of :func:`_is_excluded`: does ``rel_path`` match ``pattern``?

    Accepts the same two forms -- an fnmatch glob against the repo-relative path
    (``tests/test_foo.py``, ``blacksmith/*.py``) or a directory prefix (``tests``,
    ``tests/``) that matches everything under it.
    """
    if fnmatch.fnmatch(rel_path, pattern):
        return True
    return fnmatch.fnmatch(rel_path, f"{pattern.rstrip('/')}/*")


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


# --- reference graph + PageRank (WU-DEP-GRAPH) --------------------------------------
#
# Pure and additive: these two functions back build_repo_map's rank_by_graph=True path
# above (WU-RANKED-MAP) -- OFF by default, since rank_by_graph itself defaults False
# and the [index].graph_rank config sub-flag that drives it also defaults False.
# Reuses the SAME stdlib symbol extraction (_extract_symbols) and git-backed file
# enumeration (_list_files) already above -- no new parsing, no new git plumbing.
# Read-only and best-effort throughout.

_WORD_RE = re.compile(r"\w+")


def build_reference_graph(
    repo_path: str | Path, *, exclude: Iterable[str] = ()
) -> dict[str, set[str]]:
    """Build a file-level reference graph over tracked files with recognized extensions.

    For each such file A, the returned set is every OTHER tracked (recognized) file B
    such that A's content mentions, as a whole word, a top-level symbol name defined in
    B. A file with no recognized symbols anywhere referencing it, or that references
    nothing, maps to an empty set -- it is never omitted from the dict. There is never
    a self-edge, even when a file uses a symbol it defines itself.

    Read-only and best-effort: an absent repo, an empty tracked-file list, or a git
    failure yields ``{}`` rather than raising.
    """
    repo_path = Path(repo_path)
    exclude = tuple(exclude)
    files = _list_files(repo_path, exclude)
    if not files:
        return {}

    recognized_files = [f for f in files if _EXT_FAMILY.get(Path(f).suffix)]
    if not recognized_files:
        return {}

    contents: dict[str, str] = {}
    symbol_to_files: dict[str, set[str]] = {}
    for rel_path in recognized_files:
        content = _read_file(repo_path, rel_path)
        contents[rel_path] = content or ""
        if content is None:
            continue
        family = _EXT_FAMILY[Path(rel_path).suffix]
        for symbol in _extract_symbols(content, family):
            symbol_to_files.setdefault(symbol["name"], set()).add(rel_path)

    words_by_file: dict[str, set[str]] = {
        rel_path: set(_WORD_RE.findall(contents[rel_path])) for rel_path in recognized_files
    }

    graph: dict[str, set[str]] = {rel_path: set() for rel_path in recognized_files}
    for a in recognized_files:
        words_a = words_by_file[a]
        if not words_a:
            continue
        for name, defining_files in symbol_to_files.items():
            if name not in words_a:
                continue
            for b in defining_files:
                if b != a:
                    graph[a].add(b)

    return graph


_PAGERANK_DAMPING = 0.85
_PAGERANK_MAX_ITERATIONS = 100
_PAGERANK_TOLERANCE = 1e-10


def rank_files(graph: dict[str, set[str]]) -> dict[str, float]:
    """Score each node in ``graph`` by PageRank centrality, normalized to sum to 1.

    A bounded, deterministic stdlib power-iteration -- fixed damping (0.85), a fixed
    iteration cap, and an early-exit convergence tolerance -- over the plain
    ``dict[str, set[str]]`` adjacency ``build_reference_graph`` returns. Dangling nodes
    (no outgoing edges) redistribute their mass evenly across every node each
    iteration, same as standard PageRank, so their score doesn't just vanish. NOT
    networkx: a hand-rolled loop over stdlib dicts/sets, no third-party dependency.

    Pure and read-only: performs no I/O, only operates on the graph structure given.
    An empty graph yields ``{}`` rather than raising or dividing by zero.
    """
    nodes = list(graph.keys())
    n = len(nodes)
    if n == 0:
        return {}

    out_degree = {node: len(graph.get(node) or ()) for node in nodes}
    predecessors: dict[str, list[str]] = {node: [] for node in nodes}
    for a, targets in graph.items():
        if a not in out_degree:
            continue
        for b in targets:
            if b in predecessors:
                predecessors[b].append(a)

    scores = {node: 1.0 / n for node in nodes}
    for _ in range(_PAGERANK_MAX_ITERATIONS):
        dangling_mass = sum(scores[node] for node in nodes if out_degree[node] == 0)
        base = (1.0 - _PAGERANK_DAMPING) / n + _PAGERANK_DAMPING * dangling_mass / n
        new_scores: dict[str, float] = {}
        for node in nodes:
            incoming = sum(scores[p] / out_degree[p] for p in predecessors[node])
            new_scores[node] = base + _PAGERANK_DAMPING * incoming
        delta = sum(abs(new_scores[node] - scores[node]) for node in nodes)
        scores = new_scores
        if delta < _PAGERANK_TOLERANCE:
            break

    total = sum(scores.values())
    if total > 0:
        scores = {node: value / total for node, value in scores.items()}
    return scores


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

# Shared "how do I query this thing" phrasing -- kept in one place so the no-match
# message (format_search_results) and the tool description (make_search_code_tool)
# never drift apart.
QUERY_SYNTAX_HELP = (
    "space-separated terms match with OR semantics, matching is case-insensitive, "
    "and terms are matched literally (not as a regex)"
)

NO_MATCHES_MESSAGE = f"no matches -- query syntax: {QUERY_SYNTAX_HELP}"

LIMIT_REACHED_LINE = "limit reached — more matches exist, refine the query"


def format_search_results(results: list[dict], *, limit: int | None = None) -> str:
    """Render :func:`search_code`'s ranked matches as the compact text handed to the agent.

    One compact block per hit: ``path:line: snippet``, followed by up to 2 indented
    context lines (when the hit carries a ``context`` entry) -- cheap for the agent to
    scan and cheap on tokens.

    An empty result list renders as an explicit "no matches" note -- naming the
    accepted query shapes (space-separated terms, OR semantics, case-insensitive,
    literal not regex) -- rather than blank text, so the agent's next query succeeds
    instead of it mistaking silence for a tool failure and giving up.

    When ``limit`` is given and the result count reached it, the output ends with an
    explicit "limit reached" line so the agent knows to refine the query rather than
    silently trusting it saw everything.
    """
    if not results:
        return NO_MATCHES_MESSAGE
    blocks = []
    for r in results:
        block_lines = [f"{r['file']}:{r['line']}: {r['snippet']}"]
        block_lines.extend(f"    {ctx}" for ctx in r.get("context") or [])
        blocks.append("\n".join(block_lines))
    text = "\n".join(blocks)
    if limit is not None and len(results) >= limit:
        text += "\n" + LIMIT_REACHED_LINE
    return text


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
        results = search_code(
            repo_path,
            args.get("query", ""),
            limit=limit,
            exclude=exclude,
            path=args.get("path") or None,
        )
        text = format_search_results(results, limit=limit)
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        SEARCH_CODE_TOOL_NAME,
        "Search this repository's tracked files for a symbol name or text query. Returns "
        "ranked matches (symbol definitions first, then plain-text hits) as `file:line: "
        "snippet` lines, each with up to 2 lines of surrounding context -- use it to find "
        "where something is defined or mentioned instead of guessing paths or reading files "
        f"blind. Query syntax: {QUERY_SYNTAX_HELP}. Pass `path` (a file path, glob, or "
        "directory like `tests/test_foo.py`, `blacksmith/*.py`, or `tests`) to scope the "
        "search to matching files -- use that instead of a path-scoped Grep.",
        # JSON Schema (not the simple dict form) so `path` can be genuinely optional.
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": f"search terms -- {QUERY_SYNTAX_HELP}",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "optional file path, glob, or directory to scope the search to"
                    ),
                },
            },
            "required": ["query"],
        },
    )(handler)


READ_SYMBOL_TOOL_NAME = "read_symbol"


def make_read_symbol_tool(repo_path: str | Path) -> SdkMcpTool[Any]:
    """Build the ``read_symbol`` SDK tool bound to ``repo_path`` (the run's worktree).

    The handler NEVER does anything but call :func:`read_symbol` against ``repo_path`` --
    same read-only, git-backed, best-effort lookup documented there.
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        text = read_symbol(repo_path, args.get("file", ""), args.get("name", ""))
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        READ_SYMBOL_TOOL_NAME,
        "Return the source of a single named top-level function/class from a tracked "
        "file in this repository -- the definition line through the line before the "
        "next top-level symbol (or end of file), capped at "
        f"{READ_SYMBOL_MAX_LINES} lines. The first output line is a `file:start-end` "
        "header naming the block's line span, so you never need a Grep just to learn "
        "the line number. Prefer this over Read when you only need one "
        "function or class, not the whole file -- it's cheaper and skips everything "
        "else in the file. An unknown file or symbol name returns a 'not found' "
        "message listing the file's known top-level symbols.",
        {"file": str, "name": str},
    )(handler)


# The in-process MCP server's name -- single source of truth. Both node tiers build their
# qualified ``mcp__<server>__<tool>`` grants from this, so the server name can never drift
# out of sync with the allow-list entries (it used to be re-hardcoded per node with a
# "must match" comment -- exactly the kind of duplication that silently rots).
INDEX_MCP_SERVER_NAME = "blacksmith-index"


def create_index_mcp_server(
    repo_path: str | Path,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude: Iterable[str] = (),
) -> McpSdkServerConfig:
    """Build the in-process MCP server exposing ``search_code``, ``read_symbol``,
    ``search_class``, ``search_method``, and ``search_method_in_class`` for a call's
    ``ClaudeAgentOptions.mcp_servers`` -- the same in-process (no subprocess, no IPC)
    ``create_sdk_mcp_server``/``tool`` pattern :mod:`blacksmith.sandbox` uses for
    ``run_command``.

    Wiring this server is what makes the tools callable. NOTE: naming a tool in a call's
    ``allowed_tools`` is NOT what gates an in-process MCP tool in the pinned Agent SDK --
    ``read_symbol`` was observed being called successfully from the implementer while it
    was absent from that call's ``allowed_tools``. The node tiers still list every index
    tool (see :data:`QUALIFIED_INDEX_TOOL_NAMES`) so the allow-list stays honest and
    symmetric, but the load-bearing switch is this function, not the allow-list."""
    return create_sdk_mcp_server(
        name=INDEX_MCP_SERVER_NAME,
        tools=[
            make_search_code_tool(repo_path, limit=limit, exclude=exclude),
            make_read_symbol_tool(repo_path),
            make_search_class_tool(repo_path, limit=limit, exclude=exclude),
            make_search_method_tool(repo_path, limit=limit, exclude=exclude),
            make_search_method_in_class_tool(repo_path, limit=limit, exclude=exclude),
        ],
    )


# --- Python AST structure extraction (WU-AST-EXTRACT) -------------------------------
#
# Pure and additive: this function is a plain library addition, not wired into
# build_repo_map/search_code/read_symbol above or into any node/tool/prompt -- a later
# unit does that wiring, if it happens at all. Unlike the regex-based
# ``_extract_symbols`` (which stays language-agnostic and only sees top-level, unindented
# declarations), this uses the stdlib ``ast`` module to walk a *parsed* Python module, so
# it can also see methods nested one level inside a class body. Still Python-only, still
# stdlib-only (no tree-sitter): out of scope for every other language.


def extract_python_structure(content: str) -> list[dict]:
    """Extract module-level classes/functions and their methods from Python ``content``.

    Returns one dict per module-level class, per method defined directly in a class
    body (``def``/``async def``, decorated or not), and per module-level function
    (``def``/``async def``). Each dict has:

    * ``kind`` -- ``"class"``, ``"method"``, or ``"function"``.
    * ``name`` -- the identifier.
    * ``class`` -- the enclosing class's name for a method, else ``None``.
    * ``signature`` -- the class/def source line, stripped.
    * ``line`` -- 1-indexed line the ``class``/``def`` statement starts on.
    * ``end_line`` -- the ``ast`` ``end_lineno`` of the definition.

    Pure and read-only: operates only on the ``content`` string passed in, no I/O.
    Best-effort: content that fails to parse (``SyntaxError``) yields ``[]`` rather
    than raising.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    lines = content.splitlines()

    def _entry(node: ast.AST, kind: str, class_name: str | None) -> dict:
        line_no = node.lineno
        signature = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
        return {
            "kind": kind,
            "name": node.name,
            "class": class_name,
            "signature": signature,
            "line": line_no,
            "end_line": node.end_lineno,
        }

    entries: list[dict] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entries.append(_entry(node, "function", None))
        elif isinstance(node, ast.ClassDef):
            entries.append(_entry(node, "class", None))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entries.append(_entry(child, "method", node.name))
    return entries


def _iter_python_structure(
    repo_path: Path, exclude: tuple[str, ...]
) -> Iterable[tuple[str, dict]]:
    """Yield ``(rel_path, entry)`` for every :func:`extract_python_structure` entry
    across tracked ``.py`` files under ``repo_path``. Read-only and best-effort: a git
    failure or absence of ``.py`` files simply yields nothing."""
    for rel_path in _list_files(repo_path, exclude):
        if Path(rel_path).suffix != ".py":
            continue
        content = _read_file(repo_path, rel_path)
        if content is None:
            continue
        for entry in extract_python_structure(content):
            yield rel_path, entry


def _structural_result(rel_path: str, entry: dict) -> dict:
    return {
        "file": rel_path,
        "line": entry["line"],
        "kind": entry["kind"],
        "name": entry["name"],
        "class": entry["class"],
        "signature": entry["signature"],
    }


def search_class(
    repo_path: str | Path,
    name: str,
    *,
    limit: int = 20,
    exclude: Iterable[str] = (),
) -> list[dict]:
    """Return class definitions across tracked ``.py`` files whose identifier equals
    ``name`` case-insensitively -- an EXACT-identifier match, not the substring/grep
    matching :func:`search_code` does. Results are deduped by ``(file, line)`` and
    capped at ``limit``. Read-only and best-effort: a blank ``name``, a repo with no
    ``.py`` files, or a git failure yields ``[]`` rather than raising.
    """
    name = (name or "").strip()
    if not name:
        return []
    name_lower = name.lower()

    repo_path = Path(repo_path)
    exclude = tuple(exclude)

    seen: set[tuple[str, int]] = set()
    results: list[dict] = []
    for rel_path, entry in _iter_python_structure(repo_path, exclude):
        if entry["kind"] != "class" or entry["name"].lower() != name_lower:
            continue
        key = (rel_path, entry["line"])
        if key in seen:
            continue
        seen.add(key)
        results.append(_structural_result(rel_path, entry))
        if len(results) >= limit:
            break
    return results


def search_method(
    repo_path: str | Path,
    name: str,
    *,
    limit: int = 20,
    exclude: Iterable[str] = (),
) -> list[dict]:
    """Return methods AND top-level functions across tracked ``.py`` files whose
    identifier equals ``name`` case-insensitively -- an EXACT-identifier match, not the
    substring/grep matching :func:`search_code` does. Each result's ``class`` is the
    enclosing class's name for a method, or ``None`` for a top-level function. Results
    are deduped by ``(file, line)`` and capped at ``limit``. Read-only and best-effort:
    a blank ``name``, a repo with no ``.py`` files, or a git failure yields ``[]``
    rather than raising.
    """
    name = (name or "").strip()
    if not name:
        return []
    name_lower = name.lower()

    repo_path = Path(repo_path)
    exclude = tuple(exclude)

    seen: set[tuple[str, int]] = set()
    results: list[dict] = []
    for rel_path, entry in _iter_python_structure(repo_path, exclude):
        if entry["kind"] not in ("method", "function"):
            continue
        if entry["name"].lower() != name_lower:
            continue
        key = (rel_path, entry["line"])
        if key in seen:
            continue
        seen.add(key)
        results.append(_structural_result(rel_path, entry))
        if len(results) >= limit:
            break
    return results


def search_method_in_class(
    repo_path: str | Path,
    class_name: str,
    method_name: str,
    *,
    limit: int = 20,
    exclude: Iterable[str] = (),
) -> list[dict]:
    """Return methods named ``method_name`` (case-insensitively) defined within a class
    named ``class_name`` (case-insensitively) across tracked ``.py`` files -- an
    EXACT-identifier match on both names, not the substring/grep matching
    :func:`search_code` does. Results are deduped by ``(file, line)`` and capped at
    ``limit``. Read-only and best-effort: a blank ``class_name``/``method_name``, a
    repo with no ``.py`` files, or a git failure yields ``[]`` rather than raising.
    """
    class_name = (class_name or "").strip()
    method_name = (method_name or "").strip()
    if not class_name or not method_name:
        return []
    class_name_lower = class_name.lower()
    method_name_lower = method_name.lower()

    repo_path = Path(repo_path)
    exclude = tuple(exclude)

    seen: set[tuple[str, int]] = set()
    results: list[dict] = []
    for rel_path, entry in _iter_python_structure(repo_path, exclude):
        if entry["kind"] != "method" or entry["name"].lower() != method_name_lower:
            continue
        if (entry["class"] or "").lower() != class_name_lower:
            continue
        key = (rel_path, entry["line"])
        if key in seen:
            continue
        seen.add(key)
        results.append(_structural_result(rel_path, entry))
        if len(results) >= limit:
            break
    return results


# --- structural search tools (WU-STRUCT-TOOLS) ---------------------------------------
#
# Same in-process create_sdk_mcp_server/tool pattern as make_search_code_tool /
# make_read_symbol_tool above -- no subprocess, no IPC, no new dependency. Each handler
# routes straight into its WU-STRUCT-SEARCH function (search_class / search_method /
# search_method_in_class), which stays read-only, best-effort, and Python-only exactly
# as documented there; these tools add no new execution path or write capability.
# Rendering reuses format_search_results by adapting each structural hit into the
# ``{file, line, snippet}`` shape it already knows how to render (a method's snippet is
# prefixed with its enclosing class, e.g. ``Foo.bar``), so the "no matches" text and
# compact ``file:line: ...`` layout stay identical to search_code's.


def _structural_snippet(entry: dict) -> dict:
    """Adapt a structural search hit into the ``{file, line, snippet}`` shape
    :func:`format_search_results` expects, prefixing a method's signature with its
    enclosing class (``Foo.bar``) so the class is visible without a second lookup."""
    prefix = f"{entry['class']}." if entry.get("class") else ""
    return {
        "file": entry["file"],
        "line": entry["line"],
        "snippet": f"{prefix}{entry['signature']}",
    }


def _render_structural_results(results: list[dict]) -> str:
    return format_search_results([_structural_snippet(r) for r in results])


SEARCH_CLASS_TOOL_NAME = "search_class"


def make_search_class_tool(
    repo_path: str | Path,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude: Iterable[str] = (),
) -> SdkMcpTool[Any]:
    """Build the ``search_class`` SDK tool bound to ``repo_path`` (the run's worktree).

    The handler NEVER does anything but call :func:`search_class` against
    ``repo_path`` -- exact-identifier, Python-only class lookup.
    """
    exclude = tuple(exclude)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        results = search_class(repo_path, args.get("name", ""), limit=limit, exclude=exclude)
        text = _render_structural_results(results)
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        SEARCH_CLASS_TOOL_NAME,
        "Find a Python class definition by its EXACT identifier (case-insensitive) "
        "across this repository's tracked `.py` files -- unlike `search_code`'s "
        "substring/text matching, this only matches the whole class name, not a "
        "partial one. Returns `file:line: signature` lines. Python-only.",
        {"name": str},
    )(handler)


SEARCH_METHOD_TOOL_NAME = "search_method"


def make_search_method_tool(
    repo_path: str | Path,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude: Iterable[str] = (),
) -> SdkMcpTool[Any]:
    """Build the ``search_method`` SDK tool bound to ``repo_path`` (the run's worktree).

    The handler NEVER does anything but call :func:`search_method` against
    ``repo_path`` -- exact-identifier, Python-only method/function lookup.
    """
    exclude = tuple(exclude)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        results = search_method(repo_path, args.get("name", ""), limit=limit, exclude=exclude)
        text = _render_structural_results(results)
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        SEARCH_METHOD_TOOL_NAME,
        "Find a Python method or top-level function by its EXACT identifier "
        "(case-insensitive) across this repository's tracked `.py` files -- unlike "
        "`search_code`'s substring/text matching, this only matches the whole name. "
        "A method's line is prefixed with its enclosing class (`Foo.bar`). Returns "
        "`file:line: signature` lines. Python-only.",
        {"name": str},
    )(handler)


SEARCH_METHOD_IN_CLASS_TOOL_NAME = "search_method_in_class"


def make_search_method_in_class_tool(
    repo_path: str | Path,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude: Iterable[str] = (),
) -> SdkMcpTool[Any]:
    """Build the ``search_method_in_class`` SDK tool bound to ``repo_path`` (the run's
    worktree).

    The handler NEVER does anything but call :func:`search_method_in_class` against
    ``repo_path`` -- exact-identifier, Python-only lookup of a method defined within a
    specific class.
    """
    exclude = tuple(exclude)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        results = search_method_in_class(
            repo_path,
            args.get("class_name", ""),
            args.get("method_name", ""),
            limit=limit,
            exclude=exclude,
        )
        text = _render_structural_results(results)
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        SEARCH_METHOD_IN_CLASS_TOOL_NAME,
        "Find a Python method by its EXACT identifier (case-insensitive) defined "
        "within a specific class (also matched by EXACT identifier, case-insensitive) "
        "across this repository's tracked `.py` files. Use this to disambiguate when "
        "several classes define a same-named method. Returns `file:line: "
        "Class.signature` lines. Python-only.",
        {"class_name": str, "method_name": str},
    )(handler)


# --- shared index-tier wiring (single source of truth for both node tiers) ----------
#
# The plan and implement nodes both (a) grant the index tools and (b) advertise them in
# their system prompt. Each used to hand-maintain its OWN copy of the qualified tool-name
# list AND the (long) advertisement paragraph -- which drifted: implement omitted
# read_symbol from its list, and both bundled the advertisement INSIDE the repo-map block,
# so an enabled index whose map came back empty (a git hiccup / empty repo) silently
# un-advertised working tools. These two definitions are the single source both nodes now
# consume, so the tool set and the advertisement can't diverge again.

QUALIFIED_INDEX_TOOL_NAMES: list[str] = [
    f"mcp__{INDEX_MCP_SERVER_NAME}__{name}"
    for name in (
        SEARCH_CODE_TOOL_NAME,
        READ_SYMBOL_TOOL_NAME,
        SEARCH_CLASS_TOOL_NAME,
        SEARCH_METHOD_TOOL_NAME,
        SEARCH_METHOD_IN_CLASS_TOOL_NAME,
    )
]


def index_tools_prompt_section() -> str:
    """The shared CORE advertisement of the index tools (what they are + query syntax).

    Injected into the plan, implement, AND review system prompts whenever the index is
    ENABLED -- gated on the index being wired, never on whether a repo map happened to
    build (an empty/failed map must not silently un-advertise tools that still work:
    ``search_code`` et al. query the repo live, map or no map). This is the tier-neutral
    core; each node appends its own one-line "when to still reach for Read" tail (the
    implementer edits, the reviewer reads slices, etc.), so the shared description can't
    drift across tiers while the per-tier nuance is preserved.
    """
    return (
        "USE THE INDEX FIRST. You have `search_code` to find where something is defined "
        "or mentioned in this repo in ONE call (query is space-separated terms matched "
        "with OR semantics, case-insensitive, and matched literally — not as a regex; "
        "pass `path` to scope the search to one file, glob, or directory instead of a "
        "path-scoped Grep), and `read_symbol` to fetch the source of one named top-level "
        "function/class once you know which file it's in. You also have three structural "
        "tools, scoped to Python (`.py`) files only: `search_class` to find a class "
        "definition by its EXACT name, `search_method` to find a method or top-level "
        "function by its EXACT name, and `search_method_in_class` to find a method by its "
        "EXACT name within a specific class by its EXACT name. For a where-is-this-class-"
        "or-method question, prefer `search_class`/`search_method`/`search_method_in_class` "
        "over `search_code` or a blind Grep — they match the exact identifier instead of "
        "ranking text mentions. Prefer these index tools over Read/Glob/Grep."
    )
