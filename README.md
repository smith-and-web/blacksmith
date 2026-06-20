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

**Status:** v0 spine complete — all 11 work units built and test-gated (90 tests),
and the first live dogfood passed end to end (plan → implement → test-gate →
PR-approval). Public so early visitors see a working spine (§11.4).

## Stack

Python 3.12 · LangGraph · Claude Agent SDK · SQLite checkpointer · managed with [`uv`](https://docs.astral.sh/uv/)

## Setup

```sh
uv sync                  # provisions Python 3.12 + installs deps
cp .env.example .env     # add your dedicated BLACKSMITH_ANTHROPIC_API_KEY
uv run pytest            # run the test suite
uv run ruff check        # lint
```

## Configuration

- **`blacksmith.config.toml`** — blacksmith's own runtime config (model tiering,
  target repo, checkpointer, API auth). Parsed by [`blacksmith/config.py`](blacksmith/config.py).
- A separate **`blacksmith.toml`** lives in each *target* repo and defines that repo's
  toolchain (`test_cmd` / `lint_cmd`), read by the test gate (WU-06).

The dedicated Anthropic API key is read from the env var named in `[api].key_env_var`
(default `BLACKSMITH_ANTHROPIC_API_KEY`) — never from subscription auth, keeping
blacksmith's metered spend isolated (PRD §8).

## Onboarding a new target repo

blacksmith operates *on* a target repo via git worktrees — nothing about blacksmith is
compiled into it, and the repo needs no prior setup beyond being a git clone. To point
blacksmith at a new project:

1. **Point blacksmith at the clone.** In `blacksmith.config.toml`, set
   `[target] repo_path` to the local path and `default_branch`.
2. **Add a `blacksmith.toml` to the target repo** so the test gate knows its toolchain.
   Commands must work from a fresh worktree checkout:
   ```toml
   # committed in the TARGET repo (e.g. a Node/TypeScript MCP server)
   test_cmd = "npm test"
   lint_cmd = "npm run lint"      # optional; runs only if tests pass
   ```
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
6. **Run**, then approve at the plan and PR gates.

> **Worked example — fixing an MCP spec violation.** Given an Obsidian MCP server whose
> tool violates the MCP spec: add `blacksmith.toml` (`npm test` / `npm run lint`), commit
> a `CLAUDE.md` capturing the server's conventions, and write a one-unit PRD whose work
> unit targets the offending tool's module with a `test_contract` that asserts the
> spec-conformant shape. blacksmith plans, implements in an isolated worktree, gates on
> `npm test`, and opens a PR for review — no claude.ai Project required.

## Build progress (WU-01…WU-11 — see PRD §6)

- [x] **WU-01** — project scaffold + config loader
- [x] **WU-02** — PRD contract schema + validator
- [x] **WU-03** — state schema + graph skeleton + checkpointer
- [x] **WU-04** — Claude Agent SDK executor wrapper *(mocked tests pass; manual live smoke `scripts/smoke.py` pending an env with the `claude` CLI)*
- [x] **WU-05** — worktree manager
- [x] **WU-06** — toolchain-aware test gate
- [x] **WU-07** — HITL interrupt nodes (plan + PR)
- [x] **WU-08** — PR node
- [x] **WU-09** — plan node
- [x] **WU-10** — implement node *(guard + diff/commit auto-tested; live agent-edit smoke pending an env with the `claude` CLI)*
- [x] **WU-11** — end-to-end wiring (happy path + human-halt-on-fail) + CLI
