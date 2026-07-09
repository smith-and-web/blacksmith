"""The production graph must wire the post-gate review loop (WU-REVIEW-LOOP).

``build_graph_for`` is the ONLY place the real run compiles its graph. It seeds the self-heal
``limits`` — but it also has to forward ``review=config.review``, or ``compile_graph`` seeds no
``review_enabled`` and the review node is never entered: the whole reviewer feature stays dark
on real runs while still passing its own unit tests. This pins the wiring so that regression
can't recur silently.

Spies on the collaborators ``build_graph_for`` constructs so the assertion is purely about the
kwargs forwarded to ``compile_graph`` — no real executor / clone / store / graph is built.
"""

from blacksmith import cli
from blacksmith.config import BlacksmithConfig


def test_build_graph_for_wires_the_review_config(monkeypatch):
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
    # Regression guard: every opt-in feature config that this call site owns stays forwarded, so
    # none can go wired-but-dark (enabled in config yet never reaching the graph). ``limits`` and
    # the ``index`` (repo map + search_code) share this exact failure mode.
    assert captured["limits"] is config.limits
    assert captured["index"] is config.index
