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

import json
import sys
import textwrap
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from blacksmith.state import Status

# Color-coded terminal status (rendered mode only — plain mode prints the bare value).
_STATUS_STYLES = {
    Status.DONE: "bold green",
    Status.HALTED: "bold red",
    Status.AWAITING_QA: "bold yellow",
}

# Terminal statuses that mean the run SUCCEEDED (a PR exists): a fully-approved run (DONE) or a
# human-gated unit parked behind a draft PR for manual QA (AWAITING_QA). On these, accumulated
# ``errors`` were all recovered from, so the report summarises rather than lists them.
_SUCCESS_STATUSES = frozenset({Status.DONE, Status.AWAITING_QA})


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
        # Per-node progress timing: a node's duration is printed when the NEXT node
        # starts (progress() now fires on node START, via the debug stream).
        self._last_node: str | None = None
        self._last_node_start: float | None = None

    # -- final report ---------------------------------------------------------

    def report(self, *, status, pr_url, errors, cost_line: str, token_line: str) -> None:
        """Render the run-end status summary.

        Plain mode prints the same ``status:`` / ``PR:`` / ``error […]`` / cost / token
        lines the machine path parses; rendered mode wraps a color-coded status panel
        around the same facts.

        On a SUCCESSFUL terminal status (DONE / AWAITING_QA — a PR exists) every entry in the
        append-only ``errors`` ledger was RECOVERED from (a turn cap that continued, a gate
        failure that self-healed): the run still finished. Listing them as ``error […]`` reads
        as a "failed successfully" report, so they are summarised as a single recovered-count
        line instead. Errors are only shown verbatim when the run did NOT succeed (HALTED),
        where they are the actual reason.
        """
        errors = list(errors or [])
        succeeded = status in _SUCCESS_STATUSES
        shown = [] if succeeded else errors
        recovered = len(errors) if succeeded else 0
        if not self.rendered:
            self._plain_report(status, pr_url, shown, recovered, cost_line, token_line)
            return
        self._rendered_report(status, pr_url, shown, recovered, cost_line, token_line)

    def _plain_report(self, status, pr_url, errors, recovered, cost_line, token_line) -> None:
        print(f"\nstatus: {status}", file=self._out)
        if pr_url:
            print(f"PR: {pr_url}", file=self._out)
        for err in errors:
            print(f"error [{err.get('node')}]: {err.get('message')}", file=self._out)
        if recovered:
            print(f"recovered: {recovered} transient failure(s)", file=self._out)
        print(cost_line, file=self._out)
        print(token_line, file=self._out)

    def _rendered_report(self, status, pr_url, errors, recovered, cost_line, token_line) -> None:
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
        if recovered:
            body.append(
                f"\nrecovered from {recovered} transient failure(s) "
                "(turn caps / gate retries)",
                style="dim",
            )
        body.append(f"\n{cost_line}", style="dim")
        body.append(f"\n{token_line}", style="dim")
        self._console.print(Panel(body, title="blacksmith", border_style=style, expand=False))

    # -- approval gate --------------------------------------------------------

    def gate(self, payload, *, as_json: bool = False) -> None:
        """Render an approval-gate payload to the report stream.

        ``as_json`` preserves the legacy raw ``json.dumps`` blob for scripting. Otherwise
        the payload is *rendered*: the plan's steps as Markdown / its target modules and
        test-contract as readable text; or the PR's diffstat, pass/fail test panel and
        files-touched list. Rendered mode colour-codes panels on a TTY; plain / non-TTY
        mode emits the same facts as flat ANSI-free text (never JSON unless ``as_json``).
        """
        if as_json:
            print(json.dumps(payload, indent=2, default=str), file=self._out)
            return
        payload = payload if isinstance(payload, dict) else {}
        if not self.rendered:
            self._plain_gate(payload)
            return
        self._rendered_gate(payload)

    @staticmethod
    def _plan_entries(payload: dict) -> list:
        """The plan(s) being approved. Prefers the multi-unit ``plans`` list
        (WU-PLAN-ALL-UNITS); falls back to a single legacy ``plan`` (e.g. an older
        checkpoint or a direct caller) so the renderer handles both payload shapes."""
        plans = payload.get("plans")
        if plans:
            return list(plans)
        single = payload.get("plan")
        return [single] if single else []

    def _plans_total_line(self, entries: list) -> str:
        """Combined cost across all the plans shown at a multi-unit plan gate."""
        total = sum(p.get("cost_usd") or 0 for p in entries)
        return f"plan total: ${total:.2f} across {len(entries)} units"

    def _gate_summary(self, payload: dict) -> str:
        """One-line summary of WHAT is being approved (precedes the y/N prompt)."""
        gate = payload.get("gate", "?")
        if gate == "plan":
            entries = self._plan_entries(payload)
            if len(entries) > 1:
                ids = ", ".join(e.get("unit_id", "?") for e in entries)
                return f"Approve the implementation PLAN for {len(entries)} work units: {ids}"
            single = entries[0] if entries else {}
            unit = payload.get("unit") or {}
            uid = single.get("unit_id") or unit.get("id", "?")
            title = single.get("title") or unit.get("title", "")
            return f"Approve the implementation PLAN for {uid} {title}".strip()
        unit = payload.get("unit") or {}
        label = f"{unit.get('id', '?')} {unit.get('title', '')}".strip()
        if gate == "pr":
            return f"Approve the PR for {label}"
        return f"Approve the {gate!r} gate for {label}"

    def _cost_tokens_line(self, slice_: dict) -> str:
        """Compact cost + tokens line for a plan/implementation state slice."""
        cost = slice_.get("cost_usd")
        cost_str = f"${cost:.2f}" if cost is not None else "unavailable"
        usage = slice_.get("usage") or {}
        return (
            f"cost: {cost_str} · tokens: input {usage.get('input_tokens', 0)}, "
            f"output {usage.get('output_tokens', 0)}"
        )

    @staticmethod
    def _review_summary(payload: dict) -> tuple[list, int]:
        """The PR gate's reviewer summary (WU-REVIEW-RENDER): any outstanding blocking
        findings the post-gate review loop gave up on, plus how many revision rounds it
        spent getting there. Read straight off the gate payload's ``unresolved_review_findings``
        / ``review_revisions`` keys (mirroring the state fields of the same name); absent
        on a payload with no review data, which renders as a clean review."""
        unresolved = payload.get("unresolved_review_findings") or []
        # Prefer the run-wide reducer total (fan-out workers' revisions land here, not in the
        # last-write-wins ``review_revisions``); fall back to the per-unit field when absent.
        resolved = payload.get("review_revisions_total") or payload.get("review_revisions") or 0
        return unresolved, resolved

    def _plain_gate(self, payload: dict) -> None:
        out = self._out
        print(f"\n{self._gate_summary(payload)}", file=out)
        gate = payload.get("gate")
        if gate == "plan":
            entries = self._plan_entries(payload)
            for plan in entries:
                if len(entries) > 1:
                    header = f"{plan.get('unit_id', '?')} {plan.get('title', '')}".strip()
                    print(f"\n=== {header} ===", file=out)
                print("\nSteps:", file=out)
                print(plan.get("steps") or "(none)", file=out)
                print("\nTarget modules:", file=out)
                for mod in plan.get("target_modules") or []:
                    print(f"  - {mod}", file=out)
                print("\nTest contract:", file=out)
                print(textwrap.fill(plan.get("test_contract") or "", width=88), file=out)
                print(f"\n{self._cost_tokens_line(plan)}", file=out)
            if len(entries) > 1:
                print(f"\n{self._plans_total_line(entries)}", file=out)
        elif gate == "pr":
            impl = payload.get("implementation") or {}
            results = payload.get("test_results") or {}
            print("\nDiff summary:", file=out)
            print(impl.get("diff_summary") or "(none)", file=out)
            diff_text = payload.get("diff_text")
            if diff_text:
                print("\nFull diff:", file=out)
                print(diff_text, file=out)
            marker = "PASS" if results.get("passed") else "FAIL"
            print(f"\nTests: {marker}  ({results.get('command', '')})", file=out)
            print(results.get("output") or "", file=out)
            print("\nFiles touched:", file=out)
            for path in impl.get("files_touched") or []:
                print(f"  - {path}", file=out)
            unresolved, resolved = self._review_summary(payload)
            if not unresolved:
                print("\nreview: clean", file=out)
            else:
                print("\nUnresolved review findings:", file=out)
                for finding in unresolved:
                    print(
                        f"  - {finding.get('file', '(unknown file)')}: "
                        f"{finding.get('detail', '')}",
                        file=out,
                    )
                print(f"resolved via revision: {resolved}", file=out)

    def _rendered_gate(self, payload: dict) -> None:
        console = self._console
        console.print(Text(self._gate_summary(payload), style="bold"))
        gate = payload.get("gate")
        if gate == "plan":
            entries = self._plan_entries(payload)
            for plan in entries:
                # With multiple units, title each steps panel by its unit so the plans are
                # distinguishable; a single-unit gate keeps the plain "plan steps" title.
                title = "plan steps"
                if len(entries) > 1:
                    title = f"plan: {plan.get('unit_id', '?')} {plan.get('title', '')}".strip()
                steps = Markdown(plan.get("steps") or "_(no steps)_")
                console.print(Panel(steps, title=title, expand=False))
                mods = Text()
                for mod in plan.get("target_modules") or []:
                    mods.append(f"• {mod}\n")
                console.print(Panel(mods or Text("(none)"), title="target modules", expand=False))
                # The test_contract is verbatim from the PRD — the least actionable item at this
                # gate. De-emphasise it to a dim, collapsed secondary line so the plan STEPS and
                # target modules lead, rather than a co-equal full-width panel that competes.
                contract = plan.get("test_contract") or ""
                if contract:
                    summary = textwrap.shorten(contract, width=96, placeholder=" …")
                    console.print(
                        Text(f"test contract (from PRD, for reference): {summary}", style="dim")
                    )
                console.print(Text(self._cost_tokens_line(plan), style="dim"))
            if len(entries) > 1:
                console.print(Text(self._plans_total_line(entries), style="dim"))
        elif gate == "pr":
            impl = payload.get("implementation") or {}
            results = payload.get("test_results") or {}
            diff = Text(impl.get("diff_summary") or "(none)")
            console.print(Panel(diff, title="diff summary", expand=False))
            diff_text = payload.get("diff_text")
            if diff_text:
                console.print(Panel(Text(diff_text), title="full diff", expand=False))
            passed = results.get("passed")
            marker, style = ("PASS", "bold green") if passed else ("FAIL", "bold red")
            body = Text()
            body.append(f"{marker}  ", style=style)
            body.append(results.get("command", ""), style="dim")
            body.append("\n")
            body.append(results.get("output") or "")
            console.print(Panel(body, title="test results", border_style=style, expand=False))
            files = Text()
            for path in impl.get("files_touched") or []:
                files.append(f"• {path}\n")
            console.print(Panel(files or Text("(none)"), title="files touched", expand=False))
            unresolved, resolved = self._review_summary(payload)
            if not unresolved:
                console.print(Text("review: clean", style="dim"))
            else:
                findings = Text()
                for finding in unresolved:
                    findings.append(
                        f"• {finding.get('file', '(unknown file)')}: {finding.get('detail', '')}\n"
                    )
                console.print(
                    Panel(
                        findings, title="unresolved review findings",
                        border_style="yellow", expand=False,
                    )
                )
                console.print(Text(f"resolved via revision: {resolved}", style="dim"))

    # -- per-node progress ----------------------------------------------------

    def progress(self, node: str) -> None:
        """Announce that ``node`` is STARTING, to the progress stream.

        Called as each node begins (the debug stream drives it), so the running node is
        named while it works rather than only after it finishes. Plain / non-TTY mode
        degrades to one flat ``blacksmith: <node>`` line (the machine path). Rendered mode
        prints a "⚙ <node>" start line, and closes out the previous node with its measured
        duration ("✓ <prev> · N.Ns") — so the slow step is visible the moment it begins and
        its cost is reported the moment the next one starts.
        """
        now = self._clock()
        if self._start is None:
            self._start = now
        if not self.err_rendered:
            print(f"blacksmith: {node}", file=self._err)
            return
        if self._last_node is not None and self._last_node_start is not None:
            done = Text()
            done.append("✓ ", style="green")
            done.append(self._last_node, style="dim")
            done.append(f"  ·  {now - self._last_node_start:5.1f}s", style="dim")
            self._err_console.print(done)
        line = Text()
        line.append("⚙ ", style="cyan")
        line.append(node, style="bold cyan")
        self._err_console.print(line)
        self._last_node = node
        self._last_node_start = now
