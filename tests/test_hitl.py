"""Tests for the HITL interrupt nodes (WU-07).

Test contract (PRD §6, WU-07): graph halts at interrupt, resumes on injected
approval. We also cover the rejection path (routes to human_halt) and that the
interrupt surfaces a payload for the human to review.
"""

import subprocess
from pathlib import Path

from langgraph.types import Command

from blacksmith.config import HitlConfig
from blacksmith.contract import parse_prd
from blacksmith.graph import build_checkpointer, compile_graph
from blacksmith.nodes.hitl import combined_diff
from blacksmith.nodes.pr import CommandResult
from blacksmith.state import Status

VENDORED_PRD = Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md"

# A passing gate result lets the auto path reach the PR gate. The test_gate node is
# still a placeholder until WU-06, so seeding test_results stands in for a pass.
PASSING = {"passed": True, "output": "ok", "command": "pytest"}


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "a.txt").write_text("x\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _fake_gh_runner(url: str):
    """Succeed for git, return a canned URL for gh — no real GitHub or repo needed."""

    def run(argv, cwd=None):
        if argv and argv[0] == "gh":
            return CommandResult(0, url + "\n", "")
        return CommandResult(0, "", "")

    return run


def _graph(tmp_path, **compile_kwargs):
    saver = build_checkpointer(tmp_path / "ckpt.sqlite")
    return compile_graph(saver, **compile_kwargs), saver


def test_plan_gate_halts_then_resumes_on_approval(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("plan-approve")

    result = g.invoke({"status": Status.PENDING}, cfg)
    assert "__interrupt__" in result  # halted, surfacing a payload
    assert g.get_state(cfg).next == ("approve_plan",)

    g.invoke(Command(resume=True), cfg)  # inject approval
    state = g.get_state(cfg)
    assert state.values["approvals"]["plan"] is True
    assert state.next == ()  # ran on past the gate
    saver.conn.close()


def test_plan_gate_rejection_routes_to_human_halt(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("plan-reject")

    g.invoke({"status": Status.PENDING}, cfg)
    g.invoke(Command(resume=False), cfg)  # reject
    state = g.get_state(cfg)
    assert state.values["approvals"]["plan"] is False
    assert state.values["status"] == Status.HALTED
    assert state.next == ()
    saver.conn.close()


def test_interrupt_surfaces_plan_payload(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("payload")

    plans = [{"unit_id": "WU-X", "steps": "do x"}]
    result = g.invoke({"status": Status.PENDING, "plans": plans}, cfg)
    payload = result["__interrupt__"][0].value
    assert payload["gate"] == "plan"
    assert payload["plans"] == plans  # the gate surfaces every auto unit's plan
    saver.conn.close()


def test_pr_gate_halts_and_resumes_on_approval(tmp_path):
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    pr_url = "https://github.com/smith-and-web/kindling/pull/7"
    g, saver = _graph(tmp_path, pr_runner=_fake_gh_runner(pr_url))
    cfg = _cfg("pr-approve")
    seed = {
        "status": Status.PENDING,
        "selected_unit": unit,
        "worktree_path": "/tmp/wt",
        "test_results": PASSING,
    }

    g.invoke(seed, cfg)
    g.invoke(Command(resume=True), cfg)  # approve plan -> runs through to the PR gate
    assert g.get_state(cfg).next == ("approve_pr",)

    g.invoke(Command(resume=True), cfg)  # approve PR -> open_pr (mocked gh)
    state = g.get_state(cfg)
    assert state.values["approvals"] == {"plan": True, "pr": True}
    assert state.values["pr_url"] == pr_url
    assert state.values["status"] == Status.DONE
    assert state.next == ()
    saver.conn.close()


def test_pr_gate_rejection_halts(tmp_path):
    g, saver = _graph(tmp_path)
    cfg = _cfg("pr-reject")

    g.invoke({"status": Status.PENDING, "test_results": PASSING}, cfg)
    g.invoke(Command(resume=True), cfg)  # approve plan
    assert g.get_state(cfg).next == ("approve_pr",)

    g.invoke(Command(resume=False), cfg)  # reject PR
    state = g.get_state(cfg)
    assert state.values["approvals"]["pr"] is False
    assert state.values["status"] == Status.HALTED
    assert state.next == ()
    saver.conn.close()


# --- WU-PR-DIFF-CAPTURE: bounded combined diff surfaced at the approve_pr gate --------


def test_combined_diff_truncates_large_diff_with_marker(tmp_path):
    repo = _init_repo(tmp_path / "big-diff")
    base_ref = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "big.txt").write_text("line\n" * 5000)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "big")

    diff = combined_diff(str(repo), base_ref, max_bytes=200)

    assert "…(diff truncated at 200 bytes)…" in diff
    marker_bytes = len("\n…(diff truncated at 200 bytes)…\n".encode())
    assert len(diff.encode("utf-8")) <= 200 + marker_bytes


def test_combined_diff_git_failure_returns_empty_string_without_raising(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert combined_diff(str(missing), "HEAD", max_bytes=1000) == ""


def test_combined_diff_empty_diff_returns_empty_string(tmp_path):
    repo = _init_repo(tmp_path / "no-diff")
    base_ref = _git(repo, "rev-parse", "HEAD").strip()
    assert combined_diff(str(repo), base_ref, max_bytes=1000) == ""


def test_approve_pr_payload_includes_diff_text_when_hitl_enabled(tmp_path):
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    repo = _init_repo(tmp_path / "shared-diff")
    base_ref = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "b.txt").write_text("added by unit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unit commit")

    g, saver = _graph(tmp_path, hitl=HitlConfig(pr_diff_max_bytes=60000))
    cfg = _cfg("pr-diff-payload")
    seed = {
        "status": Status.PENDING,
        "selected_unit": unit,
        "worktree_path": str(repo),
        "pr_base_ref": base_ref,
        "test_results": PASSING,
    }

    g.invoke(seed, cfg)
    result = g.invoke(Command(resume=True), cfg)  # approve plan -> halts at approve_pr
    payload = result["__interrupt__"][0].value
    assert payload["gate"] == "pr"
    assert "diff_text" in payload
    assert "b.txt" in payload["diff_text"]
    assert "added by unit" in payload["diff_text"]
    saver.conn.close()


def test_approve_pr_payload_has_no_diff_text_when_pr_diff_max_bytes_is_zero(tmp_path):
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    repo = _init_repo(tmp_path / "shared-off")
    base_ref = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "b.txt").write_text("added by unit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unit commit")

    g, saver = _graph(tmp_path, hitl=HitlConfig(pr_diff_max_bytes=0))
    cfg = _cfg("pr-diff-off")
    seed = {
        "status": Status.PENDING,
        "selected_unit": unit,
        "worktree_path": str(repo),
        "pr_base_ref": base_ref,
        "test_results": PASSING,
    }

    g.invoke(seed, cfg)
    result = g.invoke(Command(resume=True), cfg)  # approve plan -> halts at approve_pr
    payload = result["__interrupt__"][0].value
    assert "diff_text" not in payload
    saver.conn.close()


def test_approve_pr_payload_has_no_diff_text_without_hitl_config(tmp_path):
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    repo = _init_repo(tmp_path / "shared-no-hitl")
    base_ref = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "b.txt").write_text("added by unit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unit commit")

    g, saver = _graph(tmp_path)  # no hitl kwarg at all
    cfg = _cfg("pr-diff-no-hitl")
    seed = {
        "status": Status.PENDING,
        "selected_unit": unit,
        "worktree_path": str(repo),
        "pr_base_ref": base_ref,
        "test_results": PASSING,
    }

    g.invoke(seed, cfg)
    result = g.invoke(Command(resume=True), cfg)  # approve plan -> halts at approve_pr
    payload = result["__interrupt__"][0].value
    assert "diff_text" not in payload
    saver.conn.close()


def test_pr_gate_approval_still_a_bool_and_routing_unchanged_with_hitl_enabled(tmp_path):
    """The approver contract and gate routing are unchanged even with the combined-diff
    display on: approval still opens the PR, rejection still halts (WU-PR-DIFF-CAPTURE)."""
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    repo = _init_repo(tmp_path / "shared-approve")
    base_ref = _git(repo, "rev-parse", "HEAD").strip()
    pr_url = "https://github.com/owner/demo/pull/2"

    g, saver = _graph(
        tmp_path,
        pr_runner=_fake_gh_runner(pr_url),
        hitl=HitlConfig(pr_diff_max_bytes=60000),
    )
    cfg = _cfg("pr-diff-approve")
    seed = {
        "status": Status.PENDING,
        "selected_unit": unit,
        "worktree_path": str(repo),
        "pr_base_ref": base_ref,
        "test_results": PASSING,
    }
    g.invoke(seed, cfg)
    g.invoke(Command(resume=True), cfg)  # approve plan -> halts at approve_pr
    g.invoke(Command(resume=True), cfg)  # approve PR -> open_pr
    state = g.get_state(cfg)
    assert state.values["approvals"]["pr"] is True
    assert state.values["pr_url"] == pr_url
    assert state.values["status"] == Status.DONE
    assert state.next == ()
    saver.conn.close()


def test_pr_gate_rejection_still_halts_with_hitl_enabled(tmp_path):
    unit = parse_prd(VENDORED_PRD).contract.work_unit_by_id("WU-01")
    repo = _init_repo(tmp_path / "shared-reject")
    base_ref = _git(repo, "rev-parse", "HEAD").strip()

    g, saver = _graph(tmp_path, hitl=HitlConfig(pr_diff_max_bytes=60000))
    cfg = _cfg("pr-diff-reject")
    seed = {
        "status": Status.PENDING,
        "selected_unit": unit,
        "worktree_path": str(repo),
        "pr_base_ref": base_ref,
        "test_results": PASSING,
    }
    g.invoke(seed, cfg)
    g.invoke(Command(resume=True), cfg)  # approve plan
    assert g.get_state(cfg).next == ("approve_pr",)

    g.invoke(Command(resume=False), cfg)  # reject PR
    state = g.get_state(cfg)
    assert state.values["approvals"]["pr"] is False
    assert state.values["status"] == Status.HALTED
    assert state.next == ()
    saver.conn.close()
