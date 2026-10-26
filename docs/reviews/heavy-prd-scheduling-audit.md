# S16 acceptance audit

Goal25/30: **S16 accepted locally**. Full02 on539508b passed1398 tests/5 retained
SDK warnings/5047.97s, exit0, including the actual100-chain and20-root/default2
cases. All174 source/config/input/runtime identities remained unchanged before
and after; turn24-environment-02.json and turn24-full-command-02.json retain proof.
S17/S18 permitted deferrals are accepted in that same full suite. S19 terminal
integrity and cached-evidence migration require another corrected-source full;
the earlier passing result is not proof of later changes. Historical scopes follow.

| Requirement | Production wiring | Evidence / remaining proof |
| --- | --- | --- |
| Positive integer/default2 workers | LimitsConfig.max_parallel_units, sample config, immutable invocation config and compiled defaults | Parsing/default rejection and actual3-worker runtime pass. Original20-root/default2 strict barrier/maximum passes after node-pool fix. |
| Fresh/resumed parent bounds | CLI derives each invocation from saved inputs; library bootstrap defaults before seeded state | Actual changed-local-config resume retains original3-worker setting and immutable context. |
| Streaming coordination cannot steal node slots | node_execution owns a bounded BackgroundExecutor via runtime runner-submit hook; outer coordination/checkpoint pool has at least2 slots | Original20-root barrier exposed installed LangGraph1.2.9 waiter consuming one bounded slot. Final actual single-worker CLI, SQLite synchronous custom-stream delivery before node completion and queued cancellation/drain pass. Actual node max remainsN. |
| Shared child bounds | unit_runner passes derived config to actual child invoke with inherited durability/checkpointer | Actual child configs retain exact savedN and recursion. Runtime hook is never persisted. |
| Exact recursion formula |100+20*N*(1+fix+1+review)*(1+continuations) | Formula cases and actual100-unit chain pass;100 must rerun on final integrated source. |
| Declared overlap serialization | execution_batches projects dependency frontiers into deterministic conflict batches | Path/subtree/glob/case/transitive and actual same-target/frozen-symlink cases pass. |
| Dependencies/plans unchanged | Projection reads declarations without changing original edges/approved plans | Full contract equality and later overlapping unit starting from prior candidate pass. |
| Frozen target interpretation | Pattern prefixes/internal aliases resolved from immutable planning snapshot | Alias regression passes; uncertain scopes conservatively overlap. Actual writes remain path-policy guarded. |
| Exact joins/gates | prepare_worktree stores batches/version; levels_for/layer_union validate saved inputs | Original first30 passed previously; final93-case regression live. |
| Unexpected writes remain conflicts | Disjoint declarations actually editing one file still reach rollback | Original both-worker/base/rollback/no-publication assertions retained; corrected target passes1/11.35s. |
| Migration before effects | Resume validates metadata before native scope/executor; routes/integration revalidate | Missing/changed metadata preserve checkpoint/journal/events with no executor/native/effects. |
| Admission/cancellation | Existing all-role atomic grants; queued-node cancellation plus active cleanup drain | Original four actual local-SDK modes pass (worker error, SIGINT, SIGTERM, scope-entry fault), retaining no-late-write/timing assertions. Primary full still required. |
|100-chain/20-root scale | Actual CLI/child/Git/gates/review/ledger/checkpointer/scratch remote; paid/GitHub transport replaced |100 passes under4d89e01 with301 calls/exact3.01 fixture cost;20/default2 passes with owned pool. Final integrated full must include both. |

## Source and integration boundary

- Accepted S15:44f0108/full1325/6 SDK warnings/3373.29s, exit0; complete165-source/
  config/input/Python/dependency/SQLite identity in turn21-environment-03.json.
- S16 stagea9ff632b/168-source identity turn21-scheduling-source-03.json;
  adjacent93/1061.65s passed. Verified supervisor51538/pytest51552 gone; launch
  handle truncated, so no independent supervisor exit-code claim. Ruff22 passed.
- S16 feature-only13-file delta c4c18cd..a9ff632b integrated asfc94de2, preserving
  six exact accepted S15 files. Complete168-source comparison and patch hashes in
  turn22-scheduling-integration.json. No baseline replay.
- S17 then S18 feature-only deltas780bd45..2c197fe..e51d653 integrated as
  24e52e1/33eba45. Complete169 primary sources/config/input match final-stage
  turn22-deferrals-source-03.json; integration phases/hashes recorded separately.
  Scoped227/30/31 and ruff passed; disabled feature alternatives do not prove
  Docker ownership or a live revision workflow.
- All named handles terminal. Implement S19, capture final source/runtime identity,
  then full pytest/ruff including100/20 before S16 acceptance. Historical source
  and failed/passing receipts remain retained.

## Results and limits

Foundation88/0.39s; runtime25/158.21s; rollback1/11.35s. Old consolidated01 ended
30 passed/223.38s then obsolete declared-overlap fixture interrupted as designed.
Negative rollback fixture corrected without dropping assertions. Old scale02 ended
1 passed/1 failed/2231.83s:100 passed,20 failed barrier, remaining63 did not run.

Workers01:5 passed/1 failed/494.69s; original20-root/default2 and four local SDK
cancellation modes passed. Single-worker timeout exposed insufficient coordination
slots. Final minimum-two coordination fix passed22/25.22s, including all21 pure/
checkpointer/cancellation tests and actual single-worker CLI. Fresh staging ruff
passed. All intermediate sources/failures/probes retained.

The starvation probe field named actual_node_threads_can_start_before_timeout
counts total starts, not pre-timeout starts; do not use it for timing evidence.
The original strict barrier failure and installed-source trace establish the bug.

S01-S18 acceptance is complete; S19/final corrected-source
full still required. Paid provider
billing/publication canary, human contract review and real Docker enablement smoke
remain unperformed release gates. No live readiness is inferred from offline tests.
