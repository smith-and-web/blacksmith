"""Real bounded subprocesses, process groups, checked Git and production forwarding."""

import json
import os
import shlex
import signal
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_run_context import create, git, install_transport
from test_run_context import project as project

from blacksmith import cli
from blacksmith.commands import (
    GIT_TIMEOUT_S,
    TAIL_BYTES,
    CommandError,
    command_scope,
    run_command,
)
from blacksmith.config import BlacksmithConfig, ConfigError, LimitsConfig
from blacksmith.gate import TargetToolchain, _amend_if_changed, run_fix, run_gate
from blacksmith.graph import prepare_escalation
from blacksmith.receipts import ReceiptError
from blacksmith.sbfl import collect_suspicious_locations


def python(code):
    return [sys.executable, "-c", code]


def metadata(result):
    return json.loads((result.log_dir / "outcome.json").read_text())


def test_limits_parse_and_bound_at_point_of_use(tmp_path):
    path = tmp_path / "runtime.toml"
    path.write_text("[limits]\ncommand_timeout_s=0.15\nmax_command_output_bytes=4096\n")
    config = BlacksmithConfig.load(path)
    assert LimitsConfig().command_timeout_s == 1800
    assert LimitsConfig().max_command_output_bytes == 1048576
    assert GIT_TIMEOUT_S == 120
    with command_scope(limits=config.limits):
        with pytest.raises(CommandError, match="timeout"):
            run_command(python("import time; time.sleep(60)"))
    path.write_text("[limits]\ncommand_timeout_s=0\n")
    with pytest.raises(ConfigError, match="command_timeout_s"):
        BlacksmithConfig.load(path)
    path.write_text("[limits]\ncommand_timeout_s=inf\n")
    with pytest.raises(ConfigError, match="command_timeout_s"):
        BlacksmithConfig.load(path)


def test_large_output_has_shared_disk_cap_exact_counts_and_bounded_tails():
    size = 2 * 1048576
    with command_scope(limits=LimitsConfig(max_command_output_bytes=4096)):
        result = run_command(
            python(
                f"import os; os.write(1,b'A'*{size}+b'STDOUT_END'); "
                f"os.write(2,b'B'*{size}+b'STDERR_END')"
            )
        )
    info = metadata(result)
    assert result.stdout_bytes == size + 10
    assert result.stderr_bytes == size + 10
    assert info["stdout_bytes"] == result.stdout_bytes
    assert info["stderr_bytes"] == result.stderr_bytes
    assert result.truncated
    assert sum(p.stat().st_size for p in result.log_dir.glob("*.log")) <= 4096
    assert "output truncated" in result.stdout
    assert "output truncated" in result.stderr_tail
    assert result.stdout_tail.endswith("STDOUT_END")
    assert result.stderr_tail.endswith("STDERR_END")
    assert len(result.stdout_tail) < TAIL_BYTES + 512
    with pytest.raises(CommandError, match="truncated"):
        result.require_complete_output()


@pytest.mark.parametrize("cancellation", [False, True])
def test_timeout_or_cancel_kills_sigterm_ignoring_descendant(tmp_path, cancellation):
    sentinel = tmp_path / "escaped"
    pidfile = tmp_path / "pid"
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(pidfile)!r}).write_text(str(os.getpid())); "
        f"time.sleep(1.2); Path({str(sentinel)!r}).write_text('bad')"
    )
    parent = f"import subprocess,time; subprocess.Popen({python(child)!r}); time.sleep(60)"
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set) if cancellation else None
    if timer:
        timer.start()
    start = time.monotonic()
    with pytest.raises(CommandError, match="cancelled" if cancellation else "timeout") as exc:
        run_command(
            python(parent),
            timeout=20 if cancellation else 0.3,
            cancel=cancel,
            terminate_grace_s=0.15,
        )
    assert time.monotonic() - start < 2
    assert pidfile.exists()  # The descendant really started before termination.
    pid = int(pidfile.read_text())
    # A reparented zombie may remain briefly on Linux; it cannot execute writes.
    try:
        os.kill(pid, signal.SIGCONT)
    except ProcessLookupError:
        pass
    time.sleep(1.3)
    assert not sentinel.exists()
    log_dir = Path(str(exc.value).split("logs: ")[1])
    info = json.loads((log_dir / "outcome.json").read_text())
    assert info["cancelled"] is cancellation
    assert info["timed_out"] is not cancellation


def test_parent_exiting_does_not_leave_background_writer(tmp_path):
    sentinel = tmp_path / "late"
    child = (
        f"import time; from pathlib import Path; time.sleep(.8); Path({str(sentinel)!r}).touch()"
    )
    result = run_command(
        python(f"import subprocess; subprocess.Popen({python(child)!r})"), terminate_grace_s=0.1
    )
    assert result.returncode == 0
    time.sleep(0.9)
    assert not sentinel.exists()


def test_sensitive_git_output_and_argv_are_redacted(project):
    root, *_ = project
    secret = "SENSITIVE_TEST_SENTINEL"
    git(root, "config", "example.secret", secret)
    result = run_command(["git", "-C", str(root), "config", "--get", "example.secret"])
    assert result.stdout.strip() == secret
    assert secret not in "".join(p.read_text() for p in result.log_dir.iterdir())
    assert json.loads((result.log_dir / "intent.json").read_text())["argv"] == "[redacted]"


def test_toolchain_strips_credentials_but_keeps_normal_environment(tmp_path, monkeypatch):
    for key in (
        "ANTHROPIC_API_KEY",
        "BLACKSMITH_ANTHROPIC_ADMIN_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        monkeypatch.setenv(key, "TEST_ONLY_CREDENTIAL")
    monkeypatch.setenv("BUILD_COLOR", "present")
    code = (
        "import os; print(sorted(k for k,v in os.environ.items() "
        "if v=='TEST_ONLY_CREDENTIAL')); print(os.environ['BUILD_COLOR'])"
    )
    result = run_command(python(code), toolchain=True)
    assert result.stdout == "[]\npresent\n"
    # Publication commands keep authorized GitHub credentials.
    assert run_command(python("import os; print(bool(os.getenv('GH_TOKEN')))")).stdout == "True\n"


def test_command_receipts_are_ordered_and_observations_are_fresh(project):
    context = create(project, "command-receipts")
    with command_scope(context, unit_id="U1", attempt_id="a1", stage="verify"):
        first = run_command(python("print('first')"))
        second = run_command(python("print('second')"))
    assert first.stdout == "first\n" and second.stdout == "second\n"
    with sqlite3.connect(context.journal().path) as conn:
        rows = conn.execute(
            "SELECT kind,status,outcome_json FROM operations WHERE unit_id='U1' ORDER BY rowid"
        ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        (f"command:1:verify:{Path(sys.executable).name}", "completed"),
        (f"command:2:verify:{Path(sys.executable).name}", "completed"),
    ]
    for _, _, outcome in rows:
        assert json.loads(outcome)["stdout_bytes"] > 0
    assert first.log_dir.parent == context.directory / "commands"
    context.journal().verify()


def test_parallel_command_ids_are_allocated_transactionally(project):
    context = create(project, "parallel-commands")

    def worker(_):
        with command_scope(context, unit_id="U1", attempt_id="a1", stage="probe"):
            return run_command(python("print('ok')")).log_dir.name

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(worker, range(8)))
    assert len(set(ids)) == 8
    context.journal().verify()


def test_critical_intent_failure_prevents_command_and_sbfl_does_not_swallow(project):
    context = create(project, "command-failure")
    sentinel = context.directory / "must-not-exist"
    command = shlex.join(python(f"from pathlib import Path; Path({str(sentinel)!r}).touch()"))
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute(
            "CREATE TRIGGER no_command BEFORE INSERT ON operations "
            "WHEN NEW.kind LIKE 'command:%' BEGIN SELECT RAISE(ABORT,'disk'); END"
        )
    with command_scope(context):
        with pytest.raises(ReceiptError):
            run_command(command, shell=True)
        with pytest.raises(ReceiptError):
            collect_suspicious_locations(
                context.planning_path,
                coverage_cmd=command,
                coverage_json="missing",
                junit_xml="missing",
            )
    assert not sentinel.exists()


def test_failed_add_amend_and_reset_are_checked(project):
    root, *_ = project
    (root / "new.txt").write_text("new")
    lock = root / ".git" / "index.lock"
    lock.touch()
    with pytest.raises(CommandError):
        _amend_if_changed(root)
    with pytest.raises(CommandError):
        prepare_escalation({"worktree_path": str(root), "pre_implement_ref": "HEAD"})
    lock.unlink()
    git(root, "config", "core.hooksPath", str(root / ".git" / "hooks"))
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    before = git(root, "rev-parse", "HEAD")
    with pytest.raises(CommandError):
        _amend_if_changed(root)
    assert git(root, "rev-parse", "HEAD") == before
    assert git(root, "diff", "--cached", "--name-only") == "new.txt"


@pytest.mark.parametrize("parallel", [False, True])
def test_cli_saved_limits_reach_real_gate_and_resume(project, monkeypatch, parallel):
    root, config, prd, contract = project
    command = shlex.join(
        python(
            "import os,time; "
            "assert not os.getenv('TEST_MODEL_SECRET'); "
            "assert not os.getenv('TEST_ADMIN_SECRET'); "
            "print('START',flush=True); time.sleep(60)"
        )
    )
    (root / "blacksmith.toml").write_text(f"test_cmd = {json.dumps(command)}\n")
    git(root, "add", "blacksmith.toml")
    git(root, "commit", "-m", "timeout gate")
    config.write_text(
        config.read_text() + "[limits]\ncommand_timeout_s=0.2\nmax_command_output_bytes=2048\n"
        '[api]\nkey_env_var="TEST_MODEL_SECRET"\nadmin_key_env_var="TEST_ADMIN_SECRET"\n'
    )
    if parallel:
        from test_run_context import write_prd

        contract["work_units"].append({**contract["work_units"][0], "id": "U2"})
        write_prd(prd, contract)
    monkeypatch.setenv("TEST_MODEL_SECRET", "TEST_ONLY_MODEL")
    monkeypatch.setenv("TEST_ADMIN_SECRET", "TEST_ONLY_ADMIN")
    calls = []
    install_transport(monkeypatch, calls)
    assert (
        cli.main([str(prd), "--config", str(config), "--thread-id", "bounds", "--approve", "plan"])
        == 1
    )
    paths = list(config.parent.glob(".blacksmith/runs/*/operations.sqlite"))
    assert len(paths) == 1

    def outcomes():
        with sqlite3.connect(paths[0]) as conn:
            return [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT outcome_json FROM operations WHERE kind LIKE 'command:%' "
                    "AND outcome_json IS NOT NULL"
                )
            ]

    assert any(row["timed_out"] for row in outcomes())
    # Local limit changes must not remove the saved deadline on ordinary resume.
    config.write_text(config.read_text().replace("command_timeout_s=0.2", "command_timeout_s=60"))
    cli.main(["resume", "--config", str(config), "--thread-id", "bounds", "--approve", "plan"])
    with sqlite3.connect(paths[0]) as conn:
        intents = [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT intent_json FROM operations WHERE kind LIKE 'command:%'"
            )
        ]
    gates = [row for row in intents if row["toolchain"]]
    assert gates and all(row["timeout_s"] == 0.2 for row in gates)
    assert all(row["max_output_bytes"] == 2048 for row in gates)
    assert all(row["timeout_s"] == 120 for row in intents if not row["toolchain"])


def test_gate_state_contains_tail_and_full_log_path(tmp_path):
    command = shlex.join(python("print('x'*100000); print('FINAL_TAIL')"))
    (tmp_path / "blacksmith.toml").write_text(f"test_cmd={json.dumps(command)}\n")
    with command_scope(limits=LimitsConfig(max_command_output_bytes=2048)):
        result = run_gate(tmp_path)
    assert result.passed
    assert "output truncated" in result.output and "logs:" in result.output
    assert result.output.endswith("FINAL_TAIL\n")
    assert len(result.output) < TAIL_BYTES + 1024


def test_real_command_owns_new_process_group():
    result = run_command(python("import os; print(os.getpid(),os.getpgrp())"))
    pid, group = map(int, result.stdout.split())
    assert pid == group
    assert group != os.getpgrp()


def test_failed_outcome_retains_intent_and_actual_logs(project):
    context = create(project, "command-outcome-failure")
    sentinel = context.directory / "effect"
    with sqlite3.connect(context.journal().path) as conn:
        conn.execute(
            "CREATE TRIGGER no_outcome BEFORE UPDATE ON operations "
            "WHEN NEW.kind LIKE 'command:%' BEGIN SELECT RAISE(ABORT,'disk'); END"
        )
    with command_scope(context, stage="outcome"):
        with pytest.raises(ReceiptError):
            run_command(
                python(f"from pathlib import Path; Path({str(sentinel)!r}).touch(); print('DONE')")
            )
    assert sentinel.exists()
    with sqlite3.connect(context.journal().path) as conn:
        row = conn.execute(
            "SELECT operation_id,status,outcome_json FROM operations "
            "WHERE kind LIKE 'command:%:outcome:%'"
        ).fetchone()
    assert row[1:] == ("intent", None)
    log_dir = context.directory / "commands" / row[0]
    assert (log_dir / "stdout.log").read_text() == "DONE\n"
    assert json.loads((log_dir / "outcome.json").read_text())["returncode"] == 0


@pytest.mark.parametrize("stage", ["setup_cmd", "test_cmd", "lint_cmd", "fix_cmd", "sbfl"])
def test_all_toolchain_stages_obey_command_deadline(tmp_path, stage):
    slow = shlex.join(python("import time; time.sleep(60)"))
    values = {"test_cmd": "true", stage: slow} if stage != "sbfl" else {"test_cmd": "true"}
    with command_scope(limits=LimitsConfig(command_timeout_s=0.1)):
        if stage == "sbfl":
            assert (
                collect_suspicious_locations(
                    tmp_path, coverage_cmd=slow, coverage_json="missing", junit_xml="missing"
                )
                == []
            )
        else:
            with pytest.raises(CommandError, match="timeout"):
                if stage == "fix_cmd":
                    run_fix(tmp_path, toolchain=TargetToolchain(**values))
                else:
                    run_gate(tmp_path, toolchain=TargetToolchain(**values))


def test_already_cancelled_command_never_starts(tmp_path):
    sentinel = tmp_path / "must-not-start"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CommandError, match="cancelled before start"):
        run_command(
            python(f"from pathlib import Path; Path({str(sentinel)!r}).touch()"), cancel=cancel
        )
    assert not sentinel.exists()


def test_keyboard_interrupt_cleans_process_group_and_records_interruption(
    project, tmp_path, monkeypatch
):
    from blacksmith import commands

    context = create(project, "command-interrupt")
    sentinel = tmp_path / "late"
    pidfile = tmp_path / "started"
    child = (
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(pidfile)!r}).touch(); time.sleep(1); Path({str(sentinel)!r}).touch()"
    )
    selector_type = commands.selectors.DefaultSelector

    class InterruptedSelector(selector_type):
        def select(self, timeout=None):
            events = super().select(timeout)
            if pidfile.exists():
                raise KeyboardInterrupt()
            return events

    monkeypatch.setattr(commands.selectors, "DefaultSelector", InterruptedSelector)
    with command_scope(context, stage="interrupt"), pytest.raises(KeyboardInterrupt):
        run_command(
            python(f"import subprocess,time; subprocess.Popen({python(child)!r}); time.sleep(60)"),
            terminate_grace_s=0.1,
        )
    assert pidfile.exists()
    time.sleep(1.1)
    assert not sentinel.exists()
    with sqlite3.connect(context.journal().path) as conn:
        status, outcome = conn.execute(
            "SELECT status,outcome_json FROM operations WHERE kind LIKE 'command:%:interrupt:%'"
        ).fetchone()
    assert status == "interrupted"
    assert json.loads(outcome)["error_type"] == "KeyboardInterrupt"
