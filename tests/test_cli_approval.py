"""Headless approval modes for the CLI (dogfood feedback: gates must be scriptable).

The drive loop already accepts an injected approver; these cover the flag-driven
approvers that make non-interactive / CI runs possible without a terminal.
"""

from __future__ import annotations

import argparse
import io
import json

from blacksmith.cli import _auto_approver, _cli_approver, _select_approver
from blacksmith.render import Renderer


def _args(*, auto_approve=False, approve=None):
    return argparse.Namespace(auto_approve=auto_approve, approve=approve)


def test_auto_approver_all_approves_every_gate():
    approve = _auto_approver(None)
    assert approve({"gate": "plan"}, {}) is True
    assert approve({"gate": "pr"}, {}) is True


def test_auto_approver_subset_denies_unlisted_gate():
    approve = _auto_approver({"plan"})
    assert approve({"gate": "plan"}, {}) is True
    assert approve({"gate": "pr"}, {}) is False  # denial halts before the PR gate


def test_select_approver_auto_approve_flag():
    approve = _select_approver(_args(auto_approve=True))
    assert approve({"gate": "pr"}, {}) is True


def test_select_approver_approve_list_trims_and_filters():
    approve = _select_approver(_args(approve=" plan , "))
    assert approve({"gate": "plan"}, {}) is True
    assert approve({"gate": "pr"}, {}) is False


def test_select_approver_defaults_to_interactive():
    assert _select_approver(_args()) is _cli_approver


def test_cli_approver_renders_payload_not_raw_json(monkeypatch):
    """The interactive approver renders the plan gate rather than dumping raw JSON."""
    payload = {
        "gate": "plan",
        "unit": {"id": "WU-1", "title": "demo", "layers": ["logic"]},
        "plan": {
            "target_modules": ["out.txt"],
            "test_contract": "the gate passes",
            "steps": "1. write out.txt",
            "cost_usd": 0.01,
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    }
    out = io.StringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO(), plain=True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    assert _cli_approver(payload, {}, renderer=renderer) is True
    text = out.getvalue()
    assert "1. write out.txt" in text  # rendered plan steps
    assert "out.txt" in text  # target module name
    assert json.dumps(payload, indent=2, default=str) not in text  # no raw blob


def test_cli_approver_json_flag_preserves_raw_payload(monkeypatch):
    payload = {"gate": "pr", "unit": {"id": "WU-1", "title": "demo", "layers": []}}
    out = io.StringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO(), plain=True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert _cli_approver(payload, {}, renderer=renderer, as_json=True) is False
    assert json.dumps(payload, indent=2, default=str) in out.getvalue()
