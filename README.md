# blacksmith

Agentic development orchestrator — a [LangGraph](https://langchain-ai.github.io/langgraph/)
state machine that drives a single work unit through
**plan → implement → test-gate → review → PR**, with durable checkpointed state and
human approval gates. The [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview)
is the per-node execution engine; blacksmith operates *on* a target repository
(Kindling) via git worktrees, `cargo`, and `gh`.

The point of v0 is to bootstrap a working, inspectable understanding of LangGraph's
state-machine / checkpoint / human-in-the-loop model — Kindling is the dogfood;
LangGraph fluency is the deliverable. See [`blacksmith-v0-prd.md`](blacksmith-v0-prd.md)
for the full spec.

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
