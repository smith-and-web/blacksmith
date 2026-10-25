# Publication reconciliation and recovery

S14 connects both versioned PR nodes to one publication service. Local acceptance
is tracked in the [evidence record](reviews/heavy-prd-evidence.md) and
[implementation checklist](reviews/heavy-prd-progress.md). Heavy-PRD readiness
still depends on the later slices and unrun live release gates.

S15's [admission recovery](heavy-prd-admission.md) distinguishes a denied unstarted
summary, reusable completed overrun response and started budget-stop/unknown call.
Only the latter needs an explicitly acknowledged continuation. These paths retain
the same approved candidate and never authorize repeating an uncertain native
push/create. Prepared PR metadata keeps measured and conservative accounting separate.

## Identity before effects

Normal and terminal-QA drafts share the [exact-candidate approval](heavy-prd-approval.md).
Before a query, summary or effect, the service validates the approved clean known
candidate, original run identity, final gate/review evidence, workspace, branch,
base and origin. Both Git fetch origin and every configured push URL must identify
the saved repository. A foreign or extra push endpoint refuses publication even
if the fetch URL still matches.

The target binds run ID, exact commit/tree, saved host/owner/repository, head/base
branches and draft intent. Branches include the run UUID. The publisher queries
the exact remote ref with bounded `git ls-remote --refs`, and paginated GitHub
pull-request data with explicit saved host/repository and head identity. It requests
all PR states and validates returned base/head repositories, refs, candidate SHA,
draft flag, PR number/URL and marker. A response filter alone is insufficient.
The base is validated from each response rather than hiding a mismatched-base PR
behind a filter. Multiple matches or any identity mismatch halt for inspection.

A different remote commit is a conflict. Push uses the approved object explicitly
as `<SHA>:refs/heads/<owned-branch>` without force. Exact matching branch/PR effects
are reused. An exact branch with no PR skips push and proceeds to PR reconciliation.
Remote queries run before optional summary generation and again after it, so a
long summary cannot authorize a push/create using an earlier remote observation.
Local approval is checked again before each effect and the terminal receipt.
Per-run locks exclude competing local owners; these checks do not lock out an
independent remote administrator. Non-fast-forward changes remain Git conflicts.

The PR body contains one machine-readable HTML comment:

```text
<!-- blacksmith-publication: <canonical JSON target> -->
```

The JSON includes `version`, `run_id`, `candidate_commit`, `candidate_tree`,
`origin`, `branch`, `base_branch` and `draft`. Matching requires the exact target,
including the original run/candidate. The service does not choose the first of
multiple matches, change a draft to ready, reopen a PR or merge it.

## Intent, observation and result

The critical operation journal records each fresh remote observation and each
push/create intent before execution. Native commands retain their bounded logs
and independent process/exit receipts. After push/create, including a nonzero or
ambiguous response, the service observes the remote again. A command's printed URL
alone never establishes publication success.

Once optional summary generation finishes, private immutable content and its
cost-event references are sealed before publication effects. Candidate-keyed SDK
receipts and saved content prevent repeated summaries across recovery. Summary
events use the run identity; prepared build metadata includes that event through
the keyed reducer. Unknown usage remains unknown. Global spending admission and
SDK cancellation are S15 work; no exact invoice ceiling is claimed here.

Artifacts live alongside S13's approval files under the private run directory:

```text
publication/<target-digest>-content.json
publication/<target-digest>-result.json
```

Each has critical intent, content/hash seal and completion records. A local seal
can finish an interrupted file publication without regenerating the content or
replaying an external effect. An altered/unsealed artifact refuses reuse. Completed
artifacts that become missing or corrupt refuse execution; recovery does not
invent a replacement success receipt.

The result binds target, exact observed PR, observation ID, original effect IDs and
owned artifact path. `DONE` or `AWAITING_QA` requires this validated result and its
original approval. Resume validates retained evidence before constructing an
executor. Terminal URL/status/QA fields must agree with the result and approval;
a bool, substituted URL, revoked approval or false QA label cannot establish success.

## Explicit recovery

On failure, retain the candidate, branch, every worker, original failed/uncertain
receipts, command logs, prepared content and recovery capture. Inspect the absolute
path and exact next-action command printed by the CLI.

For a definite publication-stage failure:

```sh
blacksmith resume --config /absolute/runtime.toml --thread-id <id> --retry-stage
```

This is the saved stage under the frozen inputs and consumed budgets. A matching
branch/PR is reused. If push succeeded but create failed, retry queries and creates
only the missing PR, reusing the summary and existing candidate. It does not rerun
plans, units, reviews, gates or approvals solely to retry publication. Original
failure records remain immutable beside the positive reconciliation evidence.

An ordinary node interrupted before its graph checkpoint resumes with `None` and
reconciles its recorded effects. A complete remote PR after a lost response or
process exit is adopted only after exact identity/marker validation. The four
offline exit drills cover push, PR create, result-before-checkpoint and sealed
content-before-file, preserving call IDs and costs without duplicate effects.

An unconfirmed native create with no observed PR halts for process/effect
inspection. A query returning nothing is not proof that an old process stopped.
A later exact positive PR observation can establish the effect without repeating
create; the original uncertain command remains uncertain. A missing remote branch
after a known completed push is a conflict, not permission to recreate it.

New workspaces record `publication_version=1`. Older active workspaces without it
refuse with `legacy_publication_requires_manual_recovery`, preserving checkpoints,
artifacts and spend. Pre-workspace runs may enter the new topology. Historical
completed runs remain inspectable through the read-only recovery command; no old
approval or publication hashes are manufactured.

The terminal exits remain **0** `DONE`, **2** `AWAITING_QA`, and **1** halt/error.
The QA handoff reports the approved pending unit/check and draft URL. It does not
claim human QA passed. Repeating a terminal resume performs no SDK/effect replay.

## Evidence boundaries

Tests execute actual graphs, SQLite, gates, Git refs and scratch bare-remote pushes,
replacing paid transport and GitHub responses. These prove the tested local
reconciliation behavior. They do not prove a live GitHub/provider release gate,
exactly-once billing, physical termination of an unknown process or Docker isolation.
The disposable-target paid canary, explicit human contract review before merge,
and any Docker enablement smoke remain separate unrun gates.
