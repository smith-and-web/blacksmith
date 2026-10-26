"""Disabled features refuse before effects; historical evidence stays inspectable."""

import json
from types import SimpleNamespace

import pytest
from langgraph.graph import END, START, StateGraph
from test_run_context import project as project
from test_run_context import saved_values

from blacksmith import cli
from blacksmith.budget import BudgetLedger
from blacksmith.commands import command_scope
from blacksmith.config import BlacksmithConfig, ConfigError, SandboxConfig
from blacksmith.contract import parse_prd
from blacksmith.executor import Executor
from blacksmith.graph import UnitDeps, build_checkpointer, build_graph, compile_graph
from blacksmith.receipts import CallContext
from blacksmith.recovery import capture
from blacksmith.run_context import create_run_context
from blacksmith.state import BlacksmithState, Status
from blacksmith.unit_graph import build_unit_graph


def enabled_config(project):
    config = BlacksmithConfig.load(project[1])
    return config.model_copy(update={"sandbox": SandboxConfig(enabled=True)})


def forbidden(*args, **kwargs):
    pytest.fail("unsupported sandbox reached a downstream dependency or effect")


def test_enabled_runtime_config_load_refuses_without_resolving_target(project, monkeypatch):
    project[1].write_text(project[1].read_text() + "[sandbox]\nenabled=true\n")
    monkeypatch.setattr(BlacksmithConfig, "resolve_repo_path", forbidden)
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        BlacksmithConfig.load(project[1])


def test_enabled_graph_factory_refuses_before_executor_store_clone_or_topology(
    project, monkeypatch,
):
    config = enabled_config(project)
    for name in ["Executor", "CloneManager", "build_store", "compile_graph"]:
        monkeypatch.setattr(cli, name, forbidden)
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        cli.build_graph_for(config, None)


def test_enabled_executor_and_fresh_preflight_refuse_before_native_or_credentials(
    project, monkeypatch,
):
    config = enabled_config(project)
    monkeypatch.setattr(BlacksmithConfig, "resolve_api_key", forbidden)
    monkeypatch.setattr("blacksmith.run_context.git_read", forbidden)
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        Executor(config)
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        create_run_context(config, parse_prd(project[2]), "unsupported", required_tools=())
    assert not (project[1].parent / ".blacksmith").exists()


@pytest.mark.parametrize("entrypoint", ["build", "compile", "unit"])
def test_library_graph_rejects_enabled_manager_before_constructing_any_nodes(
    monkeypatch, entrypoint,
):
    manager = SimpleNamespace(config=SimpleNamespace(enabled=True))
    monkeypatch.setattr("blacksmith.graph.StateGraph", forbidden)
    monkeypatch.setattr("blacksmith.unit_graph.StateGraph", forbidden)
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        if entrypoint == "compile":
            compile_graph(None, sandbox=manager)
        elif entrypoint == "build":
            build_graph(sandbox=manager)
        else:
            build_unit_graph(UnitDeps(sandbox=manager))


def historical_enabled_run(project, monkeypatch):
    """Real inputs/Git/journal/checkpoint/capture under the former permissive policy.

    No container or paid transport is started: this is a pre-workspace historical
    checkpoint. Only the new execution-policy check is temporarily bypassed while
    constructing its old-format input/evidence; ordinary parsing remains strict.
    """
    config = enabled_config(project)
    thread_id = "historical-sandbox"
    with monkeypatch.context() as old:
        old.setattr(BlacksmithConfig, "require_supported_execution", lambda self: None)
        context = create_run_context(config, parse_prd(project[2]), thread_id,
                                     required_tools=("git",))
        saver = build_checkpointer(config.checkpointer.db_path)
        try:
            graph = StateGraph(BlacksmithState)
            graph.add_node("persist_historical", lambda state: {})
            graph.add_edge(START, "persist_historical")
            graph.add_edge("persist_historical", END)
            compiled = graph.compile(checkpointer=saver)
            compiled.invoke({"run_context": context.model_dump(), "state_version": 2,
                             "recovery_version": 1,
                             "run_id": context.run_id, "prd_path": str(project[2]),
                             "status": Status.HALTED},
                            {"configurable": {"thread_id": thread_id}}, durability="sync")
            capture(context, saver, reason="historical sandbox disabled before workspace")
        finally:
            saver.conn.close()
    return context, thread_id


def test_historical_enabled_run_inspection_succeeds_but_resume_native_and_sdk_refuse(
    project, monkeypatch, capsys,
):
    context, thread_id = historical_enabled_run(project, monkeypatch)
    context.verify_inputs()
    assert context.execution_config().sandbox.enabled is True
    assert BudgetLedger(context).balance().remaining_usd == 50
    before = saved_values(project[1], thread_id)
    operations, events = context.journal().operations(), context.journal().events()
    monkeypatch.setattr(cli, "Executor", forbidden)
    monkeypatch.setattr("blacksmith.run_context.git_read", forbidden)
    args = ["--config", str(project[1]), "--thread-id", thread_id]
    assert cli.main(["resume", *args]) == 1
    assert "sandbox_unsupported_until_hardened" in capsys.readouterr().err
    assert cli.main(["recovery", "inspect", *args]) == 0
    output = capsys.readouterr()
    assert '"valid": true' in output.out
    assert "sandbox_unsupported_until_hardened" not in output.err
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        with command_scope(context):
            forbidden()
    call = CallContext(str(context.journal().path), context.run_id,
                       context.journal().context_hash, "__run__", "legacy-plan",
                       run_context=context.model_dump())
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        BudgetLedger.for_call(call)
    with pytest.raises(ConfigError, match="sandbox_unsupported_until_hardened"):
        capture(context, None, reason="unsupported capture must create no intent")
    assert saved_values(project[1], thread_id) == before
    assert context.journal().operations() == operations
    assert context.journal().events() == events


def test_saved_reserved_feature_settings_are_inspected_without_enabling_or_mutating_them(
    project, monkeypatch, capsys,
):
    project[1].write_text(project[1].read_text() + "[respond]\nmax_attempts=7\n")
    context, thread_id = historical_enabled_run(project, monkeypatch)
    before = saved_values(project[1], thread_id)
    operations, events = context.journal().operations(), context.journal().events()
    retained = {name: (context.directory / name).read_bytes() for name in (
        "manifest.json", "runtime-config.json", "input.prd.md", "trusted-blacksmith.toml")}
    # Inspection must show the immutable saved setting, not this new local choice.
    project[1].write_text(project[1].read_text().replace("max_attempts=7", "max_attempts=2"))
    monkeypatch.setattr(cli, "Executor", forbidden)
    monkeypatch.setattr("blacksmith.run_context.git_read", forbidden)
    assert cli.main(["recovery", "inspect", "--thread-id", thread_id,
                     "--config", str(project[1])]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True
    assert result["disabled_features"] == {
        "respond": {"supported": False, "status": "unsupported_until_hardened",
                    "reserved_max_attempts": 7},
        "sandbox": {"supported": False, "status": "unsupported_until_hardened",
                    "enabled_in_saved_config": True}}
    assert saved_values(project[1], thread_id) == before
    assert context.journal().operations() == operations
    assert context.journal().events() == events
    assert {name: (context.directory / name).read_bytes() for name in retained} == retained


def test_disabled_production_graph_never_advertises_sandbox_tool(project, monkeypatch):
    config = BlacksmithConfig.load(project[1])
    monkeypatch.setattr("blacksmith.sandbox.SandboxManager.start", forbidden)
    monkeypatch.setattr("blacksmith.sandbox.SandboxManager.stop", forbidden)
    monkeypatch.setattr("blacksmith.sandbox.create_sandbox_mcp_server", forbidden)
    # Construction itself must remain available with the normal disabled setting.
    with monkeypatch.context() as offline:
        offline.setattr(cli, "Executor", lambda config: None)
        offline.setattr(cli, "build_store", lambda path: None)
        graph = cli.build_graph_for(config, None)
    assert graph is not None and config.sandbox.enabled is False
