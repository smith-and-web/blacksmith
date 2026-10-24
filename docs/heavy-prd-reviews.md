# Cumulative review evidence and recovery

Heavy-PRD remediation remains in progress. This describes S12's review behavior;
the [checklist](reviews/heavy-prd-progress.md) and
[acceptance record](reviews/heavy-prd-evidence.md) track final verification and the
remaining S13-S19 and live release gates.

Enabled review follows a passing, exact-candidate unit gate. Both sequential units
and parallel workers use the same compiled unit graph and review policy. A reviewer
receives the immutable PRD prose/full contract, approved plan, saved unit base SHA,
current gated commit and actual cumulative patch. Every revision is reviewed from
the original unit base, including earlier changes, deletions, renames and binary
patch evidence. The diffstat is orientation, never the review evidence itself.

The full patch is retained under the run's private
`reviews/patches/<identity>.patch`, bound to the run/unit/base/candidate in a critical
operation receipt. Git writes it directly to an artifact, so the command-log cap
does not truncate the patch. Patches up to 128 KiB appear inline. Larger patches
appear as an explicit path/hash/byte-count manifest with the read-only `read_diff`
MCP tool. Its byte `offset` and `max_bytes` arguments return at most 32768 source
bytes plus `next_offset`, total size, hash and completion flag. Each read checks
the original artifact hash; callers cannot select another file. UTF-8 display
escapes invalid bytes. Native read/index tools remain read-only; no shell or
editing tool is granted to the reviewer.

Reviewers return one complete JSON object, optionally in one fenced JSON block:

```json
{
  "verdict": "needs_changes",
  "findings": [
    {"severity": "blocking", "file": "src/example.py", "detail": "Required behavior was deleted."}
  ]
}
```

`verdict` is `clean` or `needs_changes`; findings require nonblank string `file`
and `detail` and severity `blocking` or `advisory`. Unknown/missing fields,
duplicate JSON keys, unknown severities, malformed JSON or surrounding prose do
not count as a valid verdict. Any validated blocking finding from any panelist
blocks the candidate, including a minority of one. Advisory-only findings remain
nonblocking. The top-level verdict cannot override a blocking finding.

The typed `ReviewReceipt` contains run/unit/attempt/base/candidate/tree identity,
status, validated findings, ordered panel results, model-call cost references,
patch manifest and its own artifact path. Critical seals precede final JSON
publication; completed SDK results and sealed decisions can be reused after a
lost node checkpoint. Per-unit receipts cross parent/child and fan-out boundaries.
Join, integration, final verification and publication entry validate the original
review artifacts and model receipts for each exact unit candidate. A later HEAD
does not invalidate an earlier unit's immutable evidence or substitute it for a
review of a different candidate.

| Status | Meaning and routing |
|---|---|
| `clean` | Every enabled panel seat returned a validated nonblocking verdict for this candidate. Advance to the next deterministic stage. |
| `blocking` | At least one panelist found a blocking defect. Revise only within the existing revision allowance and budget, then gate and review the full cumulative patch again. Exhaustion halts and retains work. |
| `unavailable` | Error, missing terminal result, lost stream, malformed verdict, denied/failed call or missing evidence. Halt without automatic reviewer retry; no normal or draft publication. |
| `disabled` | Review was explicitly disabled/unwired. Record that status with no reviewer call; never label it clean. |

After an unavailable seat, remaining seats are not automatically admitted. Completed
valid seats remain reusable. A receipt is evidence of the recorded review outcome,
not proof that every natural-language requirement or live SDK permission has been
verified. Missing cost remains unknown; S15 supplies conservative reservation
accounting and admission across every paid role. This change adds no normal
reviewer calls, although full patch context can increase input tokens.

## Explicit recovery

Ordinary resume never repeats an uncertain SDK call or replenishes counters.
Inspect retained evidence first:

```sh
blacksmith recovery inspect --thread-id RUN --config /path/to/blacksmith.config.toml
blacksmith resume --thread-id RUN --config /path/to/blacksmith.config.toml --continue-attempt
```

Explicit review continuation uses the original candidate, base, gate and worker,
consumes the remaining continuation allowance, and creates new call identities
only for unavailable/failed/unknown seats. Completed valid seats reuse their exact
original call identities and costs. A completed SDK response containing malformed
JSON is unavailable and gets a new key only under explicit recovery. Original
failed/uncertain receipts and artifacts remain unchanged. No exactly-once live
billing or live cancellation guarantee is made.

An unchanged blocked candidate cannot request another review to seek a different
vote. If a blocker halted only because the run budget was exhausted and an actual
revision allowance remains, an explicit positive finite `--add-budget-usd N`
permits the existing recovery policy to revise the code. It does not re-review the
same SHA. Exhausted revision allowance remains exhausted. Current reporting uses
the latest typed candidate receipts while preserving historical findings; revision
attempt counts are never presented as counts of resolved findings.

Corrupt/missing/foreign review or patch artifacts stop CLI resume before executor
construction. An active workspace lacking `review_version=1` refuses execution
with `legacy_review_requires_manual_recovery`; old completed state remains
inspectable and no hashes/review claims are invented. The general
[recovery runbook](heavy-prd-run-context.md#recovery-artifacts-and-explicit-restart)
covers artifact repair and uncertain native effects.

The S12 crash drills use real Git, SQLite, unit graphs and native gates, replacing
paid transport. They cover exits before decision sealing, after a critical seal
but before the artifact, and after the receipt but before the node checkpoint.
These are offline proofs. Exact-SHA human approval/publication reconciliation,
global budgets/deadlines, scale scheduling and the authorized live canary remain
later release gates. A separate large-PR combined-diff display bug found during
S12 verification is recorded for S13; the full reviewer artifact/tool path already
handles patches larger than the native command-log cap.
