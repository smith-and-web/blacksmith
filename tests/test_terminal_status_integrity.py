"""Only positive SDK success can substantiate review or a streamed finding."""

import json
from dataclasses import replace

import pytest
from test_activity_integrity import drive, verdict
from test_live_intra_node import _result_message
from test_review_integrity import (
    CLEAN,
    ORIGINAL_COLLECT,
    configure_review,
    install,
    run,
)
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli, receipts
from blacksmith.config import BlacksmithConfig
from blacksmith.executor import Executor
from blacksmith.receipts import CallContext, ReceiptError
from blacksmith.review_receipts import verify_receipt
from blacksmith.run_context import context_from_state
from blacksmith.state import Status

NON_SUCCESS = ["error_max_turns", "error_during_execution", "unrecognized_terminal"]


def legacy_completed_run(project, monkeypatch, *, parallel=False, version=None):
    """Construct sealed pre-policy history using the former permissive runtime.

    Only the new policy guard/intent tag is bypassed during construction. Actual
    Git, graph, journal, gates and checkpoints still run; billed transport is fake.
    """
    configure_review(project, parallel=parallel)
    calls = install(monkeypatch)
    original = receipts.OperationJournal.intent

    def old_intent(self, key, intent):
        if key.kind.startswith("model."):
            intent = dict(intent)
            intent.pop("terminal_policy_version", None)
            if version is not None:
                intent["terminal_policy_version"] = version
        return original(self, key, intent)

    with monkeypatch.context() as old:
        old.setattr(receipts, "validate_model_terminal_policy", lambda journal: None, raising=False)
        old.setattr(receipts.OperationJournal, "intent", old_intent)
        assert run(project, "old-terminal-policy") == 1
    state = saved_values(project[1], "old-terminal-policy")
    assert state["final_gate_receipt"] and not state.get("publication_receipt")
    assert all(row["review_receipt"].status == "clean"
               for row in state["unit_outcomes"].values())
    return context_from_state(state), state, calls


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("version", [None, True, 2])
def test_legacy_terminal_policy_is_inspectable_but_refuses_resume_before_effects(
    project, monkeypatch, capsys, parallel, version,
):
    context, state, calls = legacy_completed_run(
        project, monkeypatch, parallel=parallel, version=version)
    before_calls = list(calls)
    operations, events = context.journal().operations(), context.journal().events()
    inputs = {name: (context.directory / name).read_bytes() for name in (
        "manifest.json", "runtime-config.json", "input.prd.md", "trusted-blacksmith.toml")}

    def forbidden(*args, **kwargs):
        pytest.fail("legacy terminal evidence reached native execution, SDK or an approval")

    monkeypatch.setattr(cli, "Executor", forbidden)
    monkeypatch.setattr(cli, "_resolve_approver", forbidden)
    monkeypatch.setattr("blacksmith.run_context.git_read", forbidden)
    with pytest.raises(ReceiptError, match="legacy_model_terminal_policy_requires_manual_recovery"):
        cli._step(None, state, {}, on_node=forbidden)
    outcome = next(iter(state["unit_outcomes"].values()))
    unit = context.prd().contract.work_unit_by_id(outcome["unit_id"])
    with pytest.raises(ReceiptError, match="legacy_model_terminal_policy_requires_manual_recovery"):
        verify_receipt(outcome["review_receipt"], state, unit=unit,
                       base=outcome["base_commit"], candidate=outcome["source_commit"])
    args = ["--config", str(project[1]), "--thread-id", "old-terminal-policy"]
    assert cli.main(["resume", *args, "--approve", "plan,pr"]) == 1
    assert "legacy_model_terminal_policy_requires_manual_recovery" in capsys.readouterr().err
    assert saved_values(project[1], "old-terminal-policy") == state
    assert context.journal().operations() == operations and context.journal().events() == events
    assert calls == before_calls
    assert {name: (context.directory / name).read_bytes() for name in inputs} == inputs
    assert cli.main(["recovery", "inspect", *args]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["model_terminal_policy"]["supported"] is False
    assert "legacy_model_terminal_policy" in report["model_terminal_policy"]["diagnostic"]
    assert context.journal().operations() == operations and context.journal().events() == events


def test_library_sdk_refuses_legacy_model_policy_before_options_clock_or_new_receipts(
    project, monkeypatch,
):
    context, _, _ = legacy_completed_run(project, monkeypatch)
    journal = context.journal()
    operations, events = journal.operations(), journal.events()
    prior = next(row for row in operations if row.key.kind == "model.review")
    call = CallContext(str(journal.path), context.run_id, journal.context_hash,
                       prior.key.unit_id, prior.key.attempt_id, run_context=context.model_dump())
    executor = Executor(BlacksmithConfig.load(project[1]))

    def forbidden(self):
        pytest.fail("legacy model resolved credentials before policy refusal")

    monkeypatch.setattr(BlacksmithConfig, "resolve_api_key", forbidden)
    with pytest.raises(ReceiptError, match="legacy_model_terminal_policy_requires_manual_recovery"):
        executor.run_review("old cached evidence must not be promoted", call_context=call)
    assert journal.operations() == operations and journal.events() == events


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("subtype", NON_SUCCESS)
def test_error_or_unknown_sdk_terminal_cannot_be_reviewed_clean(
    project, monkeypatch, parallel, subtype,
):
    configure_review(project, parallel=parallel)
    calls = install(monkeypatch)
    installed_collect = Executor._collect

    async def contradictory_terminal(self, prompt, options, *, emit_findings=False):
        ordinary = await installed_collect(self, prompt, options, emit_findings=emit_findings)
        if not emit_findings:
            return ordinary

        async def query(*, prompt, options, transport=None):
            yield replace(_result_message(CLEAN), subtype=subtype, is_error=False)

        # Parallel seats share the graph's Executor; inject transport on an
        # isolated collector instead of mutating another seat's query function.
        collector = Executor(self._config, query_fn=query)
        return await ORIGINAL_COLLECT(collector, prompt, options, emit_findings=True)

    monkeypatch.setattr(Executor, "_collect", contradictory_terminal)
    assert run(project, "terminal-status") == 1
    state = saved_values(project[1], "terminal-status")
    context = context_from_state(state)
    units = {"A", "B"} if parallel else {"U1"}
    assert state["status"] == Status.HALTED and not state.get("pr_url")
    assert set(state["unit_outcomes"]) == units
    for outcome in state["unit_outcomes"].values():
        assert not outcome["ok"] and not outcome["review_clean"]
        assert outcome["failed_stage"] == "review"
        assert outcome["review_receipt"].status == "unavailable"
    reviews = [row for row in context.journal().operations() if row.key.kind == "model.review"]
    assert len(reviews) == len(units)
    for row in reviews:
        assert row.status == "failed"
        result = row.outcome["result"]
        assert result["is_error"] is True and result["terminal_received"] is True
        assert result["error_kind"] == ("max_turns" if subtype == "error_max_turns" else "other")
        assert result["text"] == CLEAN
    assert len(calls) == len(state["cost_events"]) == 3 * len(units)
    assert not state.get("final_gate_receipt") and not state.get("publication_receipt")
    assert not any(row.key.kind.startswith("command:") and row.intent["stage"] == "open_pr"
                   for row in context.journal().operations())
    assert (context.directory / "recovery/index.json").is_file()


@pytest.mark.parametrize("subtype", NON_SUCCESS)
def test_non_success_terminal_never_emits_findings_even_when_error_flag_is_false(
    tmp_path, monkeypatch, subtype,
):
    text = verdict()
    result, events = drive(tmp_path, monkeypatch,
                           [replace(_result_message(text), subtype=subtype, is_error=False)])
    assert result.is_error and result.terminal_received and result.text == text
    assert result.error_kind == ("max_turns" if subtype == "error_max_turns" else "other")
    assert not any(payload.get("activity") == "finding" for _, payload in events)
