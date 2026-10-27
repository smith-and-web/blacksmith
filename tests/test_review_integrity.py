"""S12 reviews through real Git/CLI/unit graphs/gates/SQLite and real SDK options."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from mcp import types as mcp_types
from test_integration_integrity import configure, stop_at_pr
from test_run_context import git, saved_values, write_prd
from test_run_context import project as project

from blacksmith import cli
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.graph import build_checkpointer
from blacksmith.receipts import ReceiptError
from blacksmith.review_patch import DIFF_SERVER, DIFF_TOOL, make_read_diff_tool
from blacksmith.review_receipts import ReviewReceipt, parse_verdict, verify_receipt
from blacksmith.run_context import context_from_state
from blacksmith.state import Status

CLEAN = '{"verdict":"clean","findings":[]}'
BLOCKING = '{"verdict":"needs_changes","findings":[{"severity":"blocking",' \
           '"file":"result.txt","detail":"deleted behavior is still required"}]}'
ORIGINAL_COLLECT = Executor._collect


def install(monkeypatch, *, responses=None, implement=None, inspect=None):
    """Replace paid transport only; use Executor.run/build_options/critical accounting."""
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")
    calls, counts = [], Counter()
    monkeypatch.setattr("blacksmith.nodes.pr.open_pull_request", lambda *a, **k:
                        pytest.fail("publication is forbidden in these review drills"))

    async def collect(self, prompt, options, *, emit_findings=False):
        unit = re.search(r"^Unit ([^:]+):", prompt, re.M).group(1)
        role = "plan" if prompt.startswith("Produce a concise") else (
            "review" if emit_findings else "implement")
        counts[(unit, role)] += 1
        number = counts[(unit, role)]
        calls.append((role, unit, prompt, options))
        assert options.setting_sources == [] and options.strict_mcp_config
        assert options.cwd and options.tools
        if role == "plan":
            text = "EXACT_APPROVED_PLAN_" + unit
        elif role == "implement":
            assert prompt.startswith("Implement this work unit")
            if implement:
                implement(unit, number, Path(options.cwd), prompt)
            else:
                target = re.search(r"^Target modules: (.+)$", prompt, re.M).group(1)
                (Path(options.cwd) / target).write_text(unit + "\n")
            text = "implemented"
        else:
            assert set(options.tools) == {"Read", "Glob", "Grep"}
            assert {"Write", "Edit", "Bash"}.isdisjoint(options.allowed_tools)
            assert "EXACT_PROSE_SENTINEL" in prompt and "EXACT_APPROVED_PLAN_" + unit in prompt
            if inspect:
                await inspect(unit, number, prompt, options)
            text = responses(unit, number) if responses else CLEAN
            if text in {"missing_terminal", "stream_error"}:
                mode = text

                async def query(*, prompt, options):
                    yield AssistantMessage(content=[TextBlock(CLEAN)], model=options.model)
                    if mode == "stream_error":
                        raise OSError("offline stream lost")

                self._query = query
                return await ORIGINAL_COLLECT(self, prompt, options, emit_findings=emit_findings)
        return ExecutorResult(text, options.model, text == "terminal_error", 1, .01, {}, "offline")

    monkeypatch.setattr(Executor, "_collect", collect)
    return calls


def run(project, thread):
    return cli.main([str(project[2]), "--config", str(project[1]),
                     "--thread-id", thread, "--approve", "plan"])


def configure_review(project, *, parallel=False, panel=1, revisions=0):
    if parallel:
        configure(project)
    project[1].write_text(project[1].read_text() + f"[review]\npanel_size={panel}\n"
                          f"[limits]\nmax_review_revisions={revisions}\n")


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("mode", ["malformed", "terminal_error", "missing_terminal",
                                  "stream_error", "minority_blocking"])
def test_unavailable_or_any_panel_blocker_halts_both_shapes_before_later_effects(
    project, monkeypatch, parallel, mode,
):
    configure_review(project, parallel=parallel, panel=3)
    def response(unit, seat):
        return (BLOCKING if seat == 2 else CLEAN) if mode == "minority_blocking" else mode
    calls = install(monkeypatch, responses=response)
    assert run(project, "strict-review") == 1
    state = saved_values(project[1], "strict-review")
    context = context_from_state(state)
    expected = "blocking" if mode == "minority_blocking" else "unavailable"
    assert state["status"] == Status.HALTED and not state.get("pr_url")
    assert not state.get("final_gate_receipt") and not state.get("join_destinations")
    units = {"A", "B"} if parallel else {"U1"}
    assert set(state["unit_outcomes"]) == units
    for unit in units:
        outcome = state["unit_outcomes"][unit]
        record = outcome["review_receipt"]
        assert isinstance(record, ReviewReceipt) and record.status == expected
        assert not outcome["ok"] and not outcome["review_clean"]
        assert outcome["failed_stage"] == "review"
        assert Path(outcome["clone_path"]).is_dir()
        assert record.patch and Path(record.patch.path).is_file()
        assert len(record.panel_results) == (3 if expected == "blocking" else 1)
        assert len([c for c in calls if c[:2] == ("review", unit)]) == len(record.panel_results)
        assert len([c for c in calls if c[:2] == ("implement", unit)]) == 1
    assert not any(o.key.kind.startswith("command:") and o.intent["stage"] == "open_pr"
                   for o in context.journal().operations())
    assert (context.directory / "recovery/index.json").is_file()


@pytest.mark.parametrize("text", [
    "", "fine", "{}", "[]", '{"findings":[]}', '{"verdict":"clean"}',
    '{"verdict":"blocked","findings":[]}',
    '{"verdict":"clean","findings":[],"extra":true}',
    '{"verdict":"clean","verdict":"needs_changes","findings":[]}',
    '{"verdict":"clean","findings":[{"severity":"typo","file":"a","detail":"b"}]}',
    '{"verdict":"clean","findings":[{"severity":"blocking","file":3,"detail":"b"}]}',
    '{"verdict":"clean","findings":[{"severity":"blocking","file":"a","detail":" "}]}',
    'prose only, no block',
    'prose ' + CLEAN,                                   # bare object with surrounding prose
    '```json\n' + CLEAN,                                # unterminated fence
    '```json\n' + CLEAN + '\n```\n```json\n' + CLEAN + '\n```',  # two blocks
    'prose ```json\n' + CLEAN + '\n``` and ```\n{}\n```',
])
def test_verdict_requires_one_strict_complete_object(text):
    with pytest.raises(ValueError):
        parse_verdict(text)


@pytest.mark.parametrize("text", [
    CLEAN,
    '```json\n' + CLEAN + '\n```',
    '```\n' + CLEAN + '\n```',
    # Reviewers write a prose summary around the block despite the prompt; the block is
    # the verdict and the prose is ignored (seen twice on the first external PRD run).
    'I verified the four edits and the test.\n\nNo blocking bugs found.\n\n```json\n'
    + CLEAN + '\n```',
    '```json\n' + CLEAN + '\n```\n\nSummary: nothing blocking.',
    'prefix ```json\n' + CLEAN + '\n``` trailing',
])
def test_verdict_accepts_one_fenced_block_anywhere(text):
    verdict = parse_verdict(text)
    assert verdict.verdict == "clean" and verdict.findings == ()


@pytest.mark.parametrize("parallel", [False, True])
def test_deletion_only_defect_survives_cumulative_revision_review(project, monkeypatch, parallel):
    configure_review(project, parallel=parallel, revisions=1)
    root, _, prd, contract = project
    for unit in contract["work_units"]:
        unit["target_modules"] = [unit["id"].lower() + ".py"]
        (root / unit["target_modules"][0]).write_text("DENY_REQUIRED_SENTINEL\nkeep = True\n")
    write_prd(prd, contract)
    git(root, "add", "-A")
    git(root, "commit", "-m", "required behavior before unit")
    base = git(root, "rev-parse", "HEAD")
    patches = []

    def change(unit, number, workspace, prompt):
        path = workspace / (unit.lower() + ".py")
        path.write_text("keep = True\n" + ("unrelated_revision = True\n" if number == 2 else ""))

    async def inspect(unit, number, prompt, options):
        assert "-DENY_REQUIRED_SENTINEL" in prompt
        manifest = json.loads(prompt.split("includes deletions/renames/binary changes):\n")[1]
                              .splitlines()[0])
        assert manifest["base_commit"] == base
        assert manifest["candidate_commit"] == git(Path(options.cwd), "rev-parse", "HEAD")
        if number == 2:
            assert "+unrelated_revision = True" in prompt
        patches.append((unit, manifest))

    calls = install(monkeypatch, implement=change, inspect=inspect,
                    responses=lambda unit, number: BLOCKING)
    assert run(project, "cumulative") == 1
    state = saved_values(project[1], "cumulative")
    for unit, row in state["unit_outcomes"].items():
        assert not row["ok"] and row["review_revisions"] == 1
        assert row["review_receipt"].status == "blocking" and row["base_commit"] == base
        records = [r for u, r in patches if u == unit]
        assert len(records) == 2
        assert records[0]["candidate_commit"] != records[1]["candidate_commit"]
        assert len([c for c in calls if c[:2] == ("implement", unit)]) == 2
    assert not state.get("final_gate_receipt") and not state.get("pr_url")


def test_large_patch_over_command_log_cap_has_full_artifact_and_actual_bounded_mcp_tool(
    project, monkeypatch,
):
    # PR diff rendering is a separate S13 seam; exercise the full reviewer patch
    # over the native command cap with that optional display disabled.
    project[1].write_text(project[1].read_text() + "[limits]\nmax_command_output_bytes=4096\n"
                          "[hitl]\npr_diff_max_bytes=0\n")
    content = "FULL_PATCH_SENTINEL\n" + ("added source line " + "x" * 80 + "\n") * 14000
    verified = []

    def change(unit, number, workspace, prompt):
        (workspace / "result.txt").write_text(content)

    async def inspect(unit, number, prompt, options):
        assert "This prompt contains a manifest, not a truncated patch" in prompt
        assert "FULL_PATCH_SENTINEL" not in prompt and DIFF_TOOL in options.allowed_tools
        server = options.mcp_servers[DIFF_SERVER]["instance"]
        listing = await server.request_handlers[mcp_types.ListToolsRequest](None)
        assert [t.name for t in listing.root.tools] == ["read_diff"]
        manifest = json.loads(prompt.split("includes deletions/renames/binary changes):\n")[1]
                              .splitlines()[0])
        from blacksmith.review_receipts import ReviewPatch
        request = mcp_types.CallToolRequest(method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="read_diff", arguments={"offset": 0, "max_bytes": 32768}))
        actual_rpc = await server.request_handlers[mcp_types.CallToolRequest](request)
        assert not actual_rpc.root.isError
        assert json.loads(actual_rpc.root.content[0].text)["sha256"] == manifest["sha256"]
        definition = make_read_diff_tool(ReviewPatch.model_validate(manifest))
        chunks, offset = [], 0
        while True:
            response = await definition.handler({"offset": offset, "max_bytes": 32768})
            assert not response.get("isError")
            row = json.loads(response["content"][0]["text"])
            assert len(row["patch"].encode()) <= 32768
            chunks.append(row["patch"])
            offset = row["next_offset"]
            if row["complete"]:
                break
        full = "".join(chunks).encode()
        assert len(full) == manifest["bytes"] > 1048576
        assert hashlib.sha256(full).hexdigest() == manifest["sha256"]
        assert b"+FULL_PATCH_SENTINEL" in full
        invalid = await definition.handler({"offset": 0, "max_bytes": 32769})
        assert invalid["isError"]
        verified.append(manifest)

    install(monkeypatch, implement=change, inspect=inspect)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        run(project, "large")
    state = saved_values(project[1], "large")
    assert len(verified) == 1 and state["review_receipt"].status == "clean"
    assert state["final_gate_receipt"].candidate_commit == state["review_receipt"].candidate_commit


def test_rename_and_binary_patch_are_reviewer_evidence_not_only_current_files(project, monkeypatch):
    root = project[0]
    (root / "binary.dat").write_bytes(b"\x00ORIGINAL_BINARY\xff" * 40)
    git(root, "add", "binary.dat")
    git(root, "commit", "-m", "binary source")
    inspected = []

    def change(unit, number, workspace, prompt):
        (workspace / "README.md").rename(workspace / "renamed.md")
        (workspace / "binary.dat").write_bytes(b"\x00CHANGED_BINARY\xff" * 40)

    async def inspect(unit, number, prompt, options):
        assert "rename from README.md" in prompt and "rename to renamed.md" in prompt
        assert "GIT binary patch" in prompt and "FULL CUMULATIVE PATCH" in prompt
        inspected.append(prompt)

    install(monkeypatch, implement=change, inspect=inspect)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        run(project, "binary-rename")
    state = saved_values(project[1], "binary-rename")
    assert len(inspected) == 1 and state["review_receipt"].status == "clean"
    assert not state.get("pr_url")


@pytest.mark.parametrize("tamper", ["patch", "missing_patch", "receipt", "base", "candidate"])
def test_review_tampering_cannot_reach_executor_on_resume(project, monkeypatch, tamper):
    install(monkeypatch)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        run(project, "tamper")
    state = saved_values(project[1], "tamper")
    context, record = context_from_state(state), state["review_receipt"]
    if tamper == "patch":
        Path(record.patch.path).write_text("changed")
    elif tamper == "missing_patch":
        Path(record.patch.path).unlink()
    elif tamper == "receipt":
        Path(record.artifact_path).write_text("{}")
    else:
        field = "base_commit" if tamper == "base" else "candidate_commit"
        changed = record.model_copy(update={field: "0" * 40})
        with pytest.raises(ReceiptError):
            verify_receipt(changed, state, unit=state["selected_unit"],
                           base=record.base_commit, candidate=record.candidate_commit)
        return
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    before = saver.get_tuple({"configurable": {"thread_id": "tamper"}}).checkpoint
    saver.conn.close()
    monkeypatch.setattr(cli, "Executor", lambda *a, **k: pytest.fail("executor before integrity"))
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "tamper"]) == 1
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple({"configurable": {"thread_id": "tamper"}}).checkpoint == before
    saver.conn.close()


def test_legacy_review_checkpoint_refuses_before_executor_without_rewriting(project, monkeypatch):
    install(monkeypatch)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        run(project, "old-review")
    state = saved_values(project[1], "old-review")
    context = context_from_state(state)
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    from blacksmith.graph import compile_graph
    graph = compile_graph(saver)
    graph.update_state({"configurable": {"thread_id": "old-review"}}, {"review_version": 0})
    before = saver.get_tuple({"configurable": {"thread_id": "old-review"}}).checkpoint
    saver.conn.close()
    monkeypatch.setattr(cli, "Executor", lambda *a, **k: pytest.fail("legacy executor"))
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "old-review"]) == 1
    saver = build_checkpointer(context.execution_config().checkpointer.db_path)
    assert saver.get_tuple({"configurable": {"thread_id": "old-review"}}).checkpoint == before
    saver.conn.close()
