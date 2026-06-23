"""WU-ISSUE-INGEST: scaffold a Contract v1 PRD skeleton from a GitHub issue.

These tests use a MOCKED ``gh`` runner — no network. They assert that:

* ``fetch_issue`` parses the JSON ``gh issue view`` emits;
* the scaffolder writes a PRD skeleton whose frontmatter ``parse_prd`` ACCEPTS
  (component, primary_target_repo, layers, >=1 untouchable, exactly one work unit with
  non-empty target_modules and placeholder test_contract);
* the prose seeds the issue title/body as Purpose context and carries explicit TODO
  markers for the fields a human must complete (target_modules, test_contract);
* ``_pr_body`` links ``Closes #N`` only when the run state carries an issue number.
"""

from __future__ import annotations

import json

import pytest

from blacksmith.contract import WorkUnit, parse_prd
from blacksmith.issue import (
    Issue,
    IssueError,
    fetch_issue,
    scaffold_from_issue,
    scaffold_prd,
)
from blacksmith.nodes.pr import CommandResult, _pr_body

ISSUE = Issue(
    number=21,
    title="Add a --version flag",
    body="The CLI should print its version.\nSee the spec for details.",
)


def _gh_runner(*, rc=0, payload=None, stderr=""):
    """A mocked ``gh`` runner that records calls and returns canned JSON (no network)."""
    calls: list[list[str]] = []

    def run(argv, cwd=None):
        calls.append(list(argv))
        stdout = "" if payload is None else json.dumps(payload)
        return CommandResult(rc, stdout, stderr)

    run.calls = calls
    return run


# --- fetch_issue -------------------------------------------------------------


def test_fetch_issue_parses_gh_json():
    runner = _gh_runner(payload={"number": 21, "title": "T", "body": "B"})
    issue = fetch_issue(21, runner=runner)
    assert (issue.number, issue.title, issue.body) == (21, "T", "B")
    # It shelled out to `gh issue view` for JSON — no GitHub SDK/HTTP client.
    assert runner.calls[0][:3] == ["gh", "issue", "view"]
    assert "--json" in runner.calls[0]


def test_fetch_issue_nonzero_raises():
    runner = _gh_runner(rc=1, stderr="not found")
    with pytest.raises(IssueError, match="gh issue view"):
        fetch_issue(99, runner=runner)


def test_fetch_issue_bad_json_raises():
    runner = _gh_runner(rc=0)  # returns empty stdout, not valid JSON
    with pytest.raises(IssueError, match="parse"):
        fetch_issue(1, runner=runner)


# --- scaffolder produces a parse_prd-accepted skeleton -----------------------


def test_scaffold_writes_prd_that_parse_prd_accepts(tmp_path):
    runner = _gh_runner(
        payload={"number": ISSUE.number, "title": ISSUE.title, "body": ISSUE.body}
    )
    path = scaffold_from_issue(
        ISSUE.number,
        component="kindling",
        primary_target_repo="smith-and-web/kindling",
        out_dir=tmp_path,
        runner=runner,
    )
    assert path.is_file()

    prd = parse_prd(path)  # raises ContractError if the skeleton is non-conforming
    contract = prd.contract
    assert contract.component == "kindling"
    assert contract.primary_target_repo == "smith-and-web/kindling"
    assert contract.layers  # >=1 layer declared
    assert len(contract.untouchables) >= 1
    # Exactly one work unit, with non-empty target_modules and a test_contract placeholder.
    assert len(contract.work_units) == 1
    unit = contract.work_units[0]
    assert unit.target_modules and all(unit.target_modules)
    assert unit.test_contract.strip()


def test_scaffold_seeds_purpose_and_marks_human_todos(tmp_path):
    runner = _gh_runner(
        payload={"number": ISSUE.number, "title": ISSUE.title, "body": ISSUE.body}
    )
    path = scaffold_from_issue(
        ISSUE.number,
        component="kindling",
        primary_target_repo="smith-and-web/kindling",
        out_dir=tmp_path,
        runner=runner,
    )
    prd = parse_prd(path)
    body = prd.body.lower()

    # The issue title and body are seeded as Purpose context.
    assert "purpose" in body
    assert ISSUE.title.lower() in body
    assert "the cli should print its version." in body
    assert str(ISSUE.number) in prd.body

    # Explicit TODO markers for the fields a human must complete.
    assert "todo" in body
    assert "target_modules" in body
    assert "test_contract" in body


def test_scaffold_prd_string_is_self_contained():
    """scaffold_prd builds a complete frontmatter+prose document on its own."""
    text = scaffold_prd(
        ISSUE,
        component="demo",
        primary_target_repo="owner/demo",
    )
    assert text.startswith("---\n")
    assert "TODO" in text


# --- _pr_body links Closes #N only when the run carries an issue -------------


def _unit() -> WorkUnit:
    return WorkUnit(
        id="WU-001",
        title="scaffold",
        layers=["logic"],
        target_modules=["x.py"],
        test_contract="pytest",
        depends_on=[],
    )


def test_pr_body_includes_closes_when_issue_number_present():
    body = _pr_body({"selected_unit": _unit(), "issue_number": 21})
    assert "Closes #21" in body


def test_pr_body_omits_closes_when_no_issue_number():
    body = _pr_body({"selected_unit": _unit()})
    assert "Closes #" not in body
