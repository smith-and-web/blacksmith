"""Explicit rollback recovery and staged joins retain exact destination commits."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_integration_integrity import configure, stop_at_pr, transport
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli, joins
from blacksmith.executor import Executor
from blacksmith.gate import GateError
from blacksmith.integration import join_key
from blacksmith.run_context import context_from_state, unit_hash


@pytest.mark.parametrize("after_commit", [False, True])
def test_join_rollback_retry_reuses_exact_prior_effects_and_immutable_failed_receipt(
    project,
    monkeypatch,
    after_commit,
):
    configure(project)
    calls = transport(monkeypatch)
    original = joins.git
    created = {}

    def fail(path, *args, check=True):
        if not after_commit and args[0] == "fetch" and unit_hash("B") in args[2]:
            raise GateError("injected second fetch failure")
        result = original(path, *args, check=check)
        if args[0] == "commit" and "Blacksmith-Source-Commit:" in args[-1]:
            name = "A" if not created else "B"
            created[name] = git(path, "rev-parse", "HEAD")
            if after_commit and name == "B":
                raise GateError("injected failure after owned commit")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(joins, "git", fail)
        assert (
            cli.main(
                [
                    str(project[2]),
                    "--config",
                    str(project[1]),
                    "--thread-id",
                    "join-retry",
                    "--approve",
                    "plan",
                ]
            )
            == 1
        )
    before = saved_values(project[1], "join-retry")
    context = context_from_state(before)
    failed = context.journal().get(join_key(context, "B", 0))
    assert (
        failed.status == "failed"
        and git(context.workspace_path, "rev-parse", "HEAD") == before["level_bases"]["0"]
    )
    first = before["join_destinations"]["A"]
    original_calls = list(calls)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(
            ["resume", "--config", str(project[1]), "--thread-id", "join-retry", "--retry-stage"]
        )
    after = saved_values(project[1], "join-retry")
    assert calls == original_calls and len(calls) == 6
    assert after["unit_outcomes"] == before["unit_outcomes"]
    assert after["join_destinations"]["A"] == first
    assert context.journal().get(failed.key) == failed
    picks = [o.intent["argv"] for o in context.journal().operations()
             if o.key.kind.startswith("command:") and isinstance(o.intent.get("argv"), list)
             and "cherry-pick" in o.intent["argv"] and "--no-commit" in o.intent["argv"]]
    assert len(picks) == 2
    for unit in ("A", "B"):
        source = before["unit_outcomes"][unit]["source_commit"]
        assert sum(source in argv for argv in picks) == 1
    assert after["final_gate_receipt"].outcome == "passed"
    assert git(context.workspace_path, "rev-list", "--count", context.base_commit + "..HEAD") == "2"
    assert {e["event_id"] for e in after["cost_events"]} == {
        e["event_id"] for e in before["cost_events"]
    }
    if after_commit:
        assert after["join_destinations"]["B"]["destination_commit"] == created["B"]
        assert after["join_generations"].get("0:B", 0) == 0
        proof = [
            o for o in context.journal().operations() if o.key.kind == "join.pick.reconciliation"
        ]
        assert len(proof) == 1 and proof[0].status == "completed"
    else:
        assert after["join_generations"]["0:B"] == 1
    assert not after.get("pr_url")


CRASH_STAGED = """
import os, sys
sys.path.insert(0, sys.argv[1])
from pytest import MonkeyPatch
from test_integration_integrity import transport
from blacksmith import cli, joins
import blacksmith.nodes.pr as pr
mp=MonkeyPatch()
transport(mp)
def forbidden(*args, **kwargs):
    raise AssertionError('publication forbidden in join drill')
pr.open_pull_request=forbidden
original=joins.git
commits=0
def crash(path, *args, check=True):
    global commits
    if args[0]=='commit' and 'Blacksmith-Source-Commit:' in args[-1]:
        commits += 1
        if commits==int(sys.argv[4]):
            os._exit(103)
    return original(path, *args, check=check)
joins.git=crash
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'join-stage', '--approve', 'plan'])
"""


@pytest.mark.parametrize("pick,explicit", [(1, True), (2, False)])
def test_process_exit_with_owned_staged_pick_resumes_without_repick_or_sdk(
    project,
    monkeypatch,
    pick,
    explicit,
):
    configure(project)
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH_STAGED,
            str(Path(__file__).parent),
            str(project[2]),
            str(project[1]),
            str(pick),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 103, child.stderr
    before = saved_values(project[1], "join-stage")
    context = context_from_state(before)
    assert before["join_cursor"] == pick - 1
    calls = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]
    assert len(calls) == 6

    async def forbidden(*args, **kwargs):
        pytest.fail("staged join restart must not call model transport")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", forbidden)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(
            [
                "resume",
                "--config",
                str(project[1]),
                "--thread-id",
                "join-stage",
                *(["--retry-stage"] if explicit else []),
            ]
        )
    after = saved_values(project[1], "join-stage")
    assert after["final_gate_receipt"].outcome == "passed"
    assert len(after["join_destinations"]) == 2
    if pick == 2:
        assert after["join_destinations"]["A"] == before["join_destinations"]["A"]
    assert git(context.workspace_path, "rev-list", "--count", context.base_commit + "..HEAD") == "2"
    assert [o for o in context.journal().operations() if o.key.kind.startswith("model.")] == calls
    assert len(after["cost_events"]) == 6


def test_integration_failure_retries_combined_candidate_without_attributing_last_unit(
    project,
    monkeypatch,
    tmp_path,
):
    marker = tmp_path / "combined-fixed"
    configure(project, f"[ ! -f a.txt ] || [ ! -f b.txt ] || test -f '{marker}'")
    calls = transport(monkeypatch)
    assert (
        cli.main(
            [
                str(project[2]),
                "--config",
                str(project[1]),
                "--thread-id",
                "combined",
                "--approve",
                "plan",
            ]
        )
        == 1
    )
    before = saved_values(project[1], "combined")
    assert before["integration_gate_receipts"]["0"].outcome == "failed"
    assert all(row["ok"] for row in before["unit_outcomes"].values())
    original = list(calls)
    head = git(before["worktree_path"], "rev-parse", "HEAD")
    marker.touch()
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(
            ["resume", "--config", str(project[1]), "--thread-id", "combined", "--retry-stage"]
        )
    after = saved_values(project[1], "combined")
    assert calls == original and after["unit_outcomes"] == before["unit_outcomes"]
    assert after["join_destinations"] == before["join_destinations"]
    assert git(before["worktree_path"], "rev-parse", "HEAD") == head
    assert after["integration_gate_receipts"]["0"].outcome == "passed"
    assert after["final_gate_receipt"].outcome == "passed"
    assert after["cost_events"] == before["cost_events"]


CRASH_RESTORE = """
import os, sys
from blacksmith import cli, joins
from blacksmith.executor import Executor
import blacksmith.nodes.pr as pr
os.environ['BLACKSMITH_ANTHROPIC_API_KEY']='unused-offline'
async def forbidden_model(*args, **kwargs):
    raise AssertionError('model transport forbidden during join restoration')
Executor._collect=forbidden_model
def forbidden_publication(*args, **kwargs):
    raise AssertionError('publication forbidden during join restoration')
pr.open_pull_request=forbidden_publication
original=joins.git
def crash(path, *args, check=True):
    result=original(path,*args,check=check)
    if args[:2]==('reset','--hard') and args[2]==sys.argv[3]:
        os._exit(104)
    return result
joins.git=crash
cli.main(['resume','--config',sys.argv[1],'--thread-id',sys.argv[2],
          '--retry-stage','--approve','plan'])
"""


def test_process_exit_after_restoring_prior_destination_finishes_without_reset_or_repick(
    project,
    monkeypatch,
):
    configure(project)
    transport(monkeypatch)
    original = joins.git

    def fail(path, *args, check=True):
        if args[0] == "fetch" and unit_hash("B") in args[2]:
            raise GateError("injected rollback")
        return original(path, *args, check=check)

    with monkeypatch.context() as local:
        local.setattr(joins, "git", fail)
        assert (
            cli.main(
                [
                    str(project[2]),
                    "--config",
                    str(project[1]),
                    "--thread-id",
                    "restore",
                    "--approve",
                    "plan",
                ]
            )
            == 1
        )
    before = saved_values(project[1], "restore")
    context = context_from_state(before)
    first = before["join_destinations"]["A"]
    models = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH_RESTORE,
            str(project[1]),
            "restore",
            first["destination_commit"],
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 104, child.stderr
    assert git(context.workspace_path, "rev-parse", "HEAD") == first["destination_commit"]
    restore = next(o for o in context.journal().operations() if o.key.kind == "join.restore")
    assert restore.status == "intent"
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "restore"])
    after = saved_values(project[1], "restore")
    assert after["join_destinations"]["A"] == first
    assert context.journal().get(restore.key).status == "completed"
    assert after["final_gate_receipt"].outcome == "passed"
    assert git(context.workspace_path, "rev-list", "--count", context.base_commit + "..HEAD") == "2"
    assert [o for o in context.journal().operations() if o.key.kind.startswith("model.")] == models


def test_final_gate_failure_resumes_final_scope_without_repeating_unit_or_paid_calls(
    project,
    monkeypatch,
    tmp_path,
):
    from test_run_context import install_transport

    marker = tmp_path / "final-fixed"
    command = (
        "mkdir -p .checks; printf x >> .checks/count; "
        f"[ $(wc -c < .checks/count) -lt 2 ] || test -f '{marker}'"
    )
    root = project[0]
    (root / "blacksmith.toml").write_text("test_cmd=" + json.dumps(command) + "\n")
    (root / ".gitignore").write_text(".checks/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "final stage recovery gate")
    project[1].write_text(project[1].read_text() + "[review]\nenabled=false\n")
    calls = []
    install_transport(monkeypatch, calls)
    assert (
        cli.main(
            [
                str(project[2]),
                "--config",
                str(project[1]),
                "--thread-id",
                "final",
                "--approve",
                "plan",
            ]
        )
        == 1
    )
    before = saved_values(project[1], "final")
    assert before["final_gate_receipt"].outcome == "failed"
    assert before["unit_outcomes"]["U1"]["ok"] and len(calls) == 2
    marker.touch()
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(project[1]), "--thread-id", "final", "--retry-stage"])
    after = saved_values(project[1], "final")
    assert after["final_gate_receipt"].outcome == "passed" and len(calls) == 2
    assert after["unit_outcomes"] == before["unit_outcomes"]
    assert after["cost_events"] == before["cost_events"]
    assert (Path(before["worktree_path"]) / ".checks/count").read_text() == "xxx"
