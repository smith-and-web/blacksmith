"""The anti-dark guard: every opt-in feature must reach the NODE that reads it.

Two seams, guarded in order:

1. ``build_graph_for`` -> ``compile_graph`` (test ``..._forwards_every_opt_in_feature``).
   ``build_graph_for`` is the ONLY place the real run compiles its graph; a feature gated by a
   ``config.*`` section must be forwarded to ``compile_graph`` or it stays "wired but dark" —
   enabled in config yet never reaching the graph, while its own unit tests still pass. That bug
   recurred FOUR times (reviewer loop, live sink, repo-map index, sandbox).

2. ``compile_graph`` -> the unit-build NODES (test ``..._covers_every_injectable_implement_param``
   here, plus the behavioural fan-out tests in ``test_parallel.py``). Seam 1 stops one step early:
   it never checks a forwarded feature reaches the node that reads it, and never touches the
   fan-out ``build_unit`` worker — which is exactly how the reviewer (finding #1) and index/
   sandbox (finding #2) went dark on parallel units while the forwarding guard stayed green.
   Since the fix, BOTH implement paths (the sequential ``implement`` node and the ``build_unit``
   worker) go through the ONE ``UnitDeps.implement_kwargs()``, so this seam is guarded by pinning
   that method against ``implement``'s own signature: a new injectable dep added to ``implement``
   but forgotten in the bundle fails here loudly, on both paths at once.

Seam-1 test spies on the collaborators ``build_graph_for`` constructs, so its assertion is purely
about the kwargs forwarded to ``compile_graph`` — no real executor / clone / store / graph is built.
"""

import inspect

from blacksmith import cli
from blacksmith.config import BlacksmithConfig
from blacksmith.graph import UnitDeps
from blacksmith.nodes.implement import implement
from blacksmith.sandbox import SandboxManager


def test_build_graph_for_forwards_every_opt_in_feature(monkeypatch):
    captured: dict = {}

    def fake_compile(checkpointer, **kwargs):
        captured.update(kwargs)
        return "graph"

    monkeypatch.setattr(cli, "compile_graph", fake_compile)
    monkeypatch.setattr(cli, "Executor", lambda config: "executor")
    monkeypatch.setattr(cli, "CloneManager", lambda path: "clone-manager")
    monkeypatch.setattr(cli, "build_store", lambda db_path: "store")

    config = BlacksmithConfig()
    assert cli.build_graph_for(config, "checkpointer") == "graph"

    # The review loop is wired with the run's ReviewConfig, and it defaults ENABLED — so a real
    # run actually enters the review node rather than silently skipping it.
    assert captured["review"] is config.review
    assert captured["review"].enabled is True
    # The self-heal limits and the repo-map / search_code index are forwarded straight from config.
    assert captured["limits"] is config.limits
    assert captured["index"] is config.index
    # SBFL is forwarded straight from config too, so the fault-localization feedback can't become a
    # wired-but-dark feature: enabled in [sbfl] yet never reaching the fix_retry node.
    assert captured["sbfl"] is config.sbfl
    # The target repo's default branch is forwarded so the PR is opened against it
    # (gh pr create --base) rather than whatever gh guesses.
    assert captured["default_branch"] == config.target.default_branch
    # The sandbox is the exception: build_graph_for is the only place a live SandboxManager is
    # constructed, so assert it forwards one whose config is bridged from the run's [sandbox]
    # settings (the mapping that was missing — the feature was dark). The manager is inert until
    # prepare_worktree starts it, so constructing one for a disabled run is a no-op.
    assert isinstance(captured["sandbox"], SandboxManager)
    assert captured["sandbox"].config.enabled == config.sandbox.enabled
    assert captured["sandbox"].config.image == config.sandbox.image
    assert captured["sandbox"].config.exec_timeout_s == config.sandbox.exec_timeout_s


def test_unit_deps_covers_every_injectable_implement_param():
    """Seam 2 (finding #4): the bundle handed to BOTH implement paths must carry every dep
    ``implement`` accepts, so a feature can't be live sequentially yet dark on fan-out.

    Both the sequential ``implement`` node and the fan-out ``build_unit`` worker call
    ``implement`` via the ONE ``UnitDeps.implement_kwargs()``. So a new keyword-only dep added
    to ``implement`` MUST also be carried by the bundle — otherwise it is silently dropped on
    both paths. This pins that invariant against ``implement``'s own signature: forget to thread
    a new dep through the bundle and this fails, naming the missing param, instead of the feature
    going dark while every other test stays green (exactly how findings #1/#2 slipped through)."""
    injectable = {
        name
        for name, param in inspect.signature(implement).parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    }
    # A fully-populated bundle; implement_kwargs only emits the sandbox keys when a sandbox is
    # wired, so a non-None sentinel is enough to exercise the full key set.
    populated = UnitDeps(
        executor=object(),
        gate=object(),
        fix=object(),
        index_config=object(),
        sandbox=object(),
        sandbox_exec_timeout_s=120,
    )
    missing = injectable - set(populated.implement_kwargs())
    assert not missing, (
        f"UnitDeps.implement_kwargs() omits implement()'s injectable param(s) {sorted(missing)}: "
        "a new dep was added to implement but not threaded through the bundle, so it is DARK on "
        "both the sequential node and the fan-out worker. Add it to UnitDeps + implement_kwargs()."
    )
