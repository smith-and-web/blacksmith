"""``build_graph_for`` must forward EVERY opt-in feature to the graph (the anti-dark guard).

``build_graph_for`` is the ONLY place the real run compiles its graph. Every opt-in feature is
gated by a ``config.*`` section, and each must be forwarded to ``compile_graph`` — or the
feature stays "wired but dark": enabled in config yet never reaching the graph, while its own
unit tests still pass. That exact bug has recurred FOUR times (the reviewer loop, the live
dashboard sink, the repo-map index, and the sandbox). This test pins the whole forwarding set
so it can't recur silently: whenever a new opt-in feature is added, it must be asserted here.

Spies on the collaborators ``build_graph_for`` constructs so the assertion is purely about the
kwargs forwarded to ``compile_graph`` — no real executor / clone / store / graph is built.
"""

from blacksmith import cli
from blacksmith.config import BlacksmithConfig
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
