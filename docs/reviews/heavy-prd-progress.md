# Heavy-PRD implementation progress

- Goal status: active; implementation incomplete.
- Goal turn: 16 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; S13 recovery12/render42 and ruff passed, full suite launched with143-source identity and durable approval/migration docs. Regression-05 now terminal52/210.03s.
- Current turn: 16; finish S13 full verification/closeout, then S14 remote publication reconciliation. Counter incremented15->16 exactly once on automatic continuation; never on compaction or tool calls.
- Turn 13 boundary exception: a missed test crash injection reached a real push attempt to a synthetic GitHub remote; it failed before authentication (128), with no gh creation command. Receipt: `heavy-prd-turn-13-unexpected-push.json`. Child publication guard and native offline transport/auth isolation added before further testing.
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
- S11 foundation commit: `68a80c3` (local only; partial at turn 11).
- S11 controller commit: `03d4308` (local only; partial at turn 12).
- S11 completion commit: `2e97459` (local only; turn 13 full proof passed).
- S12 completion commit: `5f483e7` (local only; turn 14 full proof passed).
- S13 completion commit: `3581d289088a1f3ec4010bd1a62a40bea005aaee` (local only; turn16 full1181/ruff proof).
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
- [x] S11: Artifacts and recovery CLI.
- [x] S12: Honest patch reviews and receipts.
- [x] S13: Human-unit validation and exact-SHA approval.
- [ ] S14: Publication reconciliation.
- [ ] S15: Run-wide admission and cancellation.
- [ ] S16: Bounded scheduling and recursion.
- [ ] S17: Sandbox ownership or explicit disabled-feature rejection.
- [ ] S18: Hardened respond or explicit unsupported-command error.
- [ ] S19: Observability, docs and complete acceptance matrix.

## Acceptance and verification

See [heavy-prd-evidence.md](heavy-prd-evidence.md) for commands, scoped results, requirement coverage and release gates. A checked slice requires production wiring and regression evidence; partial modules are not completion.

## Current handoff: turn16 closeout

Counter **16/30**, incremented15->16 once after objective/repository/ledger/brain checks. Do not increment on compaction or tool calls. Previous turn was progress; this turn completed S13 local acceptance/commit/brain proof and implemented unaccepted S14 in isolation. The full S01-S19 goal remains active. At turn30 stop implementation, save the precise handoff and pause under the explicit user authorization.

**Accepted primary:** feature/heavy-prd-readiness, S13 implementation3581d289088a1f3ec4010bd1a62a40bea005aaee. S01-S13 are complete locally. Full S13 command `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t15-full-01` finished **1181 passed in2097.72s (34:57), exit0, no warnings/skips/xfails**; handle59474 explicitly terminal. Ruff15 and refreshed primary ruff16 passed. Complete143-source set/hashes, original inputs, dependencies and final receipt hashes match turn15 environment JSON before and after commit. Host pre-commit and source/docs git-show whitespace checks passed, excluding only raw log receipts. Original spec/probe remain unchanged/untracked/unstaged. No source changed in the primary while its full suite ran.

S13 production proof: common normal/terminal-QA final gate -> deterministic prepared proposal checkpoint -> real PR interrupt -> normal/draft route. Immutable per-round critical proposal/decision artifacts bind exact clean known candidate, branch/base/origin/draft, automatic unit/final gate/review references and pending QA. Both publisher entries guard before summary and each effect; exact SHA refspec without force. Plan-only/stale/forged/missing evidence refuses with retained work. Full cumulative PR patch is private and independent of capped native stdout; preview is explicitly bounded. Restart reuses completed decisions, holds incomplete intent, makes a fresh round after explicit rejection recovery, and refuses legacy/corrupt files before executor. CLI0 DONE/2 AWAITING_QA/1 failure with QA/check/draft URL. Runtime docs: heavy-prd-approval.md. Failed development receipts remain retained.

**Unaccepted S14:** separate local worktree `/private/tmp/blacksmith-heavy-prd-publication-stage`, branch feature/heavy-prd-publication-staging. Source-only S13 snapshotf14580b matched all143 primary sources. WIP c3286f0ef5f1caf6ea00f9c6c46f6434612da979 durably saves only the10-file S14 delta. Do not merge the branch or replay its baseline. Transfer only f14580b..c3286f0 (or later fixes) using checked git-apply after this S13 closeout. Staging .venv symlink and copied untracked spec are excluded; never stage either. The WIP contains146 Python source files; manifest/receipt hashes are in `heavy-prd-turn-16-staging-source.json` in the primary. All finalized staging logs were copied into primary docs/reviews.

S14 WIP wiring: new publication service validates explicit remote ref and paginated API repository/base/head/SHA/draft/run marker before effects, checks both fetch and push endpoint identity, halts conflicts/multiple/foreign matches without summary or force, persists summary content/cost plus critical local seals, observes after push/create including error exits, reuses exact positive remote effects and persists a validated publication result. Unknown native create without positive PR observation holds. Normal/draft nodes, state markers, pre-executor migration/evidence validation and sealed explicit publication-stage retry are wired. Summary cost identity is __run__; prepared PR metadata includes its summary event idempotently. No S14 acceptance or live-publication claim.

All test handles are terminal. Scoped S14: integrity-02=17 passed/4 failed/265.88s (all13 protocol and4 migration passed; four exact exit assertions caught an argv-position error in the new crash harness). Corrected recovery-03=4 passed/4 deselected/69.12s proves actual exits106-109 after push/create/result/content seal with original cost/candidate/intent reuse and no duplicate effects. Edges-05=2 passed/17 deselected/74.94s proves unknown-native hold/positive reconciliation and missing completed branch refusal. Boundaries-06=4 passed/43 deselected/120.60s proves critical push-intent/outcome faults and two foreign push-URL cases. Wiring-02=36 passed/579.29s against the original S13 approval/recovery cases; later-added push-URL cases separately covered. Staging ruff-12 passed. Earlier known accounting-normalization failures, explicit SIGINT stops, transient insertion-metadata comparison failure and failed crash-injection receipts remain retained. No assertions weakened/skips/xfails.

**S14 remaining:** fresh consolidated67-case run against the exact current146-source WIP, then primary transfer, final full pytest/ruff with a fresh source/dependency record, publication runbook/README/architecture/migration/recovery docs, final R07 acceptance audit and brain/local commit. Expected full total1212 includes31 new cases, but this is not a test result. Validate artifact seals, complete receipt invariants, no duplicate effects/costs and terminal no-op resume. Preserve legacy explicit-approved library seams only; versioned missing metadata refuses. Do not manufacture historical receipts.

Brain: existing Projects/blacksmith.md updated through MCP with S13 commit/completion/full1181/143-source proof, turn16/30, isolated unaccepted S14, S14-S19/live holds and historical failed-push disclosure. Target lint0 errors/0 warnings. After89 seconds, index lastRun.errors=0 and invalidFrontmatter.count=0. Semantic retrieval and exact full-note readback verified all written content (`bd694404-7749-4e8b-8701-67b1c5534cb1`); positive feedback once. No memory-folder write.

No push, merge, live publication, paid canary or Docker smoke in turn16. Scratch bare-remote pushes are offline fixtures. Preserve the historical turn13 unintended failed push disclosure below through the final goal report. S14-S19, explicit contract.py human review before merge and all live gates remain outstanding. This closeout is recorded by a separate docs-only commit; no S13 implementation work remains.

## Historical turn 14 closeout

S01-S12 are complete under the scoped offline acceptance audit in `heavy-prd-evidence.md`. Full S01-S19 goal stays **active at 14/30**, with S13-S19 and all live gates outstanding. Do not increment on compaction. No subagents. Original spec/probe remain unchanged/untracked. All turn-14 source changes were verified against the pre-run source hashes; local commit/brain proof is recorded below.

S12 production wiring: `review_patch.py` captures full cumulative unit-base/candidate binary/rename patches through Git --output, independent of native command-log caps; <=128 KiB inline, larger explicit manifest plus bounded read-only read_diff MCP. `review_receipts.py` strictly validates one complete JSON verdict, finding fields/severities and typed panel/receipt evidence, with critical intent/seal/artifact/outcome and primary-key model-cost references. Any panel blocker blocks; unavailable halts without automatic retry; disabled has a typed disabled receipt and no reviewer call. Shared child graph halts exhausted blockers. Receipts cross keyed outcomes/joins/final/publication entry and validate before CLI executor on resume. Saved enablement/panel size and review_version=1 are enforced; old active metadata refuses without inventing hashes or changing checkpoints. Explicit unavailable recovery replaces only bad/unknown seat identities under remaining allowance, preserving valid seats, original candidate/gate/counters/costs/failure receipts. An unchanged blocked candidate cannot seek another vote; a budget addition can permit a real remaining code revision. Latest typed receipts drive current status while historical findings remain; revision attempts never count resolved findings.

## Turn 14 verification

- Final full command: `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t14-full-01 > docs/reviews/heavy-prd-turn-14-pytest-01.log 2>&1`, **exit 0: 1138 passed in 1136.16 seconds (18:56)**. Handle `14689` explicitly polled terminal. No warnings/skips/xfails. All 54 new S12 cases and existing regressions included. Fresh explicit temporary roots avoid repeated old read-only snapshot cleanup; no warning suppression or skipped checks.
- `uv run ruff check`, exit 0. All **140 Python source hashes and the complete source-file set** match the pre-run record. Environment records installed versions, base `123b4ed`, exact source/input and final full/ruff hashes. Source/docs whitespace excludes only verbatim raw `.log` receipts. No dependency/lockfile change or weakened assertions.
- Import-01: 2 collection errors (cycle fixed by local atomic_write import). Wiring-01: 20 passed/3 failed/56.20 s (two invalid `blocked` protocol fakes corrected to needs_changes, plus obsolete exhausted-publication expectation changed to strict halt/no effects).
- Integrity-01: 32 passed/1 failed/64.62 s. Full large reviewer evidence passed, then unrelated optional PR diff display hit native-log truncation; failure retained for S13. Large-patch-02: 1/36.50 s, actual registered RPC plus full bounded >1 MiB reconstruction/hash under 4096-byte command cap, optional PR display disabled only to isolate S12.
- Review-regression-01: 62 passed/2 failed/104.07 s (late obsolete pass assertion and disabled no-PRD library seam); corrected regression-02: 13/44.82 s. Optional writer, consumed state/counters and source assertions preserved.
- Recovery-01: 10/52.48 s, including real process exits before seal/after seal-before artifact/after receipt-before checkpoint, no completed-SDK replay, valid-seat reuse and unchanged-blocker refusal. Integrity/recovery-02: 56/190.37 s. Render-regression-01: 62 passed/10 failed/38.41 s (old false-clean/resolved/publication assertions); corrected render-regression-02: 81/38.11 s. Blocker-budget-01: 1/7.37 s, actual revision/new SHA/latest clean reporting without erasing old blockers. All failed receipts retained.
- All test/lint handles are terminal. Current local verification is not general readiness, exactly-once provider billing, live cancellation, global admission or successful publication evidence. No paid canary/merge/successful publication/Docker smoke ran in turn 14.

## Turn 14 durable closeout

- S12 implementation committed locally as `5f483e71af1f02106875d5385b24715a4f90fea4`; host pre-commit checks passed. Post-commit Python-source set/hashes and full/ruff receipt hashes match final proof. `git show --check` passed excluding only verbatim raw `.log` receipts. Only the two original unchanged untracked inputs remained.
- Existing `Projects/blacksmith.md` updated through MCP with S12 completion, turn 14/30, exact 1138-test proof, S13 approval/display handoff, S13-S19/live boundaries and preserved turn-13 failed-push disclosure. Targeted lint: 0 errors/0 warnings. After 83 seconds, index lastRun.errors=0 and invalidFrontmatter.count=0. Semantic retrieval and exact full-note readback verified every scope/counter/proof item (`b3344637-d931-4b46-8865-c452caae5251`); positive feedback recorded once.
- All test/lint/implementation-commit handles are terminal. No push/merge/publication/paid canary/Docker smoke performed in turn 14. The full native goal stays active; next automatic continuation is turn 15, S13. A user status question during closeout did not cancel or replace the objective.

## Historical boundary exception (retain through final goal report)

Turn 13's missed test crash injection patched impl._git instead of the actual impl.run_command, reached legacy publication and attempted a real push to a synthetic GitHub fixture remote. The critical receipt failed 128 before authentication, with verified username-unavailable stderr; no gh creation command or successful publication occurred. Exact receipt/hash/stderr: `heavy-prd-turn-13-unexpected-push.json`. Do not claim no push attempt occurred across the goal.

Correction preceded all later tests: fixed injection, plan-only approval and child publication guard; pytest denies native network Git protocols (scratch file transport allowed), isolates gh config and empties publication tokens so dotenv cannot restore credentials. Host Git settings unchanged. Python socket guards alone had not protected native Git children.

## Next action: automatic goal turn17

Current counter remains **16/30**. On the next automatic continuation read the objective, repository instructions, current ledger and brain, then increment **16->17 exactly once**. All current test handles are terminal; do not restart an old handle. Finish S14 from the exact isolated WIP/delta described above. Run the fresh consolidated67-case source check, transfer only the checked S14 delta to feature/heavy-prd-readiness, and complete full pytest/ruff/source identity, docs, recovery evidence and acceptance audit. Never mark S14 complete from the existing scoped logs alone. Then continue S15 in dependency order.

S14 remote publication reconciliation, S15 global admission/cancellation, S16 bounded scheduling/recursion, S17/S18 conditional disabled alternatives, and S19 observability/docs/full matrix remain required. No paid/publish/Docker release gate has passed. Do not push, merge, publish or run paid canaries. Keep the full native goal active until implementation/local acceptance is genuinely complete; never narrow it to a completed slice.

Later interactions: S15 must keep budget denial typed unavailable, distinguish denial-before-start from admitted/uncertain calls, and permit budget-only recovery without consuming a continuation for an unstarted call. Keep valid cached seat options/reservations when remaining cap changes. S19 must fix the keyed review-revision display tally (recovered child totals can still add twice), bounded finding/error/history presentation, validation/identity of best-effort streamed findings, and stale ReviewConfig/legacy helper/test docstrings describing earlier advisory behavior. Unit-owned counters/caps and current strict-review routes are already authoritative. No global invoice ceiling or physical orphan-termination claim.

At turn 30 stop implementation, save precise completed/outstanding/verification/next-action handoff, call update_goal(paused) under explicit user authorization and stop. Never mark unfinished work complete, reset the counter or resume automatically.
