# Version-2 run inputs and migration

Heavy-PRD remediation is still in progress. This documents S03, not a readiness
release. The [implementation checklist](reviews/heavy-prd-progress.md) and
[acceptance record](reviews/heavy-prd-evidence.md) identify the remaining work and
unrun live gates.

## Preflight and selected source

Before constructing the model executor, the CLI validates the execution contract,
required local host tools (`git`, `gh`, `claude`), Git identity, source commit,
committed `blacksmith.toml`, required auto-layer command strings and writable run
storage. A dirty tracked checkout is refused: commit or stash it first. Ignored
local PRDs and unrelated untracked files are allowed. Offline `blacksmith validate`
does not invoke Git, the SDK, or the network.

Repository identity includes the remote host and full owner/repository path.
Equivalent SSH and HTTPS forms match; a bare `owner/repo` PRD target denotes GitHub.
Other hosts can be expressed as a full remote URL. Credential-bearing origin URLs
are refused; use Git's credential helper instead.

The CLI prints the exact committed HEAD selected for the run. It persists the full
original PRD bytes, including CRLF line endings, normalized execution configuration,
trusted toolchain text, their SHA-256 hashes and the source identity. Only credential
environment-variable names belong in the configuration snapshot; their values are
not read into it.

A read-only local planning clone has its own Git metadata and no hard links to the
source. Planner and critic reads and index tools use this clone. The implementation
workspace is cut from that same commit after approval, even if the original branch
advances, its PRD is removed, or the runtime config changes during approval. Each
parallel worker gets its own run-owned clone. The original checkout is not modified.

Clones isolate Git state and guarded file writes. They are not OS sandboxes;
authoritative generated tests execute on the host. Native commands now have bounded
execution and toolchain credential filtering, described below. Exact gate receipts
remain a subsequent remediation slice. General use on untrusted repositories is not
established by these offline tests.

## Paths and ownership

`target.repo_path`, the checkpointer/store/metrics/live database paths, and the
transcript directory resolve relative to the runtime config file's directory.
Absolute paths retain their meaning and are stored canonically. Without an explicit
target, the repository containing the config directory is selected. A nested caller's
working directory does not redirect storage or the target.

For example, a config at `/project/ops/blacksmith.config.toml` with
`repo_path = ".."` selects `/project`; the default checkpoint database is under
`/project/ops/.blacksmith/`, including when invoked from `/project/src/`.

Each fresh run has a UUID and these paths under the config directory:

```text
.blacksmith/runs/<run-id>/
  manifest.json
  input.prd.md
  runtime-config.json
  trusted-blacksmith.toml
  operations.sqlite
  execution.lock
  commands/<operation-id>/{intent.json,stdout.log,stderr.log,outcome.json}
  planning/
  workspace/
  units/<sha256-of-raw-unit-id>/workspace/
```

Branches use `blacksmith/<run-id>/<unit-slug>-<unit-id-hash>`. IDs with the same
normalized slug remain distinct, and repeated unit IDs in different runs do not
collide. The old `branch_for` naming is for legacy callers and display only.

POSIX `flock` locks protect the full CLI drive/resume, including approval waits.
A thread lock prevents competing fresh invocations; a run lock also covers resumes
using another config file. A second owner exits before graph construction. Lock
files may remain on disk after exit; the kernel lock, not file existence, determines
ownership.

## Critical operation receipts

Each version-2 run has a separate SQLite operation journal in WAL mode with
`synchronous=FULL`. It is not the LangGraph checkpointer, memory store, metrics DB
or live event sink. Missing/corrupt/mismatched journals stop execution before an
executor is constructed on resume; a missing journal is never recreated as a new
empty run. Receipt writes fail closed.

Operation IDs hash the tuple `(run_id, unit_id, attempt_id, operation_kind)`.
Intent commits before the effect; its outcome and cost events commit atomically
afterward. Repeating an identical receipt/event is idempotent. Conflicting intent,
outcome or event content is an integrity error, preserving the original receipt.

Planning snapshot creation, version-2 SDK calls and implementation commits use
these receipts. SDK roles include planner, critic, initial/escalated implementer,
review-panel seats and PR summary. A completed SDK response can be reused after a
checkpoint interruption without calling the provider again. Missing terminal
results, lost streams and cancellation remain explicit uncertain outcomes;
automatic replay refuses them with `interrupted_attempt_requires_resume`.
Known streamed turns and reported terminal usage survive stream errors.

Implementation commits carry `Blacksmith-Operation-Id: <id>` trailers. Completed
commit reuse requires the recorded HEAD, a matching trailer and a clean workspace;
unknown extra changes stop for inspection. An interrupted commit intent still
requires stage-specific reconciliation in S11. Native commands now record individual
intent/outcome receipts. Semantic fixer/squash/join/publication reconciliation and full
artifact recovery remain later slices; a command receipt alone does not make an
interrupted multi-command stage safe to repeat.

Cost events use call IDs and keyed reduction so a replayed update cannot add the
same call twice. Missing costs remain unknown, not measured zero-dollar calls.
Run-wide reservation admission and conservative budget charging are S15 work;
the journal alone is not a spending limit or an exactly-once billing guarantee.
`completed` means the recorded effect completed, not that a review approved a
candidate or all acceptance requirements passed.

SDK native tool sets are explicit: planner/critic/reviewer get read-only tools;
the implementer gets guarded file tools. Summaries have no native or MCP tools.
Installed SDK option/CLI-argument construction was checked locally; no live SDK
permission canary has run.

## Bounded native commands

`[limits].command_timeout_s` defaults to 1800 seconds for setup, fix, test, lint and
optional SBFL commands. Git and gh have a separate 120-second default; internal
callers can supply a command-specific deadline. `[limits].max_command_output_bytes`
defaults to 1048576 bytes, shared by each command's stdout and stderr log files.
The immutable saved settings apply to sequential workers, parallel workers and
resume. These limits add no model calls and do not replace run-wide admission.

Each command starts in a new POSIX session with closed stdin. Timeout or cancellation
sends TERM to its process group, allows up to five seconds for termination, then
sends KILL and reaps the direct child. Background descendants in the same group are
terminated even if the shell exits first. This is process-group ownership, not a
security boundary against hostile programs which deliberately escape their group.
Docker exec requires separate in-container ownership/termination evidence in S17.

Output is drained incrementally into capped files. Logs retain emitted byte counts
and explicit truncation metadata/markers; graph feedback includes bounded tails and
the retained command-log directory. Git outputs used for identity, paths or other decisions must
be complete, so truncation fails closed. Sensitive Git config/remote/identity output
is available only to its caller; persisted argv/output are redacted. Pre-context
preflight reads use bounded temporary log directories; once run identity exists,
logs and journal receipts are inside its run directory.

Toolchain children receive a copied environment with known model/admin/GitHub
credentials and the configured API credential variables removed. Authorized Git/gh
publication commands retain their normal credential access. Host files, credential
helpers and the network are not isolated: trusted host gates are not OS sandboxes.

Command IDs use transactionally allocated ordinals inside the run/unit/attempt/stage
identity. Observations always run again when requested; a previous HEAD/status result
is never reused as current truth. Critical intent persistence precedes spawning, and
outcome persistence follows process cleanup. An uncertain outcome stays available
for its owning stage to reconcile. Native commands are never automatically retried
by this service. Failed add, amend, reset, clean, squash and other required Git
operations stop execution rather than silently accepting incomplete work.

## Execution validation and archived PRDs

IDs, titles and module targets must be nonempty. Targets must be relative and cannot
traverse to a parent or name an absolute/Windows drive path. Hard path untouchables
follow the [authoring guide](prd-authoring-guide.md); a custom runtime config inside
the target is protected and included in the plan approval's hard rules.

An executable PRD may contain zero or one human QA unit. That unit must use only
human layers, be the sole terminal unit and transitively depend on every auto unit.
Human-only PRDs, human siblings, mixed auto/human units and mid-DAG human units are
invalid. Structural Contract-v1 parsing remains available for archived documents;
the historical v0 PRD is readable but does not satisfy today's execution semantics.

## Resume and checkpoint migration

New runs carry `state_version = 2`. Resume validates the recorded run context,
manifest and input hashes, selected source identity, planning clone and any recorded
workspace before constructing an executor. Missing/corrupt files stop with a recovery
diagnostic and no model call. Saved model, limit, toolchain and target settings are
authoritative; changing the local config does not silently replace them on resume.

Only real pending approval interrupts receive `Command(resume=...)`. An ordinary
interrupted node is driven with `None`. SDK response reuse and uncertain-call
refusal are in place; full side-effect reconciliation and explicit terminal-stage
recovery are still required by S06-S11. These slices do not establish exactly-once
billing across a killed provider process or recovery of terminal policy/gate failures.

Pre-migration checkpoints remain readable through the checkpointer and existing
history tools. Execution-resume refuses them with
`legacy_checkpoint_requires_manual_recovery`, without deleting or rewriting their
records. Fresh runs also refuse an existing thread ID; select a new `--thread-id`.
If an older nested invocation stored databases elsewhere, explicitly select those
paths for inspection rather than assuming they moved with the new path semantics.

No live canary, real push/PR creation or Docker recovery smoke is part of this
implementation proof. Those gates require separate authorization.
