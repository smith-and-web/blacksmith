# S15 implementation and acceptance handoff

Status: isolated WIP at goal18/30; S14 is accepted/committed locally. S15 remains
unchecked. All test handles are terminal. R08/R12 decisions remain settled.

## Exact source and transfer boundary

- Primary: feature/heavy-prd-readiness, S14 implementation8652ac237f3f10ab2ac10f37ab40eec15b4de00d.
  Final S14 full1219/ruff/146-source proof is in turn17 environment-02.json.
- S15: /private/tmp/blacksmith-heavy-prd-admission-stage,
  feature/heavy-prd-admission-staging, **1d9841f5e99368b5f92ef01cd9aab0a2fd10f01a**
  afterf2ff68b/fec4d88. Source-only baseline23ad7ff copied146 frozen S14 sources
  before its strict legacy receipt-fixture correction. Current156-source set/hashes
  and terminal receipt hashes: heavy-prd-turn-18-admission-source.json.
- Transfer only the delta23ad7ff..latest after the remaining audit. Never merge or
  replay the snapshot baseline; preserve primary tests/test_receipt_wiring.py.
  The copied untracked spec and .venv symlink are excluded. Tracked staging tree
  is clean. No S15 source has entered the primary.

## Implemented, with scoped evidence only

- Config defaults run50/call5/model1800s/run21600s, finite positive validation.
  --unlimited-cost explicitly freezes None and its critical choice; per-call/time
  limits remain. Fresh budget/clock policies bootstrap; missing/corrupt old policy
  refuses before executor without inventing spend/timestamps.
- Atomic IMMEDIATE ledger grants min(call cap, remaining unreserved run allowance).
  All six paid Executor roles bind immutable CallContext/model key to grant, SDK
  max_budget_usd and settlement. Actual SDK results count before separate settlement;
  unknown/nonterminal usage consumes full reservation and incomplete accounting.
  Cached results reuse original grants/options and finish lost settlement. Actual
  overrun stops all roles, including critic/summary, and denies further admission.
- Denied-before-start review becomes typed unavailable, never clean. Budget-only
  critic/implement/review/summary retry preserves unstarted identity, continuation,
  fix/revision/attempt counters and valid paid seats; one positive finite amendment
  is durable/idempotent. Ordinary resume cannot reset allowance. Graph recovery
  decisions now read authoritative ledger balance instead of worker event sums.
- OwnedCLITransport uses the installed SDK protocol plus an owned POSIX session;
  both public query/client paths receive it. Timeout/raw asyncio cancellation
  completes TERM, grace5, KILL and leader reap before returning. Real local CLI
  descendants cannot perform their delayed sentinel write. Provider transport is
  never called in these tests; no installed SDK file/dependency was modified.
- Active run clock spans graph steps and direct model/native calls, with a shared
  owner for parallel threads. It journals bounded5-second slices before execution,
  measures elapsed monotonic time at normal closure and conservatively charges the
  last unfinished slice after a killed owner. Offline/approval waits get no debit.
  Model/native timeouts use remaining run time; timer failure cancels active work.
  Retention capture has its own bounded native scope after active execution stops.
- Transcripts use unique call IDs without provider session IDs, retain partial
  streams and expose small paths through cost events/metrics. No unknown-session
  overwrite. Publication cost validation supports the transcript reference.

## Verification receipts (not a full S15 pass)

- Turn17 foundation18/11.58s; combined ledger/roles33/19.88s; actual CLI both shapes
  normal/draft4/117.04s, all7 paid calls bound to grants and exact .07 cost.
- Turn18 initial SDK/admission/transcripts49/152.18s. Expanded ledger/actual-role
  overrun/SDK/transcript batch74/181.34s. Includes both installed SDK entry paths,
  timeout/raw cancellation, conservative unknown cost and distinct partial files.
- Actual CLI budget-only recovery02:5/100.10s (critic, implement, review seat,
  summary, explicit unlimited policy). Initial fixture run4 failed/1 passed is
  retained: absent remote log before early halt and invalid leading-dot TOML fixed
  with assertions preserved.
- Clock foundation01:5/5.99s; graph/model/native integration02:36/119.94s.
  Forwarding03:5 passed/1 failed/54.13s proves actual CLI pause exclusion/nondefault
  SDK grant and installed SDK remaining-run deadline/child cleanup. The native case
  used command. instead of command: when looking up its receipt; corrected target04
  passed1/2.57s with all timeout/exit/receipt/elapsed assertions intact.
- Ruff08 passed. Failed development/ruff receipts remain retained. No xfail/skip or
  weakened assertion. Current156-source WIP has no full pytest/global acceptance.

## Remaining work before S15 acceptance

1. Audit and implement started SDK budget-stop recovery for planner/critic and
   summary (currently failed terminal budget results remain cached and stop again).
   Explicit continuation may create a new call identity under remaining allowance;
   denied-before-start amendment must continue to consume no continuation. Extend
   publication-stage selection to explicitly acknowledged uncertain summary calls;
   never repeat an uncertain native push/create or approved candidate/gates.
2. A completed successful call whose actual cost exceeded its reservation should
   be recoverable after a sufficient explicit budget amendment by reusing its
   original result, without a new provider call/continuation. Keep original grant,
   cost and overrun evidence. Review seats must preserve valid cached options.
3. Preserve known captured turns/terminal usage/transcript on BaseException,
   KeyboardInterrupt and cancellation. Current generic interrupted outcome records
   only error_type; conservative charge is safe but known progress may be lost.
   Other critical failures must still halt, including advisory roles.
4. Strengthen timer-failure and actual fresh-process clock crash/resume proof.
   Verify partial last-slice charge, no offline/approval debit, no clock reset,
   shared parallel owner, all writers stopped before capture, and missing/corrupt
   policy refusal before executor. Clock cleanup grace may exceed the active limit;
   report measured time rather than claiming a hard physical instant ceiling.
5. Complete before-every-new-auto-unit audit, policy/config forwarding/migration
   matrix and budget-dependent deterministic recovery checks. Paid calls already
   have atomic admission; avoid stale worker event sums as authority.
6. Honest CLI/PR/metrics reports must expose actual overrun and incomplete usage
   (charged/reserved versus measured cost), conservative clock uncertainty and the
   supported recovery action. Current legacy total-cost text is still insufficient.
   Update config samples, README/architecture and runtime/recovery runbooks.
7. Record fresh exact source/dependency/input identity, run consolidated S15 matrix
   then full pytest/ruff on final primary source; fix without weakening assertions.
   Preserve all failed/crash receipts, inspect diffs and complete local commit/brain
   verification before checking S15 complete. No mocked result is a live bill or
   provider limit/cancellation guarantee.

S16 owns max_parallel_units2, overlap serialization and recursion scaling; do not
change the original dependency contract. S17/S18 disabled alternatives remain only
as specified. No paid canary, push, merge, live publication or Docker smoke. At30
save an exact unfinished handoff and pause; do not reset the counter.
