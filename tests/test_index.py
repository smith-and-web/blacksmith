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


def test_build_repo_map_truncates_at_max_bytes(tmp_path):
    repo = _sample_repo(tmp_path)
    full_map = build_repo_map(repo, max_bytes=10_000)
    tiny_map = build_repo_map(repo, max_bytes=10)
    assert tiny_map.endswith("…(truncated)")
    assert len(tiny_map) < len(full_map)


def test_build_repo_map_respects_exclude(tmp_path):
    repo = _sample_repo(tmp_path)
    repo_map = build_repo_map(repo, max_bytes=10_000, exclude=("notes.md",))
    assert "greet.py" in repo_map
    assert "notes.md" not in repo_map


def test_build_repo_map_git_failure_returns_empty_string(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    assert build_repo_map(not_a_repo, max_bytes=1000) == ""


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
