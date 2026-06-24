"""Tests for ``blacksmith runs`` (WU-RUNS-CLI).

``runs`` is a strictly READ-ONLY reporter over the local metrics SQLite sink: it lists
recorded run history (most-recent first) or drills into one run's per-unit rows. It never
writes the sink and never feeds it back into the graph. An empty or absent store prints a
friendly message and exits 0; the output is plain, ANSI-free, and parseable.

These tests seed the sink directly via ``record_run`` (snapshots are plain dicts, which
``record_run`` accepts), then assert the read helpers and the CLI subcommand.
"""

from __future__ import annotations

from pathlib import Path

from blacksmith import cli
from blacksmith.config import BlacksmithConfig, MetricsConfig
from blacksmith.metrics import build_metrics_store, get_run, list_runs, record_run
from blacksmith.state import Status

USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_input_tokens": 300,
    "cache_creation_input_tokens": 0,
}


class _Snap:
    """A minimal snapshot stand-in exposing ``.values`` like the LangGraph snapshot."""

    def __init__(self, values: dict):
        self.values = values


def _snapshot(unit_id: str, *, pr_url: str) -> _Snap:
    """A minimal final-state snapshot shaped like the graph snapshot ``record_run`` reads."""
    return _Snap({
        "status": Status.DONE,
        "cost_events": [
            {
                "node": "implement",
                "unit_id": unit_id,
                "model": "claude-sonnet-4-6",
                "cost_usd": 0.30,
                "num_turns": 7,
                "usage": USAGE,
            }
        ],
        "unit_results": [
            {
                "unit_id": unit_id,
                "title": f"title {unit_id}",
                "files_touched": [f"{unit_id.lower()}.txt"],
                "diff_summary": "x",
            }
        ],
        "pr_url": pr_url,
    })


def _seed_two_runs(db_path: Path):
    """Record two runs (newest = run-b) into a metrics store and return it closed."""
    store = build_metrics_store(db_path)
    record_run(
        store,
        _snapshot("WU-A", pr_url="https://github.com/owner/demo/pull/1"),
        thread_id="run-a",
        prd_path="a.md",
        started_at=100.0,
        ended_at=110.0,
    )
    record_run(
        store,
        _snapshot("WU-B", pr_url="https://github.com/owner/demo/pull/2"),
        thread_id="run-b",
        prd_path="b.md",
        started_at=200.0,
        ended_at=242.5,
    )
    store.close()


def _config_for(db_path: Path) -> BlacksmithConfig:
    return BlacksmithConfig(metrics=MetricsConfig(db_path=db_path))


# --- read helpers ------------------------------------------------------------


def test_list_runs_most_recent_first(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)

    store = build_metrics_store(db_path)
    runs = list_runs(store, 20)
    store.close()

    assert [r["thread_id"] for r in runs] == ["run-b", "run-a"]


def test_get_run_returns_run_and_unit_rows(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)

    store = build_metrics_store(db_path)
    run, units = get_run(store, "run-a")
    store.close()

    assert run is not None
    assert run["thread_id"] == "run-a"
    assert [u["unit_id"] for u in units] == ["WU-A"]
    assert units[0]["title"] == "title WU-A"


def test_get_run_unknown_thread_returns_none(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)

    store = build_metrics_store(db_path)
    run, units = get_run(store, "nope")
    store.close()

    assert run is None
    assert units == []


# --- CLI ---------------------------------------------------------------------


def test_cli_runs_lists_both_newest_first(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    monkeypatch.setattr(cli, "load_dotenv", lambda _p: None)
    monkeypatch.setattr(cli, "_load_config", lambda _arg: _config_for(db_path))

    code = cli.main(["runs"])
    out = capsys.readouterr().out

    assert code == 0
    assert "thread_id" in out and "total_cost" in out and "pr_url" in out
    # No control / ANSI escape codes in the output.
    assert "\x1b" not in out
    # Both runs listed, newest (run-b) before run-a.
    assert out.index("run-b") < out.index("run-a")


def test_cli_runs_drilldown_shows_unit_rows(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "metrics.sqlite"
    _seed_two_runs(db_path)
    monkeypatch.setattr(cli, "load_dotenv", lambda _p: None)
    monkeypatch.setattr(cli, "_load_config", lambda _arg: _config_for(db_path))

    code = cli.main(["runs", "run-a"])
    out = capsys.readouterr().out

    assert code == 0
    assert "run run-a" in out
    assert "WU-A" in out
    assert "title WU-A" in out
    assert "\x1b" not in out


def test_cli_runs_empty_store_prints_friendly_message(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "absent" / "metrics.sqlite"
    monkeypatch.setattr(cli, "load_dotenv", lambda _p: None)
    monkeypatch.setattr(cli, "_load_config", lambda _arg: _config_for(db_path))

    code = cli.main(["runs"])
    out = capsys.readouterr().out

    assert code == 0
    assert "no runs recorded yet" in out
    # A read-only command never creates the metrics file.
    assert not db_path.exists()
