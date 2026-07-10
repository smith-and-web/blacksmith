"""Tests for the repo map + code search index (WU-CODE-INDEX).

Test contract: integration tests against a small, real git repo created in tmp_path
(real ``git init`` + a couple of committed source files) — no mocking of git.
"""

import subprocess
from pathlib import Path

from blacksmith.index import build_repo_map, search_code


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
