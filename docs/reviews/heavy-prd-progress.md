# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 5 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S04 code/evidence committed as 1b0d63f/0cba61d; 868 tests and ruff passed.
- Current turn: 5; S05 implemented and verified; all verification processes terminal.
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

Completed slices: S01 path policy; S02 full requirements/plan context; S03 immutable version-2 context, preflight, locks and migration; S04 critical operation journal/SDK/commit receipts; S05 checked bounded native execution. Final turn-5 full offline suite: **891 passed in 147.68 s** (`heavy-prd-turn-05-pytest-03.log`); `uv run ruff check`: **passed**. Command/process/Git/CLI tests: **23 passed in 14.29 s** (`heavy-prd-turn-05-commands-07.log`). All failed/intermediate receipts are retained. No assertions weakened, xfails or skips added.

S05 interface: `commands.run_command`, `CommandResult`, `CommandError`, `command_scope`, `command_scope_for_state`, `toolchain_environment`. The service owns a POSIX process group, drains incremental output into capped files, reports byte counts/truncation/bounded tails and durable log paths, sends TERM then KILL with one grace period of at most five seconds, and reaps its child. Timeout/cancellation never look like success. Git/gh defaults are 120 seconds; setup/fix/test/lint/SBFL use finite saved `command_timeout_s` (default 1800). `max_command_output_bytes` defaults to 1048576 across stdout/stderr. Toolchain children lose known model/admin/GitHub credentials plus configured custom API variable names. Sensitive Git argv/output are redacted in persisted logs. Critical command intents/outcomes use `OperationJournal.command_intent` with transactionally allocated ordinals; fresh observations are never cached. No Python retry loop was added.

Production wiring covers CLI bootstrap/resume and graph node scopes, including parallel workers. Central Git helpers, gates/fixers, SBFL, index, approval diff, PR/gh runner and legacy respond Git calls use the service. Checked add/amend/reset/clean/squash errors stop execution. A command receipt does not make its owning multi-command stage replay safe. Semantic fixer/join/publication reconciliation, exact gate receipts and complete failure artifact retention remain subsequent work. Pre-context preflight reads use bounded temporary logs; run-owned commands log under the run directory. Trusted host gates remain outside an OS sandbox. Conditional Docker exec/remote-spike behavior is not claimed proved.

Outstanding: S06-S19. In particular, unit gates still select the first layer and can reload clone toolchain config; fixer failures/dirty source and exact commit/tree/config receipt enforcement are **S06 next**. Per-unit reset state, common compiled unit subgraph, per-unit plan checkpoints and approved hashes, integration joins, artifact/recovery CLI, honest patch review, exact-SHA approval/publication, run-wide budget/model cancellation and 100-unit scaling remain incomplete. Unknown/ambiguous SDK calls refuse automatic replay. No live release gate has passed; no push/merge/publication/paid canary/Docker smoke was performed.

**Next action (goal turn 6):** read this ledger and attachment request, increment the counter once from 5 to 6, inspect preserved worktree/source inputs and brain context, then implement S06 from R04 and the slice table. Add typed `GateReceipt` with run/scope/candidate commit+tree/toolchain hash/ordered layers/command outcomes/timestamps/artifact paths and passed|failed|error|timeout|dirty. Resolve the immutable context's trusted toolchain, run every declared auto layer (deduplicate identical command tuples while recording coverage and not-run layers), check fixer staging/amend and policy audit, forbid amending unrelated prior work, reject source changes by setup/test/lint, and write a fresh nonpassing receipt on errors before any routing can consume stale evidence. Use the bounded command service and critical journal; preserve injected fake interfaces where legacy API compatibility is required. Cover real Git/gate/graph/checkpointer regressions and no downstream effects, then full pytest and ruff. Integration join/final-union scheduling belongs to S10; build the receipt/reuse primitive now.

The counter never resets automatically. Tool calls and context compaction do not increment it. At turn 30 stop implementation, save completed/outstanding slices, exact verification results and next action, and explicitly pause under the user's authorization. Do not resume without instruction or mark unfinished work complete. Original untracked specification and probe hashes are unchanged; neither is staged. `contract.py` changes still require human review before any merge; no merge is in scope. No subagents are authorized.
