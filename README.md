# blacksmith

[![CI](https://github.com/smith-and-web/blacksmith/actions/workflows/ci.yml/badge.svg)](https://github.com/smith-and-web/blacksmith/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Agentic development orchestrator — a [LangGraph](https://langchain-ai.github.io/langgraph/)
state machine that drives a single work unit through
**plan → implement → test-gate → review → PR**, with durable checkpointed state and
human approval gates. The [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview)
is the per-node execution engine; blacksmith operates *on* a target repository
(Kindling) via git worktrees, `cargo`, and `gh`.

The point of v0 is to bootstrap a working, inspectable understanding of LangGraph's
state-machine / checkpoint / human-in-the-loop model — Kindling is the dogfood;
LangGraph fluency is the deliverable. See [`blacksmith-v0-prd.md`](blacksmith-v0-prd.md)
for the full spec. Writing your own PRD? The [PRD authoring guide](docs/prd-authoring-guide.md)
documents the contract every PRD must conform to.

**Status:** v0 spine complete — all 11 work units built and test-gated (108 tests).
Two live dogfoods passed end to end (plan → implement → test-gate → PR): blacksmith
against its own repo, and against an external Node/TypeScript target. The live
agent-edit path is confirmed working — not aspirational.

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

- **`blacksmith.config.toml`** — blacksmith's own runtime config (model tiering,
  target repo, checkpointer, API auth). Parsed by [`blacksmith/config.py`](blacksmith/config.py).
  It's discovered by walking up from the current directory to the git root, so a
  globally-installed `blacksmith` works from any nested path inside the repo.
- A separate **`blacksmith.toml`** lives in each *target* repo and defines that repo's
  toolchain — `test_cmd`, optional `lint_cmd`, and optional `setup_cmd` (a one-off
  provisioning step like `npm ci`, run before the gate) — read by the test gate.

The dedicated Anthropic API key is read from the env var named in `[api].key_env_var`
(default `BLACKSMITH_ANTHROPIC_API_KEY`) — never from subscription auth, keeping
blacksmith's metered spend isolated.

## Running

```sh
# from inside the target repo, with blacksmith installed globally:
blacksmith <path/to/prd.md>                 # run one work unit from a PRD
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
`--thread-id` names the checkpointer thread for the run. Interactive runs pause for a
y/n at the plan and PR gates; `--auto-approve` approves both, and `--approve plan,pr`
approves only the gates you name (an unlisted gate is denied, halting the run there).

## Onboarding a new target repo

blacksmith operates *on* a target repo via git worktrees — nothing about blacksmith is
compiled into it, and the repo needs no prior setup beyond being a git clone.

> **blacksmith only sees committed state.** Each run cuts a fresh worktree from `HEAD`,
> so anything it should use must be **committed, not just staged** — including
> `blacksmith.toml` and `CLAUDE.md` (a staged-but-uncommitted config reads as "no
> `blacksmith.toml` found"). That worktree also starts with **no installed dependencies**
> (`node_modules`, virtualenvs, etc. are gitignored) — which is what `setup_cmd` is for.

To point blacksmith at a new project:

1. **Point blacksmith at the clone.** Either set `[target] repo_path` in
   `blacksmith.config.toml` to the local path, or omit `repo_path` and run blacksmith
   from inside the repo — it then targets the git root of your current directory
   (see [Running](#running)). Set `default_branch` if it isn't `main`.
2. **Add a `blacksmith.toml` to the target repo** so the test gate knows its toolchain.
   Because the gate runs in a fresh worktree with no installed deps, a Node/TS target
   needs `setup_cmd` to install them — `test_cmd = "npm test"` alone dies with
   `vitest: command not found`:
   ```toml
   # committed in the TARGET repo (e.g. a Node/TypeScript MCP server)
   setup_cmd = "npm ci"           # optional; one-off provisioning, runs before the gate
   test_cmd = "npm test"
   lint_cmd = "npm run lint"      # optional; runs only if tests pass
   ```
   Commands run through a shell, so chains work directly
   (`test_cmd = "npm ci && npm test"`) with no `sh -c` wrapper. cargo needs no
   `setup_cmd` — it fetches its own deps.
3. **Give it context — commit a `CLAUDE.md`.** For a repo with no claude.ai Project, a
   root-level `CLAUDE.md` is how its conventions reach the agent: blacksmith reads the
   worktree's `CLAUDE.md` and injects it into the implementer's system prompt as
   *project context*. For safety it does **not** load the repo's `.claude/settings.json`
   — permissions and hooks are never inherited from a target — and the PRD untouchables
   always override the repo's own guidance.
4. **Write a Contract v1 PRD** for the work — see
   [`docs/prd-authoring-guide.md`](docs/prd-authoring-guide.md). Set `primary_target_repo`,
   declare `layers`, list `untouchables`, and define `work_units`. v0 runs exactly one
   **root** unit, so make the unit you want built first `depends_on: []` (and an `auto`
   layer if you want the gate to decide it end to end).
5. **Have the `claude` CLI on `PATH`** — the Agent SDK spawns it for live runs.
6. **Run it** — `uv run blacksmith path/to/your-prd.md` — then approve at the plan and
   PR gates (or run headless with `--approve`; see [Running](#running)).

> **Worked example — fixing an MCP spec violation.** Given an Obsidian MCP server whose
> tool violates the MCP spec: add `blacksmith.toml` (`npm ci` setup + `npm test` /
> `npm run lint`), commit
> a `CLAUDE.md` capturing the server's conventions, and write a one-unit PRD whose work
> unit targets the offending tool's module with a `test_contract` that asserts the
> spec-conformant shape. blacksmith plans, implements in an isolated worktree, gates on
> `npm test`, and opens a PR for review — no claude.ai Project required.

## Build progress (WU-01…WU-11 — see PRD §6)

- [x] **WU-01** — project scaffold + config loader
- [x] **WU-02** — PRD contract schema + validator
- [x] **WU-03** — state schema + graph skeleton + checkpointer
- [x] **WU-04** — Claude Agent SDK executor wrapper *(mocked tests pass; live agent path confirmed via dogfood)*
- [x] **WU-05** — worktree manager
- [x] **WU-06** — toolchain-aware test gate
- [x] **WU-07** — HITL interrupt nodes (plan + PR)
- [x] **WU-08** — PR node
- [x] **WU-09** — plan node
- [x] **WU-10** — implement node *(guard + diff/commit auto-tested; live agent-edit confirmed via dogfood)*
- [x] **WU-11** — end-to-end wiring (happy path + human-halt-on-fail) + CLI
