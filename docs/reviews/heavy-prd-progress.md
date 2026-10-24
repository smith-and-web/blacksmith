# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 13 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S11 controller committed as 03d4308/ff83c7e; 1047 tests and ruff passed.
- Current turn: 13; S11 complete, final 1084-test suite and ruff passed; S12 is next.
- Turn 13 boundary exception: a missed test crash injection reached a real push attempt to a synthetic GitHub remote; it failed before authentication (128), with no gh creation command. Receipt: `heavy-prd-turn-13-unexpected-push.json`. Child publication guard and native offline transport/auth isolation added before further testing.
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
- S11 foundation commit: `68a80c3` (local only; partial at turn 11).
- S11 controller commit: `03d4308` (local only; partial at turn 12).
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
- [x] S11: Artifacts and recovery CLI.
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

## Current handoff (turn 13 closeout)

S01-S11 are complete under the scoped offline acceptance audit in `heavy-prd-evidence.md`. Full S01-S19 goal remains active at counter **13/30**. Do not increment on compaction. S12-S19 and all live gates remain outstanding. No subagents. Original specification/probe remain unchanged/untracked.

Final full-03 handle **52548 is terminal, exit 0: 1084 passed in 837.99 seconds**. No skips, xfails or warnings in this final run. All other test/commit handles must also be terminal before the turn ends. Final ruff passes. All **136 Python source file hashes and the full source-file set** match the pre-run environment record. Final log/ruff/deviation hashes are recorded in `heavy-prd-turn-13-environment.json`. No dependency/lockfile change, weakened assertion, paid canary, merge or successful publication. The failed unintended test push and correction below remain part of acceptance evidence.

Implemented in turn 13: critical capture/verification seals before final artifacts, observed repair or retained-partial evidence without changing original receipt status, no-checkpoint/run-directory and legacy completed inspection, bootstrap capture and supported absolute next action, exact staged/committed formatter/implementation/squash recovery, original effect identities, flat outcome generations, saved child consumption across a lost parent barrier, genuinely unstarted siblings at the saved level base, join generation/rollback recovery reusing exact destination objects (including valid orphans), staged pick completion, lost restore outcome reconciliation, fresh PR rejection/depleted continuation/single-amendment/corruption/native-unknown boundaries. Helper modules: `recovery_repair.py`, `formatter_recovery.py`, `join_recovery.py`.

Budget identity fix: unrelated retained-partial captures must not change the identity of an already prepared budget amendment. A strengthened test failed at cap 5 instead of 3; epochs now include only retained failed amendments for the same checkpoint/request, and the unchanged assertion passes. Unknown native command intents with unconfirmed return codes stop recovery/admission; they are not labeled physically terminated. Native join command scopes carry the same recovery generation as their effect keys. S15 still owns global reservations/SDK cancellation/unknown cost and all-role admission, including stale parallel totals.

## Turn 13 verification

- Repair-01: 10/8.81 s; artifact-regression-01: 34 passed/2 failed/48.29 s (two exact row expectations updated for new bootstrap capture/seals, no provider/no clone assertions preserved); repair-02: 19/40.53 s.
- Effects-regression-01: 66/98.54 s. Effect-exits-01: 5 passed/1 failed/27.40 s (missed injection and boundary exception below); corrected exits-02: 6/25.49 s. Squash-01: 3/7.36 s. Join-recovery-01: 4/52.37 s. Boundaries-01: 5/33.49 s.
- Combined recovery-aggregate-01: 43/184.18 s. Join-final-01: 2/25.93 s. Pick-counts-01: 2/39.41 s, proving exactly one actual cherry-pick per sibling. Budget-epoch-01: 1 failed/4 deselected/3.01 s, preserved. Corrected budget-native-02: 2/7.60 s; budget-native-03: 2/9.22 s.
- Intermediate full-01: **1080 passed/818.47 s**; full-02: **1081 passed/832.58 s** (18 old pytest cleanup warnings). Missing-worker cases: **3 passed/51.55 s** (66 old cleanup warnings). Final full-03: **1084 passed/837.99 s**, exit 0, no warnings/skips/xfails. Ruff passes; all 136 Python source hashes match. No dependency/lockfile change or weakened assertion. Runtime/migration docs and the R06 acceptance table are updated, including scoped holds and all live gates.

## Boundary exception (must remain in final report)

A missed test injection patched `impl._git` rather than real `impl.run_command`, so its child reached legacy publication and attempted a real push to a synthetic GitHub fixture remote. The critical receipt failed **128 before authentication**, verified stderr could not read a GitHub username because no device was configured; no `gh` command or successful publication occurred. Exact receipt/hash/log paths and stderr are in `heavy-prd-turn-13-unexpected-push.json`; failed test log retained. Do not claim no push attempt occurred in turn 13.

Correction: changed injection target, guarded publication in every new child and limited approval to plan. The pytest environment now blocks native network Git transports (local scratch file allowed), isolates gh config and empties publication token variables to stop dotenv restoring real credentials. Host settings unchanged. Only paid SDK transport is replaced; no live model bill/cancellation or canary claim.

## Next action: automatic goal turn 14

Read objective/instructions/ledger/brain, increment 13 -> 14 once, then implement **S12 honest cumulative patch review and typed clean/blocking/unavailable/disabled receipts** through both execution shapes. Trace saved unit base/current gated candidate into reviewer prompt and read_diff; preserve full requirements/approved plan; fail closed on unavailable/malformed/missing terminal responses and any blocking panel seat. Add migration refusal for historical review metadata without inventing hashes. Cover deletion-only defects and cumulative revisions, remaining allowances, valid completed-seat reuse and no forbidden downstream effects with real Git/graph/checkpointer tests, replacing only paid transport. Run full pytest/ruff, retain failed receipts and record versions/source identity. Keep the counter durable and never restart it.

S13 exact approval/QA, S14 publication reconciliation, S15 global admission/deadlines, S16 bounded scheduling, S17/S18 conditional disabled alternatives and S19 full observability/matrix remain mandatory. No paid/Docker/remote release gate has passed. Do not push, merge, publish or run paid canaries; all further tests inherit native offline Git/auth isolation. The full native goal stays active; never shrink it to S11 completion.

At turn 30 stop implementation, save precise completed/outstanding/verification/next-action handoff and pause under explicit user authorization; never resume automatically. Do not shrink the full goal or mark unfinished work complete.

Follow-up interactions for later slices: S12 must allow an explicit unavailable/malformed completed SDK seat to receive a new call key under remaining allowance while reusing completed valid seats; blocking verdicts must not become same-SHA review roulette. S15 admission-denied calls have not started and an explicit budget addition should retry the original stage without consuming a continuation; cached role responses must keep their original recorded max_budget options. S19 must audit additive derived review_revisions_total through recovered child outputs (unit-owned review counters already stay unchanged), and bounded error/history views. These are remaining full-goal requirements, not current release claims.
