# SDK write-permission routing fix

2026-10-26 follow-up to [the live canary failure](heavy-prd-canary-run-19277790.md).
The owner requested the fix after the read-only review. The original implementation
goal remains complete at **28/30**; this follow-up does not reset or resume it.

## Change and cost

`OwnedCLITransport` copies guarded call options to add
`permission_prompt_tool_name="stdio"` before the SDK builds its CLI command.
The client's original options remain unchanged because the SDK rejects an explicit
permission prompt tool combined with `can_use_tool`. Unguarded calls preserve
their original options, including an explicitly selected permission prompt tool.

The owned launcher, cancellation cleanup, Python path guard, read-only auto-allow
list, default permission mode, MCP/settings isolation, budgets and retry limits
are unchanged. The fix adds no call or retry; it allows useful writes instead of
spending existing allowances on repeated permission denials. No paid canary or
recovery execution was started during the fix.

## Regression evidence

`tests/test_sdk_permissions.py` runs the real Executor, ClaudeSDKClient, SDK control
protocol, OwnedCLITransport and subprocess launcher against a local executable.
Only the billed CLI is replaced. The local executable requires the actual stdio
routing flag, asks for each permission over the SDK protocol and applies a file
operation only after receiving an allow response.

It proves relative Write and absolute Edit create/change an allowed file, while
seven requests are denied: a hard-protected sentinel, target configuration, Git
configuration, parent traversal, a symlink escape, an outside notebook and a shell
tool. The protected/configuration/outside bytes remain unchanged. Assertions also
cover strict settings/MCP isolation, read-only auto-allow tools and a single stdio
flag. Two additional cases preserve unguarded explicit/default routing options.

- Initial test construction had an incorrect fixture tuple unpack; corrected
  before using the regression as defect evidence.
- Unchanged runtime: **1 failed, 2 passed in 4.96s**, with the local CLI reporting
  `missing stdio permission routing`. Original failed log retained as
  `heavy-prd-permissions-red-02.log`.
- Fixed runtime: **3 passed in 4.21s**, with one existing SDK advisory about the
  intentionally auto-approved read tools. Receipt: `heavy-prd-permissions-green-01.log`.
- Ruff initially found one new overlong test line; wrapped without assertion
  changes. Final `uv run ruff check` passed. `git diff --check` passed.
- Full01 finished with exit1: **1 failed, 1421 passed, 6 warnings in 7423.74s**.
  Supervisor PID89782 / childPID89786, tool session1415 are terminal. Command:
  `uv run pytest -q --basetemp
  /private/tmp/blacksmith-permissions-full01-20261026`. Log:
  `heavy-prd-permissions-full-01.log`. Durable PID/command/before-after identity and
  terminal receipt: `heavy-prd-permissions-full-01-command.json` proves all177
  source/test/config/input/runtime identities unchanged. The original SDK write
  regression passes in this full run. The 100-unit chain alone failed after all
  unit commits, with a final-gate `[Errno 1] Operation not permitted`; an open_pr
  Git command intent remained unsettled. Its recovery capture/seal and full fixture
  are preserved. The 20-root/default-two-worker case passed.
- The native cleanup gap is now repaired. Native and SDK cleanup share a bounded
  process-group inventory. EPERM can reconcile only after observing no live
  members; live, unavailable, malformed and truncated inventories still fail.
  A context guard forbids recursive observers if the inventory command's own
  cleanup is denied. Observations still use the checked command service and its
  critical receipts. Existing SDK observation scope/identity is preserved.
- Native regression on the old runner: **7 failed, 1 passed in 1.64s**; original
  log retained as `heavy-prd-permissions-native-red-01.log`. The live-group case
  already failed closed; seven other cases reproduced missing reconciliation or
  observation. The repaired initial focused set passed **14/2.91s**. A separate
  truncated-inventory case was then added without weakening assertions.
- Broader native/SDK/path/deadline/cancellation set: **46 passed, 6 retained SDK
  warnings in 132.54s**. Receipt: `heavy-prd-permissions-scoped-02.log`. Ruff passes:
  `heavy-prd-permissions-ruff-02.log`.
- Full02 is terminal0: **1431 passed, 6 retained SDK warnings in 7209.51s
  (2:00:09)**. All178 Python source/test files, installed runtime and original
  inputs match before/after. Only commands.py/sdk_transport.py differ from the
  prior accepted runtime, with two added regression files. Both the actual100-unit
  chain and20-root/default-two-worker cases pass. Supervisor PID92210 /
  childPID92212 and tool session62610 are terminal. Command: `uv run pytest -q --basetemp
  /private/tmp/blacksmith-permissions-full02-20261026`. Durable log and
  before/after/terminal receipt: `heavy-prd-permissions-full-02.log` and
  `heavy-prd-permissions-full-02-command.json`. Do not mutate frozen source or
  restart this completed job. Local repair acceptance is complete.
- Final `uv run ruff check` and the fixed-source private-target verifier both
  pass. Receipts: `heavy-prd-permissions-ruff-final.log` and
  `heavy-prd-permissions-guard-verified.log`. The verifier first refused while the
  full run was unfinished (`heavy-prd-permissions-guard-pending.log`).

The full suite is expensive: real graph/Git/SQLite/restart/cancellation coverage,
with a roughly43-minute100-unit case observed in full02. The focused14-test set
took2.91s and the broader46-test set132.54s. Provider responses are replaced; these
test durations are not additional provider spend. No performance profiling or
assertion reduction was part of this repair.

## Remaining release evidence

The old paid run remains incomplete; its original journal, unknown usage,
transcripts, recovery captures and baseline target are preserved. A local protocol
test is not a successful provider canary. Real combined-tree gates/reviews,
publication approval/refusal, exact-SHA draft resume, usage reconciliation and human
utility QA remain outstanding. Sandbox/respond remain disabled.

The original private setup guard remains unchanged and fingerprints the previously
tested source. A separate `verify_permission_fix.py` under the private-canary-setup
artifact directory now binds the repaired178-file source/runtime to successful
full02, checks the complete1431-pass result/log hash and frozen inputs, and verifies
the clean baseline, sentinel, private visibility and unchanged remote refs. It
passed read-only preflight without starting a model call. Original setup inputs,
receipts and launchers remain intact. Any later paid run/recovery needs separate
authorization and a launch/continuation appropriate to the saved stages; this
verifier does not resume or replay the old run.

## Requested disk cleanup

Cleanup is complete; see `heavy-prd-permissions-disk-cleanup.json`. The current1431
fixture tree's950935 entries were fully verified in a351486394-byte archive, with
file hashes/modes/owners/symlinks and a separate manifest retained. At the owner's
urgent low-disk instruction, the redundant second full-tree recheck was stopped
and raw successful fixtures were removed using small-memory/native deletion.

Both the current1431 and historical1419 raw fixture roots are removed. A new
archive of the historical1419 root was deliberately skipped at that urgent
instruction; its original full log/source/input/exit receipts remain. Passed-test
fixture siblings from failed full01 were removed, while its failed100-unit case,
original operation/recovery evidence and full-command/log receipts remain intact.
The real paid canary is untouched. No regular-file/hardlink modes, source or
installed environment were changed; all178 source/test/input identities still
match the successful full result after cleanup. `uv cache prune` found no unused
entries. Final observed available space was about12GiB; swap growth also caused
available space to fluctuate during cleanup.
