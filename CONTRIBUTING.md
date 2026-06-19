# Contributing

Thanks for your interest in blacksmith.

## Development setup

```sh
uv sync                  # provisions Python 3.12 + installs deps (incl. dev tools)
uv run pytest            # run the test suite
uv run ruff check        # lint
```

The test suite is fully offline — it mocks the Claude Agent SDK, so it needs no API
key and no network. Live runs (and `scripts/smoke.py`) additionally require the
Claude Code `claude` CLI on your PATH and a dedicated Anthropic API key in `.env`
(see `.env.example`).

## Workflow

- Branch off `main`, open a pull request. CI (`ruff check` + `pytest`) must pass.
- Keep changes minimal and focused; match the surrounding style.
- Add or update tests for any behavior change.

## Untouchables

`blacksmith/contract.py` is the PRD contract interface that every PRD depends on —
changes there warrant extra review. See `blacksmith-v0-prd.md` §7 for the full list
of untouchable files/behaviors (notably: never make a target product depend on
AI/cloud/subscription services).
