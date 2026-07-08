"""Rendering layer + TTY/plain detection (WU-CLI-RENDER-FOUNDATION).

The presentation layer (``blacksmith.render.Renderer``) wraps a single rich Console and
decides TTY-vs-plain ONCE per stream: rendered (color / panels) only on a real TTY with
neither ``--plain`` nor ``NO_COLOR`` set; otherwise PLAIN text with zero ANSI / control
codes. These tests assert that contract for the final report and the per-node progress —
without changing any graph / gate decision.

  * a forced-TTY Console produces a colored status panel (ANSI present);
  * a non-TTY / StringIO stream produces plain output with ZERO escape codes, and the
    status / PR / cost / token lines the machine path parses remain present;
  * ``--quiet`` emits no progress;
  * ``--plain`` forces plain even on a TTY.
"""

from __future__ import annotations

import io
import re
from types import SimpleNamespace

from blacksmith.cli import _build_renderer, _progress_emitter, _report
from blacksmith.render import Renderer
from blacksmith.state import Status

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


_PLAN_PAYLOAD = {
    "gate": "plan",
    "unit": {"id": "WU-DEMO", "title": "Demo unit"},
    "plan": {
        "steps": "Do STEP-ALPHA first, then the rest.",
        "target_modules": ["mod_alpha.py", "mod_beta.py"],
        "test_contract": "CONTRACT-OMEGA verbatim from the PRD.",
        "cost_usd": 0.01,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    },
}


class _TTYStringIO(io.StringIO):
    """A StringIO that claims to be a terminal, so the layer takes the rendered path."""

    def isatty(self) -> bool:  # noqa: D401 - trivial override
        return True


def _snapshot(values):
    return SimpleNamespace(values=values)


_DONE_VALUES = {
    "status": Status.DONE,
    "pr_url": "https://github.com/owner/demo/pull/1",
    "errors": [],
    "plan": {"cost_usd": 0.05, "usage": {"input_tokens": 100, "output_tokens": 10}},
    "implementation": {"cost_usd": 0.05, "usage": {"input_tokens": 100, "output_tokens": 25}},
}


# -- final report ------------------------------------------------------------


def test_forced_tty_report_renders_a_colored_status_panel():
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    assert renderer.rendered is True  # decision made once, at construction

    _report(_snapshot(_DONE_VALUES), renderer)
    text = out.getvalue()

    assert "\x1b" in text  # ANSI escapes → it is a *colored* panel
    assert "done" in text
    assert "github.com/owner/demo/pull/1" in text


def test_non_tty_report_is_plain_with_zero_escape_codes_and_parseable_lines():
    out = io.StringIO()  # a plain StringIO is not a TTY
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    assert renderer.rendered is False

    _report(_snapshot(_DONE_VALUES), renderer)
    text = out.getvalue()

    assert "\x1b" not in text  # ZERO escape codes on the machine path
    # the exact strings the machine path relies on remain present + parseable
    assert "status: done" in text
    assert "PR: https://github.com/owner/demo/pull/1" in text
    assert "total cost: $0.10" in text
    assert "tokens: input 200, output 35" in text


def test_non_tty_report_color_codes_status_words_only_via_style():
    # halted / awaiting_qa still print their bare status value parseably in plain mode.
    for status in (Status.HALTED, Status.AWAITING_QA):
        out = io.StringIO()
        renderer = Renderer(out_stream=out, err_stream=io.StringIO())
        values = {"status": status, "errors": [], "pr_url": None}
        _report(_snapshot(values), renderer)
        text = out.getvalue()
        assert "\x1b" not in text
        assert f"status: {status}" in text


def test_errors_appear_in_plain_report():
    out = io.StringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    values = {
        "status": Status.HALTED,
        "pr_url": None,
        "errors": [{"node": "test_gate", "message": "boom"}],
    }
    _report(_snapshot(values), renderer)
    text = out.getvalue()
    assert "error [test_gate]: boom" in text
    assert "\x1b" not in text


def test_recovered_errors_are_summarised_not_listed_on_a_successful_run():
    # A DONE run whose accumulated errors were all RECOVERED from (a turn cap that continued, a
    # gate failure that self-healed) must NOT list them as "error [...]" — that reads as a
    # "failed successfully" report. They are summarised as a recovered count instead.
    out = io.StringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    values = {
        "status": Status.DONE,
        "pr_url": "https://github.com/owner/demo/pull/9",
        "errors": [
            {"node": "implement", "message": "implement exceeded its turn budget (40 turns)"},
            {"node": "test_gate", "message": "gate failed for unit WU-X"},
        ],
    }
    _report(_snapshot(values), renderer)
    text = out.getvalue()
    assert "status: done" in text
    assert "PR: https://github.com/owner/demo/pull/9" in text
    assert "error [" not in text  # recovered failures are NOT listed as errors
    assert "recovered: 2 transient failure(s)" in text


def test_rendered_successful_report_shows_recovered_summary_not_error_lines():
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    values = {
        "status": Status.DONE,
        "pr_url": "https://github.com/owner/demo/pull/9",
        "errors": [{"node": "implement", "message": "implement exceeded its turn budget"}],
    }
    _report(_snapshot(values), renderer)
    text = out.getvalue()
    assert "recovered from 1 transient failure(s)" in text
    assert "error [" not in text


# -- plan gate: contract de-emphasised so the plan leads ---------------------


def test_plan_gate_rendered_leads_with_steps_and_modules_before_contract():
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    assert renderer.rendered is True

    renderer.gate(_PLAN_PAYLOAD)
    raw = out.getvalue()
    plain = _strip_ansi(raw)

    # The plan STEPS and target modules LEAD; the contract trails them.
    steps_at = plain.index("STEP-ALPHA")
    mod_at = plain.index("mod_alpha.py")
    contract_at = plain.index("CONTRACT-OMEGA")
    assert steps_at < contract_at
    assert mod_at < contract_at

    # The contract gets a DE-EMPHASIZED (dim) secondary treatment, not a co-equal panel.
    contract_line = next(line for line in raw.splitlines() if "CONTRACT-OMEGA" in line)
    assert "\x1b[2m" in contract_line  # dim style applied to the contract line


def test_plan_gate_plain_keeps_full_contract_text_with_zero_escapes():
    out = io.StringIO()  # not a TTY → plain path
    renderer = Renderer(out_stream=out, err_stream=io.StringIO())
    assert renderer.rendered is False

    renderer.gate(_PLAN_PAYLOAD)
    text = out.getvalue()

    assert "\x1b" not in text  # machine path stays ANSI-free
    # the full contract text remains present + parseable in plain mode
    assert "CONTRACT-OMEGA verbatim from the PRD." in text
    # steps + modules still lead in plain mode too
    assert text.index("STEP-ALPHA") < text.index("CONTRACT-OMEGA")
    assert text.index("mod_alpha.py") < text.index("CONTRACT-OMEGA")


# -- per-node progress -------------------------------------------------------


def test_progress_plain_emits_blacksmith_node_lines_with_zero_escapes():
    err = io.StringIO()
    renderer = Renderer(out_stream=io.StringIO(), err_stream=err)
    emit = _progress_emitter(quiet=False, renderer=renderer)
    emit("ingest_prd")
    emit("plan")
    text = err.getvalue()
    assert "\x1b" not in text
    assert "blacksmith: ingest_prd" in text
    assert "blacksmith: plan" in text


def test_progress_rendered_marks_node_start_and_reports_prev_duration():
    err = _TTYStringIO()
    ticks = iter([0.0, 1.5])
    renderer = Renderer(out_stream=io.StringIO(), err_stream=err, clock=lambda: next(ticks))
    emit = _progress_emitter(quiet=False, renderer=renderer)
    emit("implement")  # announces implement the moment it STARTS (t=0.0)
    emit("test_gate")  # closes implement with its measured duration, starts test_gate
    text = err.getvalue()
    assert "\x1b" in text  # styled, live indicator
    assert "implement" in text  # start line shown when the node began
    assert "test_gate" in text  # next node announced on start
    assert "1.5s" in text  # implement's duration, printed when test_gate started


def test_quiet_emits_no_progress():
    err = io.StringIO()
    renderer = Renderer(out_stream=io.StringIO(), err_stream=err)
    assert _progress_emitter(quiet=True, renderer=renderer) is None
    assert err.getvalue() == ""


# -- flag wiring -------------------------------------------------------------


def test_plain_flag_forces_plain_even_on_a_tty():
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=_TTYStringIO(), plain=True)
    assert renderer.rendered is False
    assert renderer.err_rendered is False

    _report(_snapshot(_DONE_VALUES), renderer)
    text = out.getvalue()
    assert "\x1b" not in text
    assert "status: done" in text


def test_no_color_env_forces_plain():
    out = _TTYStringIO()
    renderer = Renderer(out_stream=out, err_stream=io.StringIO(), no_color=True)
    assert renderer.rendered is False


def test_build_renderer_reads_plain_flag():
    args = SimpleNamespace(plain=True)
    renderer = _build_renderer(args)
    assert renderer.plain is True
