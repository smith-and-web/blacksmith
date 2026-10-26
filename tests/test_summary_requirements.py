"""PR narration is a paid role and consumes the frozen full requirements too."""

import pytest
from test_integration_integrity import configure
from test_publication_approval import run
from test_publication_integrity import setup
from test_run_context import create, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.nodes.pr import _summary_prompt
from blacksmith.run_context import context_from_state
from blacksmith.state import Status


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("qa", [False, True])
def test_actual_paid_summary_receives_full_frozen_prd_and_contract_in_both_publications(
    project, monkeypatch, parallel, qa,
):
    configure(project, sequential=not parallel, qa=qa)
    calls, remote, _ = setup(project, monkeypatch)
    original = cli._resolve_approver

    def approver(args, renderer):
        decide = original(args, renderer)

        def approve(payload, values):
            if payload["gate"] == "plan":
                project[2].write_text("MUTATED_SOURCE_PRD_MUST_NOT_REACH_SUMMARY")
            return decide(payload, values)

        return approve

    monkeypatch.setattr(cli, "_resolve_approver", approver)
    assert run(project, "publication") == (2 if qa else 0)
    state = saved_values(project[1], "publication")
    context = context_from_state(state)
    summaries = [call for call in calls if call[0] == "summary"]
    assert len(summaries) == 1
    _, _, prompt, options = summaries[0]
    assert context.prd().body in prompt
    assert context.prd().contract.model_dump_json(indent=2) in prompt
    assert "MUTATED_SOURCE_PRD_MUST_NOT_REACH_SUMMARY" not in prompt
    assert options.tools == [] and options.allowed_tools == [] and not options.mcp_servers
    assert len(calls) == len(state["cost_events"]) == 7
    assert len(remote.effects()) == 2  # Local bare Git + fake GitHub only.
    assert state["status"] == (Status.AWAITING_QA if qa else Status.DONE)


def test_summary_uses_snapshot_instead_of_mutated_in_memory_prd(project):
    context = create(project)
    state = context.initial_state()
    frozen = context.prd()
    original_prompt = _summary_prompt(state, frozen.contract.work_units)
    state["prd"] = type(frozen)(frozen.path, frozen.contract, "MUTATED_STATE_PRD")
    prompt = _summary_prompt(state, frozen.contract.work_units)
    assert prompt == original_prompt  # Cached call identity stays bound to the snapshot.
    assert frozen.body in prompt
    assert frozen.contract.model_dump_json(indent=2) in prompt
    assert "MUTATED_STATE_PRD" not in prompt
