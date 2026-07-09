"""Tests for the review panel aggregation (WU-REVIEW-PANEL-AGGREGATE).

Pure function only -- no executor/graph wiring here. ``aggregate_panel_verdicts``
takes a list of per-reviewer findings-lists and reduces them to a single
``(review_clean, findings)`` pair: majority vote on "blocking" decides
``review_clean``; ``findings`` is the de-duped union across all reviewers.
"""

from blacksmith.nodes.review import aggregate_panel_verdicts

BLOCKING_A = {"severity": "blocking", "file": "blacksmith/foo.py", "detail": "off-by-one"}
BLOCKING_B = {"severity": "blocking", "file": "blacksmith/bar.py", "detail": "race condition"}
ADVISORY_A = {"severity": "advisory", "file": "blacksmith/foo.py", "detail": "minor nit"}


# --- (a) 3 reviewers, 1 blocking -> no consensus -> clean ----------------------


def test_three_reviewers_one_blocking_is_clean_no_consensus():
    findings_by_reviewer = [
        [BLOCKING_A],
        [],
        [],
    ]
    review_clean, findings = aggregate_panel_verdicts(findings_by_reviewer)
    assert review_clean is True
    assert findings == [BLOCKING_A]


# --- (b) 3 reviewers, 2 blocking -> consensus -> not clean ---------------------


def test_three_reviewers_two_blocking_is_not_clean_consensus():
    findings_by_reviewer = [
        [BLOCKING_A],
        [BLOCKING_B],
        [],
    ]
    review_clean, findings = aggregate_panel_verdicts(findings_by_reviewer)
    assert review_clean is False
    assert findings == [BLOCKING_A, BLOCKING_B]


# --- (c) n=1 preserves today's single-reviewer semantics -----------------------


def test_single_reviewer_any_blocking_is_not_clean():
    review_clean, findings = aggregate_panel_verdicts([[BLOCKING_A]])
    assert review_clean is False
    assert findings == [BLOCKING_A]


def test_single_reviewer_no_blocking_is_clean():
    review_clean, findings = aggregate_panel_verdicts([[ADVISORY_A]])
    assert review_clean is True
    assert findings == [ADVISORY_A]


def test_single_reviewer_no_findings_is_clean():
    review_clean, findings = aggregate_panel_verdicts([[]])
    assert review_clean is True
    assert findings == []


# --- (d) union de-dupes an identical finding raised by two reviewers -----------


def test_union_dedupes_identical_finding_across_reviewers():
    findings_by_reviewer = [
        [BLOCKING_A],
        [dict(BLOCKING_A)],  # same (file, detail), raised independently by a 2nd reviewer
    ]
    review_clean, findings = aggregate_panel_verdicts(findings_by_reviewer)
    assert findings == [BLOCKING_A]
    assert len(findings) == 1
    # 2 of 2 reviewers voted blocking on the (de-duped) same finding -> consensus.
    assert review_clean is False


def test_union_preserves_distinct_findings_from_different_reviewers():
    findings_by_reviewer = [
        [BLOCKING_A, ADVISORY_A],
        [BLOCKING_B],
    ]
    review_clean, findings = aggregate_panel_verdicts(findings_by_reviewer)
    assert findings == [BLOCKING_A, ADVISORY_A, BLOCKING_B]
    assert review_clean is False  # 2 of 2 reviewers voted blocking


# --- empty panel edge case ------------------------------------------------------


def test_empty_panel_is_clean_with_no_findings():
    review_clean, findings = aggregate_panel_verdicts([])
    assert review_clean is True
    assert findings == []
