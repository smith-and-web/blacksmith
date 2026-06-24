"""Surface per-call transcript references on the run row (WU-TRANSCRIPT-SURFACE).

ADDITIVE OBSERVABILITY: ``metrics.record_run`` records the run's transcript references on
the run row — the resolved ``<dir>/<session_id>.jsonl`` paths drawn from the run's
``cost_events`` — so a run links to the per-call transcripts that belong to it, and
``blacksmith runs <thread_id>`` prints those paths in the per-run detail. When transcripts
are disabled (no dir) or none were captured, the field is empty and the detail view omits
the transcript section cleanly. These tests never touch the graph or the network — they
record a synthetic final state and read it back.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from blacksmith.cli import _print_run_detail, _transcript_paths
from blacksmith.metrics import build_metrics_store, get_run, record_run


def _state(session_ids):
    """A minimal final snapshot (``.values``) carrying implement cost_events with sids."""
    return SimpleNamespace(
        values={
            "status": "done",
            "cost_events": [
                {"node": "implement", "unit_id": "WU-A", "model": "m", "cost_usd": 0.1,
                 "num_turns": 1, "usage": {}, "session_id": sid}
                for sid in session_ids
            ],
            "unit_results": [],
            "errors": [],
            "pr_url": None,
        }
    )


def test_record_run_records_resolved_transcript_paths_on_the_run_row(tmp_path):
    transcripts = tmp_path / "transcripts"
    store = build_metrics_store(tmp_path / "metrics.sqlite")
    record_run(
        store,
        _state(["sess-1", "sess-2"]),
        thread_id="wu",
        prd_path="prd.md",
        started_at=0.0,
        ended_at=1.0,
        transcripts_dir=transcripts,
    )

    run, _units = get_run(store, "wu")
    assert run is not None
    paths = _transcript_paths(run)
    assert paths == [
        str(transcripts / "sess-1.jsonl"),
        str(transcripts / "sess-2.jsonl"),
    ]
    store.close()


def test_runs_detail_prints_the_transcript_paths(tmp_path, capsys):
    transcripts = tmp_path / "transcripts"
    store = build_metrics_store(tmp_path / "metrics.sqlite")
    record_run(
        store,
        _state(["sess-1", "sess-2"]),
        thread_id="wu",
        prd_path="prd.md",
        started_at=0.0,
        ended_at=1.0,
        transcripts_dir=transcripts,
    )
    run, units = get_run(store, "wu")
    store.close()

    _print_run_detail(run, units)
    out = capsys.readouterr().out
    assert "transcripts:" in out
    assert str(transcripts / "sess-1.jsonl") in out
    assert str(transcripts / "sess-2.jsonl") in out


def test_duplicate_session_ids_collapse_to_one_path(tmp_path):
    transcripts = tmp_path / "transcripts"
    store = build_metrics_store(tmp_path / "metrics.sqlite")
    # Plan + an escalation can repeat the same session id; the ref is recorded once.
    record_run(
        store,
        _state(["sess-1", "sess-1"]),
        thread_id="wu",
        prd_path="prd.md",
        started_at=0.0,
        ended_at=1.0,
        transcripts_dir=transcripts,
    )
    run, _units = get_run(store, "wu")
    assert _transcript_paths(run) == [str(transcripts / "sess-1.jsonl")]
    store.close()


def test_no_transcripts_field_is_empty_and_detail_omits_section(tmp_path, capsys):
    store = build_metrics_store(tmp_path / "metrics.sqlite")
    # transcripts disabled (no dir): the run row records an empty list.
    record_run(
        store,
        _state(["sess-1"]),
        thread_id="wu",
        prd_path="prd.md",
        started_at=0.0,
        ended_at=1.0,
        transcripts_dir=None,
    )
    run, units = get_run(store, "wu")
    store.close()

    assert json.loads(run["transcripts"]) == []
    assert _transcript_paths(run) == []

    _print_run_detail(run, units)
    out = capsys.readouterr().out
    assert "transcripts:" not in out  # section omitted cleanly, no error
