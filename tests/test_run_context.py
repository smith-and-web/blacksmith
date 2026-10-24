"""Version-2 preflight and restart boundaries using real Git, CLI and SQLite."""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow
from langgraph.graph import END, START, StateGraph

from blacksmith import cli
from blacksmith.config import BlacksmithConfig
from blacksmith.context import render_context
from blacksmith.contract import ContractError, parse_prd, validate_execution_contract
from blacksmith.executor import Executor, ExecutorResult
from blacksmith.graph import build_checkpointer
from blacksmith.path_policy import policy_for_state
from blacksmith.run_context import (
    RunContextError,
    RunLock,
    atomic_write,
    context_from_state,
    create_run_context,
    origin_identity,
    run_branch,
    thread_lock,
)
from blacksmith.state import BlacksmithState, Status
from blacksmith.worktree import RunCloneManager


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
    ).stdout.strip()


def write_prd(path, contract):
    text = (
        "---\n" + json.dumps(contract) + "\n---\n# Purpose\nEXACT_PROSE_SENTINEL\n"
        "# Scope\nall\n# Untouchables\nbrand\n# Acceptance\nall checks pass\n"
    )
    path.write_text(text)
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "remote", "add", "origin", "https://github.com/example/context-demo.git")
    (root / "README.md").write_text("BASE_SENTINEL\n")
    (root / "blacksmith.toml").write_text('test_cmd = "true"\n[layers.second]\n'
                                         'test_cmd = "false"\n')
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    config = tmp_path / "runtime.toml"
    config.write_text('[target]\nrepo_path = "target"\n')
    contract = {
        "contract_version": 1, "component": "demo", "version": "v1",
        "primary_target_repo": "example/context-demo", "layers": {"python": "auto"},
        "untouchables": ["Do not change the brand"],
        "work_units": [{"id": "U1", "title": "create output", "layers": ["python"],
                        "target_modules": ["result.txt"], "test_contract": "gate passes"}],
    }
    prd = write_prd(tmp_path / "task.md", contract)
    return root, config, prd, contract


def create(project, thread="run"):
    _, config, prd, _ = project
    return create_run_context(
        BlacksmithConfig.load(config), parse_prd(prd), thread, required_tools=("git",),
    )


def saved_values(config_path, thread):
    config = BlacksmithConfig.load(config_path)
    saver = build_checkpointer(config.checkpointer.db_path)
    try:
        saved = saver.get_tuple({"configurable": {"thread_id": thread}})
        assert saved is not None
        return saved.checkpoint["channel_values"]
    finally:
        saver.conn.close()


def test_paths_resolve_from_config_directory_even_with_other_cwd(project, tmp_path, monkeypatch):
    root, path, _, _ = project
    path.write_text('[target]\nrepo_path = "target"\n[metrics]\ndb_path = "data/metrics.db"\n')
    elsewhere = tmp_path / "elsewhere" / "nested"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    config = BlacksmithConfig.load(path)
    assert config.resolve_repo_path() == root
    assert config.metrics.db_path == path.parent / "data/metrics.db"
    assert config.checkpointer.db_path == path.parent / ".blacksmith/checkpoints.sqlite"
    assert config.store.db_path == path.parent / ".blacksmith/store.sqlite"
    assert config.live.db_path == path.parent / ".blacksmith/live.sqlite"
    assert config.transcripts.dir == path.parent / ".blacksmith/transcripts"


def test_snapshot_pins_source_commit_and_does_not_chmod_source(project, monkeypatch):
    root, config, prd, _ = project
    source_mode = (root / "README.md").stat().st_mode
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "SECRET_MUST_NEVER_BE_SNAPSHOTTED")
    context = create(project)
    assert context.prd_text == prd.read_bytes().decode()
    assert context.prd_sha256 == hashlib.sha256(prd.read_bytes()).hexdigest()
    assert context.origin == "github.com/example/context-demo"
    assert (root / "README.md").stat().st_mode == source_mode
    assert not (context.planning_path / "README.md").stat().st_mode & stat.S_IWUSR
    assert not (context.planning_path / ".git/config").stat().st_mode & 0o222
    assert (root / ".git/config").stat().st_mode & stat.S_IWUSR
    for artifact in context.directory.iterdir():
        if artifact.is_file():
            assert b"SECRET_MUST_NEVER_BE_SNAPSHOTTED" not in artifact.read_bytes()
    (root / "README.md").write_text("NEW_SOURCE_COMMIT\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "source advanced during approval")
    prd.write_text("changed invalid original PRD")
    config.write_text('[target]\nrepo_path = "different-target"\n')
    clone = RunCloneManager(context).create("U1")
    assert git(clone.path, "rev-parse", "HEAD") == context.base_commit
    assert (clone.path / "README.md").read_text() == "BASE_SENTINEL\n"
    assert context.prd().body.startswith("# Purpose\nEXACT_PROSE_SENTINEL")
    assert context.execution_config().resolve_repo_path() == root
    context.verify(selected_repo=root, require_workspace=True)


def test_prompts_ignore_mutated_in_memory_prd_after_snapshot(project):
    context = create(project)
    state = context.initial_state()
    original = state["prd"]
    state["prd"] = type(original)(original.path, original.contract, "MUTATED_REQUIREMENTS")
    rendered = render_context(state)
    assert original.body in rendered
    assert "MUTATED_REQUIREMENTS" not in rendered


def test_run_and_raw_unit_ids_have_distinct_paths_and_branches(project):
    first, second = create(project, "one"), create(project, "two")
    assert first.run_id != second.run_id and first.workspace_path != second.workspace_path
    assert first.workspace_path == first.directory / "workspace"
    assert first.unit_path("A+B") != first.unit_path("A_B")
    assert run_branch(first.run_id, "A+B") != run_branch(first.run_id, "A_B")
    assert run_branch(first.run_id, "U1") != run_branch(second.run_id, "U1")
    manager = RunCloneManager(first)
    shared = manager.create("U1")
    a = manager.create_from(shared.path, "A+B")
    b = manager.create_from(shared.path, "A_B")
    assert a.path != b.path and a.branch != b.branch
    assert a.path == first.unit_path("A+B")
    assert git(a.path, "remote", "get-url", "origin") == first.origin_url


@pytest.mark.parametrize("name", ["manifest.json", "input.prd.md", "runtime-config.json",
                                 "trusted-blacksmith.toml"])
def test_corrupt_or_missing_artifacts_fail_closed(project, name):
    context = create(project)
    (context.directory / name).write_text("corrupt")
    with pytest.raises(RunContextError, match="corrupt"):
        context.verify(selected_repo=Path(context.repo_path))
    (context.directory / name).unlink()
    with pytest.raises(RunContextError, match="corrupt"):
        context.verify(selected_repo=Path(context.repo_path))


def test_wrong_source_and_missing_workspace_fail_before_execution(project, tmp_path):
    context = create(project)
    with pytest.raises(RunContextError, match="selected repository"):
        context.verify(selected_repo=tmp_path)
    with pytest.raises(RunContextError, match="missing run workspace"):
        context.verify(selected_repo=Path(context.repo_path), require_workspace=True)


def test_custom_runtime_config_inside_source_is_a_hard_path_rule(project, monkeypatch):
    root, config, _, _ = project
    config_inside = root / "custom-runtime.toml"
    config_inside.write_text('[target]\nrepo_path = "."\n')
    project = (root, config_inside, project[2], project[3])
    context = create(project)
    clone = RunCloneManager(context).create("U1")
    state = {**context.initial_state(), "worktree_path": str(clone.path)}
    assert policy_for_state(state).violation("custom-runtime.toml") == "untouchable"
    from blacksmith.nodes.hitl import approve_plan
    payloads = []

    def deny(payload):
        payloads.append(payload)
        return False

    # Production approval now requires real completed plans and their run artifacts.
    from test_plan import FakeExecutor

    from blacksmith.nodes.plan import plan
    state.update(plan(state, executor=FakeExecutor()))
    monkeypatch.setattr("blacksmith.nodes.hitl._decide", deny)
    assert approve_plan(state)["status"] == Status.HALTED
    assert "custom-runtime.toml" in payloads[0]["hard_path_rules"]


def test_run_lock_is_exclusive_across_processes_and_released_after_exit(tmp_path):
    lock_path = tmp_path / "execution.lock"
    script = (
        "import sys\nfrom pathlib import Path\n"
        "from blacksmith.run_context import RunLock, RunContextError\n"
        "try:\n with RunLock(Path(sys.argv[1])): print('acquired')\n"
        "except RunContextError:\n print('run_already_locked'); sys.exit(17)\n"
    )
    with RunLock(lock_path):
        result = subprocess.run(
            [sys.executable, "-c", script, str(lock_path)], capture_output=True, text=True,
        )
        assert result.returncode == 17 and "run_already_locked" in result.stdout
    result = subprocess.run(
        [sys.executable, "-c", script, str(lock_path)], capture_output=True, text=True,
    )
    assert result.returncode == 0 and "acquired" in result.stdout


@pytest.mark.parametrize("url", ["git@github.com:Example/Repo.git",
                              "ssh://git@github.com:22/Example/Repo.git",
                              "https://github.com/Example/Repo.git/"])
def test_full_origin_identity_matches_ssh_and_https(url):
    assert origin_identity(url) == "github.com/example/repo"
    assert origin_identity("https://elsewhere.example/Example/Repo") != origin_identity(url)


@pytest.mark.parametrize("url", ["https://token@github.com/a/b", "https://github.com/a/b?token=x",
                              "https://github.com:bad/a/b", "/local/repo"])
def test_credential_bearing_or_unusable_origin_is_rejected_without_echo(url):
    with pytest.raises(RunContextError) as exc:
        origin_identity(url)
    assert url not in str(exc.value)


def test_dirty_tracked_source_and_missing_committed_toolchain_reject_preflight(project):
    root, _, _, _ = project
    (root / "README.md").write_text("dirty")
    with pytest.raises(RunContextError, match="commit or stash"):
        create(project)
    git(root, "checkout", "--", "README.md")
    git(root, "rm", "blacksmith.toml")
    git(root, "commit", "-m", "remove verifier")
    with pytest.raises(RunContextError, match="committed blacksmith.toml"):
        create(project)


def test_untracked_or_ignored_local_prd_is_permitted(project):
    root, _, prd_path, _ = project
    (root / ".gitignore").write_text("local-prds/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "ignore local inputs")
    local = root / "local-prds/task.md"
    local.parent.mkdir()
    local.write_bytes(prd_path.read_bytes())
    context = create((root, project[1], local, project[3]))
    assert context.prd_path == str(local)
    assert "EXACT_PROSE_SENTINEL" in context.prd_text


def test_missing_identity_and_tool_availability_reject_before_snapshot(project, monkeypatch):
    root, path, prd, _ = project
    config = BlacksmithConfig.load(path)
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: None)
    with pytest.raises(RunContextError, match="required host tool"):
        create_run_context(config, parse_prd(prd), "tools")
    git(root, "config", "--unset", "user.email")
    with pytest.raises(RunContextError, match="Git preflight"):
        create_run_context(config, parse_prd(prd), "identity", required_tools=())
    assert not (path.parent / ".blacksmith/runs").exists()


@pytest.mark.parametrize("thread", ["", " ", "bad\nthread"])
def test_invalid_thread_id_stops_before_git_or_model_tools(project, monkeypatch, thread):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid identity must be rejected before Git tools")

    monkeypatch.setattr("blacksmith.run_context.git_read", forbidden)
    with pytest.raises(RunContextError, match="thread-id"):
        create(project, thread)


def test_non_utf8_committed_toolchain_has_a_preflight_diagnostic(project):
    root, _, _, _ = project
    (root / "blacksmith.toml").write_bytes(b"test_cmd = '\xff'\n")
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "invalid toolchain encoding")
    with pytest.raises(RunContextError, match="committed blacksmith.toml"):
        create(project)


def test_atomic_write_failure_keeps_original_receipt(tmp_path, monkeypatch):
    target = tmp_path / "manifest.json"
    atomic_write(target, "original")

    def fail_replace(source, dest):
        raise OSError("injected persistence failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="persistence"):
        atomic_write(target, "replacement")
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_prd_raw_bytes_survive_crlf_snapshot(project):
    _, _, path, _ = project
    data = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(data)
    prd = parse_prd(path)
    assert prd.text.encode() == data
    context = create(project)
    assert context.prd_sha256 == hashlib.sha256(data).hexdigest()
    assert context.prd().body == prd.body


@pytest.mark.parametrize("field,value", [("id", ""), ("title", "  "),
                                        ("target_modules", ["../escape"]),
                                        ("target_modules", ["/absolute"]),
                                        ("target_modules", ["C:\\host"]),
                                        ("target_modules", [" "])])
def test_invalid_identity_or_targets_reject_offline_before_executor(project, field, value, capsys):
    _, _, path, contract = project
    contract["work_units"][0][field] = value
    write_prd(path, contract)
    assert cli.main(["validate", str(path)]) == 1
    assert field in capsys.readouterr().err


@pytest.mark.parametrize("layout", ["human-only", "sibling", "mid-dag", "mixed", "multiple"])
def test_invalid_human_layouts_reject_in_offline_validate(project, layout):
    _, _, path, contract = project
    contract["layers"]["qa"] = "human"
    qa = {"id": "QA", "title": "manual QA", "layers": ["qa"],
          "target_modules": ["manual"], "test_contract": "inspect", "depends_on": ["U1"]}
    contract["work_units"].append(qa)
    if layout == "human-only":
        contract["work_units"] = [{**qa, "depends_on": []}]
    elif layout == "sibling":
        qa["depends_on"] = []
    elif layout == "mid-dag":
        contract["work_units"].append({**contract["work_units"][0], "id": "AFTER",
                                        "depends_on": ["QA"]})
    elif layout == "mixed":
        qa["layers"] = ["qa", "python"]
    else:
        contract["work_units"].append({**qa, "id": "OTHER-QA"})
    write_prd(path, contract)
    assert cli.main(["validate", str(path)]) == 1


def test_terminal_transitive_human_unit_is_valid_and_archive_remains_readable(project):
    _, _, path, contract = project
    contract["layers"]["qa"] = "human"
    contract["work_units"].extend([
        {**contract["work_units"][0], "id": "U2", "depends_on": ["U1"]},
        {"id": "QA", "title": "manual QA", "layers": ["qa"], "target_modules": ["manual"],
         "test_contract": "inspect", "depends_on": ["U2"]},
    ])
    write_prd(path, contract)
    validate_execution_contract(parse_prd(path).contract)
    archived = parse_prd(Path(__file__).resolve().parent.parent / "blacksmith-v0-prd.md")
    assert len(archived.contract.work_units) == 11
    with pytest.raises(ContractError, match="at most one"):
        validate_execution_contract(archived.contract)


def install_transport(monkeypatch, calls):
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline-transport-key")
    monkeypatch.setattr("blacksmith.run_context.shutil.which", lambda tool: f"/offline/{tool}")

    async def collect(self, prompt, options, *, emit_findings=False):
        cwd = Path(options.cwd)
        calls.append({"prompt": prompt, "cwd": cwd, "model": options.model,
                      "base": (cwd / "README.md").read_text(),
                      "system_prompt": options.system_prompt})
        if prompt.startswith("Produce a concise"):
            text = "APPROVED_SNAPSHOT_PLAN"
        elif prompt.startswith("You are reviewing a proposed"):
            text = '```json\n{"approved": true, "critique": ""}\n```'
        elif prompt.startswith("Implement this work unit"):
            target = re.search(r"^Target modules: (.+)$", prompt, re.M).group(1)
            allowed = await options.can_use_tool("Write", {"file_path": target}, None)
            assert isinstance(allowed, PermissionResultAllow)
            (cwd / target).write_text("implemented")
            assert "APPROVED_SNAPSHOT_PLAN" in prompt
            text = "implemented"
        elif emit_findings:
            text = '{"verdict": "clean", "findings": []}'
        else:
            pytest.fail("no other paid call was expected")
        return ExecutorResult(text, options.model, False, 1, 0.01, {}, "offline")

    monkeypatch.setattr(Executor, "_collect", collect)


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("critic", [False, True])
def test_real_cli_resume_uses_saved_base_input_and_execution_config(
    project, monkeypatch, capsys, indexed, critic,
):
    root, config_path, prd_path, _ = project
    if indexed:
        config_path.write_text(config_path.read_text() + "[index]\nenabled = true\n")
    if critic:
        config_path.write_text(config_path.read_text() + "[critic]\nenabled = true\n")
    calls = []
    install_transport(monkeypatch, calls)
    from blacksmith.index import create_index_mcp_server
    indexed_paths = []

    def index_server(repo_path, *, exclude=()):
        indexed_paths.append(Path(repo_path))
        return create_index_mcp_server(repo_path, exclude=exclude)

    for module in ("plan", "implement", "review"):
        monkeypatch.setattr(f"blacksmith.nodes.{module}.create_index_mcp_server", index_server)

    def stop_at_plan(payload, values):
        assert payload["gate"] == "plan"
        with pytest.raises(RunContextError, match="run_already_locked"):
            with context_from_state(values).lock():
                pytest.fail("CLI must hold the run lock while awaiting approval")
        raise KeyboardInterrupt("simulated process exit at checkpointed approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop_at_plan)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd_path), "--config", str(config_path), "--thread-id", "frozen"])
    values = saved_values(config_path, "frozen")
    context = context_from_state(values)
    assert len(calls) == 1 + int(critic)
    assert all(call["cwd"] == context.planning_path for call in calls)
    assert not calls[0]["cwd"].stat().st_mode & 0o222
    (root / "README.md").write_text("NEW_SOURCE_COMMIT\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "source changed while waiting")
    prd_path.unlink()
    config_path.write_text('[target]\nrepo_path = "target"\n[models]\n'
                           'implement = "CHANGED_LOCAL_MODEL"\n[limits]\nmax_implement_turns = 1\n')
    original_builder = cli.build_graph_for
    configs = []

    def capture_builder(config, saver):
        configs.append(config)
        return original_builder(config, saver)

    def approve_only_plan(payload, values):
        return payload["gate"] == "plan"

    monkeypatch.setattr(cli, "build_graph_for", capture_builder)
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: approve_only_plan)
    assert cli.main(["resume", "--config", str(config_path), "--thread-id", "frozen"]) == 1
    assert configs[0].models.implement != "CHANGED_LOCAL_MODEL"
    assert configs[0].limits.max_implement_turns == 40
    assert len(calls) == 3 + int(critic)  # no replay of the paid plan or critic
    assert all(call["base"] == "BASE_SENTINEL\n" for call in calls)
    assert all("EXACT_PROSE_SENTINEL" in call["prompt"] + call["system_prompt"] for call in calls)
    if indexed:
        assert indexed_paths == [context.planning_path, context.workspace_path,
                                 context.workspace_path]
    else:
        assert indexed_paths == []
    assert "selected committed HEAD " + context.base_commit in capsys.readouterr().out


def test_cli_thread_lock_denies_before_preflight_or_model_construction(
    project, monkeypatch, capsys,
):
    _, config_path, prd_path, _ = project

    def forbidden(*args, **kwargs):
        pytest.fail("locked invocations must not construct the graph or run Git preflight")

    monkeypatch.setattr(cli, "create_run_context", forbidden)
    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    with thread_lock(BlacksmithConfig.load(config_path), "locked"):
        assert cli.main([str(prd_path), "--config", str(config_path),
                         "--thread-id", "locked"]) == 1
    assert "run_already_locked" in capsys.readouterr().err


def test_real_cli_parallel_resume_forwards_frozen_context_to_distinct_workers(project, monkeypatch):
    root, config_path, prd_path, contract = project
    contract["work_units"].append({
        **contract["work_units"][0], "id": "U2", "target_modules": ["second-result.txt"],
    })
    write_prd(prd_path, contract)
    calls = []
    install_transport(monkeypatch, calls)

    def stop(payload, values):
        raise KeyboardInterrupt("stopped after all plans, before approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd_path), "--config", str(config_path), "--thread-id", "parallel"])
    context = context_from_state(saved_values(config_path, "parallel"))
    assert len(calls) == 2
    assert all(call["cwd"] == context.planning_path for call in calls)
    (root / "README.md").write_text("SOURCE_ADVANCED\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "advance source before parallel resume")
    prd_path.unlink()
    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: (
        lambda payload, values: payload["gate"] == "plan"
    ))
    assert cli.main(["resume", "--config", str(config_path), "--thread-id", "parallel"]) == 1
    assert len(calls) == 6  # two plans, two implementations, two reviews
    workers = [call for call in calls if call["prompt"].startswith("Implement this work unit")]
    assert {call["cwd"] for call in workers} == {context.unit_path("U1"), context.unit_path("U2")}
    assert all(call["base"] == "BASE_SENTINEL\n" for call in calls)
    assert all("EXACT_PROSE_SENTINEL" in call["prompt"] for call in workers)
    final = saved_values(config_path, "parallel")
    assert final["status"] == Status.HALTED and not final.get("pr_url")
    assert {Path(b["clone_path"]) for b in final["unit_outcomes"].values()} == {
        context.unit_path("U1"), context.unit_path("U2"),
    }
    assert (root / "README.md").read_text() == "SOURCE_ADVANCED\n"
    assert not (root / "result.txt").exists() and not (root / "second-result.txt").exists()


@pytest.mark.parametrize("failure", ["manifest", "missing-planning", "wrong-repo", "run-lock"])
def test_cli_resume_integrity_failures_do_not_construct_executor(
    project, monkeypatch, capsys, tmp_path, failure,
):
    root, config_path, prd_path, _ = project
    calls = []
    install_transport(monkeypatch, calls)

    def stop(payload, values):
        raise KeyboardInterrupt("process stopped at plan approval")

    monkeypatch.setattr(cli, "_resolve_approver", lambda args, renderer: stop)
    with pytest.raises(KeyboardInterrupt):
        cli.main([str(prd_path), "--config", str(config_path), "--thread-id", "integrity"])
    context = context_from_state(saved_values(config_path, "integrity"))
    if failure == "manifest":
        (context.directory / "manifest.json").write_text("corrupt")
    elif failure == "missing-planning":
        context.planning_path.rename(context.directory / "preserved-planning")
    elif failure == "wrong-repo":
        config_path.write_text(f'[target]\nrepo_path = "{tmp_path}"\n')

    def forbidden(config, saver):
        pytest.fail("integrity failure must precede model/executor construction")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    args = ["resume", "--config", str(config_path), "--thread-id", "integrity"]
    if failure == "run-lock":
        with context.lock():
            assert cli.main(args) == 1
    else:
        assert cli.main(args) == 1
    assert len(calls) == 1
    assert "resume:" in capsys.readouterr().err
    assert context.directory.is_dir()
    assert git(root, "status", "--porcelain", "--untracked-files=no") == ""


def test_legacy_completed_checkpoint_is_inspectable_and_never_rewritten_on_resume(
    project, monkeypatch, capsys,
):
    _, config_path, _, _ = project
    config = BlacksmithConfig.load(config_path)
    saver = build_checkpointer(config.checkpointer.db_path)
    try:
        graph = StateGraph(BlacksmithState)
        graph.add_node("legacy", lambda state: {"status": Status.DONE})
        graph.add_edge(START, "legacy")
        graph.add_edge("legacy", END)
        compiled = graph.compile(checkpointer=saver)
        graph_config = {"configurable": {"thread_id": "legacy"}}
        compiled.invoke({"prd_path": "archived-prd.md"}, graph_config)
        before = saver.get_tuple(graph_config).checkpoint
        assert compiled.get_state(graph_config).values["status"] == Status.DONE
    finally:
        saver.conn.close()

    def forbidden(config, saver):
        pytest.fail("legacy resume must not construct any executor")

    monkeypatch.setattr(cli, "build_graph_for", forbidden)
    assert cli.main(["resume", "--config", str(config_path), "--thread-id", "legacy"]) == 1
    assert "legacy_checkpoint_requires_manual_recovery" in capsys.readouterr().err
    reopened = build_checkpointer(config.checkpointer.db_path)
    try:
        assert reopened.get_tuple(graph_config).checkpoint == before
    finally:
        reopened.conn.close()
