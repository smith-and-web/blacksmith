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
from types import SimpleNamespace

from blacksmith.cli import _build_renderer, _progress_emitter, _report
from blacksmith.render import Renderer
from blacksmith.state import Status


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


def test_progress_rendered_shows_phase_indicator_with_elapsed():
    err = _TTYStringIO()
    ticks = iter([0.0, 1.5])
    renderer = Renderer(out_stream=io.StringIO(), err_stream=err, clock=lambda: next(ticks))
    emit = _progress_emitter(quiet=False, renderer=renderer)
    emit("implement")
    text = err.getvalue()
    assert "\x1b" in text  # styled, live indicator
    assert "implement" in text
    assert "1.5s" in text  # elapsed shown


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
