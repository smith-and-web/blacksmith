# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 12 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S11 foundation committed as 68a80c3/a5a7732; 1035 tests and ruff passed.
- Current turn: 12; S11 controller/amendments verified; remaining reconciliation/repair incomplete.
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
- S11 foundation commit: `68a80c3` (local only; S11 incomplete).
- S11 controller commit: `03d4308` (local only; S11 incomplete).
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

S01-S10 complete. **S11 remains in progress and unchecked.** Turn 11 committed immutable artifact/inspection and implementation-commit reconciliation as `68a80c3` (docs `a5a7732`), verified by 1035 tests/ruff. Turn 12 adds explicit journaled recovery through the existing graph; final full suite passed: **1047 tests in 672.62 s**, and ruff passed. The full S01-S19 native goal remains active, turn 12/30. No paid canary, publication/push/merge, Docker or remote release gate has run.

Turn 12 implementation: sealed `recovery.amend` files/receipts precede graph scheduling; dispatch validates the sealed patch plus current workspace bytes/index/HEAD. A finite queue selects saved failed units through the same S08 child with original worker, counters/model tier/base/last-passing candidate/approved inputs/cost IDs. Successful siblings are checked and reused. Terminal outcome replacement requires a generation/amendment and retains the failed history. Explicit `--retry-stage` selects a recorded deterministic stage; `--unit-id` selects a failed unit. `--continue-attempt` retains original uncertain model receipts, preserves partial files and consumes remaining continuation allowance with a new call receipt. Planner/critic and review-panel recovery reuse completed roles/seats. Review continuation retains the exact gated candidate. Plan/PR rejection schedules a fresh interrupt. Positive finite `--add-budget-usd` records an addition to the frozen limit; local config changes and ordinary resume do not reset spend. Missing completed graph costs reconcile by call ID. Printed inspection commands quote saved thread/config selections. New code is `recovery_control.py` plus CLI/state/shared-child/reducer/ownership wiring.

## Turn 12 verification closeout

Final command: `uv run pytest -q > docs/reviews/heavy-prd-turn-12-pytest-03.log 2>&1`, exit 0: **1047 passed in 672.62 s**. Ruff exit 0: **All checks passed!** All 129 Python source hashes matched the environment record after the final suite. Dependency versions: Python 3.12, LangGraph 1.2.9, checkpoint 4.1.1, SQLite saver 3.1.0, Claude Agent SDK 0.2.115, Pydantic 2.13.4, pytest 9.1.1 and ruff 0.15.21. No dependency/lockfile changes. Every verification handle is terminal.

Controller batches: 7/41.70 s; 9/35.66 s; 9/28.17 s; 10/37.04 s; combined controller/artifacts 38/73.33 s; final controller **12/45.77 s**. Regression batch 75/131.07 s. Intermediate full-01 1046/680.61 s and full-02 1047/695.02 s predate final guards/assertions. Failed new critic test expected nonexistent `approach`; corrected to exact existing `steps`. Failed parallel budget-policy fixture exposed existing stale sibling totals, explicitly open for S15; sequential retry-policy test keeps all original numeric assertions and passed 1/10.54 s. Stronger panel child/run spend assertion failed at .06 versus .05 (1 failed/30.09 s), fixed in recovery seed cost reconciliation with the assertion unchanged. All failure logs remain verbatim. Old pytest read-only temporary-directory cleanup warnings were recorded without suppression; final full-03 had none. No assertions weakened, skips or xfails.

Original spec/probe SHA-256 unchanged; both remain untracked and unstaged. S11 remains unchecked; full S01-S19 goal active at turn 12/30. No paid canary, push/publication/merge, Docker or remote smoke. Brain `Projects/blacksmith.md` updated with turn 12, exact final proof, supported recovery and explicit remaining S11/S15/live boundaries; targeted lint 0 errors/0 warnings, after more than 70 seconds index errors=0/invalidFrontmatter=0, semantic retrieval ranked the hub first and readback verified counter/proof/incomplete boundary (`691b794e-0c31-4dfc-84c1-4378455a60ee`).

Controller committed locally as `03d4308`; host pre-commit and source/docs `git show --check` passed. Only original spec/probe remain untracked. No push.

## Next action (goal turn 13): finish S11 before S12

Read objective/instructions/ledger/brain, classify turn 12 as progress, increment 12 -> 13 exactly once and recheck branch/original hashes. All turn-12 handles are terminal; no test restart/wait is required. Final source is covered by full-03 and the 129-file environment hash record. Keep original spec/probe unchanged and untracked.

Finish remaining S11 before S12. The new dispatcher is partial acceptance, not a smaller completed goal. Implement/verify generic pending formatter/gate/squash/staged-pick reconciliation, parent join-failure recovery and interruption with a sibling that has not started, preflight/no-checkpoint/old-run inspection, and interrupted capture/amendment repair. Pending critical capture/amendment intentionally refuses; do not silently overwrite old evidence. Earlier preflight errors retain original run directory/input manifest/journal but lack a complete capture. Ordinary known implementation commit replay is already supported; generic dirty/changed effects still hold for inspection.

Join recovery must preserve/reuse prior exact destination objects after whole-level rollback, use generation-aware effect keys for failed originals, retain original source/base mappings and never rerun successful sibling models. Dispatch currently queues only units with a saved child checkpoint; a sibling discarded before its first checkpoint must be scheduled as unstarted work, not treated as a finished/missing outcome. Generation-key changes must not lose ownership of original implementation/formatter evidence. Audit/commit or formatter recovery after an already-created effect needs its original intent/reconciliation proof rather than a fresh-effect replay. Unknown workspace edits/commits remain a hold.

Review explicit recovery now uses the common continuation allowance and per-seat key overrides; keep completed panel seats and old gate attempt identity. S12 will harden unavailable/blocking outcomes and must not inherit the legacy review-finalization success behavior. Cost amendments preserve original events and use saved caps, but S15 still owns global reservations, unknown-spend handling, SDK cancellation and first-call/all-role admission. No live invoice/cancellation proof is inferred from offline transport replacements.

Potential controller refinements to cover before S11 closeout: a recorded amendment lost between receipt and checkpoint must be consumed once; pending scheduled amendments must reject repeated flags instead of adding budget twice; old snapshots must remain readable; malformed workspace/outcome metadata must produce a diagnostic before executor; depleted continuations, fresh PR rejection and multiple recovered failed generations need explicit tests. Generation histories currently retain complete prior outcomes; keep checkpoint growth bounded while preserving originals in durable artifacts. Outcomes from recovery without an earlier terminal outcome should still record their current generation/amendment; cover a later failed generation explicitly. Add an explicit START recovery path map and malformed workspace AttributeError diagnostics as needed. Complete exact-source integrity tests, full pytest/ruff and docs/evidence/brain before marking S11.

S12-S19 and every live release gate remain open. Do not claim general readiness, safe publication, exact approval, live budget/cancellation or Docker isolation from the current mocked transport tests. At turn 30 stop implementation, save completed/outstanding/verification/next-action handoff and pause under the user's explicit authorization. Never reset the counter; compaction/tools are not goal turns. Resume only on instruction. No subagents authorized. `contract.py` requires human review before any eventual merge; no merge in scope.
