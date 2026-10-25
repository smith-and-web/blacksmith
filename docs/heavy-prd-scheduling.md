# Bounded execution scheduling

S16 is an isolated implementation candidate under verification. The
[progress checklist](reviews/heavy-prd-progress.md) records accepted slices and the
[evidence ledger](reviews/heavy-prd-evidence.md) records exact test/source identity.
The corrected primary S15 full02 suite is running; this document is not an acceptance claim.

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
