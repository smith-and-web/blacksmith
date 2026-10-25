"""SDK CLI transport with an owned POSIX session and cancellation-safe cleanup.

The installed SDK accepts a custom transport in both public APIs. Its subprocess
transport supplies the protocol; these two narrow overrides own process startup
and shutdown instead of depending on cancellation of the CLI leader alone.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from blacksmith.commands import run_command
from blacksmith.receipts import ReceiptError


def _signal(pid, sig, *, group=False):
    try:
        (os.killpg if group else os.kill)(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin can return EPERM while a terminated group is being reaped.
        # Permission denial alone is never evidence that writers stopped.
        if group and _group_gone(pid):
            return False
        raise


def _group_gone(pid):
    """Positive bounded observation of no live members; fail closed on uncertainty."""
    from blacksmith.commands import _scope, command_scope
    from blacksmith.run_clock import current
    previous, owner = _scope.get(), current()
    context = previous.context if previous is not None else owner.context if owner else None
    with command_scope(context, stage="sdk_cleanup",
            unit_id=previous.unit_id if previous else "__run__",
            attempt_id=previous.attempt_id if previous else "sdk-cleanup"):
        result = run_command(["ps", "-A", "-o", "pid=,pgid=,stat="], timeout=1,
                             kind=f"sdk_group_observe:{pid}")
    result.check_returncode()
    result.require_complete_output()
    rows = result.stdout.splitlines()
    if not rows:
        raise ReceiptError("SDK cleanup process observation returned no process inventory")
    states = []
    for row in rows:
        fields = row.split()
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise ReceiptError("SDK cleanup process observation is malformed")
        if int(fields[1]) == pid:
            states.append(fields[2])
    return not states or all(state.startswith("Z") for state in states)


def _stop(process):
    pid = process.pid
    group_live = None

    def leader(sig):
        if process.returncode is None:
            try:
                _signal(pid, sig)
            except PermissionError:
                # The event-loop child watcher may reap between the check and
                # signal. Never signal a reaped/reused leader; its group below
                # can still contain descendants which must be terminated.
                time.sleep(.025)
                if process.returncode is None:
                    raise

    # Freeze the launcher first. If cancellation raced setsid(), it cannot exec
    # and spawn descendants between the group probe and the leader signal.
    try:
        leader(signal.SIGSTOP)
        group_live = _signal(pid, signal.SIGTERM, group=True)
        leader(signal.SIGTERM)
        # Only the leader was stopped. Continuing an orphaned process group is
        # unnecessary and can fail even after its leader already exited.
        leader(signal.SIGCONT)
        until = time.monotonic() + 5
        while group_live and time.monotonic() < until:
            group_live = _signal(pid, 0, group=True)
            if group_live:
                time.sleep(0.025)
    finally:
        try:
            if group_live is not False:
                _signal(pid, signal.SIGKILL, group=True)
        finally:
            leader(signal.SIGKILL)


async def finish_cleanup(coro):
    """Finish cleanup even when raw asyncio cancellation interrupts the caller."""
    task = coro if asyncio.isfuture(coro) else asyncio.create_task(coro)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class OwnedCLITransport(SubprocessCLITransport):
    def __init__(self, prompt, options):
        super().__init__(prompt, options)
        self._owned_cleanup_task = None

    def _build_command(self):
        if os.name != "posix":
            raise RuntimeError("owned SDK process cleanup requires POSIX")
        command = super()._build_command()
        return [sys.executable, str(Path(__file__).with_name("_sdk_launcher.py")), *command]

    async def _check_claude_version(self):
        # The SDK's advisory version probe is otherwise an unowned subprocess.
        # Keep its diagnostic purpose with the same bounded native owner service.
        result = await asyncio.to_thread(run_command, [self._cli_path, "-v"], timeout=120)
        result.check_returncode()

    async def close(self):
        async def cleanup():
            process = self._process
            try:
                if process is not None:
                    try:
                        await asyncio.to_thread(_stop, process)
                    finally:
                        await asyncio.wait_for(process.wait(), timeout=5)
                await super(OwnedCLITransport, self).close()
            except ReceiptError:
                raise
            except Exception as exc:
                raise ReceiptError(
                    "SDK owned process cleanup failed; inspect retained call") from exc
        if self._owned_cleanup_task is None:
            self._owned_cleanup_task = asyncio.create_task(cleanup())
        await finish_cleanup(self._owned_cleanup_task)
