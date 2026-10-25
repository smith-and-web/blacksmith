# Spending admission, deadlines and recovery

S15 is under local verification. The [checklist](reviews/heavy-prd-progress.md)
and [evidence record](reviews/heavy-prd-evidence.md) identify accepted slices.
Offline proof does not satisfy the unrun provider/publication release canary.

## Saved limits

```toml
[limits]
max_run_cost_usd = 50.0
max_call_cost_usd = 5.0
model_timeout_s = 1800
command_timeout_s = 1800
max_run_seconds = 21600
max_command_output_bytes = 1048576
```

Omitting the run cap uses $50. Intentional uncapped fresh runs require
`--unlimited-cost`, recorded in the immutable policy. Per-call/time bounds still
apply. Resume uses saved limits/models; local edits cannot reset spend or time.

Initial plans, critics/revisions, cheap/escalated implementation, every review seat
and PR summary share atomic admission. Before transport they reserve
`min(call cap, remaining unreserved allowance)` and pass that exact SDK
`max_budget_usd`. Concurrent grants cannot use the same funds. New auto units check
the authoritative balance before paid execution. Legacy `respond` remains subject
to S18 hardening or explicit rejection; it is not an accepted heavy-PRD entry point.

Admission plus SDK stopping is not an exact invoice ceiling. Actual overrun remains
in receipts/reports, stops further paid calls and preserves work. Missing/nonterminal
usage consumes its full reservation conservatively, keeping any observed cost
separate. Unknown usage is never replaced with zero or a token-price estimate.

## Calls, processes and time

Unique call IDs bind run/unit/attempt/role and distinguish unstarted denial,
admitted/started, completed, interrupted and unknown work. Cached completed responses
retain their original grants/options and cost identity. Cancellation preserves
observed turns/usage/session identity and the partial transcript. Transcript filenames
use call IDs even without a provider session; an optional sink failure cannot make
unknown usage free.

Both SDK APIs own POSIX process sessions. Timeout/cancellation signals the group,
allows five seconds, then kills and reaps. Native setup/fix/test/lint/SBFL/Git/gh use
bounded execution; Git/gh retain a 120-second default. Each operation also observes
remaining run time. Cleanup can extend beyond the active deadline; measured duration
remains visible. Critical cleanup/storage failure stays a hold for inspection.
During active graph execution, Ctrl-C/SIGTERM and critical node failures notify
the shared cancellation control before LangGraph waits for worker threads. Sibling
SDK/native writers stop before recovery capture; approval waiting has no active
model process to cancel.

Graph boundaries release the clock before human approval. Parallel workers share
one active owner. Direct model/native calls use the same clock. Measured monotonic
execution spans resumes and excludes offline/approval waiting. Bounded five-second
slices are journaled before execution; rotation reserves the next before sealing the
previous one. After owner death/storage failure each pending slice stays immutable
and is charged in full plus five seconds of cleanup allowance. Timing is incomplete:
no end timestamp is invented and offline waiting is not charged. Missing/corrupt
legacy policies refuse execution before executor construction; inspection remains.
Budget additions cannot reset or extend execution time.

## Recovery

Inspect the printed manifest first. These are examples, not live-run authorization:

```sh
blacksmith recovery inspect --thread-id <id> --config <runtime-config>
blacksmith resume --thread-id <id> --add-budget-usd 5 --config <runtime-config>
blacksmith resume --thread-id <id> --continue-attempt --config <runtime-config>
blacksmith resume --thread-id <id> --continue-attempt --add-budget-usd 5 --config <runtime-config>
```

- Denied-before-start calls retry after a positive finite budget addition, keeping
  identity/counters and consuming no continuation.
- Completed successful overrun responses can be reused after an explicit budget
  amendment, without provider replay or continuation. Original evidence remains.
- Started unknown/interrupted calls and SDK budget stops need explicit
  `--continue-attempt` within saved allowance. A new paid identity is created;
  originals remain. Valid review seats/options are reused.
- Summary recovery returns to approved exact-candidate publication. Completed
  units/gates/reviews/effects are reconciled. An uncertain native create still needs
  exact positive remote observation or inspection; a model continuation cannot
  authorize repeating it.
- Pending sealed amendment flags refuse instead of adding budget twice. Ordinary
  resume never replenishes funds/counters. Exhausted time permits inspection, not
  a fresh allowance under the same run ID. Preserve checkpoints and original times.

Reports distinguish measured cost, conservative charges, reservations, remaining
funds and actual overrun. Incomplete metric totals are null with measured/charged
values separate. PR accounting/time describe publication preparation, not billing.
General readiness still requires remaining slices and authorized live release gates.
