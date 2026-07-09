"""Tests for fetching a PR's human review comments via `gh` (WU-PR-COMMENTS).

Pure fetch, unit-tested with a fake command runner — `gh` is never really invoked.
"""

import json

from blacksmith.nodes.pr import CommandResult
from blacksmith.pr_comments import fetch_pr_review_comments


def _fake_runner(*, reviews=None, inline=None, view_rc=0, api_rc=0):
    calls: list[list[str]] = []

    def run(argv, cwd=None):
        calls.append(list(argv))
        if argv[:3] == ["gh", "pr", "view"]:
            if view_rc != 0:
                return CommandResult(view_rc, "", "boom")
            return CommandResult(0, json.dumps({"reviews": reviews or []}), "")
        if argv[:2] == ["gh", "api"]:
            if api_rc != 0:
                return CommandResult(api_rc, "", "boom")
            return CommandResult(0, json.dumps(inline if inline is not None else []), "")
        return CommandResult(0, "", "")

    run.calls = calls
    return run


# --- (a) fake gh payload parses into the structured list, in order ----------


def test_parses_reviews_then_inline_comments_in_order():
    reviews = [
        {"author": {"login": "alice"}, "body": "Looks good overall"},
        {"author": {"login": "bob"}, "body": "One more pass needed"},
    ]
    inline = [
        {"path": "foo.py", "line": 10, "user": {"login": "alice"}, "body": "nit: rename"},
        {
            "path": "bar.py",
            "line": None,
            "original_line": 5,
            "user": {"login": "carol"},
            "body": "why here?",
        },
    ]
    runner = _fake_runner(reviews=reviews, inline=inline)

    result = fetch_pr_review_comments(42, runner=runner)

    assert result == [
        {"path": None, "line": None, "author": "alice", "body": "Looks good overall"},
        {"path": None, "line": None, "author": "bob", "body": "One more pass needed"},
        {"path": "foo.py", "line": 10, "author": "alice", "body": "nit: rename"},
        {"path": "bar.py", "line": 5, "author": "carol", "body": "why here?"},
    ]


def test_uses_repo_flag_and_endpoint_when_given():
    runner = _fake_runner(reviews=[], inline=[])
    fetch_pr_review_comments(7, repo="o/r", runner=runner)
    assert "--repo" in runner.calls[0] and "o/r" in runner.calls[0]
    assert runner.calls[1] == ["gh", "api", "repos/o/r/pulls/7/comments"]


# --- (b) empty/no-review PR yields [] ----------------------------------------


def test_no_reviews_yields_empty_list():
    runner = _fake_runner(reviews=[], inline=[])
    assert fetch_pr_review_comments(1, runner=runner) == []


def test_blank_bodies_are_skipped():
    reviews = [{"author": {"login": "alice"}, "body": "   "}]
    inline = [{"path": "x.py", "line": 1, "user": {"login": "alice"}, "body": ""}]
    runner = _fake_runner(reviews=reviews, inline=inline)
    assert fetch_pr_review_comments(1, runner=runner) == []


# --- (c) a gh failure is swallowed into [] (no raise) ------------------------


def test_gh_failure_on_both_calls_swallowed():
    runner = _fake_runner(view_rc=1, api_rc=1)
    assert fetch_pr_review_comments(1, runner=runner) == []


def test_gh_failure_on_one_call_still_returns_the_other():
    reviews = [{"author": {"login": "alice"}, "body": "solid"}]
    runner = _fake_runner(reviews=reviews, api_rc=1)
    assert fetch_pr_review_comments(1, runner=runner) == [
        {"path": None, "line": None, "author": "alice", "body": "solid"}
    ]


def test_non_json_output_swallowed():
    def run(argv, cwd=None):
        return CommandResult(0, "not json", "")

    assert fetch_pr_review_comments(1, runner=run) == []


# --- bot / self filtering -----------------------------------------------------


def test_filters_bot_and_own_comments():
    reviews = [
        {"author": {"login": "blacksmith[bot]"}, "body": "auto note"},
        {"author": {"login": "blacksmith"}, "body": "self note"},
        {"author": {"login": "dave"}, "body": "keep me"},
    ]
    inline = [
        {"path": "x.py", "line": 1, "user": {"login": "github-actions[bot]"}, "body": "ci note"},
        {"path": "y.py", "line": 2, "user": {"login": "erin"}, "body": "keep me too"},
    ]
    runner = _fake_runner(reviews=reviews, inline=inline)

    result = fetch_pr_review_comments(1, runner=runner)

    assert result == [
        {"path": None, "line": None, "author": "dave", "body": "keep me"},
        {"path": "y.py", "line": 2, "author": "erin", "body": "keep me too"},
    ]


def test_custom_own_login_is_filtered():
    reviews = [{"author": {"login": "my-bot-account"}, "body": "ignore me"}]
    runner = _fake_runner(reviews=reviews, inline=[])
    result = fetch_pr_review_comments(1, own_login="my-bot-account", runner=runner)
    assert result == []
