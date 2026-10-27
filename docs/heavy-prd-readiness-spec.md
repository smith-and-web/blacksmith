# Blacksmith heavy-PRD readiness: review and implementation specification

Date: 2026-10-24

Reviewed revision: `dcc3bb56940c3907b2e0fcaf13c254e1c55f9767`

Implementation audience: GPT-6.1-sol

Disposition: **Do not start heavy PRDs on this revision. Implement the release gates below first.**

This document is the implementation handoff, not a request for another architecture proposal. Decisions below are intentional changes to existing behavior, including several behaviors currently asserted by tests. Do not preserve those bugs to keep the old tests green. No runtime code was changed by this review. Implement directly on a feature branch; do not ask the current Blacksmith to bootstrap these fixes before its requirements and safety paths are repaired.

## 1. Review scope and evidence

Reviewed the production CLI → ingestion → planning → approval → isolation → sequential/parallel implementation → fixing → gating → review → publication → cleanup paths, plus resume, respond, executor permissions, toolchain configuration, indexing, sandbox lifecycle, memory, metrics, transcripts, dashboard, remote spike, and representative tests. The primary findings below come from current source, not the brain's historical status claims.

Brain context consulted:

- `Projects/blacksmith.md`
- `Reference/blacksmith - running and operating.md`
- `Reference/blacksmith - architecture and graph.md`
- `Reference/blacksmith - configuration.md`
- `Reference/blacksmith - failure classes and gotchas.md`
- `Reference/blacksmith - PRD contract and Kindling gates.md`
- `Decisions/Decision - blacksmith implementer has no host shell.md`
- `Decisions/Decision - blacksmith runs in throwaway clones.md`
- `Plans/Plan - Prime Agent ideas for blacksmith.md`

These establish the intended clone boundary, authoritative deterministic gate, local observability, bounded recovery, self-build workflow, and history of features tested through permissive fakes but broken in production. Preserve those architectural choices. The proposed assessor/evaluation PRDs are separate work; none closes the correctness defects below. In particular, the current memory discriminator already includes version and a hash of unit IDs, so the older note that it uses only unit ID is stale (content collisions remain possible but are not a heavy-PRD release blocker).

### Validation performed

- `uv run ruff check`: passed on the reviewed source.
- `uv run pytest -q`: **737 passed, 2 failed**, 131.78 seconds. Failures: `tests/test_implement.py::test_stage_and_commit_raises_when_commit_fails` and `tests/test_implement.py::test_commit_survives_conventional_commit_msg_hook`; both expected a `CommitError` but no exception occurred. Do not call this baseline green. Environment isolation is investigated separately below.
- Targeted environment check: removing inherited `GIT_*` variables alone still produced both failures. Setting subprocess-only `user.useConfigOnly=true` and `core.hooksPath=.git/hooks` made **both pass (2 passed in 2.66 seconds)**. The host has a global hooks path overriding the fixture hook, and the no-identity test does not require configured identity. Fix the test fixtures; no user Git settings were changed. This targeted rerun does not replace the recorded full-suite result.
- Offline probes: `uv run python docs/reviews/2026-10-24-readiness-probes.py`. All repository writes occur in temporary repositories; model execution and GitHub publication are faked. The combined-tree check uses the real gate and Git operations. These probes demonstrate current defects, not future acceptance tests.
- No paid model run, real push, real PR creation, Docker execution, or live recovery drill was performed. Source-confirmed behavior and local reproductions are identified separately. Passing mocks do not establish live SDK permissions or process-restart safety.

Probe output:

```json
{
  "traversal": "PermissionResultAllow",
  "notebook": "PermissionResultAllow",
  "git_config": "PermissionResultAllow",
  "combined": {"status": "done", "actual_gate_at_push": [false]},
  "plan_has_prose": false,
  "plan_cwd": null,
  "implement_has_prose": false,
  "implement_has_plan": false,
  "qa_plan_only": {"status": "awaiting_qa", "gates": ["plan"], "pushes": 1},
  "failed_workspace_exists": false,
  "stale_gate_route": "approve_pr"
}
```

## 2. Ranked findings

P0 means a violated write/publication boundary or false acceptance. P1 means a blocker for expensive, long, multi-unit runs. All findings are confirmed in source; “reproduced” additionally means the offline probe exercised the behavior. Line references are to the reviewed revision.

| ID | Priority | Finding and concrete failure | Evidence |
|---|---|---|---|
| B01 | P0 | PRD prose and the approved plan never reach implementation. A decision or acceptance criterion present only in the prose is omitted from both planning and implementation; critic-approved steps are displayed but never consumed. Planner calls also omit `cwd`, so raw Read/Glob/Grep can inspect the caller's directory rather than an explicitly configured target. | Reproduced. `nodes/plan.py:220,274`; `nodes/implement.py:299,445`; fan-out payload `graph.py:1207`. No production consumption of `prd.body`. |
| B02 | P0 | Write containment is bypassable. Relative `../` paths return true without resolution; notebook writes use `notebook_path`, which is not inspected; `.git/config` is writable. PRD-specific path untouchables never reach the hard guard: it always uses four built-in patterns. These are permission-check defects; a live SDK escape was not attempted. | Reproduced callback allowances. `nodes/implement.py:135–186,228`. |
| B03 | P0 | Parallel commits are cherry-picked without testing the combined tree. Two disjoint changes can each pass alone and fail together, yet the run ends `done` and publishes. Multi-layer units run only `layers[0]`, skipping other declared auto checks. | Combined failure reproduced. `graph.py:281–299,941–1032,1271`; `gate.py:107`. |
| B04 | P0 | Gate evidence can describe a different tree from the published commit. Gate commands can dirty the workspace; failed fixer amendments are ignored; toolchain commands are loaded from the agent-editable clone. A passing dirty tree or a modified `test_cmd = "true"` can be accepted. Git add/reset/squash failures are inconsistently checked. | Source confirmed. `gate.py:107–151,194–223`; `nodes/implement.py:390–414`; `graph.py:255,484,535,868–903`. |
| B05 | P0 | A human-QA unit routes directly to `open_draft_pr`, bypassing PR approval. In a parallel level it instead goes through the ordinary worker gate/join path; there is no human-unit routing there. Mid-DAG QA terminates the sequential run before later units. | Plan-only push reproduced. `graph.py:1119–1136,684–911`; `nodes/implement.py:211`; `tests/test_human_qa.py:306`. |
| B06 | P1 | Sequential state leaks across a parallel level. `next_unit` resets counters, but `join_level` seeds only the next unit and cursor. After sequential → parallel → sequential, stale escalation, review-clean, retry and continuation state can reach the last unit, changing model selection or skipping review. A `GateError` returns HALTED without clearing an earlier passing result; routing reads that stale pass and ignores status. | Stale gate routing reproduced; reset asymmetry source confirmed. `graph.py:450–481,299–301,1018–1032,1151`. |
| B07 | P1 | Failure destroys the expensive artifacts. Any terminal halt removes the shared clone; failed joins remove every worker clone, including successful siblings and partial failed work. Checkpoints retain paths to deleted files. A later `resume` of a terminal halt simply returns the terminal state. | Deleted failed workspace reproduced. `graph.py:917–923,961–1005,1039–1094`; `cli.py:292–318,427`. |
| B08 | P1 | Checkpoints do not make Git/model/publication side effects replay-safe. Planning all units and each worker's whole recovery loop are single nodes. A crash can replay billed work; a crash after commit but before checkpoint can re-enter implementation and reject its empty diff; a crash during join can re-pick already applied commits. Clone/branch names depend only on unit ID, causing rerun and concurrent-run collisions. PR creation has no reconciliation of an already-created PR. | Source confirmed. `nodes/plan.py:206`; `graph.py:684,941`; `worktree.py:145–185`; `nodes/pr.py:61–97`; `cli.py:292,320,427`. |
| B09 | P1 | `max_run_cost_usd` is a recovery check, not a run budget. Initial plans, new units, review calls and summary calls continue spending; workers each compare against the same stale pre-level total. Raised SDK errors lose known turn progress and record cost as unknown, which totals treat as zero. There is no model-call wall timeout; gates/fixers/SBFL can hang indefinitely. | Source confirmed. `graph.py:372–431,755–767`; `executor.py:153–197,282–300`; `gate.py:212`; `sbfl.py:57`. |
| B10 | P1 | Review cannot substantiate “diff reviewed.” It receives a diffstat and current files, no patch or original contents. Missing/malformed/error responses count as clean. Exhausted blocking findings still publish a normal PR. Panel behavior differs: sequential routing uses consensus, worker routing uses any blocking finding. An incomplete SDK stream without a terminal ResultMessage also counts as successful execution. | Source confirmed. `nodes/review.py:130–163,177–198,240–264,267–300`; `graph.py:640,835–856`; `executor.py:347–372`. |
| B11 | P1 when sandbox enabled | All workers share the single sandbox mounted on the shared clone, while their Write/Edit tools target separate build clones. Self-verification reads or modifies the wrong tree. Container name is global; startup failure still advertises the tool; reconstructed managers on resume do not reattach. Killing the docker client on timeout does not establish that the in-container command stopped. | Source confirmed. `graph.py:244,668–682,1409–1422`; `nodes/implement.py:250–289`; `sandbox.py:35,120–176`. |
| B12 | P1 | Heavy DAGs hit a fixed 150-superstep cap; a passing sequential unit normally costs implement + fix + gate + review + advance, with recovery adding more. The comment's roughly 50-unit capacity is inaccurate with review enabled. Fan-out has no application-level concurrency bound. | Source confirmed. `cli.py:194–232`; graph edges and `_fanout_sends`. |
| B13 | P1 for respond | `respond --prd` adopts only the contract's first work unit, potentially the wrong module or even human-only QA. Retries replace the original reviewer request with gate output. It ignores configured implementation limits/index/review, gates only the selected/default toolchain, and deletes the clone even after a failed push. | Source confirmed. `respond.py:222–271`; `cli.py:1020–1075`. |

Additional issues to fix while touching these paths: planner memory lookup can raise despite the best-effort contract (`nodes/plan.py:297`, `memory.py:114`); unknown-session transcripts overwrite the same filename (`executor.py:397`); live events from fan-out lack reliable task/unit identity (`cli.py:234–263`); summaries can label disabled/error review “clean” (`nodes/pr.py:396`). These are smaller than B01–B13 and do not justify a dashboard redesign.

## 3. Fixed scope and compatibility decisions

1. Keep Python, LangGraph, SQLite, Claude Agent SDK, local throwaway clones, the existing model tiers, and no host-shell tool for agents. GPT-6.1-sol is the implementer of this specification; do not migrate Blacksmith's runtime to OpenAI as part of it.
2. Keep Contract v1 field names. Add validation and document semantics rather than inventing fields. Changes to `contract.py` require the repository's explicit human review before merge. This spec describes those changes; it does not waive that review requirement.
3. Treat human units as manual QA declarations, not code-generation work. Permit zero or one human unit, exclusively human layers, terminal, and transitively dependent on every auto unit. Reject all other human arrangements in `validate` before spending. A human-only PRD with no auto unit is invalid because it cannot produce a change. Future mid-DAG human execution is out of scope.
4. All publication, draft or normal, requires the `pr` approval. No exception for `--approve plan`. No auto-merge. Blocking/unavailable review halts and preserves work; do not silently downgrade a blocked run to a draft.
5. Default review policy becomes fail-closed when enabled. Explicit `[review].enabled=false` is still supported and reported as `disabled`, never `clean`.
6. Default unit concurrency becomes 2, with a positive configurable bound. Same-file declared target overlap serializes units in declaration order, including directory-prefix and glob overlap conservatively. Runtime conflict handling is still required because declarations are not enforcement.
7. Preserve artifacts on halt, rejection, timeout, interruption and publication failure. Successful publication is the only automatic workspace deletion path, after a recovery bundle and receipt exist. Do not push a recovery branch without PR approval.
8. Keep trusted host toolchain execution for native targets in this milestone. Clones are **not** OS sandboxes: generated tests execute on the host. Strip model/admin/GitHub credential variables from toolchain processes, keep the trusted toolchain snapshot immutable, and document this trust boundary. Do not claim arbitrary untrusted repositories are safe. Full container-first authoritative execution is separate work. Disable optional self-verification until B11 is fixed; validation must reject enabled sandbox in an interim release that has not fixed it.
9. Runtime compatibility: old completed checkpoints remain inspectable. Refuse execution-resume of pre-migration checkpoints with a clear `legacy_checkpoint_requires_manual_recovery` message; do not guess missing hashes/receipts. Do not delete or rewrite them. New state carries `state_version = 2`.

## 4. Required implementation design

### R01 — Immutable run identity and preflight

Add `blacksmith/run_context.py`. A persisted `RunContext` contains `state_version`, UUID `run_id`, user-facing `thread_id`, canonical `repo_path`, full origin identity (host + owner/repo), `base_commit`, PR base branch, PRD SHA-256, full PRD text, normalized runtime-config snapshot/hash with secrets excluded, and trusted toolchain text/hash. Snapshot once before the first paid call. Plan and implement against that same base commit, even if the source checkout changes during approval. Do not silently switch to the latest remote main; report exactly which committed HEAD was selected.

Resolve runtime storage paths relative to the runtime config file's directory, not whichever nested working directory invoked the CLI. Resolve `target.repo_path` using the same rule. Absolute paths retain their meaning. Persist canonical paths; resume uses the recorded run context and validates it against the selected repository. Exclude credential values from snapshots; record env-var names only.

Before spending, validate PRD semantics, committed `blacksmith.toml` and all required layer commands, Git identity, source commit, tool availability, output directory writability, and that declared units can be scheduled. Reject a dirty tracked checkout with a precise message to commit/stash; allow ignored local PRDs. Do not require remote network access just to validate a PRD. The run preflight may check `gh auth status`, but never print tokens.

Use `fcntl.flock` for an exclusive per-run execution lock held for the whole CLI drive/resume. A second process for the same run exits before invoking tools. Different runs get distinct paths and branches: `.blacksmith/runs/<run_id>/workspace`, `units/<unit-id-hash>/workspace`, and `blacksmith/<run_id>/<unit-slug>`. Hash the raw ID to avoid normalization collisions. Validate nonempty IDs, titles and target strings; reject absolute/traversing target paths. Preserve the old `branch_for` helper only for legacy read/display code.

### R02 — Requirements and approved plans must reach every model call

Add `blacksmith/context.py` with one renderer used by plan, critic, implement, review and respond. Every call gets the exact PRD prose and full contract from the immutable snapshot. Implement/review additionally get that unit's approved plan and relevant dependency completion records. No silent character-tail truncation of requirements. Add `[limits].max_prd_bytes = 262144`; reject larger PRDs before spending and instruct the author to split them. Persist the full input and hashes in run artifacts.

Materialize a read-only planning snapshot from `base_commit`; set planner/critic `cwd` explicitly to that snapshot, and build the index over it. Plan one unit per checkpointed node invocation, with `plans_by_unit_id` keyed by ID. The existing single plan approval remains after all plans are generated. Record the approved plans' hash. Missing plan, wrong PRD hash, or changed plan after approval blocks implementation.

In fan-out, send the selected plan and immutable run context explicitly. Use the same renderer for initial attempts, continuations, fix retries and escalations. Keep original requirements, approved plan, gate feedback, review feedback and continuation instructions as separate labeled sections. Feedback truncation may bound logs, with a full-log artifact path; it must not remove the actual requirements or reviewer request.

### R03 — Enforce write boundaries and protect the verifier

Add `blacksmith/path_policy.py`; use it in the permission callback and post-operation audits. Resolve every candidate against the clone root, including relative paths; require the resolved path to remain under that root. Validate lexical and resolved paths for protected matches. Deny symlink ancestors for agent writes to prevent traversal through a link. Handle `file_path`, `path`, and `notebook_path` by tool-specific schemas; missing/malformed target is denied. Unknown tools are denied, not automatically allowed. Maintain an explicit allowlist of built-in read/write tools and the MCP tools actually wired for the role. Set SDK `setting_sources=[]` and `strict_mcp_config=True` explicitly after verifying support in the installed/pinned SDK.

Always protect `.git` and descendants, `.gitmodules`, `.mcp.json`, `.claude/settings*.json`, runtime config and `blacksmith.toml`, plus existing defaults. Keep behavioral untouchables in prompts. Contract path rules use a documented backward-compatible convention: an entry prefixed `path:` or `glob:` is enforced; a bare entry with no whitespace containing `/`, `.` or glob metacharacters is also a path rule. Remaining prose is guidance. Display the compiled hard rules in validation/plan approval. Reject malformed explicit rules. Do not infer an English sentence as a glob.

Apply policy to the actual changed paths, including both sides of renames and deletions, after implementation and after formatting, and before publication. A denied edit attempt halts the attempt with `policy_violation`; do not commit/publish unrelated changes while hiding the violation as a recovered warning. Commands run in the optional sandbox must undergo the same post-diff audit, because a shell inside a mounted clone bypasses Write/Edit guards. Protect `.git` from sandbox mutation by not exposing the host clone's metadata writable.

The authoritative gate uses the trusted preflight toolchain object, never a reload from the changed clone. This fixes direct gate replacement; it does not prove tests cannot be weakened. Tests may be legitimately edited, so enforce test integrity through actual diff review and acceptance-specific regressions, not a blanket ban on test changes.

### R04 — Gate the exact candidate commit and every required layer

Add typed `GateReceipt` records: `run_id`, unit/level/final scope, `candidate_commit`, `candidate_tree`, `toolchain_hash`, ordered layer list, commands, exit codes, timestamps, output artifact paths, and outcome `passed|failed|error|timeout|dirty`. Never reuse a receipt for a different commit/tree/config.

For a unit, run every distinct declared auto layer in declaration order. Identical resolved command tuples may be deduplicated while recording all covered layers. Fail on the first failing tuple; record remaining layers as not run. Fixers run before gating, followed by checked staging/commit and path-policy audit. A failing formatter may proceed only if it left no tracked/untracked source change; a failed stage/commit or a dirty result is an infrastructure error, never success. Never amend an unrelated prior commit when the implementer produced no commit.

Gate setup/test/lint must leave the tracked/index state and nonignored untracked source state clean. Ignored dependency/build output is allowed. If commands change source, return `dirty`; do not silently commit it after testing. Publish only a candidate whose recorded receipt matches HEAD and whose workspace is clean immediately before push.

After **every** multi-unit join, run an integration gate on the shared candidate before advancing. Run the union of all auto layers completed so far, not just the last sibling. Before PR approval, run the union of all auto layers on the final candidate, or reuse an identical complete receipt for that exact commit. These integration failures halt with artifacts and attribution; do not arbitrarily retry the last unit to repair a cross-unit defect.

A gate exception always writes a fresh nonpassing receipt and sets HALTED. Every outgoing edge checks terminal/error status before inspecting evidence. `done` requires all auto unit outcomes, the final passing receipt, review policy, PR approval and publication receipt. Deterministic commands prove what they actually ran; PR text must not assert that the natural-language test contract was automatically proven in its entirety.

### R05 — One unit execution state machine

Replace the monolithic `build_unit` loop with a compiled LangGraph unit subgraph shared by sequential and parallel scheduling. Reuse existing node functions initially; avoid rewriting executor behavior at the same time. Node boundaries: prepare attempt → implement → audit/commit → fix → gate → review → recovery decision → finalize unit. Subgraph state is per unit, persisted under run/unit namespaces; parent reduction is by unit ID, not append-only duplicate records.

Define `UnitAttemptState` with `unit_base_commit`, `attempt_base_commit`, `last_passing_commit`, attempt ID/number, model tier, fix count, continuation count, revision count, separate feedback fields, gate receipt and review receipt. Initialize through one factory. Never carry `test_results`, `review_clean`, findings, `escalated`, `pre_implement_ref`, error kind or partial-resume flags from another unit. Parent transitions may only select a unit and consume its typed outcome.

Use the same recovery policy in both shapes: initial cheap attempt; bounded partial continuations on turn cap; bounded same-model gate fixes; one stronger-model escalation; bounded review revisions on a passing candidate. Pass the **latest** gate output to escalation even when `max_fix_attempts=0`. A retry from a failing review revision returns to `last_passing_commit`; a new unit starts at the current combined tip. All Git failures stop recovery. Keep SBFL optional and best-effort, but forward it through the common dependency bundle and enforce its command timeout.

Persist intermediate attempt outcomes and cost events before starting another call. Use unique event IDs and idempotent keyed reduction so a replay cannot duplicate totals or findings. Do not introduce a second independent Python recovery loop.

### R06 — Durable artifacts and restart reconciliation

Add `blacksmith/recovery.py` and `blacksmith/receipts.py`. Use a separate per-run SQLite operation journal with WAL and explicit transactions; critical receipt writes fail closed. Observability sinks remain best-effort and cannot substitute for this journal. Operation IDs are deterministic `(run_id, unit_id, attempt_id, operation_kind)` keys.

Before external effects record intent; afterward record outcome. Git commit messages include a machine-readable operation-ID trailer. On restart reconcile actual Git HEAD/history against intent: an existing matching commit is reused, not recreated; unknown extra changes stop for inspection. Preserve uncommitted partial work after an ambiguous SDK interruption and stop with `interrupted_attempt_requires_resume`; do not automatically repeat the paid call. Explicit `resume` may continue that attempt under the existing continuation budget, with a new call receipt. No claim of exactly-once billing across a killed SDK process.

Checkpoint plan units and individual unit attempts separately. Reconcile a join one unit at a time using recorded source commit IDs, destination commit IDs and the saved level base. Require exactly one successful outcome per expected auto unit; missing/duplicate/foreign outcomes are errors. On merge conflict keep all workers and the conflict log; restore the shared tree to the saved level base using checked operations. Never discard sibling artifacts.

On halt/rejection/error preserve the full workspace, staged/unstaged binary patch, nonignored untracked files, refs bundle, run manifest, plans, command logs, model-call receipts and failure reason. Write the artifact manifest atomically. Print the absolute recovery path and supported next action. Resume of a policy/gate failure must be explicit; restore at the recorded failed stage under the original budgets, not as a fresh run. Plan/PR rejection requires a new approval interrupt on resume. Budget exhaustion requires an explicit `resume --add-budget-usd N`, recorded as an amendment; ordinary resume cannot reset spend. Do not automatically delete old runs; garbage collection is a later explicit command.

`resume` distinguishes pending interrupts from interrupted ordinary nodes: only call `Command(resume=...)` for a real interrupt; otherwise reconcile and invoke with `None`. Validate source identity, manifest hashes and clone existence before scheduling. Missing/corrupt recovery files produce a diagnostic without model spend. Read the persisted execution config; do not silently use changed local limits/models/target from a different invocation.

### R07 — Approval and publication reconciliation

Route terminal QA through final verification → `approve_pr` → publication with `draft=true`. The approval payload contains `run_id`, exact candidate SHA, draft intent, auto-unit/layer receipts, review status, and pending QA unit. Reject approval for a candidate changed after the prompt. All normal and draft pushes pass this same guard.

Before push, query existing remote branch and PR by exact repository/base/head identity. Use unique run branches. A branch pointing to the intended candidate and matching existing PR is a successful reconciled effect. A different remote commit is a conflict; never force-push. After successful push but failed PR creation, retain the branch and workspace and retry only PR reconciliation/creation. On ambiguous `gh` failure, query before retrying create. Put run ID and candidate SHA in a machine-readable PR-body marker. Reject multiple/mismatched matches instead of selecting arbitrarily.

`AWAITING_QA` remains distinct from `DONE`; report the pending QA check and draft URL. CLI exits: 0 for DONE, 2 for AWAITING_QA, 1 for halted/error; document and test all three.

### R08 — Run-wide spending, concurrency and time bounds

Add `[limits]` fields `max_parallel_units=2`, `max_call_cost_usd=5.0`, `model_timeout_s=1800`, `command_timeout_s=1800`, `max_run_seconds=21600`, `max_command_output_bytes=1048576`. Keep existing retry defaults. Set `max_run_cost_usd` default to 50.0; explicit omission now uses that default. Add an explicit CLI `--unlimited-cost` for intentional uncapped runs and record it; do not silently interpret unknown spend as free.

All paid calls, including plans, critics, reviews, summaries and respond, go through one admission service backed by the operation journal. Atomically reserve `min(max_call_cost_usd, remaining_unreserved_budget)` before starting. Refuse admission if nonpositive. Hand the reservation to SDK `max_budget_usd` (present in the installed SDK's `ClaudeAgentOptions`) and reconcile actual reported cost afterward. Concurrent reservations must sum within remaining budget. A call with missing cost consumes its full reservation conservatively and marks accounting incomplete. Persist `started`, `completed`, `interrupted`, and `unknown` call states with unique call IDs; never overwrite `unknown-session.jsonl`.

This is a spending admission bound plus the SDK's provider-side stopping behavior, **not** a mathematically exact invoice ceiling: the SDK documents stopping after a budget is exceeded. Report actual overrun if it occurs, admit no further calls, and preserve work. Do not invent token-price estimates to turn unknown usage into exact dollars. The budget check must apply before the first call, before each review-panel seat and before every new unit, not only on recovery.

Use bounded process execution with file-backed stdout/stderr and capped tails in state. On POSIX use a new process session, TERM the process group, allow 5 seconds, then KILL and reap on timeout/cancellation. Model-call timeout must close the SDK client and child processes. Apply this to setup/fix/test/lint/SBFL/Git/gh; use 120 seconds for Git/gh unless explicitly overridden by command kind. Persist full logs up to the configured cap, with a truncation marker and byte counts. Run duration excludes approval waiting and includes execution across resumes; cancellation preserves artifacts.

Derive LangGraph recursion allowance from unit count and bounded recovery counts, not a constant 150. Set a conservative bound `100 + 20 * N * (1 + max_fix_attempts + 1 + max_review_revisions) * (1 + max_implement_continuations)` for each invocation; the run deadline/budget and finite transitions are the real guards. Add a 100-unit offline chain test. Apply concurrency through LangGraph execution config and admission control; any overlapping target groups run serially without mutating the original dependency contract.

### R09 — Reviews must have evidence and honest outcomes

Add `ReviewReceipt` with `unit_id`, `base_commit`, `candidate_commit`, `status=clean|blocking|unavailable|disabled`, validated findings, panel results and cost references. Supply the actual cumulative patch `unit_base_commit..candidate_commit`, including deletions/renames, not just `--stat`; revisions are reviewed cumulatively. Include the immutable requirements and approved plan. For patches over 128 KiB, persist the full patch, put a manifest in the prompt and provide a read-only bounded `read_diff` tool; never silently treat a truncated patch as fully reviewed.

Strictly validate the JSON verdict and finding fields/severities. Missing terminal SDK result, stream error, malformed verdict or budget denial is `unavailable`, not `clean`. Any validated blocking finding from any enabled panelist blocks the candidate. Use that same policy in all scheduling paths. After revision limit, halt and preserve work. No automatic extra reviewer retry; the normal resume path may retry an unavailable review within budget. PR/UI text distinguishes disabled, unavailable, clean, and unresolved blocking, and does not count revision attempts as resolved findings.

### R10 — Sandbox ownership (conditional release gate)

Use a sandbox factory, not one shared manager in `UnitDeps`. Each active workspace owns its own container named from run ID + unit ID hash. Bind only that workspace; verify mount identity before advertising `run_command`. Start/setup failure makes the tool unavailable with an explicit prompt note, never a callable broken tool. Persist container ID, labels and workspace identity; on resume reconcile/recreate only the matching container. Cleanup may remove only containers with the run's ownership labels.

No host Docker socket or host credentials in the container. Exclude/protect Git metadata. A timed-out command must be terminated inside the container; if reliable per-command termination cannot be proven, stop that unit's container and mark self-verification unavailable. Killing only `docker exec` is insufficient. Use unique containers to keep this teardown from harming siblings. Authoritative host gates still run separately. Require a real-Docker smoke test before enabling this feature; mocked argv assertions alone do not close B11.

### R11 — Respond uses the same integrity and execution services

Keep `respond` as a command but route its work through the common run context, attempt subgraph, policy, gate receipts, budget and publication services. Build a synthetic revision work unit scoped to the PR's changed files plus the reviewer-requested targets, not `contract.work_units[0]`. Keep supplied PRD prose and all untouchables. If no PRD is supplied, use repository hard rules and explicitly report missing PRD context. Do not interpret a human-QA first unit as a request to skip revision.

Preserve original review comments as immutable task input across retries; append gate feedback separately. Cover the union of the PRD's auto layers (or default toolchain without a PRD). On success append one checked revision commit to the fetched head; verify the remote head has not advanced and use normal non-force push. A race/push failure preserves the candidate for recovery. Do not create another PR. Comment-fetch failures are errors, distinct from zero comments. Record which comment IDs were addressed so repeated invocation does not blindly reapply the same feedback.

### R12 — Test and operational evidence

Use actual Git repositories and the real gate/graph/checkpointer for integration tests. Mock only billed SDK transport and GitHub/Docker where explicitly marked. Protocol-accurate SDK fakes must reject unexpected options; include real `Executor.build_options` in tests. Add test-suite-wide prevention of live billing/network calls, even when a developer's `.env` exists. New fixture setup must isolate ambient Git config/identity/hooks and then explicitly enable the hook under test.

Every new configuration field needs parsing, production forwarding, point-of-use and behavior tests. Exercise sequential, parallel and resumed paths. Give task/unit/attempt IDs to events and use unique transcript paths from call IDs, even without provider session IDs. A failed optional memory lookup returns an empty lesson list with a diagnostic; a failed critical receipt write halts. Refresh README/runbook descriptions that currently overstate isolation, budget enforcement, review completeness or resume capabilities.

## 5. Implementation sequence

Execute these bounded slices in order. Each slice includes its own tests; the final matrix is not permission to defer coverage. Keep changes to shared `graph.py`/`state.py` serial to avoid collisions. Do not combine this work with dependency bumps or the pending assessor/evals PRDs.

| Slice | Owns | Depends on | Completion criterion |
|---|---|---|---|
| S01 | `path_policy.py`, implement guard | — | B02 callback and post-diff regressions fail closed |
| S02 | `context.py`, plan/implement/review prompts | — | Exact prose/plan sentinel tests pass in both shapes |
| S03 | `run_context.py`, config path resolution, preflight | S01–02 | Immutable base/input snapshot, validation and locks work |
| S04 | `receipts.py`, operation journal | S03 | Atomic intent/outcome writes, idempotent event keys |
| S05 | checked subprocess/Git service | S04 | Timeouts, process cleanup, bounded logs, checked failures |
| S06 | gate receipts and trusted toolchain | S01, S03–05 | Multi-layer, dirty-tree and fixer-failure regressions |
| S07 | typed unit state and reset factory | S04, S06 | No stale evidence or counters across unit transitions |
| S08 | shared compiled unit subgraph | S02, S07 | Identical bounded recovery policy sequential/parallel |
| S09 | checkpointed per-unit planning | S02–04 | Restart skips completed plans, approved hash enforced |
| S10 | join reconciliation and integration gate | S06–08 | Combined-invalid tree never reaches publication |
| S11 | artifact retention/recovery CLI | S04–10 | Failures preserve artifacts; restart drill passes |
| S12 | review receipts/patch tool/policy | S02, S06–08 | Real patch evidence, unavailable/blocking never clean |
| S13 | human-unit validation and approval routing | S03, S10, S12 | Every publication requires exact-SHA PR approval |
| S14 | publication reconciliation | S04, S11, S13 | Retry after ambiguous push/create creates no duplicate |
| S15 | budget admission and SDK cancellation | S04–05, S08–09 | All paid call classes bounded, unknown spend conservative |
| S16 | bounded scheduling and recursion sizing | S08, S10, S15 | 100-unit chain and wide DAG pass deterministic tests |
| S17 | per-workspace sandbox factory/lifecycle | S01, S05, S08, S11 | Isolated sentinel + real Docker timeout proof |
| S18 | respond integration | S08, S11–15 | Original feedback persists, exact gated revision pushed |
| S19 | observability honesty, docs, full matrix | S01–18 | No false clean/done claims; release evidence retained |

S17 may be deferred only if sandbox remains disabled and enabled configuration is rejected. S18 may be deferred only if `respond` is explicitly disabled with a clear unsupported-until-hardened error. All other slices are required before general heavy-PRD use. This is a remediation spec, not one oversized Blacksmith work unit.

## 6. Acceptance matrix and release decision

Create regression files grouped by invariant rather than one enormous test module. Tests must assert that prohibited downstream effects did not occur, not merely check an error string.

| Test | Required result |
|---|---|
| Prose-only requirement + approved-plan sentinel; index on/off; explicit other target | Planner, critic, implementer and reviewer receive correct context; all reads use snapshot cwd; no source checkout writes |
| `../`, relative symlink, absolute escape, notebook target, missing path, `.git/config`, PRD path rule, unknown tool | All denied; source sentinel unchanged; policy error prevents gate/publication |
| Parallel siblings individually green, combined red | Integration gate fails and no push/PR runner call occurs; both worker artifacts retained |
| Two auto layers: first green, second red | Unit fails; second layer executed; no publication |
| GateError after previous passing unit | New receipt is nonpassing; no stale success route |
| Sequential escalated/review-clean unit → parallel level → sequential unit | Final unit starts cheap, zero counters, no prior feedback, and receives its own review |
| Failing Git add/amend/reset/squash; fixer leaves edits; gate edits source | Infrastructure/dirty failure, artifacts retained; no successful receipt for dirty/uncommitted tree |
| Agent edits toolchain to `true` or sandbox changes protected path | Trusted toolchain unchanged; audit rejects candidate |
| Human terminal unit with `--approve plan` | No push; PR gate denied; recovery path retained |
| Human sibling/mid-DAG/mixed layer/human-only | Offline validation error before any model call |
| Review malformed/error/missing terminal result; one of three panelists blocking | Unavailable/blocking respectively; no clean label or ordinary PR; same in parallel |
| Reviewer sees deletion-only bug and revision retaining earlier defect | Full cumulative patch and base available; blocking survives until actually resolved |
| Kill after plan 2, commit, worker gate, first cherry-pick, push, PR create | New process reconciles state; completed work not repeated; no duplicated commits/PRs; ambiguous model call is explicit |
| Corrupt manifest, missing workspace, wrong repo/config, concurrent same-run resume | Stop before spend or mutation; retain original evidence |
| Retry same unit IDs in different runs, IDs with same slug | Distinct run/worker paths and remote branches |
| 100-unit chain; 20 roots with bound 2 | Completes without recursion failure; measured active workers never exceed 2 |
| Cost exhausted during plan/critic/implement/review/summary/respond, two concurrent admissions | No later paid call admitted; reservations correct; unknown usage consumes reservation; resume cannot reset totals |
| Hanging gate with child process and large output; model timeout | Deadline enforced, children terminated, bounded checkpoint/log size, artifacts recoverable |
| Sandbox sentinel per sibling; timeout and resume | Each command sees only its own workspace; no shared clone mutation; timed-out command cannot write later |
| Respond retry, first PRD unit human, remote advances, failed push | Correct synthetic unit; original request preserved; no force-push; candidate retained |
| Optional store/live/metrics failure; critical journal failure | Optional feature degrades visibly; critical failure halts before new effect |

Release evidence: full pytest and ruff pass with no xfails hiding these regressions; record dependency versions and revision; retain offline crash-injection logs and temporary-repository assertions. Then perform one deliberately small live canary against a disposable target with a fixed $10 budget, review enabled, two independent auto units and one terminal QA unit, approval initially limited to plan. Verify it stops without publication, resume with PR approval, and inspect the draft and exact gated SHA. This canary is a future release procedure, not authorization to spend or publish during this review. A separate authorized Docker smoke is required only to enable sandbox.

## 7. Cost and limits of the recommendation

Full prose/plan prompts and actual-patch reviews increase input tokens. Planning persistence avoids replaying completed plans. Integration gates add deterministic compute, generally one per joined level plus final verification; exact-commit receipt reuse avoids redundant final checks. Shared recovery restores escalation in parallel units, increasing possible spend relative to today's cheap-only workers, but the run-wide admission budget bounds new calls. Review failures now stop instead of silently publishing. Retention uses more disk; keep that cost visible rather than deleting failed paid work.

Do not use this effort to add dynamic decomposition, a recovery assessor, semantic memory, new providers, dashboard redesign, remote execution hosting, or automatic merging. Those do not repair the observed readiness failures. Heavy-PRD readiness is established only by the acceptance matrix and scoped live proof, not by the existing large mocked test count.
