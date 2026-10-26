"""Real CLI/fan-out/SDK children stop before failure capture on error or Ctrl-C."""

import json
import os
import signal
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from test_integration_integrity import configure
from test_run_context import git, saved_values
from test_run_context import project as project
from test_sdk_deadlines import _LOCAL_SDK_CONNECT

from blacksmith import cli
from blacksmith.executor import Executor
from blacksmith.receipts import ReceiptError
from blacksmith.run_context import context_from_state
from blacksmith.sdk_transport import OwnedCLITransport

FAKE = '''#!/usr/bin/env python3
import json,os,re,signal,subprocess,sys,time
from pathlib import Path
if '-v' in sys.argv:
    print('2.1.0 (Claude Code)')
    sys.exit(0)
assert 0 < float(sys.argv[sys.argv.index('--max-budget-usd')+1]) <= 5
root=Path(os.environ['OFFLINE_PROOF'])
for line in sys.stdin:
    item=json.loads(line)
    if item['type']=='control_request':
        assert item['request']['subtype']=='initialize'
        print(json.dumps({'type':'control_response','response':{
            'subtype':'success','request_id':item['request_id'],'response':{}}}),flush=True)
    elif item['type']=='user':
        prompt=item['message']['content']
        assert isinstance(prompt,str)
        unit=re.search(r'^Unit ([^:]+):',prompt,re.M).group(1)
        if prompt.startswith('Produce a concise'):
            print(json.dumps({'type':'result','subtype':'success','is_error':False,
                'duration_ms':1,'duration_api_ms':1,'num_turns':1,'session_id':'plan-'+unit,
                'result':'EXACT_APPROVED_PLAN_'+unit,'total_cost_usd':.01,'usage':{}}),flush=True)
            sys.exit(0)
        assert prompt.startswith('Implement this work unit')
        target=re.search(r'^Target modules: (.+)$',prompt,re.M).group(1)
        Path(target).write_text('partial '+unit)
        sentinel=root/(unit+'-late')
        child=subprocess.Popen([sys.executable,'-c',
            "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "time.sleep(14); pathlib.Path("+repr(str(sentinel))+").write_text('late')"],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        (root/(unit+'.json')).write_text(json.dumps({'leader':os.getpid(),'child':child.pid,
            'pgid':os.getpgrp(),'started':time.monotonic()}))
        print(json.dumps({'type':'assistant','message':{'model':'offline',
            'content':[{'type':'text','text':'partial '+unit}]}}),flush=True)
        if unit=='A' and os.environ['OFFLINE_MODE'] in {'worker_error','scope_entry_error'}:
            while not (root/'B.json').exists():
                time.sleep(.01)
            if os.environ['OFFLINE_MODE']=='scope_entry_error':
                print(json.dumps({'type':'result','subtype':'success','is_error':False,
                    'duration_ms':1,'duration_api_ms':1,'num_turns':1,'session_id':'impl-A',
                    'result':'implemented','total_cost_usd':.01,'usage':{}}),flush=True)
                sys.exit(0)
            sys.exit(97)
        while True:
            time.sleep(1)
    else:
        raise AssertionError(item)
'''


@pytest.mark.parametrize('mode', ['worker_error', 'sigint', 'sigterm', 'scope_entry_error'])
def test_parallel_sdk_writers_are_stopped_before_cli_recovery_capture(project, monkeypatch, mode):
    configure(project)
    project[1].write_text(project[1].read_text() + '[limits]\nmodel_timeout_s=20\n'
                           'max_run_seconds=30\n')
    proof = project[0].parent / 'sdk-proof'
    proof.mkdir()
    fake = proof / 'offline-claude'
    fake.write_text(FAKE)
    fake.chmod(0o700)
    monkeypatch.setenv('BLACKSMITH_ANTHROPIC_API_KEY', 'unused-offline')
    monkeypatch.setattr('blacksmith.run_context.shutil.which', lambda tool: sys.executable)
    build = Executor.build_options

    def options(self, **kwargs):
        actual = build(self, **kwargs)
        assert actual.strict_mcp_config and actual.setting_sources == []
        if actual.can_use_tool is not None:
            assert {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}.isdisjoint(actual.allowed_tools)
        return replace(actual, cli_path=str(fake), env={**actual.env,
            'OFFLINE_PROOF': str(proof), 'OFFLINE_MODE': mode})

    async def only_local(self):
        assert Path(self._cli_path) == fake
        await _LOCAL_SDK_CONNECT(self)
    monkeypatch.setattr(Executor, 'build_options', options)
    monkeypatch.setattr(OwnedCLITransport, 'connect', only_local)
    cleanup = OwnedCLITransport.close

    async def traced_cleanup(self):
        try:
            await cleanup(self)
        except ReceiptError:
            with (proof / 'cleanup-failure.txt').open('a') as out:
                out.write(traceback.format_exc())
            raise
    monkeypatch.setattr(OwnedCLITransport, 'close', traced_cleanup)
    if mode == 'scope_entry_error':
        import blacksmith.unit_graph as unit_graph
        scope = unit_graph.command_scope_for_state

        @contextmanager
        def failed_scope(state, stage):
            if state['selected_unit'].id == 'A' and stage == 'audit_commit':
                raise ReceiptError('injected critical scope-open failure')
            with scope(state, stage) as actual:
                yield actual
        monkeypatch.setattr(unit_graph, 'command_scope_for_state', failed_scope)
    compile_graph = cli.compile_graph
    effects = []

    def forbidden(argv, cwd=None):
        effects.append(argv)
        pytest.fail('publication query/effect after cancelled implementation')

    def compile(checkpointer, **kwargs):
        return compile_graph(checkpointer, **{**kwargs, 'pr_runner': forbidden})
    monkeypatch.setattr(cli, 'compile_graph', compile)
    sent = []

    def interrupt():
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if (proof / 'A.json').exists() and (proof / 'B.json').exists():
                sent.append(True)
                os.kill(os.getpid(), signal.SIGINT if mode == 'sigint' else signal.SIGTERM)
                return
            time.sleep(.01)
    sender = threading.Thread(target=interrupt) if mode in {'sigint', 'sigterm'} else None
    if sender:
        sender.start()
    started = time.monotonic()
    args = [str(project[2]), '--config', str(project[1]), '--thread-id', 'cancel-workers',
            '--approve', 'plan']
    try:
        if mode == 'sigint':
            with pytest.raises(KeyboardInterrupt):
                cli.main(args)
        else:
            assert cli.main(args) == 1
        if sender:
            assert sent == [True]
    finally:
        if sender:
            sender.join(timeout=1)
    assert time.monotonic() - started < 18
    state = saved_values(project[1], 'cancel-workers')
    context = context_from_state(state)
    models = [r for r in context.journal().operations() if r.key.kind == 'model.implement']
    assert len(models) == 2
    if mode == 'scope_entry_error':
        by_unit = {r.key.unit_id: r for r in models}
        assert by_unit['A'].status == 'completed'
        assert by_unit['A'].outcome['result']['cost_usd'] == .01
        assert by_unit['B'].status in {'unknown', 'interrupted'}
    else:
        assert all(r.status in {'unknown', 'interrupted'} for r in models)
    assert not state.get('pr_url') and effects == []
    assert any((context.directory / 'recovery').glob('*/manifest.json'))
    owners = [json.loads((proof / (unit + '.json')).read_text()) for unit in ('A', 'B')]
    for owner in owners:
        assert owner['leader'] == owner['pgid']
        with pytest.raises(ProcessLookupError):
            os.kill(owner['leader'], 0)
    # Observe beyond both delayed writes after CLI/capture returned, not just an
    # instantaneous absence check while an orphan could still be alive.
    remaining = max(p['started'] for p in owners) + 14.3 - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    assert not (proof / 'A-late').exists() and not (proof / 'B-late').exists()
    assert all((context.unit_path(unit) / (unit.lower() + '.txt')).read_text() == 'partial ' + unit
               for unit in ('A', 'B'))
    assert git(project[0], 'status', '--porcelain') == ''
