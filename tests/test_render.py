"""Reviewer summary at the approve_pr gate render and in the opened PR body
(WU-REVIEW-RENDER, WU-PR-BODY-REDESIGN).

Test contract: the approve_pr CLI render includes a reviewer summary -- a concise
"review: clean" line when there are no unresolved findings, or the list of unresolved
blocking findings (file + detail) when present, plus the count of findings resolved via
revision. The opened PR body renders the built units as a compact Markdown table (unit id,
title, files touched) instead of a wall of diffstats, a single verification line summarizing
the test gate outcome across every built unit, a "Reviewer notes" section carrying the same
summary behind a one-line severity-count header (a clean review still renders a single
"Reviewer: clean" line), and a best-effort collapsed "Build metadata" section sourced from
``cost_events``.
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


def _units(n: int) -> list[WorkUnit]:
    return [
        WorkUnit(
            id=f"WU-{i:02d}",
            title=f"unit {i}",
            layers=["py-logic"],
            target_modules=[f"wu-{i:02d}.txt"],
            test_contract="pytest",
            depends_on=[],
        )
        for i in range(1, n + 1)
    ]


def _multi_unit_state(n: int, *, test_command: str = "pytest") -> tuple[list[WorkUnit], dict]:
    units = _units(n)
    unit_results = [
        {
            "unit_id": u.id,
            "title": u.title,
            "files_touched": [f"{u.id.lower()}.txt"],
            "diff_summary": f" {u.id.lower()}.txt | 1 +",
            "test_command": test_command,
        }
        for u in units
    ]
    state = {"work_units": units, "unit_results": unit_results}
    return units, state


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


# --- WU-PR-DIFF-RENDER: the full combined diff, after the diffstat -------------------


def test_gate_render_pr_with_diff_text_shows_full_diff_section_after_diffstat_plain():
    payload = {
        **PR_PAYLOAD,
        "diff_text": "diff --git a/render.py b/render.py\n+added line",
    }
    renderer, out = _plain_renderer()
    renderer.gate(payload)
    text = out.getvalue()
    assert "diff --git a/render.py b/render.py" in text
    assert "+added line" in text
    diffstat_idx = text.index("Diff summary:")
    full_diff_idx = text.index("Full diff:")
    assert diffstat_idx < full_diff_idx
    assert text.index("diff --git a/render.py b/render.py") > full_diff_idx


def test_gate_render_pr_without_diff_text_is_byte_for_byte_unchanged_plain():
    renderer, out = _plain_renderer()
    renderer.gate(PR_PAYLOAD)  # no diff_text key at all
    text = out.getvalue()
    assert "Full diff:" not in text

    other_renderer, other_out = _plain_renderer()
    other_renderer.gate({**PR_PAYLOAD})
    assert other_out.getvalue() == text


def test_gate_render_pr_with_diff_text_shows_full_diff_section_rendered_mode():
    payload = {
        **PR_PAYLOAD,
        "diff_text": "diff --git a/render.py b/render.py\n+added line",
    }
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    renderer.gate(payload)
    text = out.getvalue()
    assert "diff --git a/render.py b/render.py" in text
    assert "full diff" in text


def test_gate_render_pr_without_diff_text_omits_full_diff_panel_rendered_mode():
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    renderer.gate(PR_PAYLOAD)  # no diff_text key at all
    text = out.getvalue()
    assert "full diff" not in text


def test_gate_render_pr_sections_still_ordered_diffstat_tests_files_review():
    payload = {
        **PR_PAYLOAD,
        "diff_text": "diff --git a/render.py b/render.py\n+added line",
    }
    renderer, out = _plain_renderer()
    renderer.gate(payload)
    text = out.getvalue()
    idx_diffstat = text.index("Diff summary:")
    idx_full_diff = text.index("Full diff:")
    idx_tests = text.index("Tests:")
    idx_files = text.index("Files touched:")
    idx_review = text.index("review: clean")
    assert idx_diffstat < idx_full_diff < idx_tests < idx_files < idx_review


# --- (c) the PR body renders a units table, in declaration order --------------------


def test_pr_body_renders_units_table_for_single_unit():
    state = {"selected_unit": _unit(), "implementation": {"files_touched": ["a.py", "b.py"]}}
    body = _pr_body(state)
    assert "| Unit | What | Files touched |" in body
    assert "| WU-01 | scaffold | a.py, b.py |" in body
    # The raw diffstat is dropped -- GitHub already renders it on the PR itself.
    assert "**Summary:**" not in body
    assert "diff --stat" not in body


def test_pr_body_renders_units_table_in_declaration_order_for_multi_unit():
    _, state = _multi_unit_state(2)
    body = _pr_body(state)
    idx1 = body.index("| WU-01")
    idx2 = body.index("| WU-02")
    assert idx1 < idx2
    # Each unit's own file is attributed to ITS row, not the other unit's.
    assert "wu-01.txt" in body[idx1:idx2]
    assert "wu-02.txt" not in body[idx1:idx2]
    assert "wu-02.txt" in body[idx2:]
    # The raw per-unit diffstat is dropped.
    assert "**Summary:**" not in body
    assert "1 +" not in body


# --- (d) the PR body renders a single verification line -----------------------------


def test_pr_body_single_verification_line_for_single_unit():
    state = {
        "selected_unit": _unit(),
        "test_results": {"passed": True, "output": "1 passed", "command": "pytest"},
    }
    body = _pr_body(state)
    assert "**Verification:** 1/1 units passed `pytest`" in body
    assert "Test gate: passed" not in body


def test_pr_body_single_verification_line_for_multi_unit():
    _, state = _multi_unit_state(2, test_command="pytest")
    body = _pr_body(state)
    assert body.count("units passed") == 1
    assert "**Verification:** 2/2 units passed `pytest`" in body
    assert "Test gate: passed" not in body


# --- (e) the PR body includes the Reviewer notes section, with a severity header ----


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
    assert "0 advisory · 1 blocking · 1 revisions" in body
    assert "resolved via revision: 1" in body
    assert "wu-s.txt" in body
    assert "off-by-one in the loop bound" in body


def test_pr_body_prefers_run_wide_revision_total_over_per_unit():
    # A fan-out run reports revisions via the run-wide reducer ``review_revisions_total`` (its
    # concurrent workers can't write the last-write-wins per-unit ``review_revisions``). The PR
    # body prefers the total; the per-unit field here is 0 (never updated on a fan-out-only run).
    state = {
        "selected_unit": _unit(),
        "unresolved_review_findings": [
            {"severity": "blocking", "file": "a.txt", "detail": "still off by one"},
        ],
        "review_revisions": 0,
        "review_revisions_total": 3,
    }
    body = _pr_body(state)
    assert "resolved via revision: 3" in body
    assert "0 advisory · 1 blocking · 3 revisions" in body


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
    assert "1 advisory · 0 blocking · 0 revisions" in body
    assert "advisory: blacksmith/nodes/plan.py" in body
    assert "mcp not fwd" in body
    # De-duped: the repeated finding is listed once, not twice.
    assert body.count("mcp not fwd") == 1


# --- (f) the PR body includes a best-effort, collapsed Build metadata section -------


def test_pr_body_includes_build_metadata_when_cost_events_present():
    state = {
        "selected_unit": _unit(),
        "cost_events": [
            {
                "node": "implement",
                "unit_id": "WU-01",
                "model": "claude-sonnet-4-6",
                "cost_usd": 0.20,
            },
            {
                "node": "review",
                "unit_id": "WU-01",
                "model": "claude-opus-4-6",
                "cost_usd": 0.05,
            },
        ],
    }
    body = _pr_body(state)
    assert "<details>" in body
    assert "<summary>Build metadata</summary>" in body
    assert "</details>" in body
    assert "Total cost: $0.2500" in body
    assert "implement=claude-sonnet-4-6" in body
    assert "review=claude-opus-4-6" in body


def test_pr_body_omits_build_metadata_when_cost_events_absent():
    state = {"selected_unit": _unit()}
    body = _pr_body(state)
    assert "<details>" not in body
    assert "Build metadata" not in body


def test_pr_body_build_metadata_is_best_effort_on_malformed_events():
    # A malformed/incomplete cost event (missing cost_usd/model) must never crash the PR node --
    # the summary is additive and fail-open.
    state = {"selected_unit": _unit(), "cost_events": [{"node": "implement"}]}
    body = _pr_body(state)
    assert isinstance(body, str)
    assert "Opened by blacksmith for review — not auto-merged." in body


# --- (g) the trailing invariants: Closes #n and the not-auto-merged line ------------


def test_pr_body_ends_with_closes_issue_then_not_auto_merged():
    state = {"selected_unit": _unit(), "issue_number": 42}
    body = _pr_body(state)
    assert "Closes #42" in body
    assert body.rstrip().endswith("Opened by blacksmith for review — not auto-merged.")
    assert body.index("Closes #42") < body.index("Opened by blacksmith for review")
