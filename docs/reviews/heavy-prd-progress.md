# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 7 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S06 committed as c13fc34/e8ecaef; 926 tests and ruff passed.
- Current turn: 7; S07 implemented and verified; final full suite terminal.
- Stop rule: at turn 30 stop implementation, write a precise handoff, and pause the goal using the user's explicit authorization. Resume only on instruction.
- Branch: `feature/heavy-prd-readiness`.
- S01-S02 implementation commit: `88070ac` (local only).
- S03 implementation commit: `e8650ba` (local only).
- S04 implementation commit: `1b0d63f` (local only).
- S05 implementation commit: `ec0aceb` (local only).
- S06 implementation commit: `c13fc34` (local only).
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

Completed slices: S01-S07 (path policy, full requirements/plan context, immutable v2 run context/preflight/locks/migration, critical journal/SDK/commit receipts, bounded native execution, exact trusted unit gates, typed unit state/shared reset factory). Final turn-7 full offline suite: **934 passed in 217.42 s** (`heavy-prd-turn-07-pytest-02.log`); ruff **passed**. Targeted compatibility group: **67 passed in 66.69 s**. State group: **8 passed in 13.07 s**. Earlier full run: 932 passed in 211.75 s before the final continuation/compatibility cases. No assertions weakened, xfails or skips added; original input hashes unchanged.

S07 interfaces: `state.UnitAttemptState` (flat TypedDict inherited by BlacksmithState), `unit_state.new_unit_attempt(unit_id, base_commit)` and `next_attempt(state, kind, base_commit=..., feedback=...)`. The factory covers every unit-owned field and excludes historical cost/error/finding/unit-result ledgers. It initializes first preparation, sequential advancement, worker entry and post-join selection from checked current combined HEAD. New units clear all prior test/review/formatter receipts, implementation, counters, model tier/escalation, pre-implement ref, error/resume/current findings and gate/review/continuation feedback.

Recovery keeps the same unit base and request, advances attempt number and the existing S04 counter-tuple identity, clears obsolete evidence and records the last passing candidate. Escalation receives the latest gate output even with no cheap fix retry. A failing review revision resets to last_passing_commit; continuations keep partial files and send a separate feedback channel into the real implement prompt. Successful implement records attempt_base_commit. Legacy consumed counters/identity encoding remain stable; absent historical bases stay unknown. The factory is not run merely because resume is invoked. Strict review receipt content remains S12; its field initializes absent.

Real mixed-DAG CLI tests use actual Executor options/guards/Git/gates/review/checkpointer with only paid SDK transport replaced: A cheap failure -> escalation -> review revision, B/C fresh parallel siblings, D fresh sequential unit with its own initial model and review. Stop/resume before D preserves identical metadata and never replays A/B/C, despite PRD deletion/local model changes. A declared terminal max-turn fixture verifies actual partial-file preservation and continuation feedback. No publication transport is reached.

Outstanding: S08-S19. The parent/worker still contain the existing recovery topology/worker Python loop; S08 must replace them with one compiled LangGraph unit subgraph, not add another loop. Per-unit plan checkpoints/hash approval are S09; join/final union gates and reconciliation are S10; complete artifact/stage recovery (including ambiguous older-v2 unit boundary metadata) is S11. Actual-patch fail-closed review, exact-SHA QA/approval/publication, global budgets/cancellation, 100-unit scale and conditional feature ownership/rejection remain incomplete. No live gate has passed; no paid canary, push, publication, merge, Docker or remote smoke. Unknown SDK interruptions still refuse automatic replay; no exactly-once billing claim.

**Next action (goal turn 8):** read the attachment and ledger; increment once from 7 to 8, preserving this counter across compaction. Recheck worktree/input hashes and brain context. Implement S08 from R05 and slice table: compiled common unit subgraph shared by sequential and fan-out scheduling, with prepare attempt -> implement -> audit/commit -> fix -> gate -> review -> recovery decision -> finalize boundaries. Reuse existing node functions/S07 state and S06 verification rather than replacing executor behavior. Split the model and audit/commit boundaries as needed while preserving direct legacy API compatibility and S04 cached SDK/commit semantics. Persist intermediate attempt/cost state before another call, use run/unit checkpoint namespaces, and reduce outcomes by unit ID. Remove the existing worker while loop and the independent sequential recovery policy; both shapes must use bounded continuations, same-model fixes, one escalation and review revisions. Carry the same UnitDeps including optional SBFL with command deadline. Parents select units/consume typed outcomes. Cover identical recovery behavior in both shapes, actual SQLite restart between model/commit and attempts, no duplicate costs/calls, and no downstream effects on failure. Then full pytest and ruff. Scheduling/integration specifics remain S10 and run-wide admission S15, but no second Python recovery loop may be introduced.

At turn 30 stop implementation, save precise completed/outstanding work, verification and next action, and pause under the user's explicit authorization. Never reset this counter or count tools/compaction as turns. Do not resume without instruction or mark unfinished work complete. Original untracked specification/probe remain unchanged and unstaged. `contract.py` changes still require human review before any merge; no merge in scope. No subagents authorized.
