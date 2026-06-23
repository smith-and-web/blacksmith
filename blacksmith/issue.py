"""Issue ingestion — scaffold a Contract v1 PRD skeleton from a GitHub issue.

``blacksmith --issue N`` fetches the issue and writes a PRD *skeleton* that
``parse_prd`` accepts, seeding the issue title/body as Purpose context and marking the
fields a human must complete (``target_modules``, ``test_contract``) with explicit
``TODO`` markers. blacksmith never invents a test contract from an issue: it scaffolds
the structure and hands the judgement calls back to the human.

The issue is fetched through the existing ``gh`` CLI (the same shell-out path the PR
node uses), routed through an injectable ``Runner`` so tests mock ``gh`` and make no
network calls — there is deliberately no GitHub HTTP client or SDK dependency.

A run started from an issue carries the originating issue number in state, so the PR
opened for it links ``Closes #N`` in its body (``nodes/pr.py``). blacksmith never
closes or merges the issue itself — that stays behind the human PR gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from blacksmith.nodes.pr import Runner, subprocess_runner

# The single work unit the skeleton seeds. Its target_modules / test_contract are
# explicit placeholders — non-empty (so the contract validates) but obviously unfinished.
_SCAFFOLD_UNIT_ID = "WU-001"
_TARGET_MODULES_TODO = "TODO/replace-with-real-target-module.py"
_TEST_CONTRACT_TODO = (
    "TODO: replace with the executable test_contract this unit must satisfy "
    "(what must pass, e.g. `pytest -k ...`)"
)
_UNTOUCHABLE_TODO = "TODO: list the files/areas this work unit must not touch"
_DEFAULT_LAYERS = {"logic": "auto"}


class IssueError(Exception):
    """Raised when the issue cannot be fetched from ``gh`` or its JSON cannot be parsed."""


@dataclass(frozen=True)
class Issue:
    """A GitHub issue, reduced to the fields the scaffolder seeds."""

    number: int
    title: str
    body: str


def fetch_issue(
    number: int,
    *,
    repo: str | None = None,
    runner: Runner = subprocess_runner,
    cwd: Path | None = None,
) -> Issue:
    """Fetch issue ``number`` via ``gh issue view`` (JSON), through ``runner``.

    Raises ``IssueError`` if ``gh`` fails or returns output that is not the expected
    JSON object. Routing through ``runner`` keeps this offline and mockable in tests.
    """
    argv = ["gh", "issue", "view", str(number), "--json", "number,title,body"]
    if repo:
        argv += ["--repo", repo]
    result = runner(argv, cwd)
    if result.returncode != 0:
        raise IssueError(
            f"gh issue view #{number} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IssueError(f"could not parse gh issue JSON for #{number}: {exc}") from exc
    if not isinstance(data, dict):
        raise IssueError(f"unexpected gh issue payload for #{number}: {data!r}")
    return Issue(
        number=int(data.get("number", number)),
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
    )


def scaffold_prd(
    issue: Issue,
    *,
    component: str,
    primary_target_repo: str,
    layers: dict[str, str] | None = None,
) -> str:
    """Render a Contract v1 PRD skeleton (frontmatter + prose) for ``issue``.

    The frontmatter is a complete, ``parse_prd``-valid contract carrying exactly one
    work unit with placeholder ``target_modules`` / ``test_contract``; the prose seeds
    the issue title/body as Purpose context and flags every field a human must finish.
    """
    layers = dict(layers or _DEFAULT_LAYERS)
    title = issue.title or f"Issue #{issue.number}"
    contract = {
        "contract_version": 1,
        "component": component,
        "version": "v0",
        "primary_target_repo": primary_target_repo,
        "layers": layers,
        "untouchables": [_UNTOUCHABLE_TODO],
        "work_units": [
            {
                "id": _SCAFFOLD_UNIT_ID,
                "title": title,
                "layers": list(layers),
                "target_modules": [_TARGET_MODULES_TODO],
                "test_contract": _TEST_CONTRACT_TODO,
                "depends_on": [],
            }
        ],
    }
    frontmatter = yaml.safe_dump(contract, sort_keys=False, allow_unicode=True)
    return f"---\n{frontmatter}---\n{_prose(issue, title)}"


def _prose(issue: Issue, title: str) -> str:
    """Markdown body: required sections, seeded Purpose, explicit human-TODO markers."""
    quoted_body = "\n".join(f"> {line}" for line in (issue.body or "").splitlines())
    if not quoted_body:
        quoted_body = "> (the issue has no description)"
    unit = _SCAFFOLD_UNIT_ID
    return f"""# {title}

## 1. Purpose

Scaffolded from GitHub issue #{issue.number}: **{title}**

{quoted_body}

TODO: refine the purpose above into the outcome this work unit must deliver.

## 2. Scope fences

TODO: state what is in and out of scope for this unit.

## 7. Untouchables

TODO: list the files/areas this unit must not touch (mirror the frontmatter).

## 10. Acceptance criteria

TODO: define the executable `test_contract` for {unit} — the test(s) that must pass.

## Work units

- **{unit} — {title}**
  - TODO: `target_modules` — replace placeholder `{_TARGET_MODULES_TODO}`.
  - TODO: `test_contract` — replace placeholder with the real contract.
"""


def scaffold_from_issue(
    number: int,
    *,
    component: str,
    primary_target_repo: str,
    out_dir: str | Path,
    layers: dict[str, str] | None = None,
    runner: Runner = subprocess_runner,
    cwd: Path | None = None,
) -> Path:
    """Fetch issue ``number`` and write its PRD skeleton under ``out_dir``; return the path."""
    issue = fetch_issue(number, runner=runner, cwd=cwd)
    text = scaffold_prd(
        issue,
        component=component,
        primary_target_repo=primary_target_repo,
        layers=layers,
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"issue-{number}.prd.md"
    path.write_text(text, encoding="utf-8")
    return path
