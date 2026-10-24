"""Explicit unavailable-review recovery and real review-receipt crash boundaries."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_integration_integrity import stop_at_pr
from test_review_integrity import BLOCKING, CLEAN, configure_review, install, run
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.executor import Executor
from blacksmith.graph import build_checkpointer
from blacksmith.nodes.pr import _reviewer_notes_lines
from blacksmith.run_context import context_from_state
from blacksmith.state import Status


@pytest.mark.parametrize("mode", ["malformed", "terminal_error", "missing_terminal"])
def test_explicit_unavailable_panel_retry_reuses_valid_seats_and_original_candidate(
    project, monkeypatch, mode,
):
    configure_review(project, panel=3)
    calls = install(monkeypatch, responses=lambda unit, number: mode if number == 2 else CLEAN)
    assert run(project, "panel-retry") == 1
    before = saved_values(project[1], "panel-retry")
    context = context_from_state(before)
    original = before["unit_outcomes"]["U1"]
    first = original["review_receipt"]
    assert first.status == "unavailable" and len(first.panel_results) == 2
    old_calls = [o for o in context.journal().operations() if o.key.kind == "model.review"]
    source = git(context.workspace_path, "rev-parse", "HEAD")
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "panel-retry",
                  "--continue-attempt"])
    after = saved_values(project[1], "panel-retry")
    row = after["unit_outcomes"]["U1"]
    record = row["review_receipt"]
    assert record.status == "clean" and row["ok"]
    assert row["source_commit"] == source == record.candidate_commit
    assert row["base_commit"] == original["base_commit"] and row["review_revisions"] == 0
    assert row["implement_continuations"] == original["implement_continuations"] + 1
    assert row["fix_attempts"] == original["fix_attempts"]
    assert row["gate_receipt"] == original["gate_receipt"]
    assert row["recovery_history"] == [original]
    assert record.panel_results[0] == first.panel_results[0]
    assert record.panel_results[1].call_id != first.panel_results[1].call_id
    assert len([c for c in calls if c[0] == "review"]) == 4  # 2 original + 2 new seats.
    assert len([c for c in calls if c[0] == "implement"]) == 1
    assert all(context.journal().get(o.key) == o for o in old_calls)
    assert json.loads(Path(first.artifact_path).read_text())["status"] == "unavailable"
    ids = [e["event_id"] for e in after["cost_events"]]
    assert len(ids) == len(set(ids)) == 6
    assert not after.get("pr_url") and after["final_gate_receipt"].outcome == "passed"


@pytest.mark.parametrize("flag", ["--continue-attempt", "--retry-stage"])
def test_exhausted_blocker_cannot_re_review_same_sha_or_reset_allowance(
    project, monkeypatch, flag,
):
    configure_review(project, revisions=0)
    calls = install(monkeypatch, responses=lambda unit, number: BLOCKING)
    assert run(project, "blocker") == 1
    state = saved_values(project[1], "blocker")
    context = context_from_state(state)
    original_models = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    checkpoint = saver.get_tuple({"configurable": {"thread_id": "blocker"}}).checkpoint
    saver.conn.close()
    monkeypatch.setattr(cli, "Executor", lambda *a, **k: pytest.fail("blocker reached executor"))
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "blocker", flag]) == 1
    after = saved_values(project[1], "blocker")
    assert after == state and len(calls) == 3
    assert after["review_receipt"].status == "blocking" and not after.get("pr_url")
    assert all(context.journal().get(o.key) == o for o in original_models)
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple({"configurable": {"thread_id": "blocker"}}).checkpoint == checkpoint
    saver.conn.close()


CRASH = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pytest
from test_review_integrity import install
from blacksmith import cli
import blacksmith.nodes.review as review_module
import blacksmith.run_context as run_context
install(pytest.MonkeyPatch())
mode = sys.argv[4]
original = review_module.retain
def retain(context, key, record=None):
    if record is not None and mode == 'before_seal':
        os._exit(91)
    result = original(context, key, record)
    if record is not None and mode == 'after_receipt':
        os._exit(92)
    return result
review_module.retain = retain
write = run_context.atomic_write
def atomic_write(path, text):
    if mode == 'sealed_before_artifact' and path.parent.name == 'reviews':
        os._exit(93)
    return write(path, text)
run_context.atomic_write = atomic_write
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'review-exit', '--approve', 'plan'])
'''


@pytest.mark.parametrize("mode,exit_code", [
    ("before_seal", 91), ("after_receipt", 92), ("sealed_before_artifact", 93),
])
def test_real_process_exit_reconciles_review_without_repeating_any_sdk_call(
    project, monkeypatch, mode, exit_code,
):
    configure_review(project, panel=3)
    child = subprocess.run([sys.executable, "-c", CRASH, str(Path(__file__).parent),
                            str(project[2]), str(project[1]), mode],
                           capture_output=True, text=True, timeout=75)
    assert child.returncode == exit_code, child.stdout + child.stderr
    before = saved_values(project[1], "review-exit")
    context = context_from_state(before)
    models = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]
    assert len(models) == 5 and all(o.status == "completed" for o in models)
    source = git(context.workspace_path, "rev-parse", "HEAD")
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", lambda *a, **k: pytest.fail("completed SDK replay"))
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "review-exit"])
    after = saved_values(project[1], "review-exit")
    record = after["unit_outcomes"]["U1"]["review_receipt"]
    assert record.status == "clean" and record.candidate_commit == source
    assert len(record.panel_results) == 3 and Path(record.artifact_path).is_file()
    assert git(context.workspace_path, "rev-parse", "HEAD") == source
    assert len(after["cost_events"]) == len({e["event_id"] for e in after["cost_events"]}) == 5
    assert all(context.journal().get(o.key) == o for o in models)
    assert len([o for o in context.journal().operations() if o.key.kind.startswith("model.")]) == 5
    assert after["final_gate_receipt"].candidate_commit == source and not after.get("pr_url")


@pytest.mark.parametrize("parallel", [False, True])
def test_disabled_review_has_explicit_receipts_and_no_model_or_clean_claim(
    project, monkeypatch, parallel,
):
    configure_review(project, parallel=parallel)
    project[1].write_text(project[1].read_text().replace("[review]\n", "[review]\nenabled=false\n"))
    calls = install(monkeypatch, responses=lambda *a: pytest.fail("disabled review ran"))
    seen = []

    def approve(payload, values):
        if payload["gate"] == "plan":
            return True
        seen.append(payload)
        raise KeyboardInterrupt("disabled review approval; no publication")
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        run(project, "disabled-review")
    state = saved_values(project[1], "disabled-review")
    assert state["status"] != Status.DONE and not state.get("pr_url")
    assert seen[0]["review_status"] == "disabled"
    assert "Reviewer: disabled" in _reviewer_notes_lines(state)
    assert not any(c[0] == "review" for c in calls)
    for row in state["unit_outcomes"].values():
        record = row["review_receipt"]
        assert row["ok"] and not row["review_clean"] and record.status == "disabled"
        assert record.panel_results == record.cost_references == ()


def test_budget_amendment_revises_a_blocker_and_reports_latest_clean_without_erasing_history(
    project, monkeypatch,
):
    configure_review(project, revisions=1)
    project[1].write_text(project[1].read_text().replace(
        "[limits]\n", "[limits]\nmax_run_cost_usd=0.025\n"))

    def change(unit, number, workspace, prompt):
        (workspace / "result.txt").write_text("version " + str(number))

    calls = install(monkeypatch, implement=change,
                    responses=lambda unit, number: BLOCKING if number == 1 else CLEAN)
    assert run(project, "blocked-budget") == 1
    before = saved_values(project[1], "blocked-budget")
    original = before["unit_outcomes"]["U1"]
    assert original["review_receipt"].status == "blocking" and original["review_revisions"] == 0
    seen = []

    def approve(payload, values):
        seen.append(payload)
        raise KeyboardInterrupt("revised candidate; no publication")
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "blocked-budget",
                  "--add-budget-usd", "0.1"])
    after = saved_values(project[1], "blocked-budget")
    outcome = after["unit_outcomes"]["U1"]
    assert outcome["review_receipt"].status == "clean" and outcome["review_revisions"] == 1
    assert outcome["source_commit"] != original["source_commit"]
    assert len([c for c in calls if c[0] == "review"]) == 2
    assert len([c for c in calls if c[0] == "implement"]) == 2
    assert seen[0]["review_status"] == "clean" and seen[0]["unresolved_review_findings"] == []
    assert after["unresolved_review_findings"]  # Original failure history is retained.
    notes = "\n".join(_reviewer_notes_lines(after))
    assert "Reviewer: clean" in notes and "unresolved (blocking)" not in notes
    assert "resolved via revision" not in notes and not after.get("pr_url")
