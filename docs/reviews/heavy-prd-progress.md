# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 10 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S09 committed as adc56d2/545e6e8; 973 tests and ruff passed.
- Current turn: 10; S10 implemented and verified; final full suite terminal.
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

Completed slices: S01-S10. Final turn-10 full suite: **1008 passed in 516.64 s** (`heavy-prd-turn-10-pytest-03.log`); ruff **passed**. Prior full-02: 1001/480.41 s. Thirty-five new integrity cases; focused 28-case group and final eight edge cases passed. Actual subprocess join drills passed at all three crash boundaries with exact SHA/commit/cost preservation and zero further SDK transport calls. All verification handles terminal. Original spec/probe hashes unchanged and both inputs remain untracked/unstaged. No assertions weakened, skips or xfails added.

S10 runtime: `joins.py` records critical saved level bases and expected units, checks complete typed outcomes plus worker source/base/path/branch/cleanliness/gate proof, and picks exactly one recorded source SHA per checkpoint. Pick/commit intents, staged tree, parent, source/operation trailers and source-to-destination mappings reconcile a completed effect after lost outcome/checkpoint. `integration.py` validates critical historical unit evidence and reachable destinations, runs completed-layer unions after each multi-unit join and the full final union before PR/QA seams, and rechecks final evidence at approval/publication entry before summary/Git/gh effects. Terminal status wins over old approval bools. Missing v2 context cannot degrade into legacy compatibility.

Controlled join failure retains failure/conflict artifacts then checked abort/reset restores the whole level base. Failed rollback retains both causes without retry. Unknown extra edits remain untouched. HALTED/rejected cleanup preserves shared/worker directories and branches, including sibling evidence. Successful cleanup still removes clones. New v2 workspaces record join_graph_version=1; active older workspaces missing it refuse before executor construction with legacy_join_graph_requires_manual_recovery, retaining exact checkpoints/budgets/artifacts. Unprovable migration is never guessed.

Outstanding S11-S19: complete atomic artifact snapshots/manifests/refs bundles and explicit terminal-stage recovery/generation/budget amendment are S11; strict unavailable/blocking review remains S12, exact-SHA approval including QA S13, publication reconciliation S14, global spending/cancellation S15, scheduling/100-unit scale S16 and conditional sandbox/respond hardening S17-S18, full observability/matrix S19. Current review-exhaustion behavior is still legacy and QA still uses its draft seam after final gating; do not claim general release readiness or safe publication. All live release gates, paid canary, push/publication, merge and real Docker/remote smoke remain unrun.

At turn 30 stop implementation, save precise completed/outstanding work, verification and next action, and pause under explicit user authorization. Never reset the counter or count tools/compaction as turns. Do not resume without instruction or mark unfinished work complete. No subagents authorized. `contract.py` requires human review before any merge; no merge in scope.

## Turn 10 verification closeout

Targeted-01: 75 passed in 97.10 s. Integrity-01: 16/100.31 s. Integrity-02: 19 passed/4 import failures (fixed); restart-01: 7/54.09 s, including three real subprocess exits. Integrity-03: 28/129.84 s. Guards-01: 4/24.40 s; guards-02: 8/41.61 s. Full-01: 977 passed/12 obsolete count/cleanup/partial-QA expectations (fixed with explicit specified-behavior assertions); compatibility-02: 12/14.48 s, compatibility-03: 8/22.08 s. Full-02: 1001/480.41 s. Final full-03: **1008 passed in 516.64 s**, exit 0. `uv run ruff check`: **passed**. Source/docs whitespace clean (verbatim failed logs excluded). No dependency or lockfile update. Every tool handle terminal; no test process abandoned.

S10 completion criterion met; goal remains active at turn 10/30. No actual invoice/exactly-once billing, live SDK cancellation, strict review/publication or generic recovery claim. See the evidence record for scoped receipts and retained failed runs.

## Next action (goal turn 11): S11 artifacts and recovery

Next goal turn is 11, increment 10 -> 11 exactly once after reading objective/current evidence/brain. Implement `recovery.py` and recovery CLI from R06, preserving all original budgets and receipts. Current CLI `resume()` already distinguishes real interrupts (`Command(resume=...)`) from ordinary pending nodes (`None`), but terminal HALTED/rejected checkpoints have no next task and currently return without restoring their failed stage. `RunContext.verify` checks immutable manifest/inputs/source/planning/primary existence and journal integrity before executor creation. S08/S09/S10 guards conservatively refuse old missing unit/planning/join topology markers; retain that evidence, never invent missing hashes/bases or reset paid work. Provide read-only inspection/diagnostics for unprovable migrations.

Artifact preservation: S10 skips primary/worker deletion on HALTED/rejection and saves join failure/conflict text/patch before rollback. Exceptions/interrupts leave existing directories. Missing are complete atomic artifact manifests, staged/unstaged full binary patches, nonignored untracked snapshots, refs bundles, stage/reason/next-action records, absolute recovery paths on every halt/rejection/error, and critical capture-failure handling. Successful cleanup still removes clones; no automatic old-run garbage collection should be added. Use bounded checked command service; full patch/bundle outputs can be explicit owned artifacts rather than unbounded stdout. Preserve unknown partial files before any reconciliation/reset.

Effect reconciliation: S04 implementation commit helper still refuses an intent interrupted after Git commit; use exact operation trailer/parent/tree and retained intent evidence to reuse it. S06 pending formatter/gate intent, S08 pending squash and S10 uncommitted pending pick have conservative stop diagnostics; reconcile only provable effects/stages. S10 completed/lost-outcome join commits now reconcile, with source/destination/parent/tree critical mappings and real process drills. Failed receipts stay immutable, so explicit failed-stage retries need a recorded recovery generation/amendment and new operation key, not overwritten status. Key helper `join_key` currently encodes join:<level>; state counter tuple attempt IDs are owned by `attempt_identity`. Keep default generation compatible with completed historical calls. `merge_unit_outcomes` rejects conflicting terminal rows, so an authorized replacement needs a preserved prior receipt/hash and explicit generation chain; never silently discard prior failures/costs.

Explicit resume must restore the recorded stage and current unit/clone using the same S08 child, preserve consumed continuation/fix/review counters and all cost IDs, and never automatically replay an uncertain SDK call. An explicitly authorized SDK continuation consumes remaining continuation budget and records a new call receipt. Plan/PR rejection requires a new approval interrupt; ordinary resume cannot bypass it. `resume --add-budget-usd N` is a persistent explicit amendment for cost exhaustion; no ordinary reset of spend. Full global admission/reservation/unknown-cost accounting remains S15, but this amendment/recovery behavior belongs to S11. Test actual process exits with partial binary/untracked files, missing/corrupt artifacts/provider-zero, stage retry without sibling/model replay, rejection/reapproval, intent/commit reconciliation and consumed-budget preservation. Then full pytest/ruff, docs/evidence/brain and local-only commits. S12 review receipts/fail-closed policy remains next after S11; no publication or paid canary.
