# Live canary review: 19277790-18ab-4792-8e45-be61a8cccd9b

Reviewed 2026-10-26. **Stopped and incomplete; live release acceptance failed.**
The subsequently authorized [local permission fix](heavy-prd-permissions-fix.md)
has a separate verification record; the findings below describe the preserved run.
Thread: `heavy-prd-private-canary-20261025T213150`. No paid call or recovery
execution was started by this review. The completed implementation goal stays
at **28/30**; this review does not resume or reset it.

## P1: custom SDK transport drops permission callback routing

`blacksmith/executor.py:451` constructs `OwnedCLITransport` with the original
options before passing it to `ClaudeSDKClient`. The installed SDK **0.2.115**,
`claude_agent_sdk/client.py:179-194`, copies options to add
`permission_prompt_tool_name="stdio"`, but uses an already supplied custom
transport without propagating that copy. `OwnedCLITransport` retains the original
options (`blacksmith/sdk_transport.py:116-124`), so its CLI command lacks
`--permission-prompt-tool stdio`. The Python path guard is registered with the
query but the CLI has no route for asking it to grant writes.

The live implementation transcripts contain **85 denied Write requests** across
three calls, including the required `canary/labels.py`, `canary/limits.py` and
their tests. The repeated message is: "Claude requested permissions to write to
<path>, but you haven't granted it yet." Read/index tools worked. This is separate
from the informational connector warning and is not an API authentication failure.

A zero-cost local reproduction invokes the installed SDK's real `_connect_inner`
and stops inside the supplied transport's `connect`, before spawning any process
or sending a model request. It proves the original transport lacks the permission
flag; building with the copied options produces it. This is command-routing proof,
not a successful live write or a completed protected-path acceptance check.

**Next action:** correct option propagation while preserving the owned-process
transport and path guard. Add a regression through the real SDK client/transport
boundary that proves permitted writes reach the guard and protected writes remain
denied. Do not use bypass permissions, blanket write grants, higher limits or
automatic paid retries as a workaround.

## Observed failures and accounting

| Call | Outcome | SDK estimate |
| --- | --- | --- |
| U01 plan | Completed; reused on resume | $0.04269545 |
| U02 original plan | Interrupted, no terminal usage | Unknown; $1 reserved charge |
| U02 resumed plan | Completed | $0.03120510 |
| U02 implement | Turn cap; 41 SDK-reported turns | $0.73064725 |
| U01 implement | SDK success terminal, but no file changes; audit halted | $0.62211520 |
| U02 implement continuation | Timed out after 300 seconds; no terminal usage | Unknown; $1 reserved charge |

Known SDK estimates total approximately **$1.426663**. Admission accounting charges
approximately **$3.426663 of $10**, including $2 for the two unknown-cost calls.
Accounting remains incomplete and provider billing was not verified. The original
uncertain receipts remain intact. One explicit continuation amendment is recorded;
no allowance increase is recorded. Two implementation workers overlapped in distinct
workspaces. Repeated denials consumed turns without progress; an early stop for
this failure pattern is a useful follow-up, rather than increasing the turn cap.

No execution-lock owner remains. The root's last committed checkpoint still says
`implementing`; this is not evidence that a process remains active. U01's child
checkpoint is halted with `implement produced no file changes; nothing to gate or
open a PR`. U02's last child checkpoint precedes its timed-out continuation.
Three recovery captures and seals completed; those are preservation receipts,
not successful implementation outcomes.

## Verified boundaries and remaining gates

- Original target plus shared, U01 and U02 workspaces are clean. All nine baseline
  file hashes match in each, including the protected sentinel. Neither utility nor
  either required test file exists. The original target HEAD remains the baseline.
- GitHub still reports the target private. All remote refs match the pre-canary
  receipt. The only PR is the closed setup-only draft #1; no canary branch/PR exists.
- There are no test-gate, reviewer, summary or publication receipts. Running the
  seed smoke test again would not establish utility acceptance.
- All 176 Python source/test files still match the frozen full-suite identity.
  The historical 1419-pass/Ruff result remains source-level evidence; it did not
  detect this live SDK wiring defect. No full suite was rerun for this read-only
  diagnosis and documentation update.
- Live combined-tree tests, strict reviews, plan-only publication refusal, exact-SHA
  resumed draft publication, accounting reconciliation and human utility QA remain
  outstanding. Enabled Docker ownership/timeout/restart proof remains a separate
  conditional gate; sandbox and respond remain disabled.

## Evidence retained locally

Under `/Users/josh.smith/.codex/artifacts/blacksmith-heavy-prd-readiness/private-canary-setup/`:

- `run-19277790-review.json`: read-only journal/checkpoint summaries, exact call
  IDs, known/conservative charges, denial counts, four workspace checks and current
  GitHub/ref observations.
- `review_run_19277790.py`: reproducible inspection and offline routing probe;
  succeeded with exit 0. It never loads credentials or starts a model request.
- `.blacksmith/runs/19277790-18ab-4792-8e45-be61a8cccd9b/`: original operation
  journal, immutable inputs, recovery captures and retained worker clones.
- `.blacksmith/transcripts/`: original per-call transcripts. Do not overwrite,
  discard unknown usage, or replay an effect to make the old run appear successful.

Source reviewed: `dc52cc4`; implementation source identity is unchanged from the
previous full-suite acceptance. Runtime source was not changed during this review.
The current repository Ruff check and diff whitespace check both passed. The two
updated brain notes passed targeted lint with zero errors and warnings.
