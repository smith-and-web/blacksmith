# S15 implementation and acceptance handoff

Status: isolated partial admission wiring at goal17/30. S14 full rerun02 remains
live in the primary source tree. S15 is not accepted: production deadlines,
CLI unlimited choice and budget-only recovery are unfinished. The settled R08/R12 requirements govern the remaining work.

## Saved source and foundation proof

Worktree: `/private/tmp/blacksmith-heavy-prd-admission-stage`, branch
`feature/heavy-prd-admission-staging`. Baseline23ad7ff copied exactly the146 frozen
S14 Python sources. WIPs bc11a18 and latest
`a10683e09f8a7abe57a0720fc7ae93296e2c4760` add the ledger foundation. Latest
`fec4d88c216ff9b3b8edb700b491ae5c256f7120` adds CallContext run identity, fresh
policy bootstrap, Executor role grants/cached-option reuse/settlement and a resume
policy check plus tests. Transfer only the delta against23ad7ff after S14 acceptance;
do not merge/replay the baseline.
Copied spec and .venv symlink are untracked and excluded. Source identity is in
heavy-prd-turn-17-budget-source.json. No primary source was changed by this work.

The ledger uses existing critical operations, preserving the journal schema.
Fresh policy initialization records the explicit unlimited choice; absent or
corrupt policy refuses. An IMMEDIATE SQLite transaction reserves the smaller of
$5 and remaining unreserved budget. Same-call replay reuses the original grant.
Denials persist a zero-charge, not-started record and typed identity. Actual SDK
result cost is read in the allocation transaction even before separate settlement,
so a settlement gap cannot hide spend/overrun. Missing cost consumes the full
reservation with incomplete accounting. Model SDK budget must equal its grant.
An overrun denies further admissions under the unchanged budget; existing verified
budget amendments determine the revised cap. Cached results retain original grants.

Foundation-01:14 passed/13.41s. Foundation-02: collection error from a test import
of a nonexistent module; fixed to the actual contract parser. Foundation-03:
**16 passed/11.75s**, including eight concurrent reservations under a $12 cap,
idempotent grants, missing-cost states, known zero versus unknown, before-settlement
cost/overrun, critical rollback, hash-valid semantic corruption, wrong SDK budget
and explicit uncapped policy retaining the $5 per-call reserve. Focused ruff05
passed. Foundation-04 adds semantic call/cap identity checks:18 passed/11.58s,
focused ruff06 passed. These tests write explicit offline model receipts; they are not SDK/runtime
admission or billing evidence. Subsequent isolated monetary wiring: combined ledger/Executor
roles01 passed33/19.88s; actual CLI sequential/parallel normal/draft02 passed4/117.04s,
all7 paid calls bound to their grants with exact settled cost. Focused ruff10 passed.
Unknown nonterminal partial cost remains charged at full reservation; cached responses
keep original options and finish lost settlement without provider replay. S15 remains
partial: no deadline/cancel/unlimited-CLI/budget-only recovery acceptance.

## Required production integration

1. Policy now initializes during fresh RunContext bootstrap after the recovery index,
   before any model call. Add CLI `--unlimited-cost`, freeze the selected None cap
   only with its explicit critical policy choice, and record it. Validate missing
   old admission metadata before executor on resume without inventing costs or
   timestamps. Keep historical completed inspection and immutable original inputs.
2. Initial Executor role wiring is present and scoped-tested; finish the full integration: plan, critic, initial and
   escalated implement, review seats, summary, and respond if enabled (S18 may
   explicitly disable it). Supply sufficient validated run identity through
   CallContext now carries and verifies the full immutable RunContext alongside
   journal identity. Keep that binding through every production path. Pass the actual reservation as SDK max_budget_usd. Completed cached
   calls/seats must reuse the exact recorded original budget option even after
   remaining cap changes; they must not reserve or call the provider again.
3. Reserve before the critical model intent/SDK start. Distinguish denied/unstarted
   calls from admitted/uncertain ones. Propagate denial/timeout/budget-stop through
   advisory critic/summary paths instead of silently falling back and publishing.
   Review denial must produce typed unavailable evidence, never clean or an
   uncaught generic critical error. Other critical storage/integrity errors still
   fail closed. Keep original call/denial/reservation/cost receipts immutable.
4. Budget-only resume of a denied-before-start role must preserve the original
   attempt/continuation counters and may retry that unstarted SDK identity under
   the amended cap. Reuse valid review seats and original options. A new review
   decision generation may be needed without giving a never-started seat an
   unnecessary new paid-call identity. Started uncertain calls still require the
   explicit existing continuation allowance; unchanged blocked code cannot seek
   another review vote. Include publication summary/budget-only recovery, which
   the current S14 stage selection enables only with --retry-stage.
5. Use ledger balance in budget-dependent graph/recovery decisions and before each
   new auto unit; stale per-worker cost_events cannot authorize additional spend.
   Preserve paid partial work on denial/overrun. Report actual overrun and incomplete
   accounting; do not turn missing usage into a zero-dollar claim or an estimate.
   SDK per-call budget termination must halt later paid/effect paths and preserve
   its original result/cost, including critic and summary roles.
6. Apply model_timeout_s to both query and guarded ClaudeSDKClient paths, and close
   the SDK connection plus all owned native children on timeout/cancellation. Use
   an owned POSIX session/process group, TERM then5-second grace/KILL/reap. Verify
   transport cleanup against the installed SDK. The installed 0.2.115 close source
   explicitly notes a raw asyncio cancellation limitation; do not infer complete
   child cleanup from its version or close method alone. Do not modify installed
   SDK files. Public query/ClaudeSDKClient support custom transport; options has
   max_budget_usd and cli_path. Tests must use a local fake CLI/transport, never a
   real paid query, and prove a delayed child cannot write after timeout.
7. Persist accumulated execution time across resumes, excluding approval waits.
   CLI graph-step boundaries can isolate active execution from the approver; direct
   node/model/command callers also need an enforceable fallback. Enforce remaining
   run time before/during model and native work, retaining Git/gh's120-second cap.
   Do not count offline pause/approval time or invent old timestamps. Cancellation
   must stop active writers before recovery capture; uncertain physical process
   state must remain explicitly uncertain. Preserve partial bytes/evidence.
8. Give transcripts unique call-based paths, even without a provider session ID;
   never overwrite unknown-session.jsonl. Extend honest cost/deadline reporting and
   docs/config samples. Keep optional sink failures distinct from critical policy.

S16 owns bounded scheduling/recursion and max_parallel_units=2. Do not mutate the
original contract to serialize overlap groups. S17/S18 conditional disabled-feature
alternatives remain allowed only as specified. No dependency bump or paid canary.

## Required acceptance before S15 completion

- Real CLI/graph/Git/checkpointer paths for both scheduling shapes and resume;
  budget exhaustion in every paid role/seat, before the first call and next unit.
- Concurrent reserves within cap; actual cost/refund, unknown full charge,
  interruption/crash, overrun denial, and critical SQL failures before transport.
- Explicit finite positive budget amendments, repeated amendment recovery without
  double addition, original cached seat options, and no continuation consumed for
  denied/unstarted calls. Normal resume cannot reset spend.
- Finite validation/defaults/config forwarding and explicit unlimited flag proof.
- Actual SDK option construction and protocol-accurate fakes; real local child
  timeout/cancel/sentinel tests, run-clock persistence and approval-wait exclusion.
- Unique transcript paths; incomplete accounting and no false review/done labels.
- Final full pytest/ruff with exact source/dependency/input identity and retained
  failed/crash receipts. No mocked test satisfies a live provider or release gate.
