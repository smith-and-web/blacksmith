# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 6 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S05 committed as ec0aceb/484883a; 891 tests and ruff passed.
- Current turn: 6; S06 implemented and verified; all verification processes terminal.
- Stop rule: at turn 30 stop implementation, write a precise handoff, and pause the goal using the user's explicit authorization. Resume only on instruction.
- Branch: `feature/heavy-prd-readiness`.
- S01-S02 implementation commit: `88070ac` (local only).
- S03 implementation commit: `e8650ba` (local only).
- S04 implementation commit: `1b0d63f` (local only).
- S05 implementation commit: `ec0aceb` (local only).
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

Completed slices: S01 path policy; S02 full requirements/plan context; S03 immutable version-2 context/preflight/locks/migration; S04 critical operation journal/SDK/commit receipts; S05 bounded native execution; S06 trusted multi-layer exact-candidate unit gates. Final turn-6 full offline suite: **926 passed in 221.30 s** (`heavy-prd-turn-06-pytest-03.log`); ruff **passed**. Integrity group: **35 passed in 69.90 s** (`integrity-07.log`). A configured no-effect sentinel fixture was strengthened after full-suite collection and separately passed (`heavy-prd-turn-06-subset.log`: 1 passed in 8.63 s). Intermediate failures are retained; no assertions weakened, skips or xfails added.

S06 interfaces: immutable Pydantic `GateReceipt`, `GateCommandReceipt`, `GateLayerReceipt`, `FixReceipt` in `gate_receipts.py`; `verification.run_candidate_gate`, `verify_gate_receipt`, `fix_candidate`, `required_layers`, `candidate_identity`. Production `auto_fix` and `test_gate` route version-2 state to this service; legacy standalone gate helpers retain their low-level API. Trusted commands come from immutable RunContext. Every distinct declared auto layer is covered in order; identical full command tuples may deduplicate with explicit coverage. Layer subsets are rejected. Gate checks HEAD/tree/index/nonignored source around each command; dirty, timeout and error are terminal, nonpassing receipts. Ordinary test failure still follows existing bounded recovery pending S08.

Critical intent precedes effects. JSON receipt artifacts and journal outcomes must persist before success. Cached passing gates verify exact SHA/tree/config/layer order, clean workspace, critical receipt JSON and stdout/stderr/outcome artifact hashes. Native command exceptions now retain structured results/log paths so gate failure evidence includes actual exits/timing/artifacts. Multi-layer state feedback is bounded to a 32 KiB aggregate tail; all artifact links remain. The checkpointer explicitly permits the typed receipt models; per-unit results and fan-out records retain receipts.

Formatters require the current attempt's journal-owned implementation commit/trailer; implementation now records its candidate SHA. Successful formatters use checked staging/amend and audits before/after (including hook changes). Clean failures may proceed with `clean_failure` evidence; partial dirty/protected edits, Git errors or timeout halt. Completed formatter receipts reuse an already amended clean owned commit without another amend; pending/unknown stage reconciliation remains S11. Fan-out forwards formatter updates and treats infrastructure gate failures as terminal rather than retrying them.

Outstanding: S07-S19. Unit receipts do not prove the merged branch or final layer union: integration/final scheduling is S10. Full artifact retention/recovery CLI, per-unit checkpoints/reset state, shared compiled subgraph, approved plan hashes, honest actual-patch review, exact-SHA approval/publication, run-wide admission/model cancellation, wide/100-unit scaling and conditional-feature rejection/ownership remain incomplete. Unknown SDK calls refuse automatic replay; no exactly-once billing claim. Sandbox is not yet an OS isolation/ownership proof. No live gate has passed; no paid canary, push, publication, merge or real Docker smoke.

**Next action (goal turn 7):** read the attachment request and this ledger; increment the counter exactly once from 6 to 7 (compaction is not a turn); recheck worktree/original input hashes and brain context. Implement S07's typed `UnitAttemptState` and one reset factory before changing the execution topology in S08. Include unit/attempt bases, last passing SHA, attempt identity/number, model tier, fix/continuation/revision counters, separate gate/review/continuation feedback and gate/review receipt fields. Reset every transient evidence/error/resume/escalation/review field when selecting a new unit, consistently for sequential and fan-out. Initialize a new unit from current combined tip; preserve the current attempt's state only for its own bounded recovery. Keep parent transitions limited to selection and consuming typed outcomes. Add mixed sequential/escalated/reviewed -> parallel -> sequential regressions, including resume; final unit must start cheap with zero counters/no old feedback and receive its own review. Use S06 receipts and S04 attempt identity without losing cached SDK/commit semantics. Do not introduce another Python recovery loop. Then full pytest and ruff; S08 will replace the existing worker loop with a common compiled unit subgraph.

The counter never resets automatically. At turn 30 stop implementation, save completed/outstanding slices, exact verification and next action, and explicitly pause under the user's authorization. Do not resume without instruction or mark unfinished work complete. Original untracked spec/probe hashes remain unchanged; neither is staged. `contract.py` changes still require human review before any merge; no merge is in scope. No subagents authorized.
