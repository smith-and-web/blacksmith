# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 2 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: no progress; interruption before execution. Revalidated by clean tracked worktree on main.
- Current turn: 2; S01 and S02 implemented and verified. Full offline suite: 783 passed; ruff passed. This turn made implementation and verification progress.
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
- [ ] S03: Immutable run context, paths, preflight and locks.
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

Completed slices: S01 fail-closed path policy/guards/audits and S02 exact shared requirements/selected-plan context. Full offline suite: **783 passed in 103.81 s** (`heavy-prd-turn-02-pytest-03.log`); `uv run ruff check`: passed (`heavy-prd-turn-02-ruff.log`). Earlier failure receipts retained. The original two Git fixture failures are fixed through environment isolation, not host settings changes.

Outstanding: S03-S19, including immutable base/run snapshot and locks, operation journal, checked bounded commands, exact all-layer gate receipts, shared checkpointed unit subgraph, restart/recovery, honest reviews, QA/PR approval, publication reconciliation, admission budget, large-DAG bounds, disabled-or-hardened sandbox/respond and final operational docs. No live gate has passed. See the full invariant matrix in `heavy-prd-evidence.md`.

**Next action (goal turn 3):** increment this counter once to 3, then implement S03 (`run_context.py`, config-relative canonical storage/target paths, immutable base/PRD/config/toolchain manifest, pre-spend semantic validation, planning snapshot, unique run/worker identities, per-run flock and version-2 migration boundary). Wire the same base into planning and clone creation, and exercise real Git/CLI/checkpointer tests, including source changing during plan approval and concurrent lock denial. Do not silently change budgets or accept legacy resume state.

The goal remains active. At turn 30, stop implementation, save completed/outstanding work, exact verification results and next action, then explicitly pause using the user's authorization. No push/merge/publication/paid canary. Preserve the original untracked spec and probe. Repository `contract.py` changes require human review before any merge; no merge is in scope. No subagents are authorized or required by current instructions.
