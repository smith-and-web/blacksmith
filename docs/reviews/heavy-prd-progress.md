# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 3 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S01-S02 code and evidence committed as 88070ac/519f361; full suite 783 passed and ruff passed.
- Current turn: 3; S03 complete and verified. Final full suite 833 passed in 120.36 s; ruff passed. No process is left running. This turn made implementation, regression, migration, documentation and evidence progress.
- Stop rule: at turn 30 stop implementation, write a precise handoff, and pause the goal using the user's explicit authorization. Resume only on instruction.
- Branch: `feature/heavy-prd-readiness`.
- S01-S02 implementation commit: `88070ac` (local only).
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
- [ ] S04: Transactional operation journal.
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

Completed slices: S01 fail-closed path policy/guards/audits, S02 exact shared requirements/selected-plan context, S03 version-2 immutable run context/preflight/locks/migration. Final turn-3 full offline suite: **833 passed in 120.36 s** (`heavy-prd-turn-03-pytest-03.log`); `uv run ruff check`: **passed** (`heavy-prd-turn-03-ruff.log`). Earlier verification output is retained. The targeted run-context suite had **50 passed in 19.85 s** before the final added approval assertions; those assertions are covered by the full 833-test run.

Outstanding: S04-S19. This includes the operation journal and replay-safe effects, checked bounded execution, trusted all-layer exact-candidate gate receipts, common typed unit execution subgraph, per-unit planning checkpoints/approved hashes, join integration gates, durable failure artifacts and explicit recovery, strict evidence-backed reviews, final QA/PR approval, publication reconciliation, run-wide budget/deadline admission, DAG scale bounds, disabled-or-hardened sandbox/respond and final acceptance matrix/docs. Current S03 approval restart tests do not prove recovery from killed SDK processes, retained terminal failures or ambiguous Git/publication effects.

**Next action (goal turn 4):** increment this counter once to 4. Implement S04 `blacksmith/receipts.py`: separate per-run SQLite operation journal with WAL, explicit atomic intent/outcome transactions, deterministic run/unit/attempt/kind operation keys, immutable/idempotent event IDs and fail-closed critical writes. Persist operation identity before external effects and retain receipts separately from best-effort observability. Wire journal creation/validation through the version-2 run context and CLI so a receipt-store failure stops before model/Git mutation. Include actual SQLite concurrency/reopen/failure tests and production point-of-use tests. Then S05 checked bounded subprocess/Git service; do not jump ahead to independent Python recovery loops.

S03 interfaces: `create_run_context(config, parsed_prd, thread_id)` creates and validates artifacts; `context_from_state` validates v2 metadata; `RunCloneManager` builds from immutable planning source; main/resume own thread/run locks across drive and approvals. Source/config paths resolve relative to config location. `validate_execution_contract` separates current scheduling semantics from historical structural parsing. Runtime/migration runbook: `docs/heavy-prd-run-context.md`. Per-call budget admission, exact gate receipts, terminal recovery and publication reconciliation remain unimplemented; do not claim readiness from the current green suite.

The goal remains active. At turn 30, stop implementation, save completed/outstanding work, exact verification results and next action, then explicitly pause using the user's authorization. No push/merge/publication/paid canary. Preserve the original untracked spec and probe. `contract.py` changes require human review before any merge; no merge is in scope. No subagents are authorized or required by current instructions.
