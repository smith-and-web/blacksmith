"""Real installed SDK protocol and local CLI descendants; never a provider call."""

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from test_run_context import create
from test_run_context import project as project

from blacksmith.budget import BudgetLedger
from blacksmith.executor import Executor, ModelCallStopped
from blacksmith.receipts import call_context_for
from blacksmith.sdk_transport import OwnedCLITransport

_LOCAL_SDK_CONNECT = SubprocessCLITransport.connect


def local_cli(project, monkeypatch, *, model_timeout=1, run_seconds=21600):
    root = project[0].parent
    sentinel, started = root / "late-write", root / "started.json"
    cli = root / "offline-claude"
    cli.write_text('''#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
if '-v' in sys.argv:
    print('2.1.0 (Claude Code)')
    sys.exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
for line in sys.stdin:
    item = json.loads(line)
    if item['type'] == 'control_request':
        assert item['request']['subtype'] == 'initialize'
        print(json.dumps({'type':'control_response', 'response':{
            'subtype':'success', 'request_id':item['request_id'], 'response':{}}}), flush=True)
    elif item['type'] == 'user':
        child = subprocess.Popen([sys.executable, '-c',
            "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "time.sleep(9); pathlib.Path(" + repr(os.environ['OFFLINE_SENTINEL'])
            + ").write_text('late')"])
        with open(os.environ['OFFLINE_STARTED'], 'w') as out:
            json.dump({'leader':os.getpid(),'child':child.pid,'pgid':os.getpgrp()}, out)
        print(json.dumps({'type':'assistant','message':{'model':'offline',
            'content':[{'type':'text','text':'partial before timeout'}]}}), flush=True)
    else:
        raise AssertionError(item)
while True:
    time.sleep(1)
''')
    cli.chmod(0o700)

    async def only_local(self):
        assert Path(self._cli_path) == cli
        await _LOCAL_SDK_CONNECT(self)
    monkeypatch.setattr(OwnedCLITransport, "connect", only_local)
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    project[1].write_text(project[1].read_text() + f"[limits]\nmodel_timeout_s={model_timeout}\n"
                           f"max_run_seconds={run_seconds}\n")
    context = create(project)
    executor = Executor(context.execution_config())
    build = executor.build_options

    def options(**kwargs):
        actual = build(**kwargs)
        return replace(actual, cli_path=str(cli), env={**actual.env,
            "OFFLINE_SENTINEL": str(sentinel), "OFFLINE_STARTED": str(started)})
    monkeypatch.setattr(executor, "build_options", options)
    return context, executor, sentinel, started


def assert_stopped(started, sentinel):
    pids = json.loads(started.read_text())
    assert pids['pgid'] == pids['leader']
    with pytest.raises(ProcessLookupError):
        os.kill(pids['leader'], 0)  # The SDK leader was reaped, not merely signalled.
    time.sleep(4.25)  # Beyond the child's delayed write, after TERM grace and KILL.
    assert not sentinel.exists()


@pytest.mark.parametrize("guarded", [False, True])
def test_both_real_sdk_entry_paths_timeout_and_kill_descendants(
    project, monkeypatch, guarded,
):
    context, executor, sentinel, started = local_cli(project, monkeypatch)
    call = call_context_for(context.initial_state(), unit_id="U1", attempt_id="timeout")
    with pytest.raises(ModelCallStopped, match="timeout") as stopped:
        executor.run_plan("offline protocol", call_context=call,
                          can_use_tool=(lambda *args: None) if guarded else None)
    assert stopped.value.started and stopped.value.result.num_turns == 1
    assert stopped.value.result.terminal_received is False
    assert Path(stopped.value.result.transcript_path).is_file()
    assert "partial before timeout" in Path(stopped.value.result.transcript_path).read_text()
    operation = context.journal().get(call.key("model.plan"))
    assert operation.status == "unknown"
    assert operation.outcome['result']['error_kind'] == "timeout"
    balance = BudgetLedger(context).balance()
    assert balance.charged_usd == 5 and balance.accounting_incomplete
    assert_stopped(started, sentinel)


def test_raw_asyncio_cancellation_finishes_owned_child_cleanup(project, monkeypatch):
    _, executor, sentinel, started = local_cli(project, monkeypatch)

    async def cancel():
        options = executor.build_options(model="offline", can_use_tool=lambda *args: None)
        task = asyncio.create_task(executor._collect("offline cancellation", options))
        until = time.monotonic() + 3
        while not started.exists() and time.monotonic() < until:
            await asyncio.sleep(.01)
        assert started.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())
    assert_stopped(started, sentinel)


def test_remaining_run_deadline_bounds_actual_sdk_before_longer_model_timeout(project, monkeypatch):
    context, executor, sentinel, started = local_cli(
        project, monkeypatch, model_timeout=100, run_seconds=2)
    call = call_context_for(context.initial_state(), unit_id='U1', attempt_id='run-deadline')
    with pytest.raises(ModelCallStopped, match='run_deadline'):
        executor.run_plan('bounded by persisted remaining execution', call_context=call)
    assert context.journal().get(call.key('model.plan')).status == 'unknown'
    assert_stopped(started, sentinel)
