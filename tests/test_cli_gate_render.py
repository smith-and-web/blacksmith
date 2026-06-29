"""Approval-gate rendering (WU-CLI-GATE-RENDER).

Test contract: the interactive approver renders the plan / PR gate payloads instead of
dumping a raw ``json.dumps`` blob. The PLAN gate shows the plan steps, target modules,
test contract and a compact cost/tokens line; the PR gate shows the diffstat, the
test pass/fail marker + output, and the files-touched list. ``--json`` reproduces the
legacy raw payload, a non-TTY stream carries zero escape codes, and the prompt's
returned bool is unchanged (True on "y", False on "n").
"""

from __future__ import annotations

import io
import json

from blacksmith.cli import _cli_approver
from blacksmith.render import Renderer

PLAN_PAYLOAD = {
    "gate": "plan",
    "unit": {"id": "WU-XY", "title": "render the gate", "layers": ["logic"]},
    "plan": {
        "unit_id": "WU-XY",
        "title": "render the gate",
        "target_modules": ["blacksmith/render.py", "blacksmith/cli.py"],
        "test_contract": "the rendered output contains the plan steps and module names",
        "steps": "1. Read render.py\n2. Add the gate method\n3. Wire the CLI flag",
        "cost_usd": 0.42,
        "usage": {"input_tokens": 1200, "output_tokens": 340},
    },
}

PR_PAYLOAD = {
    "gate": "pr",
    "unit": {"id": "WU-XY", "title": "render the gate", "layers": ["logic"]},
    "implementation": {
        "files_touched": ["blacksmith/render.py", "blacksmith/cli.py"],
        "diff_summary": " blacksmith/render.py | 90 ++++++\n blacksmith/cli.py | 12 +-",
        "cost_usd": 0.10,
        "usage": {"input_tokens": 50, "output_tokens": 10},
    },
    "test_results": {
        "passed": True,
        "output": "12 passed in 1.23s",
        "command": "pytest && ruff check",
    },
}


def _plain_renderer():
    """A renderer whose report stream is a plain (non-TTY) StringIO."""
    out = io.StringIO()
    return Renderer(out_stream=out, err_stream=io.StringIO(), plain=True), out


def test_plan_gate_renders_plan_text_and_modules_not_raw_json():
    renderer, out = _plain_renderer()
    renderer.gate(PLAN_PAYLOAD)
    text = out.getvalue()
    # The rendered plan text and target-module names appear...
    assert "Add the gate method" in text
    assert "blacksmith/render.py" in text
    assert "blacksmith/cli.py" in text
    assert "the rendered output contains the plan steps" in text
    # ...and a compact cost/tokens line.
    assert "$0.42" in text
    assert "input 1200" in text
    # ...but NOT a raw escaped-JSON blob.
    assert '"steps":' not in text
    assert json.dumps(PLAN_PAYLOAD, indent=2, default=str) not in text


def test_plan_gate_renders_every_units_plan():
    # The multi-unit plan payload (WU-PLAN-ALL-UNITS) surfaces a plan for EVERY auto unit at the
    # single gate — the gap that previously left units after the first unshown.
    payload = {
        "gate": "plan",
        "plans": [
            {
                "unit_id": "WU-01", "title": "first unit", "target_modules": ["a.py"],
                "test_contract": "a works", "steps": "1. do ALPHA",
                "cost_usd": 0.10, "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            {
                "unit_id": "WU-03", "title": "third unit", "target_modules": ["c.py"],
                "test_contract": "c works", "steps": "1. do GAMMA",
                "cost_usd": 0.20, "usage": {"input_tokens": 200, "output_tokens": 20},
            },
        ],
    }
    renderer, out = _plain_renderer()
    renderer.gate(payload)
    text = out.getvalue()
    # BOTH units' ids, steps and modules appear — not just the first.
    assert "WU-01" in text and "WU-03" in text
    assert "do ALPHA" in text and "do GAMMA" in text
    assert "a.py" in text and "c.py" in text
    # A combined total across the units, and the summary names how many are being approved.
    assert "plan total: $0.30 across 2 units" in text
    assert "2 work units" in text


def test_pr_gate_renders_diffstat_and_pass_marker():
    renderer, out = _plain_renderer()
    renderer.gate(PR_PAYLOAD)
    text = out.getvalue()
    assert "blacksmith/render.py | 90" in text  # the diffstat
    assert "PASS" in text  # the pass/fail marker
    assert "12 passed in 1.23s" in text  # the test output
    assert "blacksmith/cli.py" in text  # files touched
    assert '"diff_summary":' not in text


def test_pr_gate_renders_fail_marker():
    payload = {
        **PR_PAYLOAD,
        "test_results": {"passed": False, "output": "1 failed", "command": "pytest"},
    }
    renderer, out = _plain_renderer()
    renderer.gate(payload)
    assert "FAIL" in out.getvalue()


def test_json_flag_reproduces_raw_payload():
    renderer, out = _plain_renderer()
    renderer.gate(PLAN_PAYLOAD, as_json=True)
    assert json.dumps(PLAN_PAYLOAD, indent=2, default=str) in out.getvalue()


def test_non_tty_stream_has_zero_escape_codes():
    renderer, out = _plain_renderer()
    renderer.gate(PLAN_PAYLOAD)
    renderer.gate(PR_PAYLOAD)
    assert "\x1b" not in out.getvalue()


def test_approver_returns_true_on_yes(monkeypatch):
    renderer, _ = _plain_renderer()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    assert _cli_approver(PLAN_PAYLOAD, {}, renderer=renderer) is True


def test_approver_returns_false_on_no(monkeypatch):
    renderer, _ = _plain_renderer()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    assert _cli_approver(PLAN_PAYLOAD, {}, renderer=renderer) is False


def test_approver_does_not_dump_raw_json_by_default(monkeypatch):
    renderer, out = _plain_renderer()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    _cli_approver(PR_PAYLOAD, {}, renderer=renderer)
    assert json.dumps(PR_PAYLOAD, indent=2, default=str) not in out.getvalue()
