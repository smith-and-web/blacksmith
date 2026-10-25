# S15 implementation and acceptance handoff

Goal turn19/30. S01-S14 accepted locally; S15 implemented and awaiting full
primary verification. S16-S19 remain required. Progress/evidence records retain
all prior failures, source snapshots and recovery proof.

## Exact source

- Primary branch feature/heavy-prd-readiness, accepted base1afc9c2/S14 implementation8652ac2.
- Source-only staging commit **7a48358eebf855bd48a02ab2d0422b76fd0284ba** at
  /private/tmp/blacksmith-heavy-prd-admission-stage, branch feature/heavy-prd-admission-staging.
- Only delta23ad7ff..7a48358 was checked/applied to primary. All165 Python source
  hashes match staging except the deliberately retained accepted strict
  tests/test_receipt_wiring.py fixture. Baseline was not replayed. Spec/.venv excluded.
- Complete source/config/input/runtime/dependency/patch/receipt identity:
  heavy-prd-turn-19-environment-01.json. Both original inputs unchanged/untracked.
- Primary ruff/whitespace passed. Live full pytest **26162**, log
  heavy-prd-turn-19-pytest-01.log, basetemp /private/tmp/blacksmith-heavy-prd-t19-full-01.
  No primary Python edits after launch. Poll same handle; do not restart on timeout.

## Implemented

- Default run50/call5/model1800s/run21600s policies, finite positive validation,
  frozen explicit --unlimited-cost choice. Resume validates policies before any
  native observation or executor; migration tests prove unchanged old evidence.
- Atomic critical journal grants for all six Executor roles, exact SDK option
  forwarding, conservative unknown/partial spend, historical overrun, incomplete
  accounting and one keyed partial event/transcript per unique call.
- Explicit recovery distinguishes denied-before-start, started budget-stop/unknown
  and completed successful overrun. It reuses valid cached results/options/seats,
  retains failed receipts/counters and requires a continuation only for new paid work.
- Persistent monotonic execution clock shared across threads, excludes approval
  waits, reserves bounded slices before work and charges uncertain crash slices plus
  cleanup allowance conservatively. Native/model work shares remaining deadline.
- Owned local SDK POSIX process sessions; TERM/grace5/KILL/reap under timeout,
  raw task cancellation, SIGINT/SIGTERM and critical graph failures before LangGraph
  waits or recovery capture. Cleanup is idempotent and critical on uncertainty.
  Darwin group-probe EPERM is accepted only after complete bounded PID/PGID/state
  evidence shows no live members; no command/environment contents collected.
- Ledger-based new-unit guards, measured versus conservative/held/remaining cost,
  persistent duration and incomplete markers in CLI, metrics and PR snapshots.
  Metrics accounting migration is additive; unknown exact totals stay null.
- Runtime/config/migration/recovery runbook: ../heavy-prd-admission.md.

## Scoped evidence and remaining acceptance

Final transport17: **11 passed/7 expected retained SDK warnings/108.67s**. Four
additional actual CLI scope-entry repeats passed after fixing the observed EPERM
race, including one positively observed no-live reconciliation. Real CLI started
call recovery covers five roles by denied/budget-stop/unknown/overrun cases;
clock crash/critical rotation, accounting, migration and new-unit tests are retained
in turn19 evidence. Earlier failed fixture and production-behavior receipts remain.
These are scoped proof; they do not replace the full primary suite.

Next: observe full26162 terminal result, verify the exact165-source/config/input/
dependency set, fix actual failures without weakening assertions and refresh ruff.
Only then commit/check S15. Then S16-S19 in dependency order. No paid provider,
real publication or live Docker acceptance is inferred. Full goal stays active;
at30 save precise handoff and pause without automatic resumption.
