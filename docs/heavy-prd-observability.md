# Observations and retained evidence

S19 is implemented under local verification. The [checklist](reviews/heavy-prd-progress.md)
and [evidence ledger](reviews/heavy-prd-evidence.md) distinguish scoped results from
the required final combined suite and unrun live release gates.

The operation journal, immutable requirements/plans, exact-candidate gate/review
receipts, approvals and recovery manifests control execution. Live events, metrics,
memory lessons and display callbacks are optional observations. Their failure cannot
substitute a success decision for missing critical evidence. Critical receipt errors
continue to halt before downstream effects.

An optional open, lookup, write or display failure emits a diagnostic such as
`optional memory lookup unavailable (OSError); execution continues`. Only the
exception class is printed; exception messages may contain credentials or paths.
Warnings are limited per feature per graph invocation and shared across its worker
contexts. A separately opened sink has its own warning budget. Approval/resume
invocations may produce a fresh diagnostic. A failed memory lookup supplies an
empty lesson list. Diagnostics do not add model calls or reset configured budgets.

## Identity and activity

Node boundaries use actual LangGraph task IDs. Child stages and model activity carry
run, unit, attempt and task IDs; child activity also carries its actual parent task
ID. Model-call IDs and roles come from the same immutable identity used by the
critical journal. A child writer bridge forwards synchronous subgraph activity to
the parent stream without assigning it to the most recently started sibling.
Standalone injected-library activity receives unique call IDs too, while its
ExecutorResult retains the legacy comparison behavior. Transcripts remain
keyed by unique call ID even when provider session IDs are absent.

Finding previews reuse the strict complete verdict parser and require a successful
terminal result. Malformed, extra-field, duplicate-field, incomplete and error
responses cannot create a plausible finding preview. Review status comes from typed
receipts: disabled and unavailable remain distinct from clean. Observation loss is
not a clean review. Full review text, findings and critical receipts are preserved.

Aggregate run/unit summaries cannot supply a historical task ID that was never
recorded. Such fields are null. Legacy events without IDs remain viewable with a
limited name-based fallback; a known new task never falls back to another sibling.

## Bounded presentation

CLI and PR finding/error previews show at most 20 rows, with explicit omitted counts
and references to retained run artifacts. Details are capped at 2,048 characters,
file labels at 512, and node labels at 128; terminal control characters are escaped.
The live dashboard shows at most 20 findings, the latest 200 steps and 100 unit rows,
with explicit omissions. A producer's omitted-finding count is included. Display
limits do not delete event history or change receipts/checkpoints. An interrupted
unfinished dashboard step is labeled unfinished. Multiple active sibling steps can
remain active together.

Raw gate JSON and immutable PRD/approved-plan inputs remain complete. Review revision
totals derive from current keyed unit outcomes, excluding retained recovery history;
recovered children add only their new delta to the legacy additive display counter.
Attempts do not count resolved findings. Missing historical per-unit counts display
as unavailable instead of inventing zero or trusting an inflated legacy total.

## Local checks

`test_optional_observability.py` injects optional outages through real CLI, Git,
gate, journal, budget and checkpoint paths. `test_observability_integrity.py` proves
sequential/parallel review recovery and parent/child event identity.
`test_activity_integrity.py` exercises actual graph streaming and strict previews;
`test_presentation_integrity.py` proves bounded output and unchanged full evidence.

The standalone JavaScript regression executes the actual served inline program with
an offline DOM stub; it does not claim browser layout verification. Export and run:

```sh
uv run python -c 'from pathlib import Path; from blacksmith.dashboard import LIVE_HTML; Path("/tmp/blacksmith-live-probe.html").write_text(LIVE_HTML)'
node tests/dashboard_activity_probe.mjs /tmp/blacksmith-live-probe.html
```

Node is a verification tool for this probe, not a Blacksmith runtime/pytest
dependency. The ordinary dashboard endpoint/SSE regression tests remain in pytest.
Offline transports and this probe establish no live provider billing, remote
publication, Docker ownership or general heavy-PRD readiness.
