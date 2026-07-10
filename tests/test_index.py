"""Tests for the repo map + code search index (WU-CODE-INDEX).

Test contract: integration tests against a small, real git repo created in tmp_path
(real ``git init`` + a couple of committed source files) — no mocking of git.
"""

import subprocess
from pathlib import Path

from blacksmith.index import (
    READ_SYMBOL_MAX_LINES,
    build_repo_map,
    create_index_mcp_server,
    format_search_results,
    read_symbol,
    search_code,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _commit_all(repo: Path, message: str = "initial") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _sample_repo(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path / "repo")
    (repo / "greet.py").write_text(
        "def hello(name):\n"
        "    return f'hi {name}'\n"
        "\n"
        "\n"
        "class Greeter:\n"
        "    pass\n"
    )
    (repo / "notes.md").write_text(
        "# Notes\n\nRemember to say hello to the team in the standup.\n"
    )
    _commit_all(repo)
    return repo


def test_build_repo_map_lists_known_file_and_symbol(tmp_path):
    repo = _sample_repo(tmp_path)
    repo_map = build_repo_map(repo, max_bytes=10_000)
    assert "greet.py" in repo_map
    assert "def hello(name):" in repo_map
    assert "class Greeter" in repo_map


def test_build_repo_map_under_budget_is_unchanged_and_unmarked(tmp_path):
    repo = _sample_repo(tmp_path)
    full_map = build_repo_map(repo, max_bytes=100_000)
    same_map = build_repo_map(repo, max_bytes=100_000)
    assert full_map == same_map
    assert "symbols omitted" not in full_map


def test_build_repo_map_respects_exclude(tmp_path):
    repo = _sample_repo(tmp_path)
    repo_map = build_repo_map(repo, max_bytes=10_000, exclude=("notes.md",))
    assert "greet.py" in repo_map
    assert "notes.md" not in repo_map


def test_build_repo_map_git_failure_returns_empty_string(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    assert build_repo_map(not_a_repo, max_bytes=1000) == ""


# --- byte-budget priority (WU-MAP-COVERAGE) ---------------------------------------


def _priority_repo(tmp_path: Path) -> Path:
    """A repo with one file at each symbol-drop priority tier:

    * ``pkg/core.py``       -- priority 1 (code outside tests/ and docs/)
    * ``tests/test_thing.py`` -- priority 2 (under tests/), with several symbols so
      dropping it alone frees a large, unambiguous chunk of the byte budget
    * ``docs/config.py``    -- priority 3 (recognized extension, but under docs/)
    * ``notes.md``          -- unrecognized extension; never has symbols to drop,
      but its path must always be listed
    """
    repo = _init_repo(tmp_path / "repo")
    (repo / "pkg").mkdir()
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    (repo / "pkg" / "core.py").write_text("def priority_one():\n    pass\n")
    (repo / "tests" / "test_thing.py").write_text(
        "\n".join(f"def test_priority_two_{i}():\n    pass\n" for i in range(5))
    )
    (repo / "docs" / "config.py").write_text("def priority_three():\n    pass\n")
    (repo / "notes.md").write_text("# Notes\n")
    _commit_all(repo)
    return repo


def test_build_repo_map_over_budget_lists_every_path_and_drops_lowest_priority_first(
    tmp_path,
):
    repo = _priority_repo(tmp_path)
    full_map = build_repo_map(repo, max_bytes=100_000)
    assert "priority_one" in full_map
    assert "priority_two" in full_map
    assert "priority_three" in full_map

    # The map with only the tests/ and docs/ symbol outlines dropped (core.py's kept):
    # paths for every file, in git ls-files order, with just core.py's symbol line.
    partial = (
        "docs/config.py\n"
        "notes.md\n"
        "pkg/core.py\n"
        "  def priority_one():\n"
        "tests/test_thing.py"
    )
    # A budget comfortably above the size of that partial map (but far below the full
    # map, since tests/test_thing.py alone carries five symbol lines) forces exactly
    # the two lowest-priority files' symbols to be dropped and no more.
    max_bytes = len(partial.encode("utf-8")) + 80

    repo_map = build_repo_map(repo, max_bytes=max_bytes)

    # Every tracked file's path is still listed, even the ones whose symbols were cut.
    for path in ("pkg/core.py", "tests/test_thing.py", "docs/config.py", "notes.md"):
        assert path in repo_map

    # Highest priority (outside tests/ and docs/) kept; tests/ and docs/ dropped.
    assert "priority_one" in repo_map
    assert "priority_two" not in repo_map
    assert "priority_three" not in repo_map

    # Explicit marker naming how many files had their symbols omitted -- never a
    # mid-file byte cut.
    assert repo_map.endswith("\n…(2 files' symbols omitted)…")
    assert len(repo_map) < len(full_map)


def test_build_repo_map_tiny_budget_still_lists_every_path(tmp_path):
    repo = _priority_repo(tmp_path)
    repo_map = build_repo_map(repo, max_bytes=1)

    for path in ("pkg/core.py", "tests/test_thing.py", "docs/config.py", "notes.md"):
        assert path in repo_map
    assert "priority_one" not in repo_map
    assert "priority_two" not in repo_map
    assert "priority_three" not in repo_map
    assert repo_map.endswith("\n…(3 files' symbols omitted)…")


def test_search_code_ranks_symbol_definition_above_text_mention(tmp_path):
    repo = _sample_repo(tmp_path)
    results = search_code(repo, "hello")

    assert results, "expected at least one match"
    assert results[0]["file"] == "greet.py"
    assert results[0]["line"] == 1
    assert results[0]["kind"] == "function"
    assert "def hello" in results[0]["snippet"]

    kinds = [r["kind"] for r in results]
    assert kinds.index("function") < kinds.index("text")  # definition ranked first
    text_hit = next(r for r in results if r["kind"] == "text")
    assert text_hit["file"] == "notes.md"


def test_search_code_dedupes_and_caps_at_limit(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    source = "\n".join(f"def func_{i}():\n    pass\n" for i in range(30))
    (repo / "many.py").write_text(source)
    _commit_all(repo)

    results = search_code(repo, "func", limit=5)
    assert len(results) == 5
    assert len({(r["file"], r["line"]) for r in results}) == 5  # deduped


def test_search_code_empty_or_absent_query_returns_empty_list(tmp_path):
    repo = _sample_repo(tmp_path)
    assert search_code(repo, "") == []
    assert search_code(repo, "   ") == []
    assert search_code(repo, None) == []


def test_search_code_git_failure_returns_empty_list(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    assert search_code(not_a_repo, "hello") == []


# --- multi-term / case-insensitive matching (WU-SEARCH-TERMS) ---------------------


def test_search_code_multi_term_query_matches_each_term(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "mod.py").write_text(
        "def open_pr():\n    pass\n\n\n"
        "def pr_runner():\n    pass\n\n\n"
        "def cost_event():\n    pass\n"
    )
    _commit_all(repo)

    results = search_code(repo, "open_pr pr_runner cost_event")
    snippets = [r["snippet"] for r in results]
    assert any("def open_pr" in s for s in snippets)
    assert any("def pr_runner" in s for s in snippets)
    assert any("def cost_event" in s for s in snippets)


def test_search_code_multi_term_text_search_uses_or_semantics(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "notes.md").write_text("alpha here\nbeta here\ngamma here\n")
    _commit_all(repo)

    results = search_code(repo, "alpha gamma")
    hit_lines = {(r["file"], r["line"]) for r in results}
    assert ("notes.md", 1) in hit_lines
    assert ("notes.md", 3) in hit_lines
    assert ("notes.md", 2) not in hit_lines


def test_search_code_ranks_by_number_of_matched_terms(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "mod.py").write_text(
        "def alpha_beta():\n    pass\n\n\ndef alpha_only():\n    pass\n"
    )
    _commit_all(repo)

    results = search_code(repo, "alpha beta")
    assert results[0]["snippet"].startswith("def alpha_beta")
    names = [r["snippet"] for r in results]
    assert any(s.startswith("def alpha_only") for s in names)


def test_search_code_symbol_matching_is_case_insensitive(tmp_path):
    repo = _sample_repo(tmp_path)
    results = search_code(repo, "HELLO")
    assert any(r["kind"] == "function" and "def hello" in r["snippet"] for r in results)


def test_search_code_text_matching_is_case_insensitive(tmp_path):
    repo = _sample_repo(tmp_path)
    results = search_code(repo, "STANDUP")
    assert any(r["kind"] == "text" and "standup" in r["snippet"].lower() for r in results)


def test_search_code_literal_query_with_regex_metacharacters(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "mod.py").write_text("def cost_event(node=None):\n    pass\n")
    _commit_all(repo)

    results = search_code(repo, "cost_event(node=")
    assert results, "expected a literal match despite regex metacharacters"
    assert any("cost_event(node=" in r["snippet"] for r in results)


def test_search_code_single_term_dedupes_and_caps_at_limit(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    source = "\n".join(f"def func_{i}():\n    pass\n" for i in range(30))
    (repo / "many.py").write_text(source)
    _commit_all(repo)

    results = search_code(repo, "func", limit=5)
    assert len(results) == 5
    assert len({(r["file"], r["line"]) for r in results}) == 5


# --- context lines / limit signal / no-match help (WU-SEARCH-FEEDBACK) -----------


def test_search_code_results_include_bounded_context_after_match(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "mod.py").write_text(
        "def alpha():\n"
        "    line_a\n"
        "    line_b\n"
        "    line_c\n"
    )
    _commit_all(repo)

    results = search_code(repo, "alpha")
    assert results
    hit = results[0]
    assert hit["context"] == ["    line_a", "    line_b"]
    # bounded at 2 lines even though a third line follows the match
    assert "line_c" not in "\n".join(hit["context"])


def test_search_code_text_hit_includes_context_after_match(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "notes.md").write_text("alpha here\nfollow-up one\nfollow-up two\nfollow-up three\n")
    _commit_all(repo)

    results = search_code(repo, "alpha")
    hit = next(r for r in results if r["kind"] == "text")
    assert hit["context"] == ["follow-up one", "follow-up two"]


def test_search_code_missing_file_context_falls_back_to_snippet_only(tmp_path, monkeypatch):
    import blacksmith.index as index_mod

    repo = _sample_repo(tmp_path)
    monkeypatch.setattr(index_mod, "_read_file", lambda repo_path, rel_path: None)

    results = search_code(repo, "hello")
    assert results, "expected at least one match even with reads failing"
    for hit in results:
        assert hit["context"] == []
        assert hit["snippet"]  # snippet alone still present


def test_format_search_results_at_limit_appends_refine_line():
    results = [
        {"file": "a.py", "line": i, "kind": "text", "snippet": f"hit {i}"} for i in range(3)
    ]
    rendered = format_search_results(results, limit=3)
    assert rendered.splitlines()[-1] == "limit reached — more matches exist, refine the query"


def test_format_search_results_under_limit_omits_refine_line():
    results = [{"file": "a.py", "line": 1, "kind": "text", "snippet": "hit"}]
    rendered = format_search_results(results, limit=5)
    assert "limit reached" not in rendered


def test_format_search_results_no_limit_arg_omits_refine_line():
    results = [{"file": "a.py", "line": 1, "kind": "text", "snippet": "hit"}]
    rendered = format_search_results(results)
    assert "limit reached" not in rendered


def test_search_code_at_limit_render_has_refine_line(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    source = "\n".join(f"def func_{i}():\n    pass\n" for i in range(30))
    (repo / "many.py").write_text(source)
    _commit_all(repo)

    results = search_code(repo, "func", limit=5)
    rendered = format_search_results(results, limit=5)
    assert rendered.endswith("\nlimit reached — more matches exist, refine the query")


def test_format_search_results_renders_context_in_compact_block():
    results = [
        {
            "file": "a.py",
            "line": 3,
            "kind": "function",
            "snippet": "def foo():",
            "context": ["    pass", ""],
        }
    ]
    rendered = format_search_results(results)
    assert rendered == "a.py:3: def foo():\n        pass\n    "


def test_format_search_results_no_matches_names_query_shapes():
    rendered = format_search_results([])
    assert rendered.startswith("no matches")
    for shape in ("space-separated", "OR", "case-insensitive", "literal", "not as a regex"):
        assert shape in rendered


def test_search_code_tool_description_states_query_shapes():
    from blacksmith.index import make_search_code_tool

    tool_def = make_search_code_tool(Path("."))
    for shape in ("space-separated", "OR", "case-insensitive", "literal"):
        assert shape in tool_def.description


# --- read_symbol (WU-READ-SYMBOL) -------------------------------------------------


def _symbol_repo(tmp_path: Path) -> Path:
    """A repo with a def, a class, and a decorated def -- to exercise boundaries."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "greet.py").write_text(
        "def hello(name):\n"
        "    return f'hi {name}'\n"
        "\n"
        "\n"
        "class Greeter:\n"
        "    pass\n"
        "\n"
        "\n"
        "@decorator\n"
        "def bye():\n"
        "    pass\n"
    )
    _commit_all(repo)
    return repo


def test_read_symbol_extracts_function_body(tmp_path):
    repo = _symbol_repo(tmp_path)
    body = read_symbol(repo, "greet.py", "hello")
    assert body.startswith("def hello(name):\n    return f'hi {name}'")
    assert "class Greeter" not in body


def test_read_symbol_extracts_class_body_with_decorator_free_boundary(tmp_path):
    repo = _symbol_repo(tmp_path)
    body = read_symbol(repo, "greet.py", "Greeter")
    assert body.startswith("class Greeter:\n    pass")
    # bye()'s decorator belongs to bye, not to the preceding Greeter block.
    assert "@decorator" not in body
    assert "def bye" not in body


def test_read_symbol_last_symbol_runs_to_end_of_file(tmp_path):
    repo = _symbol_repo(tmp_path)
    body = read_symbol(repo, "greet.py", "bye")
    assert body == "def bye():\n    pass"


def test_read_symbol_caps_at_150_lines_with_truncation_marker(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    body_lines = [f"    x = {i}" for i in range(200)]
    (repo / "big.py").write_text("def big():\n" + "\n".join(body_lines) + "\n")
    _commit_all(repo)

    result = read_symbol(repo, "big.py", "big")
    rendered_lines = result.splitlines()

    assert rendered_lines[0] == "def big():"
    assert rendered_lines[-1] == f"…(truncated at {READ_SYMBOL_MAX_LINES} lines)…"
    assert len(rendered_lines) == READ_SYMBOL_MAX_LINES + 1


def test_read_symbol_under_cap_has_no_truncation_marker(tmp_path):
    repo = _symbol_repo(tmp_path)
    body = read_symbol(repo, "greet.py", "hello")
    assert "truncated" not in body


def test_read_symbol_unknown_file_returns_not_found(tmp_path):
    repo = _symbol_repo(tmp_path)
    result = read_symbol(repo, "does_not_exist.py", "hello")
    assert "not found" in result
    assert "does_not_exist.py" in result


def test_read_symbol_untracked_file_returns_not_found(tmp_path):
    repo = _symbol_repo(tmp_path)
    (repo / "untracked.py").write_text("def ghost():\n    pass\n")  # written, never committed

    result = read_symbol(repo, "untracked.py", "ghost")
    assert "not found" in result
    assert "untracked.py" in result


def test_read_symbol_unknown_symbol_lists_known_symbols(tmp_path):
    repo = _symbol_repo(tmp_path)
    result = read_symbol(repo, "greet.py", "nonexistent_symbol")
    assert "not found" in result
    assert "nonexistent_symbol" in result
    assert "hello" in result
    assert "Greeter" in result
    assert "bye" in result


def test_read_symbol_unrecognized_extension_lists_no_symbols(tmp_path):
    repo = _sample_repo(tmp_path)
    result = read_symbol(repo, "notes.md", "anything")
    assert "not found" in result
    assert "no known top-level symbols" in result


def test_read_symbol_git_failure_returns_not_found(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    result = read_symbol(not_a_repo, "greet.py", "hello")
    assert "not found" in result


def test_create_index_mcp_server_carries_both_tools(tmp_path):
    """VERIFY-AT-BUILD: builds a live McpSdkServerConfig and lists its registered tools
    via the real ``mcp`` Server instance -- no mocked SDK internals."""
    import asyncio

    from mcp import types as mcp_types

    repo = _symbol_repo(tmp_path)
    server_config = create_index_mcp_server(repo)
    server = server_config["instance"]
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    result = asyncio.run(handler(None))

    names = {t.name for t in result.root.tools}
    assert names == {"search_code", "read_symbol"}
