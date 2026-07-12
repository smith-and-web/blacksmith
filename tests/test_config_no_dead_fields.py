"""Config-leaf drift guard: every ``BlacksmithConfig`` leaf must be consumed somewhere.

A declared-but-unread config field is a config->behaviour drift bug: the field is
documented and settable, yet changing it does nothing. This has bitten blacksmith three
times at once (``api.prompt_caching``, ``models.triage``, ``target.default_branch`` — all
declared, documented, and read nowhere). This test pins the whole leaf surface so a new
dead field can't merge silently: every scalar leaf on ``BlacksmithConfig`` must be
referenced — as an attribute access ``.name`` or a dict/string key ``"name"`` — in at
least one ``blacksmith/`` module OTHER than ``config.py`` itself.

It is a NAME-based grep, so it is deliberately conservative: it proves a leaf is wired to
*something*, not that the wiring is correct. A leaf consumed only through a resolver method
that lives in ``config.py`` (``resolve_api_key`` etc.) is listed in ``_RESOLVER_CONSUMED``
with the resolver that reads it — the only sanctioned way for a leaf to be "used" without
appearing outside ``config.py``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from blacksmith import config as config_mod
from blacksmith.config import BlacksmithConfig

# Leaves consumed ONLY through a resolver method inside config.py (so their bare name need
# not appear in any other module). Each maps to the resolver that reads it — keeping this
# list honest: a genuinely dead leaf can't hide here without a matching, real resolver.
_RESOLVER_CONSUMED = {
    "key_env_var": "resolve_api_key",
    "admin_key_env_var": "resolve_admin_key",
    "repo_path": "resolve_repo_path",
    "graph_rank": "resolve_graph_rank",
    "coverage_cmd": "resolve_sbfl_config",
    "coverage_json": "resolve_sbfl_config",
    "junit_xml": "resolve_sbfl_config",
    "max_locations": "resolve_sbfl_config",
}


def _leaf_names(model: type[BaseModel]) -> set[str]:
    """Every scalar leaf field name reachable from ``model``, recursing into nested
    config sections (themselves BaseModels) but stopping at scalar fields."""
    leaves: set[str] = set()
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            leaves |= _leaf_names(annotation)
        else:
            leaves.add(name)
    return leaves


def _blacksmith_sources_excluding_config() -> str:
    """Concatenated text of every ``blacksmith/`` .py module except ``config.py``."""
    pkg_dir = Path(config_mod.__file__).parent
    chunks = []
    for path in sorted(pkg_dir.rglob("*.py")):
        if path.name == "config.py":
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_every_config_leaf_is_referenced_outside_config():
    leaves = _leaf_names(BlacksmithConfig)
    sources = _blacksmith_sources_excluding_config()
    config_src = Path(config_mod.__file__).read_text(encoding="utf-8")

    dead: list[str] = []
    for leaf in sorted(leaves):
        referenced = (
            f".{leaf}" in sources
            or f'"{leaf}"' in sources
            or f"'{leaf}'" in sources
        )
        if referenced:
            continue
        # Sanctioned exception: consumed through a resolver method inside config.py.
        resolver = _RESOLVER_CONSUMED.get(leaf)
        if resolver is not None and f"def {resolver}" in config_src:
            continue
        dead.append(leaf)

    assert not dead, (
        "config leaves declared but read nowhere (config->behaviour drift): "
        f"{dead}. Either wire each into behaviour, delete it, or — if it is consumed "
        "only via a config.py resolver — add it to _RESOLVER_CONSUMED with that resolver."
    )
