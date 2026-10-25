"""Exact-SHA approval through actual CLI, graphs, Git, gates and checkpoints."""

from copy import deepcopy
from pathlib import Path

import pytest
from test_integration_integrity import configure
from test_review_integrity import install as install_models
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.nodes import pr as pr_module
from blacksmith.nodes.pr import CommandResult
from blacksmith.run_context import context_from_state
from blacksmith.state import Status

OPEN_HELPER = pr_module.open_pull_request
COMPILE = cli.compile_graph


def install(monkeypatch, *, on_summary=None, on_push=None):
    calls = install_models(monkeypatch)
    collect = Executor._collect
    monkeypatch.setattr(pr_module, "open_pull_request", OPEN_HELPER)
    remote, payloads, workspaces = [], [], []
    original_approver = cli._resolve_approver

    async def paid_transport(self, prompt, options, *, emit_findings=False):
        if prompt.startswith("Write a concise pull request"):
            assert options.tools == [] and options.allowed_tools == [] and not options.mcp_servers
            calls.append(("summary", "__run__", prompt, options))
            if on_summary:
                on_summary(workspaces[-1])
            return ExecutorResult('{"title":"Offline summary","summary":"Recorded changes."}',
                                  options.model, False, 1, .01, {}, "offline")
        return await collect(self, prompt, options, emit_findings=emit_findings)

    def github(argv, cwd=None):
        remote.append(list(argv))
        if argv[:2] == ["git", "push"]:
            if on_push:
                on_push(Path(cwd))
            return CommandResult(0, "", "")
        assert argv[:3] == ["gh", "pr", "create"], argv
        return CommandResult(0, "https://github.com/example/context-demo/pull/1\n", "")

    def compile_graph(checkpointer, **kwargs):
        return COMPILE(checkpointer, **{**kwargs, "pr_runner": github})

    def approver(args, renderer):
        original = original_approver(args, renderer)

        def decide(payload, values):
            payloads.append(payload)
            if payload["gate"] == "pr":
                workspaces.append(Path(values["worktree_path"]))
            return original(payload, values)
        return decide

    monkeypatch.setattr(Executor, "_collect", paid_transport)
    monkeypatch.setattr(cli, "compile_graph", compile_graph)
    monkeypatch.setattr(cli, "_resolve_approver", approver)
    return calls, remote, payloads


def run(project, thread, approve="plan,pr"):
    return cli.main([str(project[2]), "--config", str(project[1]), "--thread-id", thread,
                     "--approve", approve])


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("qa", [False, True])
def test_plan_only_never_publishes_normal_or_draft_and_retains_exact_proposal(
    project, monkeypatch, parallel, qa,
):
    configure(project, sequential=not parallel, qa=qa)
    calls, remote, payloads = install(monkeypatch)
    assert run(project, "plan-only", "plan") == 1
    state = saved_values(project[1], "plan-only")
    assert remote == [] and not any(c[0] == "summary" for c in calls)
    assert state["status"] == Status.HALTED and state["approvals"]["pr"] is False
    assert not state.get("pr_url") and state["pr_approval"]["approved"] is False
    payload = payloads[-1]
    assert [p["gate"] for p in payloads] == ["plan", "pr"]
    assert payload["run_id"] == state["run_id"]
    assert payload["candidate_commit"] == state["final_gate_receipt"].candidate_commit
    assert payload["candidate_commit"] == git(Path(state["worktree_path"]), "rev-parse", "HEAD")
    assert payload["draft"] is qa and bool(payload["pending_qa_unit"]) is qa
    assert payload["review_status"] == "clean"
    assert {r["unit_id"] for r in payload["auto_unit_receipts"]} == {"A", "B"}
    assert all(r["gate_receipt"]["layers"] == ["python"]
               and r["review_receipt"]["status"] == "clean" for r in payload["auto_unit_receipts"])
    assert all(Path(row["clone_path"]).is_dir() for row in state["unit_outcomes"].values())


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("qa", [False, True])
def test_both_approved_publications_push_only_approved_sha_and_report_qa_exit(
    project, monkeypatch, parallel, qa, capsys,
):
    configure(project, sequential=not parallel, qa=qa)
    calls, remote, payloads = install(monkeypatch)
    assert run(project, "approved") == (2 if qa else 0)
    state = saved_values(project[1], "approved")
    approved = state["pr_approval"]["proposal"]
    assert state["status"] == (Status.AWAITING_QA if qa else Status.DONE)
    assert state["pr_url"].endswith("/pull/1") and state["pr_approval"]["approved"] is True
    assert len(remote) == 2 and remote[0][:2] == ["git", "push"]
    assert remote[0][-1] == approved["candidate_commit"] + ":refs/heads/" + state["branch"]
    assert remote[1][:3] == ["gh", "pr", "create"] and ("--draft" in remote[1]) is qa
    assert approved["candidate_commit"] == state["final_gate_receipt"].candidate_commit
    assert [p["gate"] for p in payloads] == ["plan", "pr"]
    assert len([c for c in calls if c[0] == "summary"]) == 1
    assert not any(c[1] == "QA" for c in calls)
    output = capsys.readouterr().out
    if qa:
        assert "QA pending: QA: inspect" in output and "status: awaiting_qa" in output
        assert state["pending_qa_unit"]["id"] == "QA"
        original_calls, original_remote = list(calls), list(remote)
        assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "approved"]) == 2
        assert calls == original_calls and remote == original_remote
    else:
        assert "QA pending:" not in output and state["pending_qa_unit"] is None


@pytest.mark.parametrize("qa", [False, True])
@pytest.mark.parametrize("change", ["dirty", "commit", "branch", "origin"])
def test_changed_candidate_or_identity_after_prompt_rejects_before_summary_or_push(
    project, monkeypatch, qa, change,
):
    configure(project, qa=qa)
    calls, remote, _ = install(monkeypatch)
    seen = []

    def approve(payload, values):
        seen.append(payload)
        if payload["gate"] == "pr":
            path = Path(values["worktree_path"])
            if change in {"dirty", "commit"}:
                (path / "unapproved.txt").write_text("external edit after prompt")
                if change == "commit":
                    git(path, "add", "unapproved.txt")
                    git(path, "commit", "-m", "unapproved external commit")
            elif change == "branch":
                git(path, "checkout", "-b", "unapproved-other-branch")
            else:
                git(path, "remote", "set-url", "origin", "https://github.com/example/foreign.git")
        return True

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve)
    assert run(project, "changed") == 1
    state = saved_values(project[1], "changed")
    assert [p["gate"] for p in seen] == ["plan", "pr"]
    assert state["status"] == Status.HALTED and state["approvals"]["pr"] is False
    assert remote == [] and not any(c[0] == "summary" for c in calls)
    assert Path(state["worktree_path"]).is_dir() and not state.get("pr_url")
    context = context_from_state(state)
    assert (context.directory / "publication/approval-0-proposal.json").is_file()
    assert not (context.directory / "publication/approval-0-decision.json").exists()


@pytest.mark.parametrize("stage", ["summary", "push"])
def test_guard_rechecks_after_summary_and_between_push_and_create(project, monkeypatch, stage):
    def mutate(workspace):
        (workspace / "unapproved.txt").write_text("external update while effect was running")
        git(workspace, "add", "unapproved.txt")
        git(workspace, "commit", "-m", "external update")

    calls, remote, _ = install(monkeypatch,
        on_summary=mutate if stage == "summary" else None,
        on_push=mutate if stage == "push" else None)
    assert run(project, "recheck") == 1
    state = saved_values(project[1], "recheck")
    assert state["status"] == Status.HALTED and not state.get("pr_url")
    assert not any(argv[:3] == ["gh", "pr", "create"] for argv in remote)
    assert len(remote) == (1 if stage == "push" else 0)
    assert len([c for c in calls if c[0] == "summary"]) == 1
    assert len([e for e in state["cost_events"] if e["node"] == "summary"]) == 1
    assert (Path(state["worktree_path"]) / "unapproved.txt").is_file()


@pytest.mark.parametrize("case", ["missing", "foreign_run", "sha", "round", "wrong_route"])
def test_publication_node_cannot_use_bool_or_foreign_approval_in_place_of_exact_evidence(
    project, monkeypatch, case,
):
    calls, remote, _ = install(monkeypatch)
    assert run(project, "guard") == 0
    state = deepcopy(saved_values(project[1], "guard"))
    original_calls, original_remote = list(calls), list(remote)
    if case == "missing":
        state["pr_approval"] = None
    elif case in {"foreign_run", "sha"}:
        field = "run_id" if case == "foreign_run" else "candidate_commit"
        state["pr_approval"]["proposal"][field] = "foreign"
    elif case == "round":
        state["pr_approval_round"] += 1
    callback = pr_module.open_draft_pr if case == "wrong_route" else pr_module.open_pr
    result = callback(state, runner=lambda *a, **k: pytest.fail("invalid approval runner"),
                      executor=type("Forbidden", (), {"run_summary": lambda *a, **k:
                              pytest.fail("invalid approval summary")})())
    assert result["status"] == Status.HALTED
    assert calls == original_calls and remote == original_remote


def test_large_pr_preview_has_explicit_cap_independent_of_native_log_cap(project, monkeypatch):
    project[1].write_text(project[1].read_text() + "[limits]\nmax_command_output_bytes=4096\n"
                          "[hitl]\npr_diff_max_bytes=256\n")
    calls, remote, payloads = install(monkeypatch)
    original = Executor._collect

    async def large(self, prompt, options, *, emit_findings=False):
        if prompt.startswith("Implement this work unit"):
            # Let the real options, commit, gate and review run over a large change.
            (Path(options.cwd) / "result.txt").write_text("line\n" * 220000)
            calls.append(("implement", "U1", prompt, options))
            return ExecutorResult("done", options.model, False, 1, .01, {}, "offline")
        if emit_findings:
            # S12 already proves full bounded reconstruction; this test targets PR display.
            assert "CUMULATIVE PATCH MANIFEST" in prompt
        return await original(self, prompt, options, emit_findings=emit_findings)
    monkeypatch.setattr(Executor, "_collect", large)
    assert run(project, "large-preview", "plan") == 1
    payload = payloads[-1]
    assert payload["gate"] == "pr" and "…(diff truncated at 256 bytes)…" in payload["diff_text"]
    marker = "\n…(diff truncated at 256 bytes)…\n"
    assert len(payload["diff_text"].encode()) <= 256 + len(marker.encode())
    assert payload["diff_manifest"]["bytes"] > 1048576 and remote == []
    assert not any(c[0] == "summary" for c in calls)
