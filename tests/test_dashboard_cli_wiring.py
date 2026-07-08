"""The `blacksmith dashboard` CLI must wire the live-events sink into the server.

The dashboard serves a `/live` fleet view, but that view only shows runs when the server is
given `live_db_path`. `_dashboard` seeds the metrics DB — it also has to forward
`config.live.db_path`, or the live view renders but is permanently empty (the same
wired-but-dark class of bug as the reviewer loop). This pins the wiring.
"""

from blacksmith import cli
from blacksmith.config import BlacksmithConfig


def test_dashboard_cli_wires_the_live_sink(monkeypatch):
    captured: dict = {}

    def fake_serve(db_path, *, port=0, live_db_path=None):
        captured.update(db_path=db_path, port=port, live_db_path=live_db_path)
        return 0

    config = BlacksmithConfig()
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_load_config", lambda path: config)
    monkeypatch.setattr(cli, "serve_dashboard", fake_serve)

    assert cli._dashboard([]) == 0
    # The live sink is wired (so /live actually shows runs), alongside the metrics DB.
    assert captured["live_db_path"] == config.live.db_path
    assert captured["db_path"] == config.metrics.db_path
