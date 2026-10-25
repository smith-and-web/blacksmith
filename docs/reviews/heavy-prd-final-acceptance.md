# Heavy-PRD final acceptance audit

Goal turn26/30. **Implementation remains unaccepted as a whole.** S01 and S03–S18 have
local full-suite proof; S02 is reopened for paid PR-summary context. Prior baseline: full02 on539508be01b498749f6cb49c33c80b9d35047a04
ended exit0, **1398 passed/5 retained SDK advisories/5047.97s**, including actual
100-chain and20-root/default2 cases. All174 source/config/input/runtime identities
were unchanged before/after; turn24-environment-02.json and
turn24-full-command-02.json retain the proof. The earlier saved-config drift
failure is corrected with its original assertion intact; failed receipts remain.
S19 is open: final audit reproduced SDK error/unknown terminal subtypes accepted
as successful when is_error=false. Positive success is now required; focused
67/50/9 and Ruff pass. The cached-evidence migration correction is integrated as
85fb50e5f6da462ee5c51962c51af994fa100e8c: policy1 model intents prove stricter
normalization before reuse; unsupported historical results refuse execution while
remaining inspectable. All175 tested sources/config/inputs/runtime match stage
24dc05b after hooks; final compatibility134/347.66s passes with no warnings/skips/
xfails, primary Ruff passes and1414 tests collect. Original assertions are retained.
Final corrected-source full1414 is running exactly once (20455; supervisor25554/uv25555), frozen in turn25-environment-01.json with terminal receipt turn25-full-command-01.json. Its passing terminal result/unchanged identities and acceptance closeout are still required; no pass claimed.
This audit maps the settled
specification to production paths and regression evidence. A test's existence is
not its passing receipt. Exact command/results and frozen source/runtime manifests
belong in [the evidence ledger](heavy-prd-evidence.md).

The [turn26 inventory](heavy-prd-turn-26-acceptance-inventory.json) maps all12
requirements,19 slices and21 specification acceptance rows to existing production
files and collected regression cases on the exact frozen175-source candidate.
The audit reproduced another R02 gap: summary narration omits the full contract
and reads mutable state prose. Baseline1 failed/18.93s; isolated five-case correction
is under verification. S02 is reopened; even a green current1414 cannot accept that
newer source. Its1414 collected cases are not passing results. Every row remains pending the
terminal full run, unchanged identities and this acceptance closeout. Sandbox and
respond rows explicitly use the specification's permitted disabled alternatives.

## Required design and production wiring

| Requirement | Production path | Regression groups / existing audit |
| --- | --- | --- |
| R01 frozen run/preflight/paths/lock | CLI bootstrap/resume → RunContext manifest, immutable PRD/config/toolchain, flock → run-owned planning/shared/unit clones | test_run_context, test_contract, test_cli_validate, test_clone; heavy-prd-run-context.md |
| R02 full requirements/approved plans | context renderer → per-unit plan/critic in frozen cwd → critical plan artifacts/approval → shared child implement/review prompts in every retry shape | test_requirements_context, test_plan_integrity, test_unit_subgraph |
| R03 write/verifier boundary | role-specific SDK grants + callback PathPolicy → post-implementation/formatter audit → exact candidate guards before gates/publication; immutable trusted toolchain | test_path_policy, test_gate_integrity, test_receipt_wiring; sandbox enabled settings refused |
| R04 exact layer gates/joins | checked commit/fix → all declared auto layers → keyed unit outcomes → saved-source joins → joined layer-union gate → final exact candidate → normal/draft publisher guard | test_gate_integrity, test_integration_integrity, test_join_recovery, test_publication_approval |
| R05 shared owned recovery | unit_state factory → same compiled unit graph in both shapes → bounded continuations/fixes/escalation/review revisions → keyed UnitOutcome | test_unit_state, test_unit_subgraph, test_self_heal, test_fanout_self_heal, test_review_recovery |
| R06 durable artifacts/restart | atomic intents/outcomes/events + per-unit checkpoints → explicit stage controller → observed Git/formatter/join reconciliation; failure artifacts retained, uncertain models never silently replay; immutable capture/bundle/index + read-only inspect/repair | test_receipts, test_receipt_wiring, test_plan_integrity, test_effect_recovery, test_join_recovery, test_recovery_artifacts/control/repair/boundaries |
| R07 approval/publication | exact-SHA normal/draft proposal/critical decision → common publisher guard → remote ref/PR observation + sealed content/result → effect reconciliation; success cleanup requires retained publication evidence | test_publication_approval, test_approval_recovery, test_publication_integrity, test_publication_recovery |
| R08 budget/deadline/scheduling | all-role Executor admission + native command service + durable active clock + saved worker bounds and finite recursion; owned SDK/node cleanup before capture | test_budget_admission, test_executor_admission, test_run_clock, test_sdk_deadlines, test_parallel_cancellation, test_commands, test_scheduling_runtime; S15/S16 audits |
| R09 complete honest review | immutable cumulative patch/read_diff + strict verdict/terminal handling + per-seat critical receipts → any blocker/unavailable halts; disabled explicitly reported | test_review_integrity, test_review_recovery, test_review_panel, test_activity_integrity, test_render |
| R10 sandbox | **Permitted S17 deferral:** config/execution/SDK/native/graph/workspace boundaries reject enabled settings; no tool/startup path | test_feature_deferrals, test_sandbox_lifecycle, test_sandbox_tool; heavy-prd-disabled-features.md. Docker lifecycle not implemented/accepted |
| R11 respond | **Permitted S18 deferral:** valid CLI request/wrapper/library refuse before environment/config/PRD/GitHub/SDK/clone; help/parse compatibility retained | test_respond_cli, test_respond_flow; heavy-prd-disabled-features.md. Hardened revision workflow not implemented/accepted |
| R12 operational/test honesty | isolated Git fixtures, prohibited live SDK/Python network, native Git transport/auth isolation, real graph/Git/gates/SQLite, immutable evidence, optional diagnostics and task-owned previews | conftest; test_optional_observability, test_observability_integrity, test_activity_integrity, test_presentation_integrity; dashboard_activity_probe.mjs |

## New configuration trace

Every row is parsed by the strict typed configuration and saved in the immutable
runtime snapshot. Resume uses the saved values; missing required historical policy
refuses execution without rewriting old evidence. No dependency versions changed.

| Field / behavior | Parse → forwarding → point of use | Behavioral proof |
| --- | --- | --- |
| max_prd_bytes=262144 | LimitsConfig → bootstrap/ingest and planner → byte-size refusal before executor | test_requirements_context oversized configured input + test_run_context preflight; full requirements remain untruncated |
| max_parallel_units=2, positive strict integer | LimitsConfig → saved invocation_config/compiled defaults → owned node pool + shared child invoke | test_scheduling exact formula/default/invalid cases; test_scheduling_runtime actual1/3 workers, changed-local-config resume and final100/20 |
| max_run_cost_usd=50, explicit unlimited | typed finite cap + CLI --unlimited-cost → immutable budget policy → transactional BudgetLedger.reserve before every model role | test_budget_admission, test_executor_admission, test_admission_recovery and test_unit_admission; no implicit unlimited reset on resume |
| max_call_cost_usd=5 | saved policy → min(call cap, unreserved funds) grant → exact SDK max_budget_usd | test_executor_admission every role, concurrent grants/cached original options; test_clock_forwarding actual CLI configured cap |
| model_timeout_s=1800 | saved limits → bounded SDK collection/owned POSIX session → timeout bounded by remaining active run time | test_sdk_deadlines actual installed SDK local processes and both APIs; test_clock_forwarding configured limit |
| max_run_seconds=21600 | saved limits/persistent journal clock → graph execution scope, native/model remaining deadlines → stop/capture after child cleanup | test_run_clock, test_clock_forwarding, test_clock_recovery, test_parallel_cancellation; approval waits excluded |
| command_timeout_s=1800 | saved limits → command_scope in parent/child/native helpers → process-session TERM/grace/KILL/reap; Git/gh default120 seconds | test_commands parsing, all toolchain stages, configured real CLI both shapes/resume, remaining-time forwarding |
| max_command_output_bytes=1048576 | saved limits → file-backed native output capture → shared stdout/stderr cap, counted truncation, bounded checkpoint tails/full artifact refs | test_commands exact byte counts/caps/tails and CLI saved-value resume; test_review_integrity private full patch remains available despite smaller native output cap |
| sandbox.enabled=false | structural history parsing → require_supported_execution at runtime entry points → reject true before effects | test_feature_deferrals + lifecycle/tool tests; enabled historical inspect remains read-only |
| reserved respond.max_attempts | saved structural config → read-only recovery disabled_features metadata, explicitly unsupported | test_feature_deferrals nondefault saved7/local2 shows7 without effects/evidence mutation; unchanged config drift guard; no command enablement |
| existing review/recovery limits | saved config → shared child dependency bundle/counters → finite retries and recursion formula | test_unit_subgraph both shapes; test_review_recovery valid-seat reuse; test_scheduling formula and child configuration |

## Specification acceptance matrix

All rows require the final combined full pytest/Ruff run. Current S19 scopes and
earlier accepted results are in the ledger; no row below represents a live release
gate or a claim based on mocks alone.

| Required scenario | Local evidence / prohibited downstream effect |
| --- | --- |
| Prose/approved plan/index on-off/other target | test_requirements_context + test_plan_integrity use full sentinels and real options/cwd; no source checkout writes |
| Traversal/symlink/absolute/notebook/missing/metadata/untouchable/unknown tool | test_path_policy callback + actual-diff audits; source sentinel survives; no commit/publication |
| Individually green siblings, combined red | test_integration_integrity actual combined gate red; both artifacts retained; no push/create |
| First auto layer green, second red | test_gate_integrity runs both; no accepted receipt/publication |
| Gate exception after earlier pass | fresh nonpassing receipt + HALTED-first routing in test_gate_integrity/graph; no stale success |
| Sequential escalated → parallel → sequential | test_unit_state/test_unit_subgraph reset tier/counters/feedback and own review |
| Git add/amend/reset/squash/fixer/gate mutations | test_commands/test_gate_integrity/test_effect_recovery checked failures and exact retained candidate; no dirty passing evidence |
| Agent verifier replacement/protected sandbox edit | frozen trusted toolchain + post-diff policy tests; sandbox execution explicitly refused |
| Terminal human + plan-only approval | test_publication_approval both shapes; no summary/push/create; exact proposal retained |
| Invalid human sibling/mid-DAG/mixed/human-only | test_contract/test_cli_validate before model execution |
| Malformed/error/missing-terminal/minority blocking review | test_review_integrity both shapes + test_review_panel; unavailable/blocking never clean or ordinary publication |
| Deletion-only/cumulative revisions | actual Git patches/read_diff RPC in test_review_integrity; original behavior defect remains blocking |
| Kill after plan2/commit/gate/pick/push/create | real process exits and fresh-process recovery in plan/effect/join/publication recovery files; original receipts immutable, no repeated paid model/commit/PR effect |
| Corrupt/missing/wrong repo-config/concurrent resume | test_run_context/test_recovery_artifacts/control/boundaries; refuse before spend/mutation, old evidence unchanged |
| Repeated/raw IDs sharing slug | test_run_context distinct UUID paths/branches/raw-ID hashes |
| 100 chain /20 roots bound2 | test_scheduling_runtime actual CLI/shared graph/Git/gates/review/ledger/SQLite; strict active-worker measurement and original barrier passed in full1398 on539508b. Later terminal-policy changes still require a corrected-source full rerun |
| Exhaustion in all paid roles/concurrent grants | S15 audit and admission/recovery files; no later admitted call, unknown usage charged, totals survive resume; respond refuses before calls |
| Hanging native child/large output/model timeout | test_commands/test_sdk_deadlines/test_parallel_cancellation actual local process groups and late-write sentinels; bounded output/state, retained partials |
| Sandbox sibling sentinel/timeout/resume | **Permitted disabled alternative:** enabled config rejects before effects. Real Docker lifecycle/smoke remains a future enablement gate |
| Respond retry/human-first/remote race/push failure | **Permitted disabled alternative:** no execution/effects; scratch remote histories unchanged. Actual revision behavior remains deferred |
| Optional store/live/metrics vs critical receipt | test_optional_observability actual CLI effects/call counts/balance unchanged with safe bounded diagnostics; test_receipts/test_receipt_wiring critical faults halt before effects |
| Honest activity/counts/presentation | new S19 graph/Git recovery and journal identity tests + actual inline-JS offline probe; full original evidence retained, no invented clean/resolved counts |

## Migration and recovery evidence

State-version1 completed checkpoints remain readable; execution refuses
legacy_checkpoint_requires_manual_recovery. Version2 still requires the current
recovery, scheduling, budget, clock and positive-terminal model policy. Model
intents record exact integer terminal_policy_version1; missing/boolean/future
versions refuse before native work, SDK credentials, clock writes or budget
amendments. Read-only inspection reports unsupported model policy separately
from artifact integrity. The new migration regression also guards direct dispatch,
retained-review verification and library Executor calls. Missing/corrupt metadata is not
backfilled with invented hashes, historical usage or timestamps. Existing context,
checkpoint, journal and bundle bytes stay unchanged on refusal. Read-only recovery
inspection uses a disabled local sandbox config and does not construct execution
dependencies. See test_admission_migration, test_scheduling_runtime,
test_feature_deferrals and the named recovery boundary tests.

Failed/interrupted runs and raw receipts are retained. Completed accepted S13–S15
fixture trees were archived only after byte/mode/symlink verification and live-owner
checks; the archive proof JSON contains hashes, original paths and restore commands.
No protected .env entry was read or archived. Failed test roots were preserved.

## Final verification and release boundary

Before local completion, save the complete Python/JS/config/input/runtime identity,
run full pytest (including100/20) and Ruff, verify terminal receipts and unchanged
source/dependencies/inputs, then update this audit/checklist. Expected counts and
partial progress are not passing results. Node probe is behavior evidence with a
minimal DOM stub, not browser layout proof. Installed SDK process tests use local
offline scripts, not provider billing proof. Git pushes only target scratch local
bare repositories; GitHub/Docker services are explicitly replaced where documented.

Remaining live gates: explicit human review of contract.py before merge; authorized
disposable-target $10 paid canary with two independent auto units plus terminal QA,
plan-only refusal then exact-SHA draft approval/publication; real Docker ownership/
timeout/restart smoke before any future sandbox enablement. None has run. This task
prohibits push/merge/publication/paid canaries. The historical turn13 unintended
external push attempt failed128 before authentication; its receipt is retained and
must remain disclosed in the goal closeout. No successful live publication is claimed.
