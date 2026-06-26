# blacksmith

[![CI](https://github.com/smith-and-web/blacksmith/actions/workflows/ci.yml/badge.svg)](https://github.com/smith-and-web/blacksmith/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Agentic development orchestrator — a [LangGraph](https://langchain-ai.github.io/langgraph/)
state machine that drives a PRD's work units, in dependency order, each through
**plan → implement → test-gate → review → PR**, with durable checkpointed state and
human approval gates. The [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview)
is the per-node execution engine; blacksmith operates *on* a target repository via
isolated git clones, the repo's own test/lint toolchain, and `gh`.

The deliverable is LangGraph fluency — a working, inspectable grasp of its
state-machine / checkpoint / human-in-the-loop model. blacksmith now drives most of
its own development: it builds its features from Contract v1 PRDs, on itself. See
[`blacksmith-v0-prd.md`](blacksmith-v0-prd.md) for the original spec. Writing your own
PRD? The [PRD authoring guide](docs/prd-authoring-guide.md) documents the contract
every PRD must conform to.

**Status:** well past the v0 spine — blacksmith builds its own features end to end, and
nearly everything below was shipped that way (write a PRD → blacksmith builds it → review
the PR). Current on `main`: **multi-unit execution** of a PRD's whole `depends_on` DAG
(topological order, independent units in parallel within a level, one combined PR);
**clone-based isolation** (each run/unit works in a throwaway `git clone`, never the real
checkout); a **human-QA path** (a `human`-gated unit opens a *draft* PR and ends
`AWAITING_QA`, branch preserved); **tiered models** (implement on Sonnet first, escalate to
Opus only on a gate failure); **long-term memory** (per-repo gate-failure lessons fed back
to the planner via a SQLite Store); a local **observability suite** — per-run/per-unit
metrics recording, a `blacksmith runs` history command, a built-in `blacksmith dashboard`,
and per-call agent **transcripts**; a **rendered interactive CLI** (Markdown plans, diffs,
live progress) that degrades to plain output when piped; **fresh-run state isolation**;
per-run token/cache instrumentation; and an org **cost reporter**. 300+ tests, CI-green.
Proven against its own repo and an external Node/TypeScript target.

## Stack

Python 3.12 · LangGraph · Claude Agent SDK · SQLite checkpointer · managed with [`uv`](https://docs.astral.sh/uv/)

## Setup

```sh
uv sync                  # provisions Python 3.12 + installs deps
cp .env.example .env     # add your dedicated BLACKSMITH_ANTHROPIC_API_KEY
uv run pytest            # run the test suite
uv run ruff check        # lint
```

### Global install

Install `blacksmith` as a user-wide tool so it's on your `PATH` and runnable from
inside any repo:

```sh
uv tool install .                                          # from a local clone
uv tool install git+https://github.com/smith-and-web/blacksmith   # from the git URL
```

Once installed, `cd` into the target repo and run `blacksmith <prd>` directly (no
`uv run` prefix). blacksmith discovers its `blacksmith.config.toml` by walking up from
the current directory to the git root, and — when `[target].repo_path` is omitted —
operates on **that** repo (the git root of where you're standing). See
[Running](#running).

## Configuration

- **`blacksmith.config.toml`** — blacksmith's own runtime config: model tiering, target
  repo, checkpointer, the long-term memory `[store]`, the run-metrics `[metrics]` sink,
  agent `[transcripts]`, and API auth. Parsed by [`blacksmith/config.py`](blacksmith/config.py).
  It's discovered by walking up from the current directory to the git root, so a
  globally-installed `blacksmith` works from any nested path inside the repo.
- A separate **`blacksmith.toml`** lives in each *target* repo and defines that repo's
  toolchain — `test_cmd`, optional `lint_cmd`, optional `setup_cmd` (a one-off
  provisioning step like `npm ci`, run before the gate), and optional `fix_cmd` (a
  deterministic formatter/auto-fixer like `cargo fmt --all`, run before the gate and folded
  into the unit's commit) — read by the test gate.

The dedicated Anthropic API key is read from the env var named in `[api].key_env_var`
(default `BLACKSMITH_ANTHROPIC_API_KEY`) — never from subscription auth, keeping
blacksmith's metered spend isolated.

## Running

```sh
# from inside the target repo, with blacksmith installed globally:
blacksmith <path/to/prd.md>                 # run a PRD's work units (the whole DAG)
blacksmith <prd> --approve plan,pr          # non-interactive (CI / headless)

# from a clone of blacksmith itself, without a global install:
uv run blacksmith <path/to/prd.md>
```

**Run-inside-the-repo flow.** When `[target].repo_path` is omitted from
`blacksmith.config.toml` (or the `[target]` section is dropped entirely), blacksmith
operates on the git root of the directory you run it from. So you can `cd` into any
repo, commit a `blacksmith.config.toml` (no `repo_path` needed) plus a `blacksmith.toml`,
and run `blacksmith <prd>` from anywhere inside it. Setting an explicit absolute
`[target].repo_path` still works unchanged and takes precedence.

The PRD path is the single positional argument. `--config` points at a non-default
`blacksmith.config.toml` (otherwise it's discovered by walking up to the git root);
`--thread-id` names the checkpointer thread; a fresh run resets a terminally-finished
thread and refuses a paused one, so a reused id won't resurrect prior state — a fresh id
per run is still the simplest habit.
Interactive runs pause for a y/n at the plan and PR gates; `--auto-approve` approves
both, and `--approve plan,pr` approves only the gates you name (an unlisted gate is
denied, halting the run there). `--quiet` silences the per-node progress stream.

Other entry points:

```sh
blacksmith validate <prd>            # offline contract check — field-level errors, zero model spend
blacksmith resume --thread-id <id>   # continue an interrupted run from its SQLite checkpoint
blacksmith runs [<thread-id>]        # recorded run history; pass a thread-id to drill into one run
blacksmith dashboard                 # local read-only web dashboard over recorded run metrics (localhost)
blacksmith --issue <N>               # scaffold a PRD skeleton from GitHub issue #N (PR links Closes #N)
blacksmith costs                     # org usage + cost from the Admin API (read-only; needs an admin key)
```

## Onboarding a new target repo

blacksmith operates *on* a target repo via git clones — nothing about blacksmith is
compiled into it, and the repo needs no prior setup beyond being a git clone.

> **blacksmith only sees committed state.** Each run cuts a fresh clone from `HEAD`,
> so anything it should use must be **committed, not just staged** — including
> `blacksmith.toml` and `CLAUDE.md` (a staged-but-uncommitted config reads as "no
> `blacksmith.toml` found"). That clone also starts with **no installed dependencies**
> (`node_modules`, virtualenvs, etc. are gitignored) — which is what `setup_cmd` is for.

To point blacksmith at a new project:

1. **Point blacksmith at the clone.** Either set `[target] repo_path` in
   `blacksmith.config.toml` to the local path, or omit `repo_path` and run blacksmith
   from inside the repo — it then targets the git root of your current directory
   (see [Running](#running)). Set `default_branch` if it isn't `main`.
2. **Add a `blacksmith.toml` to the target repo** so the test gate knows its toolchain.
   Because the gate runs in a fresh clone with no installed deps, a Node/TS target
   needs `setup_cmd` to install them — `test_cmd = "npm test"` alone dies with
   `vitest: command not found`:
   ```toml
   # committed in the TARGET repo (e.g. a Node/TypeScript MCP server)
   setup_cmd = "npm ci"           # optional; one-off provisioning, runs before the gate
   test_cmd = "npm test"
   lint_cmd = "npm run lint"      # optional; runs only if tests pass
   fix_cmd = "npm run format"     # optional; deterministic auto-fix, runs before the gate
   ```
   Commands run through a shell, so chains work directly
   (`test_cmd = "npm ci && npm test"`) with no `sh -c` wrapper. cargo needs no
   `setup_cmd` — it fetches its own deps.

   **`fix_cmd` auto-fixes mechanical failures so they never burn a model retry.** A
   formatting check in `lint_cmd` (`cargo fmt --all -- --check`, `prettier --check`) is
   100% auto-fixable, but the agent has no shell and can't reproduce a formatter by hand —
   so a single whitespace diff used to escalate Sonnet→Opus and halt. With `fix_cmd` set,
   blacksmith runs it in the worktree right after the agent commits and *before* the gate,
   then `git commit --amend`s the result into the unit's commit — so the committed (and
   cherry-picked) code is CI-clean with zero model spend. It's best-effort and self-contained:
   chain in any deps it needs (`fix_cmd = "npm ci && npm run format"`), and a failing `fix_cmd`
   just falls through to the gate (which then escalates/halts as before). For Kindling:
   ```toml
   fix_cmd = "cargo fmt --all"    # cargo needs no setup_cmd; restore the fmt --check to lint_cmd
   ```
3. **Give it context — commit a `CLAUDE.md`.** For a repo with no claude.ai Project, a
   root-level `CLAUDE.md` is how its conventions reach the agent: blacksmith reads the
   clone's `CLAUDE.md` and injects it into the implementer's system prompt as
   *project context*. For safety it does **not** load the repo's `.claude/settings.json`
   — permissions and hooks are never inherited from a target — and the PRD untouchables
   always override the repo's own guidance.
4. **Write a Contract v1 PRD** for the work — see
   [`docs/prd-authoring-guide.md`](docs/prd-authoring-guide.md). Set `primary_target_repo`,
   declare `layers`, list `untouchables`, and define `work_units`. blacksmith runs the
   **whole** `work_units` DAG in dependency order on one shared branch and opens a single
   combined PR — use `depends_on` to order them (independent units at the same level run
   in parallel). Use an `auto` layer where the test gate should decide pass/fail end to
   end; a `human` layer instead opens a draft PR for manual QA.
5. **Have the `claude` CLI on `PATH`** — the Agent SDK spawns it for live runs.
6. **Run it** — `uv run blacksmith path/to/your-prd.md` — then approve at the plan and
   PR gates (or run headless with `--approve`; see [Running](#running)).

> **Worked example — fixing an MCP spec violation.** Given an Obsidian MCP server whose
> tool violates the MCP spec: add `blacksmith.toml` (`npm ci` setup + `npm test` /
> `npm run lint`), commit
> a `CLAUDE.md` capturing the server's conventions, and write a one-unit PRD whose work
> unit targets the offending tool's module with a `test_contract` that asserts the
> spec-conformant shape. blacksmith plans, implements in an isolated clone, gates on
> `npm test`, and opens a PR for review — no claude.ai Project required.

## Build progress (WU-01…WU-11 — see PRD §6)

- [x] **WU-01** — project scaffold + config loader
- [x] **WU-02** — PRD contract schema + validator
- [x] **WU-03** — state schema + graph skeleton + checkpointer
- [x] **WU-04** — Claude Agent SDK executor wrapper *(mocked tests pass; live agent path confirmed via dogfood)*
- [x] **WU-05** — clone manager
- [x] **WU-06** — toolchain-aware test gate
- [x] **WU-07** — HITL interrupt nodes (plan + PR)
- [x] **WU-08** — PR node
- [x] **WU-09** — plan node
- [x] **WU-10** — implement node *(guard + diff/commit auto-tested; live agent-edit confirmed via dogfood)*
- [x] **WU-11** — end-to-end wiring (happy path + human-halt-on-fail) + CLI

### Beyond the v0 spine — built by blacksmith on itself

- **Multi-unit DAG execution** — topological ordering + parallel fan-out within a dependency level, accumulating onto one combined PR.
- **Clone-based isolation** — a throwaway `git clone` per run/unit; the real checkout is never touched.
- **Human-QA path** — a `human`-gated unit opens a *draft* PR and ends `AWAITING_QA`, branch preserved for manual review.
- **Tiered models** — implement on Sonnet first, escalate to Opus only on a gate failure.
- **More CLIs** — `validate` (offline contract check), `resume` (continue from a checkpoint), `runs` (recorded run history + per-run drill-down), `dashboard` (local metrics UI), `--issue N` (scaffold from a GitHub issue), `costs` (org Admin-API usage/cost), and global `uv tool install`.
- **Long-term memory** — per-repo gate-failure lessons persisted in a SQLite Store and fed back into the planner's context on later runs.
- **Observability** — per-run/per-unit metrics recorded to a local SQLite, a `blacksmith runs` history command, a built-in localhost `blacksmith dashboard`, and per-call agent transcripts linked from each run.
- **Interactive CLI** — rendered Markdown plans, diffs, and test output with a live progress indicator at the gates, degrading to plain, parseable output when piped or under `--quiet`.
- **Fresh-run state isolation** — a fresh run resets a terminally-finished thread and refuses a paused one, so a reused `--thread-id` can't resurrect a prior run's errors.
- **Cost visibility** — end-of-run cost total (summed across all units + escalations) plus per-run token + cache-hit instrumentation.
- **Robustness** — repo-consistency preflight, reason-accurate guard-block reporting, graceful executor failures, and a forward-migrating metrics store.
