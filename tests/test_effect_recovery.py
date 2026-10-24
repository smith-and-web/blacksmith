"""Real process exits across sealed gates, formatter amends and staged commits."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_integration_integrity import stop_at_pr
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.executor import Executor
from blacksmith.run_context import context_from_state

CRASH = """
import os, sys
sys.path.insert(0, sys.argv[1])
from pytest import MonkeyPatch
from test_run_context import install_transport
from blacksmith import cli, verification
import blacksmith.nodes.pr as pr
from blacksmith.receipts import OperationJournal
import blacksmith.gate as gate
import blacksmith.nodes.implement as impl
mp=MonkeyPatch()
install_transport(mp, [])
# Every child has a publication guard even if a crash injection fails to fire.
def no_publication(*args, **kwargs):
    raise AssertionError('publication forbidden in recovery drill')
pr.open_pull_request=no_publication
mode=sys.argv[4]
finish=OperationJournal.finish
def fail_finish(self, key, *args, **kwargs):
    if (mode=='gate_outcome' and key.kind.startswith('gate.unit:')
        or mode=='formatter_outcome' and key.kind.startswith('git.fix:')):
        os._exit(97)
    return finish(self, key, *args, **kwargs)
OperationJournal.finish=fail_finish
write=verification.atomic_write
def fail_write(path, text):
    if mode=='gate_artifact' and path.parent.name=='gates':
        os._exit(97)
    return write(path, text)
verification.atomic_write=fail_write
amend=verification._amend_if_changed
amended_count=0
def fail_amend(path, *, before_amend=None):
    global amended_count
    result=amend(path, before_amend=before_amend)
    if result:
        amended_count += 1
    if (mode=='formatter_amend' and result
        and amended_count==int(sys.argv[5] if len(sys.argv)>5 else 1)):
        os._exit(97)
    return result
verification._amend_if_changed=fail_amend
run=gate.run_command
def fail_run(argv, *args, **kwargs):
    if mode=='formatter_staged' and argv[3:5]==['commit','--amend']:
        os._exit(97)
    return run(argv, *args, **kwargs)
gate.run_command=fail_run
impl_run=impl.run_command
def fail_impl_run(argv, *args, **kwargs):
    if mode=='commit_staged' and argv[3]=='commit':
        os._exit(97)
    return impl_run(argv, *args, **kwargs)
impl.run_command=fail_impl_run
cli.main([sys.argv[2], '--config', sys.argv[3], '--thread-id', 'effect', '--approve', 'plan'])
"""


@pytest.mark.parametrize(
    "mode",
    [
        "gate_outcome",
        "gate_artifact",
        "formatter_outcome",
        "formatter_amend",
        "formatter_staged",
        "commit_staged",
    ],
)
def test_effect_recovery_after_real_exit_does_not_repeat_model_or_formatter(
    project,
    monkeypatch,
    mode,
):
    root, config, prd, _ = project
    fix = (
        "mkdir -p .checks; printf f >> .checks/fix-count; "
        "python3 -c \"from pathlib import Path; Path('result.txt').write_text('FORMATTED')\""
    )
    test = "mkdir -p .checks; printf g >> .checks/gate-count"
    (root / "blacksmith.toml").write_text(
        "fix_cmd=" + json.dumps(fix) + "\ntest_cmd=" + json.dumps(test) + "\n"
    )
    (root / ".gitignore").write_text(".checks/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "recovery verifier")
    config.write_text(config.read_text() + "[review]\nenabled=false\n")
    child = subprocess.run(
        [sys.executable, "-c", CRASH, str(Path(__file__).parent), str(prd), str(config), mode],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert child.returncode == 97, child.stderr
    before = saved_values(config, "effect")
    context = context_from_state(before)
    head = git(context.workspace_path, "rev-parse", "HEAD")
    model_calls = [o for o in context.journal().operations() if o.key.kind.startswith("model.")]
    assert len(model_calls) == 2 and all(o.status == "completed" for o in model_calls)

    async def forbidden(*args, **kwargs):
        pytest.fail("recovery must not repeat a completed paid call")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", forbidden)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(config), "--thread-id", "effect"])
    after = saved_values(config, "effect")
    assert after["final_gate_receipt"].outcome == "passed"
    assert (context.workspace_path / "result.txt").read_text() == "FORMATTED"
    assert (context.workspace_path / ".checks/fix-count").read_text() == "f"
    assert (context.workspace_path / ".checks/gate-count").read_text() == "gg"
    assert git(context.workspace_path, "rev-list", "--count", context.base_commit + "..HEAD") == "1"
    if mode not in {"formatter_staged", "commit_staged"}:
        assert git(context.workspace_path, "rev-parse", "HEAD") == head
    assert len(after["cost_events"]) == 2
    assert [
        o for o in context.journal().operations() if o.key.kind.startswith("model.")
    ] == model_calls
    assert not after.get("pr_url")


@pytest.mark.parametrize("first_fails", [False, True])
def test_multiple_formatter_steps_reuse_completed_commands_and_keep_clean_failure_status(
    project,
    monkeypatch,
    first_fails,
):
    from test_run_context import write_prd

    root, config, prd, contract = project
    contract["layers"] = {"first": "auto", "second": "auto"}
    contract["work_units"][0]["layers"] = ["first", "second"]
    write_prd(prd, contract)
    first = "mkdir -p .checks; printf a >> .checks/a; " + (
        "false"
        if first_fails
        else "python3 -c \"from pathlib import Path; Path('result.txt').write_text('FIRST')\""
    )
    second = (
        "mkdir -p .checks; printf b >> .checks/b; "
        "python3 -c \"from pathlib import Path; Path('result.txt').write_text('SECOND')\""
    )
    (root / "blacksmith.toml").write_text(
        'test_cmd="true"\n[layers.first]\nfix_cmd='
        + json.dumps(first)
        + "\n[layers.second]\nfix_cmd="
        + json.dumps(second)
        + "\n"
    )
    (root / ".gitignore").write_text(".checks/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "two formatter steps")
    config.write_text(config.read_text() + "[review]\nenabled=false\n")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH,
            str(Path(__file__).parent),
            str(prd),
            str(config),
            "formatter_amend",
            "1" if first_fails else "2",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 97, child.stderr
    before = saved_values(config, "effect")
    context = context_from_state(before)
    head = git(context.workspace_path, "rev-parse", "HEAD")

    async def forbidden(*args, **kwargs):
        pytest.fail("formatter recovery cannot invoke model")

    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline")
    monkeypatch.setattr(Executor, "_collect", forbidden)
    stop_at_pr(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="no publication"):
        cli.main(["resume", "--config", str(config), "--thread-id", "effect"])
    after = saved_values(config, "effect")
    assert (context.workspace_path / ".checks/a").read_text() == "a"
    assert (context.workspace_path / ".checks/b").read_text() == "b"
    assert git(context.workspace_path, "rev-parse", "HEAD") == head
    assert after["fix_receipt"].outcome == ("clean_failure" if first_fails else "passed")
    assert [row.exit_code for row in after["fix_receipt"].commands] == [1 if first_fails else 0, 0]
    assert after["final_gate_receipt"].outcome == "passed" and len(after["cost_events"]) == 2


CRASH_SQUASH = """
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from test_gate_integrity import prepare, owned_commit
from blacksmith import graph, unit_graph
from blacksmith.graph import blacksmith_serde
from blacksmith.recovery import _dump_state
from blacksmith.receipts import OperationJournal
root, config, prd = map(Path, sys.argv[2:5])
from blacksmith.contract import parse_prd
contract=parse_prd(prd).contract.model_dump(mode='json')
context,state=prepare((root,config,prd,contract))
state['unit_base_commit']=context.base_commit
state['review_revisions']=1
owned_commit(state, 'revision.txt', 'revision')
state['unit_mode']='parallel'
_dump_state(Path(sys.argv[5]), state, blacksmith_serde())
mode=sys.argv[6]
original=graph._git_run
def crash(path, *args):
    if mode=='staged' and args[0]=='commit':
        os._exit(106)
    return original(path,*args)
graph._git_run=crash
finish=OperationJournal.finish
def lost(self,key,*args,**kwargs):
    if mode=='outcome' and key.kind=='git.squash':
        os._exit(106)
    return finish(self,key,*args,**kwargs)
OperationJournal.finish=lost
unit_graph.normalize_parallel_candidate(state)
raise AssertionError('squash exit injection did not fire')
"""


@pytest.mark.parametrize("mode,tamper", [("outcome", False), ("staged", False), ("staged", True)])
def test_squash_process_exit_reconciles_known_commit_or_staged_tree_and_refuses_extra_edits(
    project,
    tmp_path,
    mode,
    tamper,
):
    import base64

    from blacksmith import unit_graph
    from blacksmith.commands import CommandError
    from blacksmith.graph import blacksmith_serde
    from blacksmith.receipts import ReceiptError

    state_file = tmp_path / "saved-state.json"
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH_SQUASH,
            str(Path(__file__).parent),
            *map(str, project[:3]),
            str(state_file),
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 106, child.stderr
    data = json.loads(state_file.read_text())
    state = blacksmith_serde().loads_typed((data["kind"], base64.b64decode(data["data"])))
    context = context_from_state(state)
    path = Path(state["worktree_path"])
    head = git(path, "rev-parse", "HEAD")
    before_tree = git(path, "rev-parse", state["implementation"]["candidate_commit"] + "^{tree}")
    if tamper:
        target = path / "result.txt"
        target.write_text("unknown extra staged-recovery edit")
        with pytest.raises((ReceiptError, CommandError)):
            unit_graph.normalize_parallel_candidate(state)
        assert git(path, "rev-parse", "HEAD") == head
        assert target.read_text() == "unknown extra staged-recovery edit"
    else:
        result = unit_graph.normalize_parallel_candidate(state)
        after = result["implementation"]["candidate_commit"]
        assert git(path, "rev-parse", "HEAD^{tree}") == before_tree
        assert git(path, "rev-list", "--count", context.base_commit + "..HEAD") == "1"
        if mode == "outcome":
            assert after == head
        assert unit_graph.normalize_parallel_candidate(state) == result
    receipts = [o for o in context.journal().operations() if o.key.kind == "git.squash"]
    assert len(receipts) == 1 and receipts[0].status == ("intent" if tamper else "completed")
