# Heavy-PRD acceptance evidence

Implementation is incomplete. No live release gate has passed.

## Baseline and inputs

- Source review baseline from specification: 737 passed, 2 failed; not a green baseline. Two ambient Git fixture failures are known; isolate fixtures rather than changing user Git settings.
- Turn 2 worktree: main tracking origin/main, no tracked changes; pre-existing untracked specification and readiness probe retained.
- Repository `CLAUDE.md` read; explicit request and specification authorize direct remediation on a feature branch.
- Brain context read: `Projects/blacksmith.md`; `Reference/blacksmith - running and operating.md`; `Reference/blacksmith - architecture and graph.md`; `Reference/blacksmith - configuration.md`; `Reference/blacksmith - failure classes and gotchas.md`; clone and no-host-shell decision notes. Notes are historical context; current source and specification govern.

## Slice evidence

### Turn 2: S01 path boundary

- Added `blacksmith/path_policy.py`; permission schemas cover Write/Edit/MultiEdit/NotebookEdit; unknown tools and malformed/missing targets denied. Explicitly wired MCP read tools remain permitted.
- Canonical containment includes relative paths; symlink write targets/ancestors denied; lexical/resolved protections cover mandatory metadata/config/verifier paths plus compiled PRD rules.
- Actual Git diff audit includes staged/unstaged changes, untracked source, rename/deletion sides; applied before/after implementation commit, after formatter on sequential/parallel paths, before PR node publication. Gate routing prioritizes HALTED.
- A denied edit halts with `policy_violation` before staging otherwise valid changes. SDK options explicitly disable settings inheritance and use strict MCP configuration; support verified against installed `ClaudeAgentOptions` signature (0.2.115).
- Git fixture isolation removed the two baseline identity/hook failures without changing any user Git setting.
- Targeted command: `uv run pytest -q tests/test_path_policy.py tests/test_implement.py tests/test_executor.py tests/test_fix.py tests/test_hitl.py tests/test_cli_validate.py`: **119 passed in 17.33 s** at the S01 checkpoint.
- Ruff at the S01 checkpoint: passed.
- Scope limits: trusted snapshot, exact gate receipt, lifecycle retention, hard publication approval and sandbox rejection still belong to later slices. These regressions do not prove live SDK containment or an OS sandbox.

### Turn 2: S02 requirements context

- Shared `blacksmith/context.py` renders full PRD prose and contract plus selected approved-plan text and dependency results for implementation/review.
- Planner/critic receive full context and explicit cwd, with matching index root. Fan-out sends selected plan, PRD and relevant parent results explicitly.
- `[limits].max_prd_bytes = 262144` parses and reaches the graph's planner node; oversized input halts before transport. Parsed PRD retains full original text.
- Gate feedback, full original review feedback and continuation guidance use separate sections; requirements and plan have no tail truncation.
- Exact sentinel tests cover sequential/fan-out, index on/off, explicit other target, unchanged source, real Git/gate/graph/checkpointer and actual `Executor.build_options`. Only paid transport replaced. Planning snapshot immutability is not claimed here; that is S03. Plan-hash approval enforcement is S09.
- Targeted context command before follow-up additions: `uv run pytest -q tests/test_requirements_context.py tests/test_plan.py tests/test_path_policy.py tests/test_cli_validate.py`: **75 passed in 9.84 s**.

### Preserved verification failures

- `heavy-prd-turn-02-pytest-01.log`: suite collection failed due to an incorrect SDK internal transport import in the newly added offline guard; corrected to the installed `subprocess_cli` module. No tests ran, no model transport ran.
- `heavy-prd-turn-02-pytest-02.log`: full suite **776 passed, 3 failed in 108.62 s**. Three old exact prompt assertions expected the missing-context behavior explicitly changed by R02. Equality assertions retained and expected full context added; superseded by the passing full rerun below.
- New regression test development also exposed a nested-event-loop error in its mock transport; transport now invokes synchronous edit scripts on a thread while retaining real SDK option validation.


## Remaining live release gates

- Authorized disposable-target paid canary: fixed $10 budget, review enabled, two independent auto units plus terminal QA, plan-only approval initially; verify no publication, resume with PR approval, inspect draft and exact gated SHA. NOT RUN; explicitly prohibited in this task.
- Real Docker ownership/sentinel/timeout/restart smoke before sandbox enablement. NOT RUN. S17 may instead reject enabled sandbox configuration and leave sandbox disabled.
- S18 may instead explicitly disable respond with unsupported-until-hardened diagnostic.

## Final required local verification

- Latest full suite (S01-S04): `uv run pytest -q`, **868 passed in 130.47 s**, no failures/skips/xfails. Final S19 full suite remains required after later slices.
- Latest `uv run ruff check`: **passed** after S01-S04. Final S19 lint remains required after later slices.
- Requirement-by-requirement acceptance matrix, crash/recovery evidence, dependency versions and revision: pending.

## Acceptance matrix tracking

| Specification invariant | Current evidence / next proof |
|---|---|
| Prose and approved-plan sentinel, index on/off, other target | `test_requirements_context.py` covers both graph shapes; immutable CLI snapshots proven in `test_run_context.py`; approved-plan hashes pending S09 |
| Traversal/symlink/notebook/metadata/PRD rules/unknown tools | `test_path_policy.py`; fail-closed callback, real changed-path audit, no commit/publication effect |
| Parallel individually green, combined red | Pending S10; require real integration gate and retained siblings |
| Multiple auto layers, later layer failing | Pending S06; all-layer receipt required |
| Gate exception after previous passing result | HALTED-first routing added; full fresh receipt regression pending S06/S07 |
| Sequential escalated → parallel → sequential state reset | Pending S07/S08; factory plus real graph sequence |
| Add/amend/reset/squash failure, fixer/gate source edits | Checked add/diff/commit added; full fault matrix pending S05/S06/S08 |
| Agent toolchain replacement or sandbox protected edit | Implement/formatter/publication path audit covered; trusted text snapshot S03 proven; gate consumption S06 pending; sandbox disabled/rejected S17 |
| Terminal human plus plan-only approval | Pending S13 exact-SHA guard; no paid/live publication allowed |
| Invalid human topology before spend | S03 offline semantic validation proves all prohibited layouts stop before calls; final publication routing remains S13 |
| Malformed/error/missing-result review and minority blocking | Pending S12 strict unavailable/blocking policy |
| Deletion-only and cumulative revision patch review | Pending S12 real cumulative patch/read_diff |
| Restart after plan/commit/gate/join/push/create | Pending S04/S09–S11/S14; new-process crash evidence required |
| Corrupt/missing/wrong manifest and concurrent resume | S03 integrity and process-lock checks stop before executor construction; stage recovery remains S11 |
| Distinct run/worker/branch identities | S03 real clone/path/branch tests pass |
| 100-unit chain and 20 roots, max active 2 | Pending S16; measured deterministic concurrency |
| Exhaustion across all paid call classes, concurrent reservations | Pending S15; missing cost consumes reservation; resume cannot reset |
| Hanging command/children/large output and model timeout | Pending S05/S15; capped file logs plus process cleanup proof |
| Per-workspace sandbox sentinel, timeout, resume | Deferred option requires explicit enabled-config rejection in S17; real Docker gate remains unrun |
| Respond preserved feedback, synthetic unit, remote race/push failure | Deferred option requires explicit unsupported-until-hardened error in S18 |
| Optional observability/memory failure, critical journal failure | Offline live-SDK/network suite prevention added; production failure injection pending S04/S19 |

Contract schema edits require explicit human review before merge (spec §3.2). No merge is authorized or attempted. Legacy state-v1 execution must be refused without rewriting checkpoints; S03 legacy refusal/readability/no-rewrite proof passes; explicit terminal-stage recovery remains S11.

## Turn 2 closeout

- Final full command: `uv run pytest -q > docs/reviews/heavy-prd-turn-02-pytest-03.log 2>&1`, exit 0: **783 passed in 103.81 seconds**. The suite-wide transport guard blocked any accidental paid SDK or non-loopback network call; actual billed transport was never invoked.
- `uv run ruff check > docs/reviews/heavy-prd-turn-02-ruff.log 2>&1`, exit 0: **All checks passed!**
- `git diff --check` on tracked implementation files before receipt staging: passed. The later full staged check reported trailing whitespace in the verbatim failed pytest receipt (`heavy-prd-turn-02-pytest-02.log`); raw output is intentionally retained unchanged. `git show --check HEAD -- blacksmith tests docs/prd-authoring-guide.md docs/reviews/heavy-prd-progress.md docs/reviews/heavy-prd-evidence.md docs/reviews/heavy-prd-turn-02-environment.json`: passed. Dependency versions and input SHA-256 values retained in `heavy-prd-turn-02-environment.json`; no dependency bump or lockfile change.
- Follow-up targeted recovery/context/parallel command: `uv run pytest -q tests/test_requirements_context.py tests/test_implement.py tests/test_memory_lessons.py tests/test_review_loop.py tests/test_parallel.py`: **78 passed in 27.38 s** before the final literal-path regression addition; final full run covers that addition.
- Original spec SHA-256 and original probe SHA-256 unchanged; neither existing untracked file was edited.
- S01 and S02 slice completion criteria are met. R01-R12 as a whole, general heavy-PRD readiness and the remaining S03-S19 work are not complete.
- Brain capture: existing `Projects/blacksmith.md` updated through MCP with partial status and fixture gotcha; targeted lint returned 0 errors/0 warnings. After >70 seconds, MCP index status showed lastRun.errors=0 and invalidFrontmatter.count=0; semantic retrieval returned the new remediation section as rank 1 (`327e1bcc-0af6-4af0-943d-61cd4b1a88fd`).

- Implementation checkpoint committed locally as `88070ac` (`fix: enforce write policy and preserve PRD plan context`). Only requested implementation/tests/docs/evidence were staged; original untracked spec and probe remain untouched. Host pre-commit checks passed. No push.

## Turn 3: S03 run context and preflight

- Persisted version-2 `RunContext` contains UUID/thread, canonical source/full origin identity/base SHA, exact original PRD bytes/hash, normalized nonsecret execution config/hash, committed trusted toolchain text/hash and canonical artifact paths. Critical artifacts use atomic replace plus file/directory fsync.
- Runtime config loading resolves target and checkpoint/store/metrics/live/transcript paths relative to its own directory. Absolute paths retain meaning through canonicalization; invocation cwd no longer redirects storage.
- Before executor construction: semantic QA/DAG/target/path-rule validation, required tool availability, Git identity, selected commit, clean tracked source, committed verifier/config and command strings, and artifact writability. Credential-bearing origins are rejected without echoing credentials. Ignored local PRDs are allowed.
- Planning clone uses no hard links and is read-only, including metadata; source permissions remain unchanged. All prompt roles take the persisted PRD. Implementation and worker clones use the pinned planning base; worker paths and branches hash raw IDs and include run identity. Custom runtime config files are protected and displayed at approval.
- Full CLI drive/resume holds POSIX thread/run locks across approval waiting. A real child Python process proves competing lock ownership is refused and OS release permits reacquisition. A callback inside the actual CLI approval verifies the run lock is still held.
- Resume verifies manifests/hashes/source/planning/workspace before executor construction and restores saved execution settings. Only actual interrupts get Command(resume=...); ordinary-node drive uses None. Detailed side-effect reconciliation remains S04-S11, not proved by this slice.
- Legacy completed checkpoints remain readable and byte-for-byte checkpoint-equivalent after execution-resume rejection. Existing thread IDs are refused for fresh runs; no checkpoint deletion/reset. Structural parsing keeps historical PRDs readable; current execution semantics reject their invalid human layouts.
- `tests/test_run_context.py` covers real Git/CLI/graph/gate/checkpointer, sequential and fan-out resume after source advancement and original PRD removal, model/limit config changes, index and critic on/off, raw-ID collisions, corrupt/missing artifacts, wrong repository, no-model lock/integrity rejection, migration, UTF-8 diagnostics and CRLF hashes. SDK transport alone is replaced; no paid or publication transport runs.
- Targeted receipts: `heavy-prd-turn-03-targeted-01.log`: 36 passed / 11.05 s; `targeted-02`: 136 passed / 11.96 s (includes existing config/contract/CLI groups); `targeted-03`: 43 passed / 13.40 s; `targeted-04`: 49 passed / 16.40 s; `targeted-05`: 50 passed / 19.85 s before the last added approval-display/lock assertions.
- Full receipts: `heavy-prd-turn-03-pytest-01.log`: 826 passed / 115.58 s; `pytest-02`: 832 passed / 119.48 s. Final `heavy-prd-turn-03-pytest-03.log`: **833 passed in 120.36 s**, exit 0, including the fan-out proof and final approval assertions; no xfails/skips/failures.
- Transitional existing assertions were kept exact and updated to canonical config-directory paths, retained-thread semantics and exclusively human terminal QA fixtures. The fresh-refusal test now uses a real persisted checkpoint and forbids graph construction instead of bypassing validation with a nonexistent PRD.
- Runtime/migration docs: `docs/heavy-prd-run-context.md`; authoring semantics and README hold notice updated. No dependency changes, push, merge, paid canary, PR publication or Docker execution.

### Turn 3 closeout

- `uv run pytest -q > docs/reviews/heavy-prd-turn-03-pytest-03.log 2>&1`: exit 0, **833 passed in 120.36 s**. `uv run ruff check > docs/reviews/heavy-prd-turn-03-ruff.log 2>&1`: exit 0, **All checks passed!**
- `git diff --check` on tracked edits: passed. No regression-hiding skips or xfails. All model/network prevention fixtures remained active.
- S03 run-context regressions include version-2 **fan-out** resume after source advancement: workers executed under distinct `units/<raw-ID-hash>/workspace` paths, preserved exact prose/plan and used the frozen base; final PR gate was denied, so no publication transport ran. Successful worker retention is not claimed here and is still S11.
- Original specification and probe SHA-256 verified unchanged; metadata/dependency versions recorded in `heavy-prd-turn-03-environment.json`. No lockfile/dependency update.
- `Projects/blacksmith.md` updated through MCP with S03 status, migration and scoped proof. Targeted brain lint: 0 errors/0 warnings. After >70 seconds, index status reported lastRun.errors=0 and invalidFrontmatter.count=0; semantic retrieval returned the updated remediation section at rank 1 (`095869bc-3534-46e9-807b-967fd68e00a1`).

- Environment metadata was collected with `uv run python` (Python 3.12.13). An initial host `python3` metadata attempt failed because that interpreter did not have the project packages; it was not used as dependency/version evidence.

- S03 implementation checkpoint committed locally as `e8650ba` (`feat: freeze run inputs and validate version-2 execution`). Staged whitespace check and host pre-commit checks passed. Original specification and probe remain untracked and untouched. No push.

## Turn 4: S04 operation journal and effect receipts

- Added `receipts.py`: a separate per-run SQLite journal, WAL/FULL sync/foreign keys, strict run/context/schema ownership, explicit transactions, deterministic operation-key tuples, immutable intent/outcome/events, content hashes and corruption checks. mode=rw reopens cannot silently recreate a missing DB; atomic outcome+cost updates roll back together.
- Real SQLite tests exercise concurrent writers (8 threads, 40 requests, 5 unique intents/events), reopen, conflicting values, schema/owner corruption, rollback triggers and a child process using os._exit after committing intent. This is offline process/storage proof, not provider billing proof.
- Run-context bootstrap initializes the journal before the planning-clone effect. Resume validates it before executor construction. Version-2 model roles and implementation commits use receipts at the actual effect boundary. Git commits contain operation-ID trailers; replay of a completed identical clean commit does not add another commit; extra changes are retained and refused.
- Executor wrappers cover plan, critic, initial/escalated implement, reviewer seats and summary. Completed SDK responses are reused; pending/unknown/interrupted outcomes cannot silently rerun transport. Missing ResultMessage is now an error; known assistant turns and terminal cost/usage are preserved on stream errors. SDK native tool surfaces are explicit and summaries are text-only. Installed SDK supports tools=[] as --tools with an empty argument; actual option construction is exercised, without a live SDK escape claim.
- Critical receipt failures rethrow through optional critic and summary paths. Actual SQL-trigger regressions prove: no planning clone if bootstrap intent cannot persist; no provider call if model intent fails; no rebilling after outcome/cost rollback; no repeated plan after checkpoint interruption; no Git staging if commit intent fails; no publication after a summary receipt failure; and no executor construction/recreation for missing/corrupt journals.
- Cost events use stable call IDs and the real keyed state reducer. Exact schema assertions retained and updated for the mandated reducer; distinct calls with equal costs remain distinct, replayed IDs count once, conflicting IDs fail and old unkeyed events retain compatibility.
- Scoped limits: native command/fixer/squash/join/publication receipts, complete crash reconciliation/artifact recovery, budget reservations, deadlines and strict review verdicts remain later slices. Unknown model spend is retained as unknown; no invoice ceiling or exactly-once billing is claimed. Respond hardening/disablement remains S18.

### Turn 4 commands and receipts

- `heavy-prd-turn-04-targeted-01.log`: existing run-context/executor/implement tests, **108 passed in 28.71 s** before the terminal-stream/tool-set additions.
- `heavy-prd-turn-04-receipts-01.log`: journal/SDK/Git unit regressions, **26 passed in 1.37 s**.
- `heavy-prd-turn-04-wiring-01.log`: journal plus real CLI wiring regressions, **35 passed in 8.53 s**.
- `heavy-prd-turn-04-targeted-02.log`: journal/wiring/full-context/SDK regressions, **57 passed in 11.64 s**.
- `heavy-prd-turn-04-pytest-01.log`: full suite **867 passed, 1 failed in 130.10 s**. The failing assertion required operator.add, intentionally replaced by keyed receipt reduction. The assertion now remains exact for merge_events, and the helper exercises that real reducer plus replay/different-call totals.
- An intermediate cost test edit briefly referenced an incorrect helper name; corrected to the existing `_accumulate`. `heavy-prd-turn-04-cost-reducer.log`: **12 passed in 2.08 s**. This did not weaken any assertion.
- Final `uv run pytest -q > docs/reviews/heavy-prd-turn-04-pytest-02.log 2>&1`: exit 0, **868 passed in 130.47 s**, no xfails/skips/failures.
- Final `uv run ruff check > docs/reviews/heavy-prd-turn-04-ruff.log 2>&1`: exit 0, **All checks passed!** Dependency/input metadata and revision are retained separately. Failed full-run output is preserved verbatim, including its whitespace.

- S04 input/dependency/revision evidence: `heavy-prd-turn-04-environment.json`. Installed SDK `_build_command` was constructed with a deliberately nonexistent CLI path and no connect/query: it emitted `--tools` plus an empty argument, `--strict-mcp-config`, and the single literal `--setting-sources=` flag. An initial metadata assertion assumed a two-argument settings flag; source/argv inspection corrected that assumption without changing runtime code.
- Brain capture updated `Projects/blacksmith.md` through MCP. Targeted lint returned 0 errors/0 warnings. After >70 seconds, index status had lastRun.errors=0 and invalidFrontmatter.count=0; semantic retrieval returned the new journal section at rank 1 (`b5092462-8817-4334-8600-8f0fec501a41`).
- `git diff --check` on tracked edits passed. Verbatim failed pytest output is retained unchanged; staged source/docs whitespace checks exclude only raw `.log` receipts, whose whitespace comes from pytest formatting.

- S04 committed locally as `1b0d63f` (`feat: journal model and Git effects with durable receipts`). Host pre-commit checks passed; no push. Only the original specification and readiness probe remain untracked.
