"""Physical owner exit and critical timer failure retain bounded clock evidence."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_run_context import create
from test_run_context import project as project
from test_sdk_deadlines import assert_stopped, local_cli

from blacksmith.budget import BudgetLedger
from blacksmith.receipts import ReceiptError, call_context_for
from blacksmith.run_clock import used

CHILD = '''
import json,os,sys,time
from pathlib import Path
from blacksmith.run_context import RunContext
from blacksmith.run_clock import execution_scope
context=RunContext.model_validate(json.loads(Path(sys.argv[1],'manifest.json').read_text())['context'])
with context.lock(), execution_scope(context) as clock:
    Path(sys.argv[1],sys.argv[2]+'.json').write_text(json.dumps({
        'remaining':clock.remaining,'incomplete':clock.incomplete,'pid':os.getpid()}))
    time.sleep(.05)
    if sys.argv[2]=='crash':
        os._exit(121)
'''


def test_fresh_process_resume_conservatively_charges_crashed_slice_without_offline_pause(project):
    project[1].write_text(project[1].read_text() + '[limits]\nmax_run_seconds=20\n')
    context = create(project)
    baseline = used(context)[0]
    process = subprocess.run([sys.executable, '-c', CHILD, str(context.directory), 'crash'],
                             capture_output=True, text=True, timeout=10)
    assert process.returncode == 121, process.stdout + process.stderr
    owner = json.loads((context.directory / 'crash.json').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(owner['pid'], 0)
    pending = [r for r in context.journal().operations()
               if r.key.kind == 'clock.slice' and r.status == 'intent']
    assert len(pending) == 1 and pending[0].intent['reserved_seconds'] == 5
    assert used(context) == (baseline + 10, True)
    time.sleep(.2)  # An offline gap never becomes execution time.
    assert used(context) == (baseline + 10, True)
    resumed = subprocess.run([sys.executable, '-c', CHILD, str(context.directory), 'resumed'],
                             capture_output=True, text=True, timeout=10)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    observed = json.loads((context.directory / 'resumed.json').read_text())
    expected = 20 - baseline - 10
    assert expected - .2 < observed['remaining'] <= expected and observed['incomplete']
    assert context.journal().get(pending[0].key) == pending[0]
    assert .04 <= used(context)[0] - baseline - 10 < .3


@pytest.mark.parametrize('phase', ['finish', 'reserve'])
def test_critical_timer_failure_stops_sdk_writers_before_return_and_preserves_partial_receipt(
    project, monkeypatch, phase,
):
    context, executor, sentinel, started = local_cli(
        project, monkeypatch, model_timeout=100, run_seconds=50)
    cli = project[0].parent / 'offline-claude'
    cli.write_text(cli.read_text().replace('time.sleep(9)', 'time.sleep(14)'))
    collect = executor._collect

    async def install_failure():
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        assert started.exists()
        action = 'UPDATE' if phase == 'finish' else 'INSERT'
        with sqlite3.connect(context.journal().path) as conn:
            conn.execute(f"CREATE TRIGGER fail_clock BEFORE {action} ON operations "
                         "WHEN NEW.kind='clock.slice' BEGIN SELECT RAISE(ABORT,'clock full'); END")

    async def collected(prompt, options, *, emit_findings=False):
        injector = asyncio.create_task(install_failure())
        try:
            return await collect(prompt, options, emit_findings=emit_findings)
        finally:
            await injector
    monkeypatch.setattr(executor, '_collect', collected)
    call = call_context_for(context.initial_state(), unit_id='U1', attempt_id='critical-clock')
    with pytest.raises(ReceiptError, match='critical journal'):
        executor.run_plan('clock storage cannot silently disable deadline', call_context=call)
    row = context.journal().get(call.key('model.plan'))
    assert row.status == 'interrupted' and row.outcome['result']['num_turns'] == 1
    assert Path(row.outcome['result']['transcript_path']).is_file()
    assert BudgetLedger(context).balance().charged_usd == 5
    assert used(context)[1]
    pending = [r for r in context.journal().operations()
               if r.key.kind == 'clock.slice' and r.status == 'intent']
    assert len(pending) == (2 if phase == 'finish' else 1)
    assert_stopped(started, sentinel)
