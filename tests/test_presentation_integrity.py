"""Bounded CLI/PR previews preserve full evidence and honest historical counts."""

import io
from copy import deepcopy

import pytest
from test_cli_render import _strip_ansi
from test_render import PR_PAYLOAD, _TTYStringIO

from blacksmith.nodes.pr import _reviewer_notes_lines
from blacksmith.observability import display_text, review_revision_count
from blacksmith.render import Renderer
from blacksmith.state import Status


@pytest.mark.parametrize("rendered", [False, True])
def test_error_display_is_bounded_without_rewriting_the_retained_ledger(rendered):
    out = _TTYStringIO() if rendered else io.StringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    errors = [{"node": "worker", "message": f"entry-{i}: " + "D" * 5000 + "\x1b[2J\x9b2J"}
              for i in range(45)]
    original = deepcopy(errors)
    renderer.report(status=Status.HALTED, pr_url=None, errors=errors,
                    cost_line="total cost: $0.00", token_line="tokens: unknown")
    text = _strip_ansi(out.getvalue())
    assert "halted" in text and "25 more errors omitted" in text
    assert "entry-0:" in text and "entry-19:" in text and "entry-20:" not in text
    assert "characters omitted" in text and "full details retained in run artifacts" in text
    assert len(text) < 60_000 and "\x1b" not in text and "\x9b" not in text
    assert errors == original


@pytest.mark.parametrize("rendered", [False, True])
def test_gate_finding_preview_and_unknown_count_do_not_claim_clean_or_zero(rendered):
    out = _TTYStringIO() if rendered else io.StringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    findings = [{"severity": "blocking", "file": f"file-{i}", "detail": "D" * 5000}
                for i in range(45)]
    payload = {**PR_PAYLOAD, "review_status": "unavailable",
               "unresolved_review_findings": findings, "review_revisions_total": None,
               "review_revisions": 7}
    original = deepcopy(payload)
    renderer.gate(payload)
    text = _strip_ansi(out.getvalue())
    assert "unavailable" in text and "review: clean" not in text
    assert "25 more findings omitted" in text and "file-19" in text and "file-20" not in text
    assert "revision attempts: unavailable" in text and "revision attempts: 7" not in text
    assert "characters omitted" in text and len(text) < 65_000
    assert payload == original


def test_pr_finding_preview_counts_all_notes_but_displays_bounded_rows():
    state = {"review_revisions_total": 999, "review_revisions": 0,
             "unit_outcomes": {"A": {"review_revisions": 1},
                               "B": {"review_revisions": 2}},
             "review_findings": [{"severity": "advisory", "file": f"file-{i}",
                                  "detail": "D" * 5000} for i in range(45)]}
    original = deepcopy(state)
    text = "\n".join(_reviewer_notes_lines(state))
    assert "45 advisory" in text and "3 revisions" in text and "999 revisions" not in text
    assert "25 more findings omitted" in text and "file-19" in text and "file-20" not in text
    assert "Reviewer: clean" not in text and "characters omitted" in text
    assert len(text) < 50_000 and state == original


@pytest.mark.parametrize(("state", "expected"), [
    ({"review_revisions_total": 999, "unit_outcomes": {"A": {"review_revisions": 1,
      "recovery_history": [{"review_revisions": 8}]}, "B": {"review_revisions": 2}}}, 3),
    ({"review_revisions_total": 999, "unit_outcomes": {"A": {}}}, None),
    ({"review_revisions_total": 999, "unit_outcomes": {"A": {"review_revisions": True}}}, None),
    ({"review_revisions_total": None, "review_revisions": 7}, None),
    ({"review_revisions_total": 3, "review_revisions": 7}, 3),
    ({"review_revisions": 2}, 2),
    ({"current_unit_id": "A", "review_revisions": 2,
      "unit_outcomes": {"A": {"review_revisions": 1}}}, 2),
])
def test_current_keyed_counts_ignore_recovery_history_and_inflated_legacy_totals(state, expected):
    assert review_revision_count(state) == expected


def test_terminal_controls_are_escaped_before_preview_without_touching_source():
    text = "\x1b[2J\x9b2J\u202e\x00" + "A" * 5000
    shown = display_text(text, 80)
    assert shown.startswith("\\x1b[2J\\x9b2J\\u202e\\x00")
    assert "characters omitted" in shown and len(shown) < 200
    assert text.startswith("\x1b[2J\x9b2J\u202e\x00")
