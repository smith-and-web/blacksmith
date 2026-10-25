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

## Current handoff: turn 16 is active

Counter **16/30** incremented once after objective/instructions/branch/ledger/brain checks. Previous turn was progress (S13 source/tests/docs/ruff and full suite launched). Regression handle76782 explicitly polled terminal:52 passed/210.03s. Full handle59474 verified live, no duplicate launch. Branch feature/heavy-prd-readiness; original spec/probe unchanged/untracked. No subagents, external push/publication, merge or paid canary this turn. S13 full verification passed (1181 tests/ruff/143-source proof); S13 is checked locally. Implementation commit and brain closeout are pending. S14 runtime stays isolated and unchecked.

Implemented so far: publication_approval.py candidate/branch/base/origin/QA/receipt-bound immutable proposal and critical decision evidence, known final tip validation, fixed per-round artifacts, cached completed decisions and conservative incomplete-decision hold. A new deterministic prepare_pr_approval parent node checkpoints proposal before the actual interrupt, including legacy real-Git graph paths. Both normal/QA final gates now reach that same approval; draft intent/pending QA derive from the immutable contract. Publisher wrapper and direct nodes check approval before summary and before each push/create effect; push uses the approved SHA refspec (no force). Generic cumulative patch capture gained publication scope to retain full PR diffs independent of command stdout limits; preview reads only configured bytes with explicit truncation. CLI migration guard/approval-artifact validation,0 DONE/2 AWAITING_QA/1 failure and pending-QA reporting added. PR rejection resumes a new round through prepare_pr_approval; original evidence stays intact.

Terminal checks: integrity-01=23 passed/1 failed/236.94s (test mutation hook used absent tool-free summary cwd; corrected hook captures actual approval workspace); edges-02=3 passed/21 deselected/17.95s proves summary/push guards and >1 MiB bounded PR preview. Wiring-03=58 passed/11.31s after updating obsolete bypass/human-only/direct-node approval fixtures with explicit stronger refusal assertions. Recovery-01=12 passed/141.05s, including real process exits after decision intent/after completed decision-before-checkpoint, completed decision reuse, incomplete hold/fresh round, corruption/migration before executor and immutable original inputs. Render-01=42 passed/0.35s including exact-SHA/draft/receipt/QA display in plain and TTY. Ruff code-05 passed. Failed receipts retained. Regression-04 collected no tests because a named file did not exist; corrected regression-05 finished52 passed/210.03s. Final S13 full1181/ruff proof is recorded below.

Full S13 suite finished1181 passed/2097.72s, terminal handle59474: `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t15-full-01 > docs/reviews/heavy-prd-turn-15-pytest-01.log 2>&1`. Pre-run `heavy-prd-turn-15-environment.json` records all143 Python sources/base260d542/dependencies/original inputs. Final ruff passed. No source edits since that record. Docs now include `heavy-prd-approval.md`, updated README/architecture/run-context/review handoffs. Both S13 handles were explicitly polled terminal; no duplicate full run was launched.

Remaining S13 closeout work: local commit and brain final proof; full regression/audit/source evidence passed. Invalid human execution is validated before paid work on production paths (S03), and again at actual Git approval entry. Preserve compatibility library seams only for explicitly approved pre-versioned callers; versioned missing context/metadata always refuses. Do not manufacture old approval hashes. Then update brain through MCP, lint/index/readback, commit requested files locally and keep full goal active. S14-S19/live gates remain.

Turn16 isolated S14 preparation: local worktree `/private/tmp/blacksmith-heavy-prd-publication-stage`, branch `feature/heavy-prd-publication-staging`. Source-only baseline `f14580b` copies exactly the143 recorded S13 source hashes; primary remains on feature/heavy-prd-readiness/260d542 with the original S13 suite live and sources unchanged. Original spec copied read-only as untracked context in the staging worktree; never stage either original input. S14 changes will be authored/tested only in that worktree until S13 full verification and primary commit. Then transfer only the S14 delta, not the staging snapshot commit. No subagents, remote effects or paid transport. Temporary worktree .venv points to the original dependency environment; invoke its Python with cwd/stated imports checked to ensure blacksmith resolves to the staging source.

Staging S14 WIP: new `blacksmith/publication.py` validates explicit remote ref and paginated GitHub API base/head/repo/SHA/draft/run marker before effects; conflict/multiple/foreign matches halt without summary or force. It persists candidate-keyed summary content with critical seals before files/effects, observes after push/create including error exits, reuses exact positive remote effects and persists a validated publication result. Unknown native create with no positive PR observation holds. Normal/draft nodes, state markers, pre-executor migration/evidence validation and sealed explicit publication-stage retry are wired. Summary cost attribution now uses __run__ to match critical accounting. No S14 completion claim.

Staging logs are under that worktree's docs/reviews. Wiring-01 explicitly interrupted and terminal after known accounting-normalization failure:3 failed/4 passed/204.88s. Integrity-01 interrupted and terminal:3 failed/1 passed/89.58s; smoke-01 terminal1 failed/20 deselected/30.90s. Raw critical node model.summary needed the existing report normalization to summary; fixed without weakening exact cost assertions. Also fixed scratch-runner Git argv mapping. Smoke-02 terminal2 passed/11 deselected/32.91s proves fresh publication and failed-create retry (one original summary/push). Integrity-02 terminal17 passed/4 failed/265.88s: all13 original protocol cases and4 migration cases passed; exact crash exit assertions caught a wrong argv position in the test harness. Corrected recovery-03 terminal4 passed/4 deselected/69.12s proves actual exits106-109 after push/create/result and content seal, without original SDK/effect replay. Edges-04 terminal4 passed/1 failed/13 deselected/71.95s: strict PR identity cases passed; a transient receipt.created insertion flag was incorrectly compared to a reread; changed to compare the persisted original. Edges-05 terminal2 passed/17 deselected/74.94s proves native-unknown hold/positive reconciliation and missing completed branch refusal. Boundaries-06 terminal4 passed/43 deselected/120.60s proves critical push-intent/outcome faults and fetch-versus-push origin mismatch refusal. Original failed/uncertain receipts remain. Stage ruff-12 passed. Wiring-02 live handle48230 covers the original36 S13 approval/recovery cases against S14; extra push-origin cases added after collection are separately covered by boundaries-06. No final S14 full-source suite yet.

S13 full handle59474 explicitly polled terminal: **1181 passed in2097.72s (34:57), exit0, no warnings/skips/xfails**. Both final ruff15 and refreshed primary ruff16 passed. All143 Python source hashes/file set, dependency versions and original spec/probe hashes match the pre-run record. Final receipt hashes saved in turn15 environment JSON. S13 scoped acceptance is complete; local commit/brain closeout pending below. S14/S15-S19 remain unchecked and every live gate unrun.

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

## Next action: automatic goal turn 16

Current counter remains **15/30**. The user requested status during ongoing work; this did not cancel or pause the full goal. On the next automatic continuation, read objective/instructions/current ledger/brain and increment **15 -> 16 exactly once**. Finish S13 from the current handoff above. Do not restart live test handles76782 (regression-05) or59474 (full pytest-01); poll to terminal. Full source identity is in turn15 environment JSON (143 files/base260d542), and final ruff passed. Any source fix requires fresh appropriate tests and final full-source proof. All S13 implementation/docs are uncommitted. Mark S13 complete only after final passing suite, source hash audit, acceptance/docs, brain lint/index/readback and local commit. Then continue S14 in dependency order.

The mandatory large-PR display defect found in turn14 is fixed in the current S13 source and passed the scoped >1 MiB/4096-byte-native-cap/256-byte-preview test. Full current-source verification remains active. Failed development receipts are retained. Decisions in the original specification remain settled.

S14 remote publication reconciliation, S15 global admission/cancellation, S16 bounded scheduling/recursion, S17/S18 conditional disabled alternatives, and S19 observability/docs/full matrix remain required. No paid/publish/Docker release gate has passed. Do not push, merge, publish or run paid canaries. Keep the full native goal active until implementation/local acceptance is genuinely complete; never narrow it to a completed slice.

Later interactions: S15 must keep budget denial typed unavailable, distinguish denial-before-start from admitted/uncertain calls, and permit budget-only recovery without consuming a continuation for an unstarted call. Keep valid cached seat options/reservations when remaining cap changes. S19 must fix the keyed review-revision display tally (recovered child totals can still add twice), bounded finding/error/history presentation, validation/identity of best-effort streamed findings, and stale ReviewConfig/legacy helper/test docstrings describing earlier advisory behavior. Unit-owned counters/caps and current strict-review routes are already authoritative. No global invoice ceiling or physical orphan-termination claim.

At turn 30 stop implementation, save precise completed/outstanding/verification/next-action handoff, call update_goal(paused) under explicit user authorization and stop. Never mark unfinished work complete, reset the counter or resume automatically.
