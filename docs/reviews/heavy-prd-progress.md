# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 11 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S10 committed as 07965ca/e23dc29; 1008 tests and ruff passed.
- Current turn: 11; S11 artifact/commit-reconciliation foundation verified; explicit controller still incomplete.
- Stop rule: at turn 30 stop implementation, write a precise handoff, and pause the goal using the user's explicit authorization. Resume only on instruction.
- Branch: `feature/heavy-prd-readiness`.
- S01-S02 implementation commit: `88070ac` (local only).
- S03 implementation commit: `e8650ba` (local only).
- S04 implementation commit: `1b0d63f` (local only).
- S05 implementation commit: `ec0aceb` (local only).
- S06 implementation commit: `c13fc34` (local only).
- S07 implementation commit: `f3a1312` (local only).
- S08 implementation commit: `b694e06` (local only).
- S09 implementation commit: `adc56d2` (local only).
- S10 implementation commit: `07965ca` (local only).
- Starting revision: `dcc3bb56940c3907b2e0fcaf13c254e1c55f9767` (verified).
- Existing untracked inputs preserved: specification and `2026-10-24-readiness-probes.py`.
- No push, merge, publication, paid model call or live Blacksmith canary authorized.

## Input preservation

- `docs/heavy-prd-readiness-spec.md` initial SHA-256: `4aecbe39632dd6aa1060b95b1319b4ec40a62824f1f094745d36dd463cd3a4a3`.
- `docs/reviews/2026-10-24-readiness-probes.py` initial SHA-256: `70e36e96aba1a449244bddca13150eeeda7fe0874e663f8b75a8f44ad0e2901c`.

## Implementation checklist (dependency order)

- [x] S01: Path policy and implement guard.
- [x] S02: Shared requirements and plan context.
- [x] S03: Immutable run context, paths, preflight and locks.
- [x] S04: Transactional operation journal.
- [x] S05: Checked bounded command and Git execution.
- [x] S06: Trusted multi-layer exact-candidate gates.
- [x] S07: Typed per-unit state and reset factory.
- [x] S08: Shared checkpointed unit subgraph.
- [x] S09: Per-unit checkpointed planning and approved hashes.
- [x] S10: Join reconciliation and integration gates.
- [ ] S11: Artifacts and recovery CLI.
- [ ] S12: Honest patch reviews and receipts.
- [ ] S13: Human-unit validation and exact-SHA approval.
- [ ] S14: Publication reconciliation.
- [ ] S15: Run-wide admission and cancellation.
- [ ] S16: Bounded scheduling and recursion.
- [ ] S17: Sandbox ownership or explicit disabled-feature rejection.
- [ ] S18: Hardened respond or explicit unsupported-command error.
- [ ] S19: Observability, docs and complete acceptance matrix.

## Acceptance and verification

See [heavy-prd-evidence.md](heavy-prd-evidence.md) for commands, scoped results, requirement coverage and release gates. A checked slice requires production wiring and regression evidence; partial modules are not completion.

## Current handoff

S01-S10 are complete. **S11 is in progress and remains unchecked.** Turn 11 implements its artifact/inspection and observed implementation-commit reconciliation foundation. Final full suite: **1035 passed in 620.65 s** (`heavy-prd-turn-11-pytest-04.log`); ruff **passed**. All verification handles terminal. Original spec/probe SHA-256 unchanged and neither original is staged. No weakened assertions, skips or xfails. No paid canary, publication/push/merge or Docker/remote release gate ran.

Implemented foundation: `recovery.py` initializes a critical per-run index before planning; protected CLI failures/rejections/exceptions under the held lock snapshot full staged/unstaged binary patches, nonignored untracked files and safe symlink metadata, refs bundles including orphan commits, latest root/child serialized state and failed phases/counters, original inputs/plans/verification/command artifacts and consistent journal backup. Completed captures flush files/directories then atomically publish manifest/index with critical ownership receipts. Schema/hash/path/corruption checks run at context verification and production call-context points; capture rejects symlinked critical directories. Read-only `recovery inspect --thread-id --config` loads no credentials/executor and reports paths/bytes/uncertain call IDs. V2 cleanup retains successful workspaces too, with no automatic collection. Incomplete capture is retained and refused, not silently regenerated.

Ordinary commit reconciliation: implementation writes staged-tree intent before Git and observed-tree outcome afterward. Exact clean HEAD/parent/tree/operation trailer reuses the committed effect after lost outcome/checkpoint. Pending original intent may complete; failed/interrupted originals remain immutable alongside `git.commit.reconciliation` observed-effect proof, accepted by formatter ownership against the original trailer. Unknown extra changes/missing old tree proof still refuse. Actual process-exit and KeyboardInterrupt tests prove same SHA/one commit/two original costs and zero further SDK transport calls. A killed SDK test proves original uncertain status/partial bytes survive and ordinary resume refuses without transport replay. No live billing/cancellation or generic stage-recovery claim.

Remaining S11 acceptance is substantial: explicit failed-stage/uncertain-continuation dispatcher, recovery generations and immutable outcome amendment, fresh approval after rejection, positive finite --add-budget-usd amendment, pending formatter/gate/squash/staged-pick reconciliation, preflight/no-checkpoint/old-run inspection and interrupted-capture repair. Earlier preflight failure preserves its original run directory/input manifest but lacks a full capture record. Contextual next-action command reporting needs original config/quoting. Current terminal resume returns HALTED without rescheduling; it is not accepted recovery. Do not advance to S12 or mark S11 complete from this foundation.

S12-S19 and every live release gate also remain open. Strict blocked/unavailable reviews, exact-SHA approval including QA, publication reconciliation, global admission/deadlines, bounded scheduling/100-unit scale, conditional sandbox/respond alternatives and full observability/matrix are not established yet. Existing exhausted-review and QA draft behavior remains legacy. No general readiness/safe-publication/exactly-once invoice claim.

## Turn 11 verification closeout

Artifacts-01: 6/20.07 s. Reconcile-01: 42/58.62 s. Artifacts-02: 16/31.24 s; artifacts-03: 19/36.43 s; artifacts-04: 24/37.98 s. Foundation-01: 35/54.39 s. Final directory-link guard: 1/23.00 s. Receipt-wiring-01: 5 passed/4 failed in 17.78 s; wiring-02: 9/40.03 s. Four old assertions assumed SDK was the last/all journal record; replacements pin exact new capture/bootstrap rows plus original model/provider/cost behavior. Full-01: 1020 passed/4 failed, 603.81 s. Full-02: 1028/4, 655.99 s. Full-03: 1034/649.58 s before last guard. Final full-04: **1035/620.65 s**, exit 0. Ruff **passed**. Source/docs whitespace clean excluding verbatim failed logs. No dependencies/lockfile changes. Every handle terminal.

## Next action (goal turn 12): finish S11 before S12

Read objective/instructions/ledger/brain, increment 11 -> 12 exactly once, recheck current files/hashes/branch. S11 is not a smaller completed goal; native goal remains full S01-S19. Build explicit recovery under original budgets/receipts using this foundation and the same compiled S08 child. No second Python unit-recovery loop.

Useful current interfaces: `recovery.initialize/validate/capture/load_state/inspect/protect_run`; `OperationJournal.operations()` yields validated history in creation order; `unit_failed_stage` is in UnitAttemptState/UnitOutcome and snapshots. `load_state` validates sealed files/serializer before returning rich state. CLI protect guard is after preflight. CLI inspect is by thread only and read-only. Capture entries are independently critical-owned, but freshness for a recovery action must derive from journal/checkpoint order, not blindly trust index list order. Ordinary CLI resume already distinguishes real interrupts (Command.resume) and interrupted nodes (None), but terminal END needs explicit dispatch.

Implement a recorded recovery amendment/generation before rescheduling. Unit/outcome reducers currently reject conflicting terminal outcomes; replacement must preserve the original/hash and require an authorized generation chain. Keep consumed continuation/fix/review counts, model tier, unit/attempt bases, last passing SHA, cost IDs and immutable PRD/plans/config. The child runner currently resets unit_initialized and parallel preparation recreates workers; recovery must enter the existing saved worker/stage without a new clone/factory reset, still using the same unit graph. Root recovery may queue failed siblings as finite graph transitions; do not repeat successful sibling models or arbitrarily repair a cross-unit integration failure by rerunning a last unit.

Explicit uncertain SDK continuation must acknowledge/retain the old receipt, consume remaining continuation allowance, preserve partial files and use a new call receipt. Ordinary resume must not repeat it. Completed planner/critic responses within a partially interrupted planning unit still need reuse (only the uncertain role/key may change). Rejection resume must create a fresh plan/PR interrupt. Cost exhaustion requires an explicit positive finite --add-budget-usd N persisted amendment; ordinary resume never resets spend. Reconcile missing state cost events from journal by call ID without changing already-recorded payloads or treating unknown usage as free. S15 owns full global reservations/unknown-spend enforcement; S11 must not bypass its future ledger.

Pending deterministic stages: formatter/gate/squash/pick intents still hold diagnostics. Reconcile only provable Git/tree/receipt/command evidence; retain unknown extra changes for inspection. S10 join keys currently join:<level>; failed receipts cannot be overwritten, so explicit retry needs generation-aware keys/mappings. A controlled join rollback leaves prior destination objects in Git and source workers intact; reuse those exact existing commits instead of duplicating them. Newly captured refs preserve orphan effects. `git.commit.reconciliation` already supports committed effects with original immutable interrupted status. Preserve preflight failures and incomplete captures, and add safe supported inspection/repair without manufacturing missing legacy hashes or paid work. Test real exits, explicit stage retry with no sibling/SDK replay, reapproval, budget preservation and unknown/corrupt/no-effect cases; then full pytest/ruff, docs/evidence/brain/local commits.

At turn 30 stop implementation, save a precise completed/outstanding/verification/next-action handoff and pause under explicit user authorization. Never reset the counter; compaction/tools are not goal turns. Resume only on instruction. No subagents authorized. No push, merge, publication or paid Blacksmith canary. `contract.py` requires human review before any eventual merge; no merge in scope.
