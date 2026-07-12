"""Tests for the review node (WU-REVIEW-NODE).

A stronger model adversarially reviews the diff of a unit that ALREADY PASSED the test
gate, using read-only tools only. This unit adds only the node + state keys — no graph
wiring — so these tests call ``review()`` directly with a FAKE executor.
"""

import operator
from pathlib import Path
from typing import Annotated, get_type_hints

from blacksmith.config import IndexConfig
from blacksmith.contract import parse_prd
from blacksmith.executor import ExecutorResult
from blacksmith.index import QUALIFIED_INDEX_TOOL_NAMES
from blacksmith.nodes.review import review
from blacksmith.state import BlacksmithState, ReviewFinding

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"

CLEAN_VERDICT = '```json\n{"verdict": "clean", "findings": []}\n```'
NEEDS_CHANGES_VERDICT = (
    '```json\n{"verdict": "needs_changes", "findings": '
    '[{"severity": "blocking", "file": "blacksmith/foo.py", '
    '"detail": "off-by-one in the loop bound"}]}\n```'
)
ADVISORY_ONLY_VERDICT = (
    '```json\n{"verdict": "needs_changes", "findings": '
    '[{"severity": "advisory", "file": "blacksmith/foo.py", "detail": "minor nit"}]}\n```'
)


def _result(text, *, is_error=False, model="claude-opus-4-8", cost=0.02, num_turns=2):
    return ExecutorResult(
        text=text,
        model=model,
        is_error=is_error,
        num_turns=num_turns,
        cost_usd=cost,
        usage={
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        },
        session_id="s1",
    )


class FakeExecutor:
    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    def run_review(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self._result


class FakePanelExecutor:
    """Returns a distinct canned result per call, in order -- for panel tests where each
    of the N calls needs its own verdict."""

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[dict] = []

    def run_review(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self._results.pop(0)


def _state(**overrides):
    prd = parse_prd(VENDORED_PRD)
    unit = prd.contract.work_unit_by_id("WU-01")
    state = {
        "prd": prd,
        "selected_unit": unit,
        "worktree_path": "/tmp/does-not-matter",
        "implementation": {
            "files_touched": ["blacksmith/foo.py"],
            "diff_summary": " 1 file changed, 2 insertions(+)",
        },
    }
    state.update(overrides)
    return state


# --- (a) clean verdict --------------------------------------------------------


def test_review_node_clean_verdict_is_clean():
    fake = FakeExecutor(_result(CLEAN_VERDICT))
    out = review(_state(), executor=fake)
    assert out["review_clean"] is True
    assert out["review_findings"] == []


# --- (b) needs_changes with a blocking finding --------------------------------


def test_review_node_needs_changes_with_blocking_finding():
    fake = FakeExecutor(_result(NEEDS_CHANGES_VERDICT))
    out = review(_state(), executor=fake)
    assert out["review_clean"] is False
    assert len(out["review_findings"]) == 1
    finding = out["review_findings"][0]
    assert finding["severity"] == "blocking"
    assert finding["file"] == "blacksmith/foo.py"
    assert "off-by-one" in finding["detail"]


def test_review_node_advisory_only_finding_is_still_clean():
    # Only BLOCKING findings gate; an advisory-only "needs_changes" verdict is surfaced
    # but never flips review_clean False (style/taste is the linter's job, not review's).
    fake = FakeExecutor(_result(ADVISORY_ONLY_VERDICT))
    out = review(_state(), executor=fake)
    assert out["review_clean"] is True
    assert out["review_findings"][0]["severity"] == "advisory"


# --- (c) unparseable / empty verdicts fail open -------------------------------


def test_review_node_unparseable_verdict_is_treated_as_clean():
    fake = FakeExecutor(_result("I looked at it, seems fine, no fenced block here."))
    out = review(_state(), executor=fake)
    assert out["review_clean"] is True
    assert out["review_findings"] == []


def test_review_node_empty_verdict_is_treated_as_clean():
    fake = FakeExecutor(_result(""))
    out = review(_state(), executor=fake)
    assert out["review_clean"] is True
    assert out["review_findings"] == []


def test_review_node_malformed_json_is_treated_as_clean():
    fake = FakeExecutor(_result('```json\n{"verdict": "needs_changes", "findings": [\n```'))
    out = review(_state(), executor=fake)
    assert out["review_clean"] is True
    assert out["review_findings"] == []


def test_review_node_error_result_treated_as_clean():
    # The model call itself failed (e.g. max-turns) -- fail-open, never wedge a green unit.
    fake = FakeExecutor(_result("Reached maximum number of turns", is_error=True, cost=0.5))
    out = review(_state(), executor=fake)
    assert out["review_clean"] is True
    assert out["review_findings"] == []
    # ...but the attempt's spend is still ledgered even though it fails open.
    assert out["cost_events"][0]["cost_usd"] == 0.5


# --- (d) cost_event recorded ---------------------------------------------------


def test_review_node_records_one_cost_event():
    fake = FakeExecutor(_result(CLEAN_VERDICT, cost=0.03, num_turns=4))
    out = review(_state(), executor=fake)
    events = out["cost_events"]
    assert len(events) == 1
    assert events[0]["node"] == "review"
    assert events[0]["unit_id"] == "WU-01"
    assert events[0]["cost_usd"] == 0.03
    assert events[0]["num_turns"] == 4


# --- tool surface --------------------------------------------------------------


def test_review_node_uses_read_only_tools_only():
    fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=fake)
    call = fake.calls[0]
    assert set(call["allowed_tools"]) == {"Read", "Glob", "Grep"}
    for tool in ("Write", "Edit", "Bash"):
        assert tool in call["disallowed_tools"]


def test_review_prompt_includes_diff_and_test_contract():
    fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=fake)
    prompt = fake.calls[0]["prompt"]
    assert "blacksmith/foo.py" in prompt
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    assert unit.test_contract in prompt


# --- pass-through / guard behaviour --------------------------------------------


def test_review_node_noop_without_executor():
    out = review(_state())
    assert out == {}


def test_review_node_missing_selected_unit_records_error():
    state = _state()
    del state["selected_unit"]
    out = review(state, executor=FakeExecutor(_result(CLEAN_VERDICT)))
    assert out["errors"][0]["node"] == "review"


def test_review_node_missing_prd_records_error():
    state = _state()
    del state["prd"]
    out = review(state, executor=FakeExecutor(_result(CLEAN_VERDICT)))
    assert out["errors"][0]["node"] == "review"


# --- state shape -----------------------------------------------------------------


def test_state_has_review_clean_and_appendonly_review_findings_field():
    hints = get_type_hints(BlacksmithState, include_extras=True)
    assert hints["review_clean"] is bool
    assert hints["review_findings"] == Annotated[list[ReviewFinding], operator.add]


def test_state_has_review_panel_size_field():
    hints = get_type_hints(BlacksmithState, include_extras=True)
    assert hints["review_panel_size"] is int


# --- panel (WU-REVIEW-PANEL-NODE) ------------------------------------------------


def test_review_panel_size_3_issues_three_calls_with_distinct_emphases():
    fake = FakePanelExecutor(
        [_result(CLEAN_VERDICT), _result(CLEAN_VERDICT), _result(CLEAN_VERDICT)]
    )
    out = review(_state(review_panel_size=3), executor=fake)

    assert len(fake.calls) == 3
    assert len(out["cost_events"]) == 3
    prompts = [c["prompt"] for c in fake.calls]
    assert "PANEL EMPHASIS for this pass: correctness" in prompts[0]
    assert "PANEL EMPHASIS for this pass: security" in prompts[1]
    assert "PANEL EMPHASIS for this pass: regression" in prompts[2]
    assert len({p for p in prompts}) == 3  # three genuinely distinct prompts


def test_review_panel_1_of_3_blocking_is_still_clean():
    # Only 1 of 3 reviewers flags blocking -- fewer than the ceil(3/2)=2 majority needed.
    fake = FakePanelExecutor(
        [_result(NEEDS_CHANGES_VERDICT), _result(CLEAN_VERDICT), _result(CLEAN_VERDICT)]
    )
    out = review(_state(review_panel_size=3), executor=fake)
    assert out["review_clean"] is True
    assert len(out["cost_events"]) == 3


def test_review_panel_2_of_3_blocking_is_not_clean():
    # 2 of 3 reviewers flag blocking -- a majority, so the unit is sent back for revision.
    fake = FakePanelExecutor(
        [
            _result(NEEDS_CHANGES_VERDICT),
            _result(NEEDS_CHANGES_VERDICT),
            _result(CLEAN_VERDICT),
        ]
    )
    out = review(_state(review_panel_size=3), executor=fake)
    assert out["review_clean"] is False
    assert len(out["cost_events"]) == 3


def test_review_panel_size_1_matches_pre_panel_single_call_output():
    # panel_size defaults to 1 (missing from state entirely) -- byte-for-byte the same
    # single-call, no-emphasis behaviour as before this unit.
    fake = FakeExecutor(_result(CLEAN_VERDICT))
    baseline = review(_state(), executor=fake)

    fake_explicit = FakeExecutor(_result(CLEAN_VERDICT))
    explicit = review(_state(review_panel_size=1), executor=fake_explicit)

    assert len(fake.calls) == 1
    assert len(fake_explicit.calls) == 1
    assert baseline == explicit
    assert baseline["review_clean"] is True
    assert baseline["review_findings"] == []
    assert len(baseline["cost_events"]) == 1
    # No panel emphasis text leaks into the single-reviewer prompt.
    assert "PANEL EMPHASIS" not in fake.calls[0]["prompt"]
    assert "PANEL EMPHASIS" not in fake_explicit.calls[0]["prompt"]


# --- index tools (WU-REVIEW-INDEX) ------------------------------------------------


def test_review_index_enabled_grants_search_and_read_symbol_tools():
    fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=fake, index_config=IndexConfig(enabled=True))
    call = fake.calls[0]
    # The reviewer now grants the SAME full index tool set as plan/implement (shared
    # QUALIFIED_INDEX_TOOL_NAMES) -- it used to grant only search_code + read_symbol.
    assert set(QUALIFIED_INDEX_TOOL_NAMES) <= set(call["allowed_tools"])
    assert "blacksmith-index" in call["mcp_servers"]


def test_review_index_enabled_still_forbids_write_tools():
    fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=fake, index_config=IndexConfig(enabled=True))
    call = fake.calls[0]
    for tool in ("Write", "Edit", "Bash"):
        assert tool in call["disallowed_tools"]
    # No write/edit/shell tool sneaks into allowed_tools alongside the index tools.
    assert "Write" not in call["allowed_tools"]
    assert "Edit" not in call["allowed_tools"]
    assert "Bash" not in call["allowed_tools"]


def test_review_index_enabled_prompt_teaches_index_and_absolute_path():
    fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=fake, index_config=IndexConfig(enabled=True))
    system_prompt = fake.calls[0]["system_prompt"]
    # Index-first guidance names both tools and the query semantics (parity with the
    # implement/plan tiers -- the reviewer had the tools but not the instructions, and
    # the observed result was full-file Reads and hallucinated paths).
    assert "USE THE INDEX FIRST" in system_prompt
    assert "search_code" in system_prompt
    assert "read_symbol" in system_prompt
    assert "`path`" in system_prompt
    # The worktree's ABSOLUTE path is stated so the reviewer never guesses one
    # (observed: Read /repo/... and Read /Users/smith/... both failed and wasted turns).
    assert str(Path("/tmp/does-not-matter").resolve()) in system_prompt
    assert "absolute path" in system_prompt.lower()


def test_review_index_disabled_prompt_is_unchanged():
    baseline_fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=baseline_fake)

    disabled_fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=disabled_fake, index_config=IndexConfig(enabled=False))

    baseline_prompt = baseline_fake.calls[0]["system_prompt"]
    disabled_prompt = disabled_fake.calls[0]["system_prompt"]
    # Disabled = byte-for-byte the pre-index prompt: no guidance text leaks in.
    assert baseline_prompt == disabled_prompt
    assert "USE THE INDEX FIRST" not in baseline_prompt
    assert "absolute path" not in baseline_prompt.lower()


def test_review_index_disabled_matches_baseline_tool_surface():
    baseline_fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=baseline_fake)

    disabled_fake = FakeExecutor(_result(CLEAN_VERDICT))
    review(_state(), executor=disabled_fake, index_config=IndexConfig(enabled=False))

    baseline_call = baseline_fake.calls[0]
    disabled_call = disabled_fake.calls[0]
    # No index_config at all vs. an explicitly disabled one produce the identical
    # (byte-for-byte) tool surface -- no index tools, no mcp_servers key.
    expected = ["Read", "Glob", "Grep"]
    assert baseline_call["allowed_tools"] == expected
    assert disabled_call["allowed_tools"] == expected
    assert "mcp_servers" not in baseline_call
    assert "mcp_servers" not in disabled_call
    for name in QUALIFIED_INDEX_TOOL_NAMES:
        assert name not in baseline_call["allowed_tools"]
