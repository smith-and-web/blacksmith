# Bounded execution scheduling

S16 is accepted locally after the full 1,398-test run. The
[progress checklist](reviews/heavy-prd-progress.md) records accepted slices and the
[evidence ledger](reviews/heavy-prd-evidence.md) records exact test/source identity.
The full run included the actual 100-unit chain and 20-root/default-two-worker cases.
The [S16 audit](reviews/heavy-prd-scheduling-audit.md) maps current sources and proof.

## Concurrency and dependency order

```toml
[limits]
max_parallel_units = 2
```

The limit is a positive integer; two is the default. Production parent execution
passes it through LangGraph invocation configuration. Each shared unit subgraph
also receives the saved invocation limits. The atomic admission service still
reserves each paid call independently; the worker limit cannot replenish money.

The original dependency contract is unchanged. Each dependency frontier is
projected into execution batches: potentially overlapping target groups run in
separate batches, in declaration order. Disjoint groups can share a batch. Every
original dependency barrier remains. Existing saved-base, unit-result, join and
integration-gate checks apply to the actual execution batches.

Target overlap includes the same path, ancestor/descendant subtrees, conservative
glob prefixes and normalized case. Internal symlink aliases are resolved against
the frozen planning snapshot. Unknown pattern scope is treated conservatively.
Unexpected edits outside declared target groups can still conflict at join; the
join must halt and preserve work instead of forcing a merge.

## Owned node execution and progress

The CLI owns a separate node executor bounded by the saved worker limit.
LangGraph custom-stream waiters and checkpoint coordination use their own pool
with at least two slots. Those slots do not increase active work-unit concurrency;
even a single-worker run can stream progress and checkpoint without starvation.
On scope exit queued nodes cancel and active cleanup drains before recovery
capture. The runtime runner-submit hook is never persisted and adds no paid call.
Direct library code using custom streaming should wrap invocation in
`blacksmith.scheduling.node_execution(config)`, as the CLI does.

This fixes an installed LangGraph1.2.9 behavior: its stream waiter occupied one
slot in the same bounded background pool as real nodes. The original20-root
barrier exposed this defect. Separate coordination needs at least two slots for
synchronous checkpointing; the configured node bound remains authoritative.

## Finite recursion allowance

Each CLI execution or resume and every unit-subgraph invocation derives:

```text
100 + 20 * N * (1 + max_fix_attempts + 1 + max_review_revisions)
             * (1 + max_implement_continuations)
```

`N` and limits come from the immutable run inputs. This replaces the fixed150
ceiling. It provides room for bounded recovery transitions; finite unit policy,
run deadline and admission are still the execution guards. No extra model calls
are added by changing this graph allowance.

## Resume and migration

Resume uses the saved worker/recovery limits even if local configuration changes.
Prepared versioned runs bind their execution batches to the original immutable
contract and planning snapshot. Missing scheduling metadata or changed/duplicate/
foreign batches refuse before native observations or executor construction; the
checkpoint, operation journal and artifacts remain unchanged. Old completed runs
remain inspectable through the read-only recovery command. Missing historical
metadata is not invented and existing paid work is not silently rescheduled.

A run interrupted before workspace preparation can derive its first schedule from
its saved inputs. Do not edit a checkpoint to bypass a scheduling diagnostic.
Inspect the retained run and use a fresh run ID when a compatible continuation is
unavailable. Live release gates remain separate from these offline invariants.

## Verification boundary

The actual100-unit chain, original20-root/default2 barrier and single-worker CLI
passed in the integrated full suite on source539508b, with exact source, input,
configuration and runtime identity retained. Later S19 changes require their own
final integrated full pytest/ruff; the earlier pass is not proof of later code.
Scratch remotes and replaced billed/GitHub transport do not prove live provider
billing, physical remote cancellation or successful publication. See the audit
for exact sources, intermediate failures, live jobs and transfer instructions.
