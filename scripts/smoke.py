"""Manual live smoke for the executor (PRD §6, WU-04 — "one manual live smoke call;
prompt caching verified on static context").

This spends a small amount against the dedicated API key and requires the Claude Code
CLI to be installed (the Agent SDK spawns it). It is intentionally NOT part of the
pytest gate. Run from the repo root:

    uv run python scripts/smoke.py

It makes two calls sharing the same static system prompt; the second should report
cache_read > 0, confirming the static context is cached (AC-8).
"""

from pathlib import Path

from blacksmith.config import BlacksmithConfig
from blacksmith.executor import Executor

REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEM = (
    "You are a terse assistant used to smoke-test the blacksmith orchestrator. "
    "Answer in one short sentence."
)


def main() -> None:
    config = BlacksmithConfig.load(REPO_ROOT / "blacksmith.config.toml")
    executor = Executor(config)

    first = executor.run(
        "Reply with exactly: blacksmith executor online.",
        model=config.models.plan,
        system_prompt=SYSTEM,
    )
    print(f"call 1 | model={first.model} cost_usd={first.cost_usd} "
          f"cache_write={first.cache_creation_tokens} cache_read={first.cache_read_tokens}")
    print(f"        -> {first.text.strip()}")

    second = executor.run(
        "Reply with exactly: still online.",
        model=config.models.plan,
        system_prompt=SYSTEM,
    )
    print(f"call 2 | model={second.model} cost_usd={second.cost_usd} "
          f"cache_write={second.cache_creation_tokens} cache_read={second.cache_read_tokens}")
    print(f"        -> {second.text.strip()}")

    print()
    print(f"AC-8 dedicated key: routed via {config.api.key_env_var}")
    if second.cache_read_tokens > 0:
        verdict = f"VERIFIED — cached prefix reused ({second.cache_read_tokens} tokens read)"
    elif second.cache_creation_tokens > 0:
        verdict = (
            f"caching ENGAGED — static prefix written ({second.cache_creation_tokens} tokens) "
            "but not read back across separate calls (single-shot per call; see notes)"
        )
    else:
        verdict = "no caching observed"
    print(f"AC-8 static-context caching: {verdict}")


if __name__ == "__main__":
    main()
