"""One unit factory, attempt-local recovery and mixed-DAG CLI/checkpointer proof."""

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import get_type_hints

import pytest
from claude_agent_sdk import PermissionResultAllow
from test_run_context import git, saved_values, write_prd
from test_run_context import project as project

from blacksmith import cli
from blacksmith import graph as graph_module
from blacksmith.config import DEFAULT_IMPLEMENT_ESCALATE_MODEL, DEFAULT_IMPLEMENT_MODEL
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.graph import prepare_escalation, prepare_fix_retry, prepare_review_revision
from blacksmith.receipts import attempt_identity
from blacksmith.state import BlacksmithState, UnitAttemptState
from blacksmith.unit_state import new_unit_attempt, next_attempt


def test_factory_covers_every_unit_channel_and_never_copies_run_ledgers():
    first = new_unit_attempt("first", "BASE")
    second = new_unit_attempt("second", "COMBINED")
    assert set(first) == set(UnitAttemptState.__annotations__)
    assert set(UnitAttemptState.__annotations__) <= set(get_type_hints(BlacksmithState))
    assert not {
        "cost_events",
        "errors",
        "unit_results",
        "review_findings",
        "unresolved_review_findings",
        "review_revisions_total",
    } & set(first)
    first["review_current_findings"].append({"severity": "blocking", "detail": "old"})
    first["implementation"]["old"] = True
    assert second["review_current_findings"] == []
    assert second["implementation"] == {}
    assert second["unit_base_commit"] == second["attempt_base_commit"] == "COMBINED"
    assert second["last_passing_commit"] is None
    assert second["attempt_number"] == 1
    assert second["attempt_id"] == "[0,false,0,0]"
    assert second["model_tier"] == "initial" and not second["escalated"]
    assert second["test_results"] is None
    assert second["gate_receipt"] is second["fix_receipt"] is second["review_receipt"] is None
    assert second["pre_implement_ref"] is None
    assert not second["review_clean"] and not second["resume_partial_implement"]
    assert (
        second["last_gate_output"]
        == second["review_feedback"]
        == second["continuation_feedback"]
        == ""
    )


def test_next_attempt_retains_own_bases_and_feedback_but_invalidates_candidate_evidence():
    state = new_unit_attempt("U", "UNIT_BASE")
    state.update(
        last_passing_commit="PASSING",
        pre_implement_ref="UNIT_BASE",
        test_results={"passed": False, "output": "LATEST_GATE", "command": "check"},
        review_feedback="KEEP_REVIEW_REQUEST",
        review_clean=True,
        review_current_findings=[{"severity": "blocking"}],
        gate_receipt="old",
        fix_receipt="old",
        review_receipt={"old": True},
        implementation={"old": True},
    )
    initial = deepcopy(state)
    escalated = next_attempt(state, "escalate", base_commit="PASSING")
    assert state == initial
    assert escalated["unit_base_commit"] == "UNIT_BASE"
    assert escalated["attempt_base_commit"] == escalated["last_passing_commit"] == "PASSING"
    assert escalated["attempt_number"] == 2
    assert escalated["attempt_id"] == "[0,true,0,0]"
    assert escalated["last_gate_output"] == "LATEST_GATE"
    assert escalated["review_feedback"] == "KEEP_REVIEW_REQUEST"
    assert escalated["model_tier"] == "escalated"
    assert not escalated["review_clean"]
    assert (
        escalated["test_results"]
        is escalated["gate_receipt"]
        is escalated["review_receipt"]
        is None
    )
    continuation = next_attempt(escalated, "continuation")
    assert continuation["resume_partial_implement"]
    assert continuation["continuation_feedback"]
    assert continuation["attempt_id"] == "[0,true,0,1]"
    assert continuation["attempt_base_commit"] == "PASSING"
    revision = next_attempt(continuation, "review", base_commit="NEW_PASS", feedback="NEW_REVIEW")
    assert revision["attempt_id"] == "[0,true,1,1]"
    assert revision["attempt_number"] == 4
    assert revision["review_feedback"] == "NEW_REVIEW"
    assert revision["continuation_feedback"] == revision["last_gate_output"] == ""
    assert not revision["resume_partial_implement"]
    assert attempt_identity(revision) == revision["attempt_id"]


@pytest.mark.parametrize("recovery", ["fix", "escalate"])
def test_failed_review_revision_resets_to_last_passing_candidate(project, recovery):
    root, *_ = project
    unit_base = git(root, "rev-parse", "HEAD")
    (root / "passing.txt").write_text("keep passing implementation")
    git(root, "add", "passing.txt")
    git(root, "commit", "-m", "passing unit")
    passing = git(root, "rev-parse", "HEAD")
    state = {
        **new_unit_attempt("U1", unit_base),
        "worktree_path": str(root),
        "pre_implement_ref": unit_base,
        "last_passing_commit": passing,
        "review_current_findings": [
            {"severity": "blocking", "file": "passing.txt", "detail": "REQUEST"}
        ],
    }
    state.update(prepare_review_revision(state))
    assert state["attempt_base_commit"] == passing
    (root / "bad.txt").write_text("bad revision")
    git(root, "add", "bad.txt")
    git(root, "commit", "-m", "bad revision")
    state["test_results"] = {"passed": False, "output": "FRESH_FAILURE", "command": "false"}
    prepared = prepare_fix_retry(state) if recovery == "fix" else prepare_escalation(state)
    assert git(root, "rev-parse", "HEAD") == passing
    assert (root / "passing.txt").read_text() == "keep passing implementation"
    assert not (root / "bad.txt").exists()
    assert prepared["last_gate_output"] == "FRESH_FAILURE"
    assert "REQUEST" in prepared["review_feedback"]
    assert prepared["unit_base_commit"] == unit_base
    assert prepared["attempt_base_commit"] == prepared["last_passing_commit"] == passing
    assert prepared["test_results"] is None


@pytest.mark.parametrize("resume", [False, True])
def test_mixed_sequential_escalated_reviewed_parallel_then_sequential_is_fresh(
    project, monkeypatch, resume
):
    root, config, prd, contract = project
    contract["work_units"] = [
        {
            "id": name,
            "title": name,
            "layers": ["python"],
            "target_modules": [name.lower() + ".txt"],
            "test_contract": "all implemented files start with good",
            "depends_on": deps,
        }
        for name, deps in (("A", []), ("B", ["A"]), ("C", ["A"]), ("D", ["B", "C"]))
    ]
    write_prd(prd, contract)
    command = (
        'for f in a.txt b.txt c.txt d.txt; do if [ -f "$f" ]; then '
        'grep -q "^good" "$f" || exit 1; fi; done'
    )
    (root / "blacksmith.toml").write_text("test_cmd=" + json.dumps(command) + "\n")
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "mixed gate")
    config.write_text(config.read_text() + "[limits]\nmax_fix_attempts=0\nmax_review_revisions=1\n")
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")
    calls, entries = [], []
    implement_counts, review_counts = {}, {}

    async def collect(self, prompt, options, *, emit_findings=False):
        assert options.setting_sources == [] and options.strict_mcp_config
        if prompt.startswith("Produce a concise"):
            return ExecutorResult("PLAN_SENTINEL", options.model, False, 1, 0.01, {}, "offline")
        unit = re.search(r"^Unit ([^:]+):", prompt, re.M).group(1)
        calls.append((unit, "review" if emit_findings else "implement", options.model, prompt))
        if emit_findings:
            review_counts[unit] = review_counts.get(unit, 0) + 1
            findings = (
                [{"severity": "blocking", "file": "a.txt", "detail": "FIX_A_REQUEST"}]
                if unit == "A" and review_counts[unit] == 1
                else []
            )
            text = json.dumps(
                {"verdict": "clean" if not findings else "blocked", "findings": findings}
            )
        else:
            implement_counts[unit] = implement_counts.get(unit, 0) + 1
            target = unit.lower() + ".txt"
            allowed = await options.can_use_tool("Write", {"file_path": target}, None)
            assert isinstance(allowed, PermissionResultAllow)
            prefix = "bad" if unit == "A" and implement_counts[unit] == 1 else "good"
            (Path(options.cwd) / target).write_text(f"{prefix} attempt {implement_counts[unit]}\n")
            if unit == "A" and implement_counts[unit] == 2:
                assert "FAILED THE TEST GATE" in prompt
            if unit == "A" and implement_counts[unit] == 3:
                assert "FIX_A_REQUEST" in prompt
            text = "implemented"
        return ExecutorResult(text, options.model, False, 1, 0.01, {}, "offline")

    monkeypatch.setattr(Executor, "_collect", collect)
    actual_implement = graph_module.implement
    stopped = False

    def capture(state, **kwargs):
        nonlocal stopped
        unit = state["selected_unit"].id
        entries.append(
            (
                unit,
                deepcopy({k: state.get(k) for k in UnitAttemptState.__annotations__}),
                git(state["worktree_path"], "rev-parse", "HEAD"),
            )
        )
        if resume and unit == "D" and not stopped:
            stopped = True
            raise KeyboardInterrupt("checkpoint after join, before D transport")
        return actual_implement(state, **kwargs)

    monkeypatch.setattr(graph_module, "implement", capture)

    def approve(payload, values):
        if payload["gate"] == "plan":
            return True
        raise KeyboardInterrupt("retain before PR approval; no publication")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "mixed"])
    if resume:
        saved = saved_values(config, "mixed")
        assert saved["current_unit_id"] == "D" and saved["attempt_number"] == 1
        assert saved["test_results"] is None and saved["gate_receipt"] is None
        assert saved["pre_implement_ref"] is None and not saved["escalated"]
        prd.unlink()
        config.write_text(config.read_text() + '[models]\nimplement="LOCAL_CHANGED_MODEL"\n')
        with pytest.raises(KeyboardInterrupt):
            cli.main(["resume", "--config", str(config), "--thread-id", "mixed"])
    assert implement_counts == {"A": 3, "B": 1, "C": 1, "D": 1}
    assert review_counts == {"A": 2, "B": 1, "C": 1, "D": 1}
    assert [model for unit, role, model, _ in calls if unit == "A" and role == "implement"] == [
        DEFAULT_IMPLEMENT_MODEL,
        DEFAULT_IMPLEMENT_ESCALATE_MODEL,
        DEFAULT_IMPLEMENT_ESCALATE_MODEL,
    ]
    for unit, snapshot, head in entries:
        if unit in {"B", "C", "D"}:
            assert snapshot == new_unit_attempt(unit, head)
    d_entries = [snap for unit, snap, _ in entries if unit == "D"]
    if resume:
        assert d_entries[0] == d_entries[1]
    values = saved_values(config, "mixed")
    assert values["current_unit_id"] == "D" and values["attempt_number"] == 1
    assert values["review_clean"] and values["gate_receipt"].outcome == "passed"
    assert values["unit_base_commit"] == d_entries[-1]["unit_base_commit"]
    assert values["last_passing_commit"] == git(values["worktree_path"], "rev-parse", "HEAD")
    assert not values.get("pr_url")


def test_real_cli_continuation_keeps_partial_work_and_uses_separate_feedback(project, monkeypatch):
    root, config, prd, _ = project
    (root / "blacksmith.toml").write_text('test_cmd="grep -qx final result.txt"\n')
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "continuation verifier")
    original_head = git(root, "rev-parse", "HEAD")
    config.write_text(config.read_text() + "[review]\nenabled=false\n")
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")
    calls = []

    async def collect(self, prompt, options, *, emit_findings=False):
        if prompt.startswith("Produce a concise"):
            return ExecutorResult("PLAN", options.model, False, 1, 0.01, {}, "offline")
        assert prompt.startswith("Implement this work unit")
        assert not emit_findings
        calls.append(prompt)
        target = Path(options.cwd) / "result.txt"
        allowed = await options.can_use_tool("Write", {"file_path": str(target)}, None)
        assert isinstance(allowed, PermissionResultAllow)
        if len(calls) == 1:
            target.write_text("partial work")
            return ExecutorResult(
                "turn cap", options.model, True, 40, 0.01, {}, "offline", error_kind="max_turns"
            )
        assert target.read_text() == "partial work"
        assert "CONTINUATION FEEDBACK:" in prompt
        assert "Retain its saved partial changes" in prompt
        assert "PLAN" in prompt
        target.write_text("final\n")
        return ExecutorResult("done", options.model, False, 1, 0.01, {}, "offline")

    monkeypatch.setattr(Executor, "_collect", collect)

    def approve(payload, values):
        if payload["gate"] == "plan":
            return True
        raise KeyboardInterrupt("no publication")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "continue"])
    values = saved_values(config, "continue")
    assert len(calls) == 2
    assert values["attempt_number"] == 2 and values["implement_continuations"] == 1
    assert values["attempt_id"] == "[0,false,0,1]"
    assert values["unit_base_commit"] == values["attempt_base_commit"] == original_head
    assert values["last_passing_commit"] == values["gate_receipt"].candidate_commit
    assert (Path(values["worktree_path"]) / "result.txt").read_text() == "final\n"
    assert git(root, "rev-parse", "HEAD") == original_head
    assert not values.get("pr_url")


def test_legacy_attempt_transition_keeps_consumed_counters_and_call_identity():
    legacy = {
        "fix_attempts": 1,
        "escalated": True,
        "review_revisions": 1,
        "implement_continuations": 2,
        "last_gate_output": "OWN_GATE",
        "review_feedback": "OWN_REQUEST",
        "pre_implement_ref": "RECORDED_BASE",
    }
    old_identity = attempt_identity(legacy)
    advanced = next_attempt(legacy, "continuation")
    assert old_identity == "[1,true,1,2]"
    assert advanced["attempt_id"] == "[1,true,1,3]"
    assert advanced["attempt_number"] == 7
    assert advanced["fix_attempts"] == legacy["fix_attempts"]
    assert advanced["review_revisions"] == legacy["review_revisions"]
    assert advanced["escalated"] and advanced["model_tier"] == "escalated"
    assert (
        advanced["last_gate_output"] == "OWN_GATE" and advanced["review_feedback"] == "OWN_REQUEST"
    )
    assert advanced["pre_implement_ref"] == "RECORDED_BASE"
    assert advanced["unit_base_commit"] is None  # unknown historical base is never invented
