# CLAUDE.md

Guidance for Claude Code working in this repository.

## What blacksmith is

A LangGraph state machine that drives a Contract-v1 PRD's work units, in
dependency order, each through **plan → implement → test-gate → review → PR**,
with durable checkpointed state and human approval gates. The Claude Agent SDK is
the per-node execution engine. blacksmith operates *on* a target repository via
throwaway git clones, that repo's own test/lint toolchain, and `gh`.

It builds most of its own features: write a Contract v1 PRD → blacksmith
implements it → review the PR.

Python 3.12 · LangGraph · Claude Agent SDK · SQLite checkpointer · `uv`.

## Commands

```sh
uv sync                 # provision + install
uv run pytest           # full suite (300+ tests)
uv run ruff check       # lint
blacksmith <prd>        # run against a PRD (installed as a uv tool)
blacksmith runs         # run history
blacksmith dashboard    # local observability UI
```

Run `uv run pytest` and `uv run ruff check` before claiming work is done. Never
add a dependency without adding it to `pyproject.toml`.

## Architecture

- `blacksmith/graph.py` — the LangGraph topology. Nodes: `ingest_prd`,
  `next_unit`, `approve_plan`, `implement`, `continue_implement`, `test_gate`,
  `auto_fix`, `fix_retry`, `escalate`, `review`, `finalize_review`,
  `prepare_review_revision`, `open_pr`, `open_draft_pr`, `approve_pr`,
  `join_level`, `human_halt`. This file is the map — read it first.
- `blacksmith/nodes/` — node bodies. `plan.py`, `implement.py`, `review.py`,
  `pr.py`, and `hitl.py` (the `interrupt()`-based approval gates).
- `blacksmith/executor.py` — Claude Agent SDK invocation, tool grants, options.
- `blacksmith/contract.py` — PRD schema and validator. `extra="forbid"`.
- `blacksmith/config.py` — `blacksmith.config.toml` parsing.
- `blacksmith/index.py` — repo map and `search_code` tool.
- `blacksmith/state.py` — the `BlacksmithState` TypedDict threaded through nodes.
- `blacksmith/metrics.py`, `dashboard.py` — observability.

Config sections: `[target]`, `[models]`, `[limits]`, `[checkpointer]`, `[api]`,
`[index]`, `[critic]`. Models are tiered — implement starts on Sonnet and
escalates to Opus only on a gate failure.

## Failure classes this codebase has actually exhibited

Prefer these over generic review concerns. They are why the `wired-check` and
`test-honesty` agents exist.

**Wired-but-dark.** A feature is configured, implemented, tested and merged, but
no live path reaches it. The chain that must hold, end to end:

```
blacksmith.config.toml key
  → config.py parses it into a typed object
    → build_graph_for / compile_graph reads it
      → the node is actually injected into the graph
        → the node body branches on it
          → executor.build_options grants any tool it needs
            → the prompt actually steers the model to use that tool
```

A granted tool that no prompt mentions is dark. A parsed config key that no
node reads is dark. Trace the whole chain; do not infer from names.

**Tests that pass by not reproducing production.** Most often a fake that is more
permissive than the real object — the recurring instance is a fake swallowing
`**kwargs`, which hides a renamed or dropped argument the real callee would
reject. Also: passing on SQLite where production is Postgres.

**Config drift.** `blacksmith.config.toml`, `config.py`'s schema, and
`.env.example` disagreeing about a key's name, type or default.

## Conventions

- Contract v1 PRDs live in `prds/`. The authoring contract is
  `docs/prd-authoring-guide.md` — frontmatter is machine-validated with
  `extra="forbid()"`, so never invent a field.
- Size each work unit to one implement turn budget (guide §7.1). Oversized units
  are the main cost driver: one took ~5 attempts and ~$32.
- `docs/code-review-prompt.md` is the whole-repo audit prompt. `/code-review`
  reviews the current diff and is the wrong tool for a standing audit.
- Gates never auto-proceed on a "no". PRs are never auto-merged.
- The API key comes from `BLACKSMITH_ANTHROPIC_API_KEY`, deliberately separate
  from any other Anthropic credential. Never read or echo `.env`.

## When working here

- This repo runs autonomous agents that spend money. Before changing anything in
  `executor.py`, `graph.py` or `[limits]`, say what it does to cost per run.
- Adding a node means touching the topology, the state, and the tests that assert
  the topology. Check all three.
- A new config key is not done until it is parsed, read at the point of use,
  reachable by default-or-documented settings, and covered by a test that would
  fail if the wiring were removed.
