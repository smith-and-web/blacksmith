# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 8 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S07 committed as f3a1312/5764174; 934 tests and ruff passed.
- Current turn: 8; S08 implemented and verified; final full suite terminal.
- Stop rule: at turn 30 stop implementation, write a precise handoff, and pause the goal using the user's explicit authorization. Resume only on instruction.
- Branch: `feature/heavy-prd-readiness`.
- S01-S02 implementation commit: `88070ac` (local only).
- S03 implementation commit: `e8650ba` (local only).
- S04 implementation commit: `1b0d63f` (local only).
- S05 implementation commit: `ec0aceb` (local only).
- S06 implementation commit: `c13fc34` (local only).
- S07 implementation commit: `f3a1312` (local only).
- S08 implementation commit: `b694e06` (local only).
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

Completed slices: S01-S08 (path policy, full requirements/plan context, immutable v2 context/preflight/locks/migration, critical journal/SDK/commit receipts, bounded native commands, exact trusted unit gates, typed/reset unit state, shared checkpointed unit execution). Final turn-8 full suite: **942 passed in 263.45 s** (`heavy-prd-turn-08-pytest-04.log`); ruff **passed**. Shared-policy/restart group: **8 passed in 28.24 s** (`subgraph-06.log`); progress group: 10 passed in 22.32 s. Intermediate failures/logs retained; no weakened assertions, skips or xfails. Original spec/probe hashes unchanged and unstaged.

S08 architecture: `graph.py` is now the parent scheduler/approval/publication map; `unit_graph.build_unit_graph` compiles one child used by every `execute_unit` task. The Python worker loop and parent recovery topology are removed. Child stages: prepare_attempt, implement, audit_commit, auto_fix, normalize_candidate (parallel only), test_gate, review, recovery_decision and finalize_unit, plus bounded recovery nodes. `nodes.implement.implement(defer_commit=True)` checkpoints successful model metadata/costs as pending_implementation; `audit_and_commit` commits without another SDK call. The direct legacy implement API defaults to immediate commit. UnitDeps now carries SBFL to both shapes. Existing helper/projection/callback APIs remain where useful; child model binding uses the real dependency seam and honors instrumentation.

Child invocation inherits the SQLite saver and requests sync durability only when a saver is present. The installed LangGraph exposes an internal future error when sync is forced without a saver; standalone legacy execution now avoids that invalid request. Each actual parent task checkpoint namespace contains a durable `unit_namespace` run/unit-hash binding. A real child process os._exit(77) drill after model/before commit proves resume starts at audit/commit, makes zero further SDK transport calls and creates exactly one commit with exactly the two original plan/model cost events. No claim for a kill inside a live SDK call.

Parent `unit_outcomes` uses typed UnitOutcome dictionaries keyed by unit ID, with idempotent identical replay and conflict refusal. `unit_results` is a keyed compatibility list. Costs/findings/errors use keyed event IDs; dependency completion records are passed separately from child local output. The same five-call recovery sequence is proven in both shapes: turn cap/continuation, cheap gate fix, single escalation and review revision; no second recovery loop. Parallel multi-commit revisions normalize with checked Git/critical squash receipts before gate/review, preserving tree and one-commit join interface. Completed squash receipt re-entry reuses the actual SHA even when the node checkpoint was lost; ambiguous intent still requires S11. Optional run/unit/attempt stage events bridge through the parent writer and preserve CLI progress without making observation critical.

Migration: a version-2 checkpoint already owning a workspace but lacking `unit_graph_version=1` now refuses execution before executor construction (`legacy_unit_graph_requires_manual_recovery`), preserving checkpoints/artifacts/budgets. Historical inspection remains; pre-workspace approval checkpoints can enter the new graph. New unit workspaces record the marker. S11 owns comprehensive explicit migration/stage reconciliation; do not guess or reset old consumed work.

Outstanding: S09-S19. Planning still runs all unit plans in one node; approved PRD/plan hashes are not enforced yet. Join completeness/integration/final layer unions are S10; full artifact preservation and recovery CLI are S11. Exhausted blocking/unavailable review still has legacy behavior until S12, so do not claim strict review or safe publication. Exact-SHA QA/approval/publication, global admission/model cancellation, 100-unit scale and conditional sandbox/respond hardening remain incomplete. No live release gate, paid canary, push/publication/merge or real Docker smoke has passed/run. Sandbox remains unproved/should stay disabled pending S17.

**Next action (goal turn 9):** read attachment/ledger, increment once 8 -> 9 (not on compaction), recheck worktree/original hashes and brain context. Implement S09 from R02/R06: one checkpointed planning unit per invocation, keyed plans_by_unit_id and deterministic cursor; preserve full PRD/cwd/index/critic context. Reuse completed SDK receipts on restart and do not regenerate already completed unit plans. Keep the single approval after all auto plans, persist exact PRD/plans hashes and immutable approved-plan evidence, and reject missing/wrong-PRD/changed plans before any implement/review call. Forward selected plan, immutable approval/hash metadata through fan-out and the unit_graph seed whitelist. Keep parent/child checkpoint durability and S04 cost identities; no second Python planning/recovery loop. Cover multi-plan restart/critic revisions, wrong/missing/tampered plan/hash, local config/PRD changes during approval, sequential/parallel/resume and no downstream effects. Preserve legacy standalone plan API where practical; diagnose unsupported active checkpoint migrations without paid replay. Then full pytest/ruff, docs/evidence/brain capture.

At turn 30 stop implementation, save precise completed/outstanding work, verification and next action, and pause under explicit user authorization. Never reset this counter or count tools/compaction as turns. Do not resume without instruction or mark unfinished work complete. Original untracked specification/probe remain unchanged/unstaged. `contract.py` requires human review before any merge; no merge in scope. No subagents authorized.
