# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 4 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S03 committed as e8650ba/8902960 with 833 passing tests, ruff and migration/restart evidence.
- Current turn: 4; S04 complete and verified. Final full suite 868 passed in 130.47 s; ruff passed. All test process handles are terminal.
- Stop rule: at turn 30 stop implementation, write a precise handoff, and pause the goal using the user's explicit authorization. Resume only on instruction.
- Branch: `feature/heavy-prd-readiness`.
- S01-S02 implementation commit: `88070ac` (local only).
- S03 implementation commit: `e8650ba` (local only).
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
- [ ] S05: Checked bounded command and Git execution.
- [ ] S06: Trusted multi-layer exact-candidate gates.
- [ ] S07: Typed per-unit state and reset factory.
- [ ] S08: Shared checkpointed unit subgraph.
- [ ] S09: Per-unit checkpointed planning and approved hashes.
- [ ] S10: Join reconciliation and integration gates.
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

Completed slices: S01 path policy, S02 full requirements/plan context, S03 immutable version-2 context/preflight/locks/migration, S04 critical operation journal and SDK/commit receipt wiring. Final turn-4 full offline suite: **868 passed in 130.47 s** (`heavy-prd-turn-04-pytest-02.log`); `uv run ruff check`: **passed**. The journal/wiring tests had **35 passed in 8.53 s**; the SDK/context/replay group had **57 passed in 11.64 s**. An initial full run had 867 passed/1 failed because an old exact state-schema assertion required append-only costs; it now asserts the actual keyed reducer and replay totals. No assertions were weakened or xfails added.

S04 interfaces: `OperationJournal.for_context/create/verify/intent/finish/get/events`, `OperationKey`, `CallContext`, `call_context_for` and `attempt_identity` in `receipts.py`. Journal identity includes the immutable context fingerprint; connections use WAL, FULL sync, foreign keys, explicit IMMEDIATE transactions and mode=rw on reopen. Intent/outcome/event identities are immutable and idempotent. SDK calls persist intent before transport, complete result plus cost atomically, reuse completed results and refuse uncertain replay. Implementation commits have operation trailers and checked reuse of an identical clean HEAD. Critical failures escape optional critic/summary fallbacks. Cost state uses `merge_events` keyed by receipt IDs; legacy unkeyed events keep append behavior.

Outstanding: S05-S19. In particular, the journal does not yet bound run spend/time or automatically reconcile every Git/fixer/squash/join/publication effect. Unknown/ambiguous SDK calls stop; explicit bounded continuation and terminal-stage recovery remain S11/S15. Full artifact retention, all-layer exact gate receipts, shared compiled unit graph, per-unit plan checkpoints/hashes, honest strict review, exact-SHA approval/publication and large-DAG limits remain incomplete. No live gate has passed.

**Next action (goal turn 5):** increment this counter once to 5. Implement S05 checked bounded subprocess/Git service. Use file-backed capped stdout/stderr with byte counts/truncation markers and bounded state tails; POSIX new sessions; on timeout/cancel TERM the process group, wait at most 5 seconds, KILL and reap. Use 120-second Git/gh defaults and configured command limits for setup/fix/test/lint/SBFL. Strip model/admin/GitHub credential variables from trusted toolchain children. Forward service settings from config through production call sites, and fail on add/amend/reset/squash/Git/gh errors. Journal deterministic command intents/outcomes with meaningful operation IDs. Use actual subprocess/child-process/large-output/Git-failure regressions; keep sandbox disabled until S17's allowed rejection or proven ownership path. Then S06 exact multi-layer gate receipts/trusted toolchain. Do not add another independent Python recovery loop.

Tests now use explicit SDK native tool sets and text-only summaries; real SDK options and installed CLI flag construction were checked without a live call. Missing terminal SDK responses are errors with known progress retained, not success. Runtime runbook: `docs/heavy-prd-run-context.md`. Full recovery after killed SDK processes, final publication and exact invoice limits are not established by the green offline suite.

The goal remains active. At turn 30, stop implementation, save completed/outstanding work, exact verification results and next action, then explicitly pause under the user's authorization. No push/merge/publication/paid canary. Preserve the original untracked spec and probe. `contract.py` changes still require human review before any merge; no merge is in scope. No subagents are authorized or required by current instructions.
