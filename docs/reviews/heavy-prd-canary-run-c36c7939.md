# Live canary review: c36c7939-b2e1-45f4-97b1-aebd0a915d45

Reviewed 2026-10-26. **The automated canary reached the intended AWAITING_QA
state and published the exact candidate.** The permission repair now has live
write, gate, review, resume and publication evidence. Human QA remains pending in
Blacksmith. The owner reported that the PR looks good; this review did not change
the checkpoint, approve QA, merge, publish or start a paid call.

The actual canary is [PR #2](https://github.com/smith-and-web/blacksmith-readiness-canary/pull/2).
PR #1 is the closed setup-only draft probe. The repository remains private.

- Thread: `heavy-prd-fixed-20261026T182630Z-9da0dc6e`.
- Gated, approved, workspace, remote-head and PR-marker commit:
  `6996ab55beac70a7999bf1c6dd587c660d933ae0`.
- Only the intended four files were added: both utilities and their tests.
  Original target/main remain at `ed93ef9507d44aa3f10a530eb3b146f7fd559cfc`;
  all nine baseline/protected hashes match in the target and candidate.
- Both unit gates and strict reviews passed. The final combined gate passed
  **12 tests and Ruff**. Independent candidate pytest/Ruff reruns and utility
  edge-case smoke checks also pass. Receipt, command-output and patch hashes match.
- Exactly **seven successful paid calls**: two plans, two implementations, two
  reviews and one summary. The saved six pre-publication call IDs were reused on
  resume, with the same candidate and no unknown/unsettled operations. Recorded
  SDK estimate/admission charge: **$0.29278945**. Provider invoice not verified.
- Two distinct worker clones; measured peak overlapping model calls **2**.
  Plan-only halt precedes AWAITING_QA in the saved live history. Approval decisions
  are refusal then approval; exactly one push and one PR-create effect are saved.
- All 178 application/test files, runtime/dependency identities and frozen inputs
  still match the successful full02 1431-test receipt. This review changed no
  application source and did not repeat the multi-hour Blacksmith suite.

## Current GitHub draft-state difference

The immutable approval and successful publication receipts record draft creation
at 18:46 UTC. GitHub then records `ready_for_review` at **18:47:48 UTC**. PR #2 is
now open and ready for review, with the exact same candidate. The strict
`03-check.sh` correctly exits1 with `Remote draft/head does not match.` because
its acceptance condition requires a current draft. This is a later GitHub state
change; no code/SHA/publication defect was found. The draft guard was not weakened,
historical receipts were not rewritten and GitHub state was not changed here.

## Remaining acceptance and evidence

Human QA needs to be recorded separately from the pending checkpoint; the owner's
positive review is retained without inventing a typed QA receipt. Provider billing
reconciliation remains outstanding, including unknown costs from the original
failed run. Real Docker lifecycle/timeout/restart acceptance remains required
before future sandbox enablement; sandbox and respond remain explicitly disabled.
Real crash-injection follow-ups remain separate from this successful nominal run.
No broad heavy-PRD or enabled-feature release is inferred from this small canary.

Detailed read-only observations, call IDs/times/costs, worker events, receipt
hashes, independent commands/results and current GitHub timeline are retained in
[the JSON evidence](heavy-prd-canary-run-c36c7939.json). The original failed run
`19277790-18ab-4792-8e45-be61a8cccd9b` remains incomplete and unchanged. The
completed implementation-goal counter stays **28/30**.
