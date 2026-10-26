"""Real SDK permission round-trips against a local CLI, never a provider."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from test_run_context import create
from test_run_context import project as project

from blacksmith.executor import Executor
from blacksmith.nodes.implement import make_pre_edit_guard
from blacksmith.path_policy import compile_path_rules
from blacksmith.receipts import call_context_for
from blacksmith.sdk_transport import OwnedCLITransport

_LOCAL_SDK_CONNECT = SubprocessCLITransport.connect

PERMISSION_CLI = '''
import json, os, sys
from pathlib import Path

if '-v' in sys.argv:
    print('2.1.0 (Claude Code)')
    sys.exit(0)

def emit(item):
    print(json.dumps(item), flush=True)

requests = json.loads(os.environ['OFFLINE_REQUESTS'])
decisions = []
for line in sys.stdin:
    item = json.loads(line)
    if item['type'] == 'control_request':
        assert item['request']['subtype'] == 'initialize'
        emit({'type': 'control_response', 'response': {
            'subtype': 'success', 'request_id': item['request_id'], 'response': {}}})
    elif item['type'] == 'user':
        # Behave like a noninteractive CLI: no routing flag means no Python grant.
        if ('--permission-prompt-tool' not in sys.argv or
                sys.argv[sys.argv.index('--permission-prompt-tool') + 1] != 'stdio'):
            raise RuntimeError('missing stdio permission routing')
        for index, request in enumerate(requests):
            emit({'type': 'control_request', 'request_id': str(index), 'request': {
                'subtype': 'can_use_tool', 'tool_name': request['tool'],
                'input': request['input'], 'tool_use_id': 'tool-' + str(index)}})
            response = json.loads(next(sys.stdin))
            assert response['type'] == 'control_response'
            assert response['response']['request_id'] == str(index)
            assert response['response']['subtype'] == 'success'
            decision = response['response']['response']
            decisions.append(decision)
            if decision['behavior'] == 'allow':
                payload = decision['updatedInput']
                target = Path(payload['file_path'])
                if request['tool'] == 'Write':
                    target.write_text(payload['content'])
                elif request['tool'] == 'Edit':
                    target.write_text(target.read_text().replace(
                        payload['old_string'], payload['new_string']))
                else:
                    raise AssertionError('unexpected tool grant')
        Path(os.environ['OFFLINE_PROOF']).write_text(json.dumps({
            'argv': sys.argv, 'decisions': decisions}))
        emit({'type': 'result', 'subtype': 'success', 'is_error': False,
            'duration_ms': 1, 'duration_api_ms': 1, 'num_turns': 1,
            'session_id': 'offline-permissions', 'result': 'permission round-trip complete',
            'total_cost_usd': 0.01, 'usage': {}})
        sys.exit(0)
    else:
        raise AssertionError(item)
'''


def test_real_executor_sdk_and_owned_cli_allow_writes_but_deny_unsafe_targets(
    project, monkeypatch,
):
    root = project[0]
    protected = root / 'protected'
    protected.mkdir()
    sentinel = protected / 'sentinel.txt'
    sentinel.write_text('protected baseline')
    outside = root.parent / 'outside.txt'
    outside.write_text('outside baseline')
    (root / 'escape').symlink_to(root.parent, target_is_directory=True)
    config_before = (root / 'blacksmith.toml').read_bytes()
    git_config_before = (root / '.git/config').read_bytes()
    requests = [
        {'tool': 'Write', 'input': {'file_path': 'module.py', 'content': 'value = 1\n'}},
        {'tool': 'Edit', 'input': {'file_path': str(root / 'module.py'),
                                 'old_string': '1', 'new_string': '2'}},
        {'tool': 'Write', 'input': {'file_path': str(sentinel), 'content': 'unsafe'}},
        {'tool': 'Write', 'input': {'file_path': 'blacksmith.toml', 'content': 'unsafe'}},
        {'tool': 'Write', 'input': {'file_path': '.git/config', 'content': 'unsafe'}},
        {'tool': 'Edit', 'input': {'file_path': '../outside.txt',
                                 'old_string': 'baseline', 'new_string': 'unsafe'}},
        {'tool': 'Write', 'input': {'file_path': 'escape/outside.txt', 'content': 'unsafe'}},
        {'tool': 'NotebookEdit', 'input': {'notebook_path': str(outside)}},
        {'tool': 'Bash', 'input': {'command': 'touch forbidden'}},
    ]
    proof = root.parent / 'permission-proof.json'
    cli = root.parent / 'offline-permission-cli'
    cli.write_text(f'#!{sys.executable}\n' + PERMISSION_CLI)
    cli.chmod(0o700)

    async def only_local(self):
        assert Path(self._cli_path) == cli
        await _LOCAL_SDK_CONNECT(self)

    monkeypatch.setattr(OwnedCLITransport, 'connect', only_local)
    monkeypatch.setenv('BLACKSMITH_ANTHROPIC_API_KEY', 'unused-offline')
    context = create(project)
    executor = Executor(context.execution_config())
    build = executor.build_options
    client_options = []

    def options(**kwargs):
        actual = replace(build(**kwargs), cli_path=str(cli))
        actual = replace(actual, env={**actual.env,
            'OFFLINE_REQUESTS': json.dumps(requests), 'OFFLINE_PROOF': str(proof)})
        client_options.append(actual)
        return actual

    monkeypatch.setattr(executor, 'build_options', options)
    guard = make_pre_edit_guard(compile_path_rules(['path:protected']), worktree_root=root)
    result = executor.run_implement(
        'offline permission protocol',
        call_context=call_context_for(
            context.initial_state(), unit_id='U1', attempt_id='permissions'),
        cwd=root, max_turns=40, permission_mode='default', can_use_tool=guard,
        allowed_tools=['Read', 'Glob', 'Grep'],
        disallowed_tools=['Bash', 'BashOutput', 'KillShell', 'Agent', 'Task'],
        tools=['Read', 'Glob', 'Grep', 'Write', 'Edit', 'NotebookEdit'],
    )
    assert result.terminal_received and not result.is_error
    assert result.text == 'permission round-trip complete'
    assert (root / 'module.py').read_text() == 'value = 2\n'
    assert sentinel.read_text() == 'protected baseline'
    assert outside.read_text() == 'outside baseline'
    assert (root / 'blacksmith.toml').read_bytes() == config_before
    assert (root / '.git/config').read_bytes() == git_config_before
    assert not (root / 'forbidden').exists()
    saved = json.loads(proof.read_text())
    assert [d['behavior'] for d in saved['decisions']] == ['allow', 'allow'] + ['deny'] * 7
    assert saved['decisions'][0]['updatedInput'] == requests[0]['input']
    assert all(d['message'].startswith('BLOCKED:') for d in saved['decisions'][2:])
    assert len(guard.blocked) == 7
    argv = saved['argv']
    assert argv.count('--permission-prompt-tool') == 1
    assert argv[argv.index('--permission-prompt-tool') + 1] == 'stdio'
    assert argv[argv.index('--permission-mode') + 1] == 'default'
    assert set(argv[argv.index('--allowedTools') + 1].split(',')) == {'Read', 'Glob', 'Grep'}
    assert '--setting-sources=' in argv and '--strict-mcp-config' in argv
    assert len(client_options) == 1 and client_options[0].permission_prompt_tool_name is None


@pytest.mark.parametrize('permission_tool', [None, 'explicit-tool'])
def test_unguarded_transport_keeps_existing_permission_options(permission_tool):
    options = ClaudeAgentOptions(cli_path='/unused-offline-cli',
                                 permission_prompt_tool_name=permission_tool)
    transport = OwnedCLITransport(prompt='', options=options)
    assert transport._options is options
    command = transport._build_command()
    if permission_tool is None:
        assert '--permission-prompt-tool' not in command
    else:
        assert command[command.index('--permission-prompt-tool') + 1] == permission_tool
