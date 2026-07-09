"""Reviewer summary at the approve_pr gate render and in the opened PR body
(WU-REVIEW-RENDER).

Test contract: the approve_pr CLI render includes a reviewer summary -- a concise
"review: clean" line when there are no unresolved findings, or the list of unresolved
blocking findings (file + detail) when present, plus the count of findings resolved via
revision. The opened PR body gains a "Reviewer notes" section carrying the same summary;
a clean review renders a single "Reviewer: clean" line.
"""

from __future__ import annotations

import io

from blacksmith.contract import WorkUnit
from blacksmith.nodes.pr import _pr_body
from blacksmith.render import Renderer

PR_PAYLOAD = {
    "gate": "pr",
    "unit": {"id": "WU-XY", "title": "render the gate", "layers": ["logic"]},
    "implementation": {
        "files_touched": ["blacksmith/render.py"],
        "diff_summary": " blacksmith/render.py | 10 ++",
    },
    "test_results": {"passed": True, "output": "1 passed", "command": "pytest"},
}


def _plain_renderer():
    out = io.StringIO()
    return Renderer(out_stream=out, err_stream=io.StringIO(), plain=True), out


class _TTYStringIO(io.StringIO):
    """A StringIO that claims to be a terminal, so the layer takes the rendered path."""

    def isatty(self) -> bool:  # noqa: D401 - trivial override
        return True


def _unit() -> WorkUnit:
    return WorkUnit(
        id="WU-01",
        title="scaffold",
        layers=["py-logic"],
        target_modules=["pyproject.toml"],
        test_contract="pytest",
        depends_on=[],
    )


# --- (a) render shows unresolved blocking findings when present --------------------


def test_gate_render_shows_unresolved_blocking_findings():
    payload = {
        **PR_PAYLOAD,
        "unresolved_review_findings": [
            {"severity": "blocking", "file": "wu-s.txt", "detail": "off-by-one in the loop bound"},
        ],
        "review_revisions": 1,
    }
    renderer, out = _plain_renderer()
    renderer.gate(payload)
    text = out.getvalue()
    assert "wu-s.txt" in text
    assert "off-by-one in the loop bound" in text
    assert "resolved via revision: 1" in text
    assert "review: clean" not in text


# --- (b) render shows "review: clean" when none -------------------------------------


def test_gate_render_shows_clean_when_no_unresolved_findings():
    renderer, out = _plain_renderer()
    renderer.gate(PR_PAYLOAD)  # no unresolved_review_findings key at all
    text = out.getvalue()
    assert "review: clean" in text
    assert "Unresolved review findings" not in text


def test_gate_render_rendered_mode_shows_unresolved_findings():
    payload = {
        **PR_PAYLOAD,
        "unresolved_review_findings": [
            {"severity": "blocking", "file": "a.py", "detail": "missing null check"},
        ],
        "review_revisions": 2,
    }
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    renderer.gate(payload)
    text = out.getvalue()
    assert "a.py" in text
    assert "missing null check" in text
    assert "resolved via revision: 2" in text


# --- (c) the PR body includes the Reviewer notes section ----------------------------


def test_pr_body_includes_reviewer_notes_section_with_unresolved_findings():
    state = {
        "selected_unit": _unit(),
        "unresolved_review_findings": [
            {"severity": "blocking", "file": "wu-s.txt", "detail": "off-by-one in the loop bound"},
        ],
        "review_revisions": 1,
    }
    body = _pr_body(state)
    assert "**Reviewer notes:**" in body
    assert "resolved via revision: 1" in body
    assert "wu-s.txt" in body
    assert "off-by-one in the loop bound" in body


def test_pr_body_reviewer_notes_clean_is_a_single_line():
    state = {"selected_unit": _unit()}  # no unresolved_review_findings at all
    body = _pr_body(state)
    assert "**Reviewer notes:**" in body
    assert "Reviewer: clean" in body
    # A clean review reports nothing beyond that one line for the section.
    idx = body.index("**Reviewer notes:**")
    section = body[idx:].splitlines()
    # section[0] is the header itself, section[1] is the single "Reviewer: clean" line
    assert section[1] == "Reviewer: clean"


def test_pr_body_surfaces_advisory_reviewer_notes():
    # An advisory (non-blocking) finding never enters unresolved_review_findings — but it must
    # still reach the PR body. Before, such a run wrongly reported "Reviewer: clean" and the
    # note was silently dropped (exactly what buried the mcp_servers finding on #65).
    state = {
        "selected_unit": _unit(),
        "review_findings": [
            {"severity": "advisory", "file": "blacksmith/nodes/plan.py", "detail": "mcp not fwd"},
            {"severity": "advisory", "file": "blacksmith/nodes/plan.py", "detail": "mcp not fwd"},
        ],
    }
    body = _pr_body(state)
    assert "**Reviewer notes:**" in body
    assert "Reviewer: clean" not in body
    assert "advisory: blacksmith/nodes/plan.py" in body
    assert "mcp not fwd" in body
    # De-duped: the repeated finding is listed once, not twice.
    assert body.count("mcp not fwd") == 1
