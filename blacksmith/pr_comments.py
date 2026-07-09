"""Fetch a PR's human review feedback via `gh` (WU-PR-COMMENTS).

A pure fetch: given a PR number, retrieve the human review feedback left on it —
top-level review bodies and inline (diff) review comments — via the existing `gh`
CLI, routed through the same injectable ``Runner`` pattern as ``nodes/pr.py`` and
``issue.py`` (so tests mock ``gh`` and make no network calls). There is no
graph/CLI wiring in this unit — callers invoke ``fetch_pr_review_comments``
directly.

Best-effort by design: any ``gh`` failure, non-JSON output, or unexpected payload
shape is caught as a typed ``PRCommentsError`` internally and swallowed into an
empty result for that source — callers never see a raw traceback, and a PR with
no reviews (or a `gh` outage) simply yields ``[]``.

Bot accounts and blacksmith's own comments are filtered out (any ``login`` ending
in the standard GitHub App ``"[bot]"`` suffix, plus ``own_login``) so blacksmith
never responds to its own review comments.
"""

from __future__ import annotations

import json
from pathlib import Path

from blacksmith.nodes.pr import Runner, subprocess_runner

_BOT_SUFFIX = "[bot]"
_DEFAULT_OWN_LOGIN = "blacksmith"


class PRCommentsError(Exception):
    """Raised internally when a `gh` call fails or returns unparseable/unexpected
    output. Always caught within this module — it never escapes to callers."""


def _run_gh_json(argv: list[str], runner: Runner, cwd: Path | None):
    result = runner(argv, cwd)
    if result.returncode != 0:
        raise PRCommentsError(
            f"gh call failed ({' '.join(argv)}): {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise PRCommentsError(f"could not parse gh JSON ({' '.join(argv)}): {exc}") from exc


def _is_own_or_bot(login: str, *, own_login: str | None) -> bool:
    if not login:
        return False
    if login.endswith(_BOT_SUFFIX):
        return True
    return bool(own_login) and login.lower() == own_login.lower()


def _review_bodies(
    pr_number: int,
    *,
    repo: str | None,
    own_login: str | None,
    runner: Runner,
    cwd: Path | None,
) -> list[dict]:
    """Top-level review bodies via ``gh pr view --json reviews`` (``path``/``line`` None)."""
    argv = ["gh", "pr", "view", str(pr_number), "--json", "reviews"]
    if repo:
        argv += ["--repo", repo]
    try:
        data = _run_gh_json(argv, runner, cwd)
    except PRCommentsError:
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for review in data.get("reviews") or []:
        if not isinstance(review, dict):
            continue
        body = (review.get("body") or "").strip()
        if not body:
            continue
        author = ((review.get("author") or {}).get("login")) or ""
        if _is_own_or_bot(author, own_login=own_login):
            continue
        out.append({"path": None, "line": None, "author": author, "body": body})
    return out


def _inline_comments(
    pr_number: int,
    *,
    repo: str | None,
    own_login: str | None,
    runner: Runner,
    cwd: Path | None,
) -> list[dict]:
    """Inline (diff) review comments via the REST endpoint, run through ``gh api``.

    When ``repo`` is not given, uses gh's own ``{owner}/{repo}`` template
    placeholders, which it resolves from the local git repository context.
    """
    endpoint = f"repos/{repo or '{owner}/{repo}'}/pulls/{pr_number}/comments"
    argv = ["gh", "api", endpoint]
    try:
        data = _run_gh_json(argv, runner, cwd)
    except PRCommentsError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for comment in data:
        if not isinstance(comment, dict):
            continue
        body = (comment.get("body") or "").strip()
        if not body:
            continue
        author = ((comment.get("user") or {}).get("login")) or ""
        if _is_own_or_bot(author, own_login=own_login):
            continue
        line = comment.get("line")
        if line is None:
            line = comment.get("original_line")
        out.append({"path": comment.get("path"), "line": line, "author": author, "body": body})
    return out


def fetch_pr_review_comments(
    pr_number: int,
    *,
    repo: str | None = None,
    own_login: str | None = _DEFAULT_OWN_LOGIN,
    runner: Runner = subprocess_runner,
    cwd: Path | None = None,
) -> list[dict]:
    """Fetch PR ``pr_number``'s human review feedback as an ordered list of
    ``{path, line, author, body}`` dicts (``path``/``line`` are ``None`` for a
    top-level review body).

    Top-level review bodies come first, in the order `gh` returns them, followed
    by inline (diff) review comments, likewise in order. Bot accounts and
    ``own_login`` (default ``"blacksmith"``) are filtered out of both.

    Never raises: a `gh` failure, empty result, or unexpected payload shape for
    either source just contributes nothing, so a PR with no reviews — or a `gh`
    outage — returns ``[]``.
    """
    out: list[dict] = []
    out.extend(_review_bodies(pr_number, repo=repo, own_login=own_login, runner=runner, cwd=cwd))
    out.extend(_inline_comments(pr_number, repo=repo, own_login=own_login, runner=runner, cwd=cwd))
    return out
