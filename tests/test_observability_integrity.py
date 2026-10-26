"""Honest observation across real CLI/child/recovery/checkpoint boundaries."""

from pathlib import Path

import pytest
from test_review_integrity import BLOCKING, CLEAN, configure_review, install, run
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli
from blacksmith.nodes.pr import _reviewer_notes_lines
from blacksmith.run_context import context_from_state


@pytest.mark.parametrize("parallel", [False, True])
def test_recovered_review_revision_is_counted_once_with_original_evidence_retained(
    project, monkeypatch, parallel,
):
    configure_review(project, parallel=parallel, revisions=1)
    selected = "A" if parallel else "U1"

    def change(unit, number, workspace, prompt):
        target = unit.lower() + ".txt" if parallel else "result.txt"
        (workspace / target).write_text("revision " + str(number))

    def response(unit, number):
        if unit != selected:
            return CLEAN
        return BLOCKING if number == 1 else "malformed" if number == 2 else CLEAN

    calls = install(monkeypatch, implement=change, responses=response)
    assert run(project, "observed-recovery") == 1
    before = saved_values(project[1], "observed-recovery")
    original = before["unit_outcomes"][selected]
    assert original["review_revisions"] == before["review_revisions_total"] == 1
    assert original["review_receipt"].status == "unavailable"
    old_artifact = Path(original["review_receipt"].artifact_path).read_bytes()
    context = context_from_state(before)
    old_models = [row for row in context.journal().operations()
                  if row.key.kind.startswith("model.")]
    old_cost_ids = {row["event_id"] for row in before["cost_events"]}
    old_other_calls = [call for call in calls if call[1] != selected]
    seen = []

    def approve(payload, values):
        if payload["gate"] == "plan":
            return True
        seen.append(payload)
        raise KeyboardInterrupt("observability proof; no publication")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    args = ["resume", "--config", str(project[1]), "--thread-id", "observed-recovery",
            "--continue-attempt"]
    if parallel:
        args += ["--unit-id", selected]
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(args)
    after = saved_values(project[1], "observed-recovery")
    outcome = after["unit_outcomes"][selected]
    assert outcome["review_revisions"] == 1
    assert after["review_revisions_total"] == seen[0]["review_revisions_total"] == 1
    assert outcome["source_commit"] == original["source_commit"]
    assert outcome["base_commit"] == original["base_commit"]
    assert outcome["fix_attempts"] == original["fix_attempts"]
    assert outcome["implement_continuations"] == original["implement_continuations"] + 1
    assert outcome["review_receipt"].status == seen[0]["review_status"] == "clean"
    assert outcome["recovery_history"] == [original]
    assert [call for call in calls if call[1] != selected] == old_other_calls
    assert len([call for call in calls if call[:2] == ("implement", selected)]) == 2
    assert len([call for call in calls if call[:2] == ("review", selected)]) == 3
    assert all(context.journal().get(row.key) == row for row in old_models)
    assert Path(original["review_receipt"].artifact_path).read_bytes() == old_artifact
    cost_ids = [row["event_id"] for row in after["cost_events"]]
    assert len(cost_ids) == len(set(cost_ids)) == len(old_cost_ids) + 1
    assert old_cost_ids < set(cost_ids)
    assert not after.get("pr_url")
    assert any(row["severity"] == "blocking" for row in after["review_findings"])
    notes = "\n".join(_reviewer_notes_lines(after))
    assert "Reviewer: clean" in notes and "unresolved (blocking)" not in notes
    assert "resolved via revision" not in notes


def test_parallel_model_activity_matches_real_parent_task_and_critical_call_identity(
    project, monkeypatch,
):
    from test_integration_integrity import stop_at_pr

    from blacksmith.executor import Executor, _emit_activity

    configure_review(project, parallel=True)
    calls = install(monkeypatch)
    collect = Executor._collect

    async def observed_collect(self, prompt, options, *, emit_findings=False):
        _emit_activity({"activity": "turn", "turn": 1})
        return await collect(self, prompt, options, emit_findings=emit_findings)

    monkeypatch.setattr(Executor, "_collect", observed_collect)
    events = []
    safe_emitter = cli._safe_event_emitter

    def observing_emitter(sink, thread_id):
        emit = safe_emitter(sink, thread_id)

        def observed(kind, payload):
            events.append((kind, payload))
            emit(kind, payload)
        return observed

    monkeypatch.setattr(cli, "_safe_event_emitter", observing_emitter)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        run(project, "parallel-observation")
    state = saved_values(project[1], "parallel-observation")
    context = context_from_state(state)
    operations = {row.key.id: row for row in context.journal().operations()
                  if row.key.kind.startswith("model.")}
    starts = {payload["task_id"]: payload for kind, payload in events if kind == "node_start"}
    activity = [payload for kind, payload in events if kind == "node_activity"]
    assert len(calls) == len(operations) == len(activity) == len(state["cost_events"]) == 6
    assert {row["unit_id"] for row in activity} == {"A", "B"}
    for payload in activity:
        row = operations[payload["call_id"]]
        assert payload["run_id"] == row.key.run_id == state["run_id"]
        assert payload["unit_id"] == row.key.unit_id
        assert payload["attempt_id"] == row.key.attempt_id
        assert payload["role"] == row.key.kind and payload["identity_source"] == "model_call"
        assert payload["task_id"]
        if payload["role"] == "model.plan":
            assert payload["task_id"] in starts and payload["parent_task_id"] is None
        else:
            parent = starts[payload["parent_task_id"]]
            assert parent["node"] == "execute_unit"
            assert parent["unit_id"] == payload["unit_id"]
            assert payload["task_id"] != payload["parent_task_id"]
            assert payload["node"] in {"implement", "review"}
    assert not state.get("pr_url")
    assert not any(row.key.kind.startswith("command:") and row.intent["stage"] == "open_pr"
                   for row in context.journal().operations())
