# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 9 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S08 committed as b694e06/84b008e; 942 tests and ruff passed.
- Current turn: 9; S09 implemented and verified; final full suite terminal.
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

Completed slices: S01-S09. Final turn-9 full suite: **973 passed in 292.66 s** (`heavy-prd-turn-09-pytest-03.log`); ruff **passed**. Focused planning/graph/plan group: **73 passed in 35.39 s**; prior planning/run-context group: **79 passed in 56.58 s**. All verification handles terminal. This remains a partial implementation, with S10-S19 and all live release gates outstanding. Original spec/probe remain unchanged and unstaged. No assertions weakened, skips or xfails added.

S09 architecture: parent `plan` self-loops one declaration-order auto unit per invocation, carrying keyed plans, a deterministic prefix/cursor and a cached static prompt/map. The CLI streams with sync durability. Per-unit plan JSON files bind full record hashes to run/unit/immutable PRD; the proposal is atomically persisted before the single approval interrupt, with accepted bundle/hash evidence afterward. Full approval fields pass through parent fan-out and `unit_graph.unit_runner` seeds. All implement/review calls validate the bundle, selected unit/plan and exact disk evidence before transport. Direct legacy nodes without versioned run identity retain their API; v2 state losing its context fails closed.

Restart evidence replaces only paid SDK transport and uses actual Executor/options/journal/Git/SQLite/CLI. Real separate processes exit between unit plans and before a critic; completed plan calls/critic revisions reuse receipts without another transport effect, preserving cost event identities. Tamper matrix covers missing/wrong/modified/rehash-substituted plans and approval files/state, foreign/duplicate units, lost snapshot/context and selected-plan/unit mismatch. Actual sequential and parallel child entries refuse lost approval bindings with no model/publication effect. These do not prove live billing/cancellation behavior.

Migration: old active workspaces without unit_graph_version=1 still refuse S08 topology migration. Old v2 plans without planning_version=1 now refuse with legacy_planning_requires_manual_recovery before executor construction; checkpoints/budgets/artifacts remain exact. New partial plans validate their prefix and files before resume; pre-plan interruptions can reuse S04 responses without inventing an old approval hash. Full terminal-stage recovery/artifact retention remains S11; blocking/unavailable review remains S12.

**Next action after S09 closeout (goal turn 10):** read attachment/ledger/instructions/brain, increment 9 -> 10 exactly once, recheck hashes/state. Implement S10 from R04/R06 in dependency order: require exactly one keyed successful outcome per expected auto unit (reject missing/duplicate/foreign/incorrect level/source/base/receipt), save level base before fan-out, join recorded exact source SHA one unit at a time with critical intent/outcome and source-to-destination mapping/restart reconciliation. Completed picks must be reused, not repeated; unknown changes halt. On conflict/fetch/pick failure retain all workers/logs, checked abort/reset shared tree to saved level base (not merely before last pick), and preserve original cause. Add checkpointed join progression as needed. Run an integration gate after every multi-unit join using the union of all completed auto layers, and a final union gate on the final exact candidate before PR approval (including terminal-human route); use verification.run_candidate_gate scope=level/final and verify_gate_receipt, with fresh nonpassing evidence on exceptions. Integration failure halts, never retries an arbitrary last worker. Test combined-invalid siblings, earlier layer missing from last sibling, exact SHA/receipt/dirty errors, missing/foreign outcomes, second-pick conflict rollback, and real restart after one join effect. Keep no push/paid canary; S11 owns comprehensive recovery and S12-S14 strict review/exact approval/publication.

At turn 30 stop implementation, save precise completed/outstanding work, verification and next action, and pause under explicit user authorization. Never reset the counter or count tools/compaction as turns. Do not resume without instruction or mark unfinished work complete. No subagents authorized. `contract.py` requires human review before any merge; no merge is in scope.

## Turn 9 verification closeout

Targeted-01: 112 passed/1 failed (path-rule fixture now generates plans first). Integrity-01: 24 passed in 19.73 s. Integrity-02: 79 passed in 56.58 s. Integrity-03: 73 passed in 35.39 s, including missing-context guard. Topology initial failure fixed with explicit transition metadata, retaining assertion; topology-02: 1 passed in 0.25 s. Full-01: 966 passed in 280.82 s; full-02: 972 passed in 297.49 s, before final extra guard/case. Final full-03: **973 passed in 292.66 s**, exit 0. `uv run ruff check`: **passed**. Source/docs whitespace clean (verbatim failed log receipts excluded). No dependency/lockfile update.

All test/verification handles were polled terminal. Original input hashes unchanged and originals remain untracked/unstaged. S09 completion criterion met; goal remains active at turn 9/30, next S10. No paid canary, push/publication, merge, real Docker smoke or remote release gate has run.
