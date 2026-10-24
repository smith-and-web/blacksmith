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
authoritative generated tests execute on the host. Credential stripping, bounded
commands and exact gate receipts are subsequent remediation slices. General use on
untrusted repositories is not established by these offline tests.

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
  execution.lock
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
interrupted node is driven with `None`; side-effect reconciliation for those nodes
is still required by S04-S11. S03 alone does not establish replay-safe paid calls or
recovery of terminal policy/gate failures.

Pre-migration checkpoints remain readable through the checkpointer and existing
history tools. Execution-resume refuses them with
`legacy_checkpoint_requires_manual_recovery`, without deleting or rewriting their
records. Fresh runs also refuse an existing thread ID; select a new `--thread-id`.
If an older nested invocation stored databases elsewhere, explicitly select those
paths for inspection rather than assuming they moved with the new path semantics.

No live canary, real push/PR creation or Docker recovery smoke is part of this
implementation proof. Those gates require separate authorization.
