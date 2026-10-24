"""Trusted all-layer gates, clean exact candidates and formatter ownership on real Git."""

import json
import sqlite3
from pathlib import Path

import pytest
from test_run_context import git, install_transport, saved_values, write_prd
from test_run_context import project as project

from blacksmith import cli, verification
from blacksmith.contract import parse_prd
from blacksmith.gate import GateError, run_gate
from blacksmith.gate_receipts import GateReceipt
from blacksmith.graph import auto_fix, route_after_test_gate
from blacksmith.graph import test_gate as gate_node
from blacksmith.nodes.implement import _stage_and_commit
from blacksmith.receipts import ReceiptError, call_context_for
from blacksmith.run_context import create_run_context
from blacksmith.state import Status
from blacksmith.verification import run_candidate_gate, verify_gate_receipt
from blacksmith.worktree import RunCloneManager


def prepare(project, toolchain='test_cmd="true"\n', *, layers=None, units=None):
    root, config, prd, contract = project
    (root / "blacksmith.toml").write_text(toolchain)
    (root / ".gitignore").write_text(".checks/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "trusted verifier")
    if layers:
        contract["layers"] = {layer: "auto" for layer in layers}
        contract["work_units"][0]["layers"] = layers
    if units:
        contract["work_units"] = units
    write_prd(prd, contract)
    from blacksmith.config import BlacksmithConfig

    context = create_run_context(
        BlacksmithConfig.load(config), parse_prd(prd), "gate", required_tools=("git",)
    )
    clone = RunCloneManager(context).create("U1")
    state = {
        **context.initial_state(),
        "worktree_path": str(clone.path),
        "selected_unit": context.prd().contract.work_units[0],
    }
    owned_commit(state)
    return context, state


def owned_commit(state, name="result.txt", content="implemented\n"):
    path = Path(state["worktree_path"])
    base = git(path, "rev-parse", "HEAD")
    (path / name).write_text(content)
    files, summary = _stage_and_commit(
        str(path),
        "test owned unit",
        call_context=call_context_for(state, unit_id=state["selected_unit"].id),
    )
    state.update(
        pre_implement_ref=base,
        implementation={
            "files_touched": files,
            "diff_summary": summary,
            "candidate_commit": git(path, "rev-parse", "HEAD"),
        },
    )


def gate_rows(context):
    with sqlite3.connect(context.journal().path) as conn:
        return conn.execute(
            "SELECT status,outcome_json FROM operations WHERE kind LIKE 'gate.%' ORDER BY rowid"
        ).fetchall()


def assert_retained(context, receipt):
    assert Path(receipt.artifact_path).is_file()
    assert json.loads(Path(receipt.artifact_path).read_text()) == receipt.model_dump(mode="json")
    context.journal().verify()


def test_second_auto_layer_fails_and_remaining_layer_is_not_run(project):
    context, state = prepare(
        project,
        'test_cmd="true"\n[layers.second]\ntest_cmd="false"\n'
        '[layers.third]\ntest_cmd="touch .checks/must-not-run"\n',
        layers=["first", "second", "third"],
    )
    result = run_candidate_gate(state)
    receipt = result.receipt
    assert not result.passed
    assert receipt.layers == ("first", "second", "third")
    assert [r.outcome for r in receipt.layer_results] == ["passed", "failed", "not_run"]
    assert [r.exit_code for r in receipt.commands] == [0, 1, None]
    assert [r.outcome for r in receipt.commands] == ["passed", "failed", "not_run"]
    assert not (Path(state["worktree_path"]) / ".checks/must-not-run").exists()
    assert receipt.toolchain_hash == context.toolchain_sha256
    assert receipt.candidate_commit == git(state["worktree_path"], "rev-parse", "HEAD")
    assert receipt.candidate_tree == git(state["worktree_path"], "rev-parse", "HEAD^{tree}")
    assert all(Path(p).is_file() for p in receipt.output_artifact_paths)
    assert_retained(context, receipt)


def test_identical_resolved_tuples_execute_once_and_cover_all_layers(project):
    context, state = prepare(
        project, 'test_cmd="mkdir -p .checks; printf x >> .checks/count"\n', layers=["a", "b", "a"]
    )
    result = run_candidate_gate(state)
    assert result.passed
    assert (Path(state["worktree_path"]) / ".checks/count").read_text() == "x"
    assert result.receipt.layers == ("a", "b")
    assert len(result.receipt.commands) == 1
    assert result.receipt.commands[0].layers == ("a", "b")
    assert all(
        row.covered_by == "a" and row.outcome == "passed" for row in result.receipt.layer_results
    )
    # Exact receipt reuse must not execute the command twice.
    replay = run_candidate_gate(state)
    assert replay.receipt == result.receipt
    assert (Path(state["worktree_path"]) / ".checks/count").read_text() == "x"
    verify_gate_receipt(result.receipt, state)
    assert_retained(context, result.receipt)


def test_clone_toolchain_cannot_replace_frozen_verifier(project, monkeypatch):
    context, state = prepare(project, 'test_cmd="false"\n')
    state["fix_attempts"] = 1
    owned_commit(state, "blacksmith.toml", 'test_cmd="true"\nfix_cmd="touch exploited"\n')

    def forbidden(*args, **kwargs):
        pytest.fail("mutable clone verifier was reloaded")

    monkeypatch.setattr("blacksmith.gate.load_toolchain", forbidden)
    state.update(auto_fix(state))
    result = gate_node(state, gate=run_gate)
    assert result["test_results"]["passed"] is False
    assert result["gate_receipt"].toolchain_hash == context.toolchain_sha256
    assert result["gate_receipt"].commands[0].command == "false"
    assert not (Path(state["worktree_path"]) / "exploited").exists()


@pytest.mark.parametrize(
    "command",
    [
        "printf changed >> README.md",
        "touch unknown.txt",
        "rm README.md",
        "printf changed >> README.md; git add README.md",
        "git commit --allow-empty -m unexpected",
    ],
)
def test_gate_source_index_and_head_changes_are_dirty_and_halt(project, command):
    context, state = prepare(project, f"test_cmd={json.dumps(command)}\n")
    update = gate_node(state, gate=run_gate)
    assert not update["test_results"]["passed"]
    assert update["status"] == Status.HALTED
    assert update["gate_receipt"].outcome == "dirty"
    assert route_after_test_gate({**state, **update}) == "human_halt"
    assert_retained(context, update["gate_receipt"])


@pytest.mark.parametrize("stage", ["setup_cmd", "lint_cmd"])
def test_setup_or_lint_source_mutation_is_not_hidden_by_passing_test(project, stage):
    context, state = prepare(project, f'test_cmd="true"\n{stage}="touch unexpected"\n')
    update = gate_node(state, gate=run_gate)
    assert update["gate_receipt"].outcome == "dirty"
    assert update["status"] == Status.HALTED
    assert not update["test_results"]["passed"]
    assert (Path(state["worktree_path"]) / "unexpected").exists()
    assert_retained(context, update["gate_receipt"])


def test_old_green_receipt_never_passes_dirty_different_commit_or_layer(project):
    _, state = prepare(project, layers=["a", "b"])
    receipt = run_candidate_gate(state).receipt
    with pytest.raises(GateError, match="unit gate layers differ"):
        verify_gate_receipt(receipt, state, layers=["a"])
    path = Path(state["worktree_path"])
    (path / "README.md").write_text("changed")
    with pytest.raises(GateError, match="does not match"):
        verify_gate_receipt(receipt, state)
    update = gate_node(
        {**state, "gate_receipt": receipt, "test_results": {"passed": True}}, gate=run_gate
    )
    assert update["gate_receipt"].receipt_id != receipt.receipt_id
    assert update["gate_receipt"].outcome == "dirty"
    assert not update["test_results"]["passed"]
    git(path, "add", "README.md")
    git(path, "commit", "-m", "foreign candidate")
    with pytest.raises(GateError, match="does not match"):
        verify_gate_receipt(receipt, state)
    assert run_candidate_gate(state).receipt.outcome == "error"


def test_gate_exception_after_green_unit_records_fresh_nonpassing_evidence(project, monkeypatch):
    units = [
        {
            "id": name,
            "title": name,
            "layers": ["python"],
            "target_modules": [name + ".txt"],
            "test_contract": "checks",
            **({"depends_on": ["U1"]} if name == "U2" else {}),
        }
        for name in ("U1", "U2")
    ]
    context, state = prepare(project, units=units)
    green = gate_node(state, gate=run_gate)
    assert green["test_results"]["passed"]
    state.update(green, selected_unit=context.prd().contract.work_units[1])
    owned_commit(state, "U2.txt")

    def fail(path, planned):
        raise GateError("verifier unavailable")

    monkeypatch.setattr(verification, "_execute", fail)
    red = gate_node(state, gate=run_gate)
    assert red["status"] == Status.HALTED
    assert red["test_results"]["passed"] is False
    assert red["gate_receipt"].outcome == "error"
    assert red["gate_receipt"].scope_id == "U2"
    assert red["gate_receipt"].receipt_id != green["gate_receipt"].receipt_id
    assert route_after_test_gate({**state, **red}) == "human_halt"
    assert_retained(context, red["gate_receipt"])


def test_timeout_is_a_typed_nonpassing_gate(project):
    context, state = prepare(project, 'test_cmd="sleep 60"\n')
    # Service consumes immutable limits, not ambient mutable config.
    from blacksmith.config import BlacksmithConfig

    root, config, prd, _ = project
    config.write_text(config.read_text() + "[limits]\ncommand_timeout_s=0.1\n")
    context = create_run_context(
        BlacksmithConfig.load(config), parse_prd(prd), "short", required_tools=("git",)
    )
    clone = RunCloneManager(context).create("U1")
    state = {
        **context.initial_state(),
        "worktree_path": str(clone.path),
        "selected_unit": context.prd().contract.work_units[0],
    }
    owned_commit(state)
    result = gate_node(state, gate=run_gate)
    assert result["gate_receipt"].outcome == "timeout"
    assert result["gate_receipt"].commands[0].outcome == "timeout"
    assert result["status"] == Status.HALTED
    assert result["test_results"]["passed"] is False
    assert_retained(context, result["gate_receipt"])


def test_formatter_amend_is_owned_audited_gated_and_replayed(project):
    context, state = prepare(
        project,
        'test_cmd="grep -qx formatted result.txt"\n'
        "fix_cmd=\"printf 'formatted\\n' > result.txt\"\n",
    )
    before = state["implementation"]["candidate_commit"]
    update = auto_fix(state)
    assert update["fix_receipt"].eligible
    after = update["implementation"]["candidate_commit"]
    assert after != before
    assert git(state["worktree_path"], "rev-list", "--count", "HEAD") == "3"
    state.update(update)
    assert git(state["worktree_path"], "status", "--porcelain") == ""
    assert call_context_for(state, unit_id="U1").key("git.commit").trailer in git(
        state["worktree_path"], "log", "-1", "--format=%B"
    )
    # Crash after amend/receipt but before checkpoint: saved pre-fixer state can reuse.
    replay = auto_fix(
        {**state, "implementation": {**state["implementation"], "candidate_commit": before}}
    )
    assert replay["fix_receipt"] == state["fix_receipt"]
    assert git(state["worktree_path"], "rev-parse", "HEAD") == after
    gate = gate_node(state, gate=run_gate)
    assert gate["test_results"]["passed"]
    assert gate["gate_receipt"].candidate_commit == after
    assert gate["gate_receipt"].fix_receipt_id == state["fix_receipt"].receipt_id
    assert_retained(context, gate["gate_receipt"])


@pytest.mark.parametrize(
    "fix",
    [
        "printf changed >> result.txt; false",
        "touch untracked; false",
        "printf changed >> blacksmith.toml",
    ],
)
def test_failed_or_protected_formatter_changes_are_retained_without_gate(project, fix):
    context, state = prepare(
        project, f'test_cmd="touch gate-must-not-run"\nfix_cmd={json.dumps(fix)}\n'
    )
    before = git(state["worktree_path"], "rev-parse", "HEAD")
    update = auto_fix(state)
    assert update["status"] == Status.HALTED
    assert not update["test_results"]["passed"]
    assert update["gate_receipt"].outcome == "dirty"
    assert not (Path(state["worktree_path"]) / "gate-must-not-run").exists()
    assert git(state["worktree_path"], "rev-parse", "HEAD") == before
    assert git(state["worktree_path"], "status", "--porcelain")
    assert_retained(context, update["gate_receipt"])


def test_clean_formatter_failure_may_proceed_to_authoritative_gate(project):
    _, state = prepare(project, 'test_cmd="true"\nfix_cmd="false"\n')
    update = auto_fix(state)
    assert update["fix_receipt"].outcome == "clean_failure"
    assert update["fix_receipt"].commands[0].exit_code == 1
    assert "status" not in update
    state.update(update)
    assert gate_node(state, gate=run_gate)["test_results"]["passed"]


def test_no_owned_implementation_commit_cannot_amend_prior_work(project):
    context, state = prepare(project, 'test_cmd="true"\nfix_cmd="touch unrelated"\n')
    state["fix_attempts"] = 1  # This attempt never implemented anything.
    before = git(state["worktree_path"], "rev-parse", "HEAD")
    update = auto_fix(state)
    assert update["status"] == Status.HALTED
    assert update["gate_receipt"].outcome == "error"
    assert not (Path(state["worktree_path"]) / "unrelated").exists()
    assert git(state["worktree_path"], "rev-parse", "HEAD") == before
    assert_retained(context, update["gate_receipt"])


@pytest.mark.parametrize("failure", ["add", "amend"])
def test_formatter_git_failure_never_becomes_a_pass(project, failure):
    context, state = prepare(project, 'test_cmd="true"\nfix_cmd="printf formatted > result.txt"\n')
    path = Path(state["worktree_path"])
    if failure == "add":
        (path / ".git/index.lock").touch()
    else:
        git(path, "config", "core.hooksPath", str(path / ".git/hooks"))
        hook = path / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
    before = git(path, "rev-parse", "HEAD")
    update = auto_fix(state)
    assert update["status"] == Status.HALTED
    assert not update["test_results"]["passed"]
    assert update["gate_receipt"].outcome in {"error", "dirty"}
    assert git(path, "rev-parse", "HEAD") == before
    assert (path / "result.txt").read_text() == "formatted"
    assert_retained(context, update["gate_receipt"])


@pytest.mark.parametrize("stage", ["intent", "outcome"])
def test_critical_gate_receipt_failure_stops_before_downstream_effects(project, stage):
    context, state = prepare(project, 'test_cmd="mkdir -p .checks; touch .checks/ran"\n')
    with sqlite3.connect(context.journal().path) as conn:
        action = "INSERT" if stage == "intent" else "UPDATE"
        conn.execute(
            f"CREATE TRIGGER no_gate BEFORE {action} ON operations "
            "WHEN NEW.kind LIKE 'gate.%' BEGIN SELECT RAISE(ABORT,'disk'); END"
        )
    with pytest.raises(ReceiptError):
        gate_node(state, gate=run_gate)
    assert (Path(state["worktree_path"]) / ".checks/ran").exists() is (stage == "outcome")
    with sqlite3.connect(context.journal().path) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM operations WHERE kind LIKE 'gate.%' AND status='completed'"
            ).fetchone()[0]
            == 0
        )


def test_corrupt_gate_artifact_cannot_be_reused_as_a_pass(project):
    _, state = prepare(project)
    receipt = run_candidate_gate(state).receipt
    Path(receipt.artifact_path).write_text("{}")
    with pytest.raises(ReceiptError, match="missing/corrupt verification"):
        run_candidate_gate(state)


@pytest.mark.parametrize("parallel", [False, True])
def test_real_cli_multilayer_failure_never_reviews_or_publishes(project, monkeypatch, parallel):
    root, config, prd, contract = project
    contract["layers"] = {"first": "auto", "second": "auto"}
    contract["work_units"][0]["layers"] = ["first", "second"]
    if parallel:
        contract["work_units"].append(
            {**contract["work_units"][0], "id": "U2", "target_modules": ["other.txt"]}
        )
    write_prd(prd, contract)
    config.write_text(config.read_text() + "[limits]\nmax_fix_attempts=0\n")
    (root / "blacksmith.toml").write_text('test_cmd="true"\n[layers.second]\ntest_cmd="false"\n')
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "multilayer")
    calls, publications = [], []
    install_transport(monkeypatch, calls)

    def publish(**kwargs):
        publications.append(kwargs)
        pytest.fail("failed multilayer run reached publication")

    monkeypatch.setattr("blacksmith.nodes.pr.open_pull_request", publish)
    assert (
        cli.main([str(prd), "--config", str(config), "--thread-id", "layers", "--auto-approve"])
        == 1
    )
    assert publications == []
    assert not any(
        call["prompt"].startswith("Review") or "adversarial" in call["prompt"].lower()
        for call in calls
        if not call["prompt"].startswith("Implement")
    )
    db = next(config.parent.glob(".blacksmith/runs/*/operations.sqlite"))
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM operations WHERE kind LIKE 'model.review%'"
            ).fetchone()[0]
            == 0
        )
        receipts = [
            GateReceipt.model_validate(json.loads(row[0])["receipt"])
            for row in conn.execute("SELECT outcome_json FROM operations WHERE kind LIKE 'gate.%'")
        ]
    assert receipts and all(
        r.outcome == "failed" and r.layers == ("first", "second") for r in receipts
    )
    values = saved_values(config, "layers")
    assert values["status"] == Status.HALTED
    if not parallel:
        assert isinstance(values["gate_receipt"], GateReceipt)
        assert values["gate_receipt"].outcome == "failed"


def test_spawn_failure_has_error_exit_timing_and_retained_command_paths(project, monkeypatch):
    from blacksmith import commands

    context, state = prepare(project)
    real_popen = commands.subprocess.Popen

    def fail_shell(argv, **kwargs):
        if isinstance(argv, str):
            raise OSError("cannot create child")
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(commands.subprocess, "Popen", fail_shell)
    result = gate_node(state, gate=run_gate)
    assert result["status"] == Status.HALTED
    receipt = result["gate_receipt"]
    assert receipt.outcome == "error"
    row = receipt.commands[0]
    assert row.outcome == "error" and row.exit_code is None
    assert row.started_at and row.finished_at
    assert row.output_artifact_paths and all(Path(p).is_file() for p in row.output_artifact_paths)
    assert_retained(context, receipt)


def test_critical_artifact_write_failure_cannot_return_passing_gate(project, monkeypatch):
    context, state = prepare(project)

    def disk_error(path, text):
        raise OSError("artifact disk unavailable")

    monkeypatch.setattr(verification, "atomic_write", disk_error)
    with pytest.raises(ReceiptError, match="critical verification artifact"):
        gate_node(state, gate=run_gate)
    assert gate_rows(context)[0] == ("intent", None)


def test_real_cli_checkpoint_resume_retains_typed_exact_receipt(project, monkeypatch):
    from blacksmith.run_context import context_from_state

    root, config, prd, _ = project
    (root / "blacksmith.toml").write_text(
        'test_cmd="mkdir -p .checks; printf x >> .checks/count"\n'
    )
    (root / ".gitignore").write_text(".checks/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "count gate")
    config.write_text(config.read_text() + "[review]\nenabled=false\n")
    calls = []
    install_transport(monkeypatch, calls)

    def approval(payload, values):
        if payload["gate"] == "plan":
            return True
        assert payload["gate"] == "pr"
        raise KeyboardInterrupt("retain real checkpoint before PR decision")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approval)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd), "--config", str(config), "--thread-id", "receipt-resume"])
    before = saved_values(config, "receipt-resume")
    receipt = before["gate_receipt"]
    assert isinstance(receipt, GateReceipt) and receipt.outcome == "passed"
    assert before["fix_receipt"].eligible
    context = context_from_state(before)
    count = context.workspace_path / ".checks/count"
    assert count.read_text() == "x"
    (root / "blacksmith.toml").write_text('test_cmd="false"\n')
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "verifier changed after approval")
    prd.unlink()
    config.write_text(config.read_text() + "[limits]\ncommand_timeout_s=0.01\n")
    with pytest.raises(KeyboardInterrupt):
        cli.main(["resume", "--config", str(config), "--thread-id", "receipt-resume"])
    after = saved_values(config, "receipt-resume")
    assert after["gate_receipt"] == receipt
    assert len(calls) == 2  # plan and implement only; no replay/reviewer/summary
    assert count.read_text() == "x"
    assert run_candidate_gate(after).receipt == receipt
    assert count.read_text() == "x"
    verify_gate_receipt(receipt, after)


def test_multilayer_state_output_is_bounded_with_complete_artifact_links(project):
    from blacksmith.verification import GATE_OUTPUT_TAIL_BYTES

    output = (
        "i=0; while [ $i -lt 1000 ]; do "
        "printf '0123456789012345678901234567890123456789'; i=$((i+1)); done"
    )
    context, state = prepare(
        project,
        f"test_cmd={json.dumps(output)}\n[layers.b]\n"
        f"test_cmd={json.dumps(output + '; printf LAST_LAYER')}\n",
        layers=["a", "b"],
    )
    result = run_candidate_gate(state)
    assert result.passed
    assert len(result.output.encode()) < GATE_OUTPUT_TAIL_BYTES + 512
    assert "gate output truncated" in result.output
    assert "LAST_LAYER" in result.output
    assert len(result.receipt.commands) == 2
    assert len(result.receipt.output_artifact_paths) == 6
    assert all(Path(path).is_file() for path in result.receipt.output_artifact_paths)
    assert_retained(context, result.receipt)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_command_artifact_corruption_prevents_passing_receipt_reuse(project, index):
    _, state = prepare(project)
    receipt = run_candidate_gate(state).receipt
    Path(receipt.commands[0].output_artifact_paths[index]).write_text("corrupt")
    with pytest.raises(ReceiptError, match="missing/corrupt verification"):
        run_candidate_gate(state)


def test_unit_gate_api_cannot_override_required_layers_with_subset(project):
    _, state = prepare(
        project,
        'test_cmd="mkdir -p .checks; touch .checks/subset-was-run"\n'
        '[layers.b]\ntest_cmd="false"\n', layers=["a", "b"]
    )
    result = run_candidate_gate(state, layers=["a"])
    assert result.receipt.outcome == "error"
    assert not result.passed
    assert result.receipt.commands == ()
    assert not (Path(state["worktree_path"]) / ".checks/subset-was-run").exists()


def test_formatter_post_amend_audit_catches_hook_introduced_protected_change(project):
    context, state = prepare(project, 'test_cmd="true"\nfix_cmd="printf formatted > result.txt"\n')
    path = Path(state["worktree_path"])
    git(path, "config", "core.hooksPath", str(path / ".git/hooks"))
    hook = path / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nprintf '\\n# forbidden' >> blacksmith.toml\ngit add blacksmith.toml\n"
    )
    hook.chmod(0o755)
    update = auto_fix(state)
    assert update["status"] == Status.HALTED
    assert update["fix_receipt"].outcome == "dirty"
    assert update["gate_receipt"].outcome == "dirty"
    assert not update["test_results"]["passed"]
    assert "forbidden" in (path / "blacksmith.toml").read_text()
    assert_retained(context, update["gate_receipt"])
