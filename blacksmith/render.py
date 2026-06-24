"""Presentation layer: a single rich Console behind a thin API (WU-CLI-RENDER-FOUNDATION).

This module owns *how* the CLI displays the final report and per-node progress — never
*what* the graph decides. It wraps one ``rich.console.Console`` and decides TTY-vs-plain
ONCE per stream:

- **rendered** (color / panels) only when the output stream is a real TTY AND neither
  ``--plain`` nor ``NO_COLOR`` is set;
- otherwise **plain** text with NO ANSI / control codes, so a piped / non-TTY run stays
  byte-for-byte parseable (the machine path: progress to STDERR, report to STDOUT).

The plain path deliberately bypasses rich entirely and writes flat ``print`` lines, so the
exact strings the machine path relies on ("status: …", "PR: …", the cost / token lines)
are emitted verbatim with zero risk of escape codes or reflow leaking in.
"""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from blacksmith.state import Status

# Color-coded terminal status (rendered mode only — plain mode prints the bare value).
_STATUS_STYLES = {
    Status.DONE: "bold green",
    Status.HALTED: "bold red",
    Status.AWAITING_QA: "bold yellow",
}


def _stream_is_rendered(stream, *, plain: bool, no_color: bool) -> bool:
    """Decide ONCE whether ``stream`` gets rich output.

    Rendered only when the stream is a genuine TTY and the user has not opted out via
    ``--plain`` (``plain``) or the ``NO_COLOR`` convention (``no_color``).
    """
    if plain or no_color:
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(callable(isatty) and isatty())


class Renderer:
    """Thin display API over one rich Console, with a plain fallback.

    ``out_stream`` carries the final report (STDOUT by default); ``err_stream`` carries
    per-node progress (STDERR by default). Each stream's rendered/plain decision is made
    once at construction.
    """

    def __init__(
        self,
        out_stream=None,
        err_stream=None,
        *,
        plain: bool = False,
        no_color: bool = False,
        clock=time.monotonic,
    ) -> None:
        self._out = out_stream if out_stream is not None else sys.stdout
        self._err = err_stream if err_stream is not None else sys.stderr
        self.plain = plain
        self.rendered = _stream_is_rendered(self._out, plain=plain, no_color=no_color)
        self.err_rendered = _stream_is_rendered(self._err, plain=plain, no_color=no_color)
        # The single rich Console the API wraps (used only on the rendered path).
        self._console = Console(
            file=self._out, force_terminal=True, no_color=False, highlight=False
        )
        self._err_console = Console(
            file=self._err, force_terminal=True, no_color=False, highlight=False
        )
        self._clock = clock
        self._start: float | None = None

    # -- final report ---------------------------------------------------------

    def report(self, *, status, pr_url, errors, cost_line: str, token_line: str) -> None:
        """Render the run-end status summary.

        Plain mode prints the same ``status:`` / ``PR:`` / ``error […]`` / cost / token
        lines the machine path parses; rendered mode wraps a color-coded status panel
        around the same facts.
        """
        if not self.rendered:
            self._plain_report(status, pr_url, errors, cost_line, token_line)
            return
        self._rendered_report(status, pr_url, errors, cost_line, token_line)

    def _plain_report(self, status, pr_url, errors, cost_line, token_line) -> None:
        print(f"\nstatus: {status}", file=self._out)
        if pr_url:
            print(f"PR: {pr_url}", file=self._out)
        for err in errors:
            print(f"error [{err.get('node')}]: {err.get('message')}", file=self._out)
        print(cost_line, file=self._out)
        print(token_line, file=self._out)

    def _rendered_report(self, status, pr_url, errors, cost_line, token_line) -> None:
        style = _STATUS_STYLES.get(status, "bold")
        body = Text()
        body.append("status: ", style="dim")
        body.append(str(status), style=style)
        if pr_url:
            body.append("\nPR: ", style="dim")
            body.append(str(pr_url), style="cyan underline")
        for err in errors:
            body.append(f"\nerror [{err.get('node')}]: ", style="red")
            body.append(str(err.get("message")), style="red")
        body.append(f"\n{cost_line}", style="dim")
        body.append(f"\n{token_line}", style="dim")
        self._console.print(Panel(body, title="blacksmith", border_style=style, expand=False))

    # -- per-node progress ----------------------------------------------------

    def progress(self, node: str) -> None:
        """Emit a concise phase indicator for ``node`` to the progress stream.

        Rendered mode shows the current node plus elapsed wall-clock; plain / non-TTY
        mode degrades to one flat ``blacksmith: <node>`` line (the machine path).
        """
        if self._start is None:
            self._start = self._clock()
        if not self.err_rendered:
            print(f"blacksmith: {node}", file=self._err)
            return
        elapsed = self._clock() - self._start
        line = Text()
        line.append("⚙ ", style="cyan")
        line.append(node, style="bold cyan")
        line.append(f"  ·  {elapsed:5.1f}s", style="dim")
        self._err_console.print(line)
