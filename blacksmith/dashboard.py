"""Localhost read-only JSON API over the metrics store (WU-DASHBOARD-SERVER).

A small stdlib ``http.server`` that exposes the local metrics SQLite sink
(``[metrics] db_path``) as JSON for a dashboard. It is strictly READ-ONLY: it opens the
metrics DB in SQLite read-only mode and reads through the existing
``metrics.list_runs`` / ``metrics.get_run`` helpers. It never writes the metrics DB,
never mutates run state, and never runs the graph.

Safety properties (inviolable):

- LOCALHOST ONLY — the server binds ``127.0.0.1`` (never ``0.0.0.0``); runs contain
  prompts and code, so no auth and no CORS are added because it is never network-exposed.
- OFFLINE — no runtime network dependency; this module serves JSON only and fetches
  nothing from any external CDN.

Endpoints (GET only):

- ``GET /`` -> a single self-contained HTML page (inline CSS + vanilla JS, no external
  CDN) that polls the two JSON endpoints below and renders the dashboard client-side.
- ``GET /live`` -> a single self-contained HTML page (WU-LIVE-UI) rendering the fleet of
  in-flight runs: it polls ``/api/runs/active`` for the fleet list and opens one
  ``EventSource("/live/<thread_id>")`` per active run to render that run's current node
  (highlighted, with elapsed), per-unit build state/cost as it ticks, and reviewer
  findings as they arrive, plus a drill-in to one run's full node/unit timeline. Same
  inline-only, no-CDN, purely-observational contract as ``GET /``.
- ``GET /api/runs`` -> JSON list of run rows, most-recent first; optional ``?limit=``.
- ``GET /api/runs/<thread_id>`` -> JSON ``{"run": <row|null>, "units": [<row>, ...]}``.
- ``GET /api/runs/active`` -> JSON list of thread_ids with recent activity in the
  ADDITIVE live-events sink (WU-RUN-EVENTS, ``[live] db_path``), each with its latest
  status and last-event ts (WU-LIVE-SERVER).
- ``GET /live/<thread_id>`` -> a ``text/event-stream`` (SSE) response that replays that
  thread's live events in seq order as ``data:`` frames, then polls a bounded number of
  times for newly-appended ones (WU-LIVE-SERVER). Purely additive OBSERVATION over the
  live sink: it only reads, never writes, and never influences the graph.
- any non-GET method -> ``405 Method Not Allowed``.

An empty or absent metrics store yields ``/api/runs == []`` (not an error); the page then
renders a friendly "no runs recorded yet" state. Likewise an empty/absent/disabled live
sink yields ``/api/runs/active == []`` and a ``/live/<thread_id>`` stream with no frames —
never an error, exactly like the metrics sink's best-effort semantics.
"""

from __future__ import annotations

import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from blacksmith.events import RUN_STATUS, RunEvent, read_events
from blacksmith.metrics import get_run, list_runs

# Inviolable: bind loopback only. Runs contain prompts and code, so the server is never
# network-exposed and therefore carries no auth.
HOST = "127.0.0.1"

# A dashboard lists more history than the terse CLI table; still bounded so a huge store
# can't render an unbounded response.
DEFAULT_LIMIT = 100

# How often (ms) the page re-polls /api/runs. Light interval so an idle dashboard left open
# stays current without hammering the read-only API.
REFRESH_MS = 5000

# A run with no live event for this long is treated as no longer in-flight and dropped from
# /api/runs/active — a backstop for a run killed before it could emit its terminal run_status
# (a concluded run is dropped immediately on its run_status, regardless of this). Generous
# vs the gap between a run's events (intra-node activity streams every few seconds; even a
# quiet node boundary is well under this).
ACTIVE_WINDOW_S = 900

# GET /live/<thread_id>: after replaying whatever is already in the sink, poll this many
# times (each separated by SSE_POLL_INTERVAL_S) for newly-appended events before closing
# the stream. Bounded — never an infinite loop — so both a real client and a test that
# reads the response to completion always get a stream that terminates.
SSE_POLL_INTERVAL_S = 0.02
SSE_MAX_EMPTY_POLLS = 5

# A single self-contained page: inline CSS + vanilla JS only. It MUST NOT reference any
# external CDN — the air-gapped / sandbox case has no network at runtime, and the served
# page fetches only the same-origin /api/runs and /api/runs/<id> JSON endpoints.
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>blacksmith dashboard</title>
<style>
  :root {
    --bg: #0f1115; --panel: #181b22; --line: #272b35; --fg: #e6e8ec;
    --muted: #99a0ad; --accent: #6ea8fe; --ok: #4ec27e; --bad: #e3685f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  header { padding: 16px 24px; border-bottom: 1px solid var(--line); }
  header h1 { margin: 0; font-size: 18px; }
  header .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  section { margin-bottom: 28px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted); margin: 0 0 12px; }
  #summary-cards { display: grid; gap: 12px;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .card { background: var(--panel); border: 1px solid var(--line);
    border-radius: 8px; padding: 14px 16px; }
  .card .label { color: var(--muted); font-size: 12px; }
  .card .value { font-size: 22px; font-weight: 600; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; background: var(--panel);
    border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); }
  th { cursor: pointer; user-select: none; color: var(--muted); font-size: 12px; }
  th:hover { color: var(--fg); }
  tbody tr { cursor: pointer; }
  tbody tr:hover { background: #1f232c; }
  .status-ok { color: var(--ok); }
  .status-bad { color: var(--bad); }
  a { color: var(--accent); }
  .trend { display: inline-block; margin-right: 32px; }
  .trend .label { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
  .empty { background: var(--panel); border: 1px dashed var(--line);
    border-radius: 8px; padding: 48px; text-align: center; color: var(--muted); }
  #run-detail { background: var(--panel); border: 1px solid var(--line);
    border-radius: 8px; padding: 16px; }
  #run-detail.hidden { display: none; }
  .close { float: right; cursor: pointer; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>blacksmith dashboard</h1>
  <div class="meta">read-only metrics &middot; <span id="last-updated">loading&hellip;</span></div>
</header>
<main>
  <!-- mount: summary cards (success rate, total + avg cost, avg cache-hit, avg duration) -->
  <section id="summary"><h2>Summary</h2><div id="summary-cards"></div></section>
  <!-- mount: trends over time (cost-per-run + success-rate sparklines, inline SVG) -->
  <section id="trends-section"><h2>Trends</h2><div id="trends"></div></section>
  <!-- mount: sortable runs table -->
  <section id="runs-section"><h2>Runs</h2><div id="runs-table"></div></section>
  <!-- mount: per-run drill-down (unit rows) -->
  <section id="detail-section"><div id="run-detail" class="hidden"></div></section>
  <!-- mount: friendly empty state -->
  <div id="empty-state" class="empty hidden">No runs recorded yet.</div>
</main>
<script>
"use strict";
// Same-origin JSON only; never fetches an external CDN (offline / air-gapped safe).
const RUNS_URL = "/api/runs";
const REFRESH_MS = __REFRESH_MS__;
let state = { runs: [], sortKey: "ended_at", sortDir: -1 };

const COLUMNS = [
  { key: "status",        label: "Status" },
  { key: "total_cost",    label: "Cost" },
  { key: "cache_hit_rate", label: "Cache hit" },
  { key: "duration_s",    label: "Duration" },
  { key: "units_count",   label: "Units" },
  { key: "pr_url",        label: "PR" },
];

const usd = (n) => "$" + (Number(n) || 0).toFixed(2);
const pct = (n) => (100 * (Number(n) || 0)).toFixed(0) + "%";
const secs = (n) => (Number(n) || 0).toFixed(1) + "s";

async function getJSON(url) {
  const resp = await fetch(url, { headers: { "Accept": "application/json" } });
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

function renderSummary(runs) {
  const el = document.getElementById("summary-cards");
  const n = runs.length;
  const successes = runs.filter((r) => r.success).length;
  const totalCost = runs.reduce((a, r) => a + (Number(r.total_cost) || 0), 0);
  const avg = (f) => (n ? runs.reduce((a, r) => a + (Number(r[f]) || 0), 0) / n : 0);
  const cards = [
    ["Success rate", n ? pct(successes / n) : "—"],
    ["Total cost", usd(totalCost)],
    ["Avg cost / run", n ? usd(totalCost / n) : "—"],
    ["Avg cache hit", n ? pct(avg("cache_hit_rate")) : "—"],
    ["Avg duration", n ? secs(avg("duration_s")) : "—"],
  ];
  el.innerHTML = cards.map(([label, value]) =>
    `<div class="card"><div class="label">${label}</div>` +
    `<div class="value">${value}</div></div>`).join("");
}

// Inline SVG sparkline — no chart library, no CDN.
function sparkline(values, color) {
  const w = 220, h = 48, pad = 4;
  if (!values.length) return `<svg width="${w}" height="${h}"></svg>`;
  const max = Math.max(...values, 0.0001), min = Math.min(...values, 0);
  const span = (max - min) || 1;
  const step = values.length > 1 ? (w - 2 * pad) / (values.length - 1) : 0;
  const pts = values.map((v, i) => {
    const x = pad + i * step;
    const y = h - pad - ((v - min) / span) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg width="${w}" height="${h}" role="img">` +
    `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/></svg>`;
}

function renderTrends(runs) {
  // Oldest -> newest so the line reads left to right.
  const chrono = runs.slice().reverse();
  const costs = chrono.map((r) => Number(r.total_cost) || 0);
  const success = chrono.map((r) => (r.success ? 1 : 0));
  document.getElementById("trends").innerHTML =
    `<div class="trend"><div class="label">Cost per run</div>` +
    `${sparkline(costs, "#6ea8fe")}</div>` +
    `<div class="trend"><div class="label">Success rate</div>` +
    `${sparkline(success, "#4ec27e")}</div>`;
}

function sortRuns(runs) {
  const k = state.sortKey, dir = state.sortDir;
  return runs.slice().sort((a, b) => {
    const av = a[k], bv = b[k];
    if (av == null) return 1;
    if (bv == null) return -1;
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

function statusCell(r) {
  const cls = r.success ? "status-ok" : "status-bad";
  return `<span class="${cls}">${r.status || "?"}</span>`;
}

function prCell(r) {
  if (!r.pr_url) return "—";
  return `<a href="${r.pr_url}" target="_blank" rel="noopener">PR</a>`;
}

function renderTable(runs) {
  const head = COLUMNS.map((c) => {
    const arrow = c.key === state.sortKey ? (state.sortDir < 0 ? " ▾" : " ▴") : "";
    return `<th data-key="${c.key}">${c.label}${arrow}</th>`;
  }).join("");
  const body = sortRuns(runs).map((r) =>
    `<tr data-thread="${r.thread_id}">` +
    `<td>${statusCell(r)}</td>` +
    `<td>${usd(r.total_cost)}</td>` +
    `<td>${pct(r.cache_hit_rate)}</td>` +
    `<td>${secs(r.duration_s)}</td>` +
    `<td>${r.units_count != null ? r.units_count : 0}</td>` +
    `<td>${prCell(r)}</td>` +
    `</tr>`).join("");
  const el = document.getElementById("runs-table");
  el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  el.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
    const key = th.getAttribute("data-key");
    if (state.sortKey === key) state.sortDir *= -1;
    else { state.sortKey = key; state.sortDir = -1; }
    renderTable(state.runs);
  }));
  el.querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click",
    () => showDetail(tr.getAttribute("data-thread"))));
}

async function showDetail(threadId) {
  const detail = document.getElementById("run-detail");
  detail.classList.remove("hidden");
  detail.innerHTML = "Loading…";
  try {
    const data = await getJSON(RUNS_URL + "/" + encodeURIComponent(threadId));
    const units = data.units || [];
    const rows = units.map((u) =>
      `<tr><td>${u.unit_id || ""}</td><td>${u.title || ""}</td>` +
      `<td>${u.gate_result || ""}</td><td>${usd(u.cost)}</td>` +
      `<td>${u.turns != null ? u.turns : 0}</td>` +
      `<td>${u.files_count != null ? u.files_count : 0}</td></tr>`).join("");
    detail.innerHTML =
      `<span class="close">✕</span><h2>Run ${threadId}</h2>` +
      (units.length
        ? `<table><thead><tr><th>Unit</th><th>Title</th><th>Gate</th>` +
          `<th>Cost</th><th>Turns</th><th>Files</th></tr></thead>` +
          `<tbody>${rows}</tbody></table>`
        : `<div class="empty">No unit rows for this run.</div>`);
    detail.querySelector(".close").addEventListener("click",
      () => detail.classList.add("hidden"));
  } catch (e) {
    detail.innerHTML = `<span class="close">✕</span>Failed to load run: ${e}`;
    detail.querySelector(".close").addEventListener("click",
      () => detail.classList.add("hidden"));
  }
}

function render(runs) {
  state.runs = runs;
  const empty = document.getElementById("empty-state");
  const sections = ["summary", "trends-section", "runs-section"];
  if (!runs.length) {
    empty.classList.remove("hidden");
    sections.forEach((id) => { document.getElementById(id).style.display = "none"; });
    return;
  }
  empty.classList.add("hidden");
  sections.forEach((id) => { document.getElementById(id).style.display = ""; });
  renderSummary(runs);
  renderTrends(runs);
  renderTable(runs);
}

async function refresh() {
  try {
    const runs = await getJSON(RUNS_URL);
    render(Array.isArray(runs) ? runs : []);
    document.getElementById("last-updated").textContent =
      "updated " + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById("last-updated").textContent = "error: " + e;
  }
}

refresh();
// Auto-refresh on a light interval so a dashboard left open stays current.
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
""".replace("__REFRESH_MS__", str(REFRESH_MS))


# The fleet page: a single self-contained page (inline CSS + vanilla JS only, no external
# CDN) that renders in-flight runs. It consumes the two READ-ONLY live endpoints already
# served by this module -- ``/api/runs/active`` (polled on the same light interval as the
# main dashboard) and one ``EventSource("/live/<thread_id>")`` per active run -- and is
# PURELY OBSERVATIONAL: it never writes anything back and a slow/failed fetch just leaves
# the page showing stale data until the next tick.
LIVE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>blacksmith live</title>
<style>
  :root {
    --bg: #0f1115; --panel: #181b22; --line: #272b35; --fg: #e6e8ec;
    --muted: #99a0ad; --accent: #6ea8fe; --ok: #4ec27e; --bad: #e3685f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  header { padding: 16px 24px; border-bottom: 1px solid var(--line); }
  header h1 { margin: 0; font-size: 18px; }
  header .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  section { margin-bottom: 28px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted); margin: 0 0 12px; }
  .hidden { display: none; }
  #fleet { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  .fleet-run { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
    padding: 14px 16px; cursor: pointer; }
  .fleet-run:hover { border-color: var(--accent); }
  .fleet-run-header { display: flex; justify-content: space-between; align-items: baseline; }
  .fleet-run-header .thread-id { font-weight: 600; }
  .fleet-run-header .current-node { color: var(--accent); }
  .fleet-run-header .elapsed { color: var(--muted); font-size: 12px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--line); }
  .findings { margin: 8px 0 0; padding-left: 18px; color: var(--muted); font-size: 12px; }
  .empty { background: var(--panel); border: 1px dashed var(--line);
    border-radius: 8px; padding: 48px; text-align: center; color: var(--muted); }
  #run-panel { background: var(--panel); border: 1px solid var(--line);
    border-radius: 8px; padding: 16px; display: flex; flex-direction: column;
    max-height: 78vh; }
  #run-panel-body { overflow-y: auto; }
  .close { float: right; cursor: pointer; color: var(--muted); }
  .run-status { font-weight: 600; margin: 2px 0 16px; }
  .run-status.running { color: var(--accent); }
  .run-status.done { color: var(--ok); }
  .run-status.halted { color: var(--bad); }
  .run-status a { color: var(--accent); }
  h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted); margin: 20px 0 8px; }
  .step { border-left: 2px solid var(--line); padding: 8px 0 8px 14px; margin-bottom: 4px; }
  .step.active { border-left-color: var(--accent); background: rgba(110,168,254,.06); }
  .step.done { border-left-color: var(--ok); }
  .step-head { display: flex; align-items: baseline; gap: 8px; }
  .step-node { font-weight: 600; }
  .step-dur { margin-left: auto; color: var(--muted); font-size: 12px; }
  .step-sub { color: var(--muted); font-size: 12px; margin-top: 3px; }
  .chips { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { background: var(--bg); border: 1px solid var(--line); border-radius: 999px;
    padding: 1px 9px; font-size: 12px; color: var(--muted); }
  .step-live { margin-top: 7px; font-size: 12px; color: var(--accent); }
  .badge { border-radius: 4px; padding: 0 6px; font-size: 11px; text-transform: uppercase; }
  .badge.passed { color: var(--ok); }
  .badge.escalated { color: #d6a75e; }
</style>
</head>
<body>
<header>
  <h1>blacksmith live</h1>
  <div class="meta">fleet of in-flight runs &middot; observation only, never mutates the graph</div>
</header>
<main>
  <!-- mount: fleet of active runs, one card per thread_id -->
  <section id="fleet-section">
    <h2>Fleet</h2>
    <div id="fleet"></div>
    <div id="fleet-empty" class="empty hidden">No active runs.</div>
  </section>
  <!-- per-run mount points: cloned once per active thread_id into #fleet -->
  <template id="fleet-run-template">
    <div class="fleet-run">
      <div class="fleet-run-header">
        <span class="thread-id"></span>
        <span class="current-node">—</span>
        <span class="elapsed">0s</span>
      </div>
      <table class="unit-costs"><tbody></tbody></table>
      <ul class="findings"></ul>
    </div>
  </template>
  <!-- mount: drill-in to one run's full node/unit timeline -->
  <section id="run-panel-section">
    <div id="run-panel" class="hidden">
      <span class="close" id="run-panel-close">✕</span>
      <div id="run-panel-body"></div>
    </div>
  </section>
</main>
<script>
"use strict";
// Same-origin only: the active-runs poll and every EventSource below hit this same host,
// never an external CDN (offline / air-gapped safe).
const FLEET_URL = "/api/runs/active";
const REFRESH_MS = __REFRESH_MS__;
const usd = (n) => n == null ? "—" : "$" + Number(n).toFixed(2);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDur = (s) => (s == null ? 0 : s).toFixed(1) + "s";

// Each run is a sequence of STEPS (one per node run — a node repeats across the recovery /
// review / fan-out loops, so each node_start opens a fresh step). Within a step we roll the
// raw activity firehose up into counts + a per-tool tally, and keep the latest activity line
// for a live "what's it doing right now" indicator.
const threads = new Map();
let currentPanelThread = null;
let panelStick = true; // auto-scroll the panel to the newest activity unless the user scrolls up

function ensureThread(threadId) {
  if (threads.has(threadId)) return threads.get(threadId);
  const tpl = document.getElementById("fleet-run-template");
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.dataset.thread = threadId;
  el.querySelector(".thread-id").textContent = threadId;
  el.addEventListener("click", () => showRunPanel(threadId));
  document.getElementById("fleet").appendChild(el);

  const entry = {
    el, node: null, nodeStartTs: null, lastSeq: -1,
    steps: [], units: new Map(), findings: [], status: "running", prUrl: null,
    // Live wiring: one SSE connection per active run, straight to that thread's stream.
    es: new EventSource("/live/" + encodeURIComponent(threadId)),
  };
  entry.es.onmessage = (evt) => handleEvent(threadId, JSON.parse(evt.data));
  threads.set(threadId, entry);
  return entry;
}

function currentStep(entry) { return entry.steps[entry.steps.length - 1] || null; }

function handleEvent(threadId, frame) {
  const entry = threads.get(threadId);
  if (!entry) return;
  // De-dupe replays: the SSE endpoint re-sends the whole thread from seq 0 on every
  // (re)connect, and EventSource auto-reconnects after the server's bounded poll window —
  // so without this the entire timeline is re-ingested and every step is double-counted.
  if (frame.seq != null) {
    if (frame.seq <= entry.lastSeq) return;
    entry.lastSeq = frame.seq;
  }
  const payload = frame.payload || {};
  if (frame.kind === "node_start") {
    entry.node = payload.node;
    entry.nodeStartTs = frame.ts;
    entry.steps.push({ node: payload.node, startTs: frame.ts, endTs: null, duration: null,
      turns: 0, tools: new Map(), last: null });
  } else if (frame.kind === "node_end") {
    const s = currentStep(entry);
    if (s && s.node === payload.node) {
      s.endTs = frame.ts;
      s.duration = payload.duration != null ? payload.duration : frame.ts - s.startTs;
    }
  } else if (frame.kind === "node_activity") {
    const s = currentStep(entry);
    if (payload.activity === "finding") {
      entry.findings.push(payload.finding || {});
      if (s) s.last = "reviewer flagged " + ((payload.finding || {}).file || "an issue");
    } else if (s && payload.activity === "tool_use") {
      s.tools.set(payload.tool, (s.tools.get(payload.tool) || 0) + 1);
      s.last = payload.tool;
    } else if (s && payload.activity === "turn") {
      s.turns += 1;
      s.last = "turn " + (payload.turn == null ? s.turns : payload.turn);
    }
  } else if (frame.kind === "unit_result") {
    entry.units.set(payload.unit_id, payload);
  } else if (frame.kind === "run_status") {
    entry.status = payload.status;
    entry.prUrl = payload.pr_url || null;
    entry.es.close();
  }
  renderThread(threadId);
}

function renderThread(threadId) {
  const entry = threads.get(threadId);
  if (!entry) return;
  entry.el.querySelector(".current-node").textContent =
    entry.status === "running" ? (entry.node || "—") : entry.status;
  const elapsed = entry.nodeStartTs ? Math.max(0, Date.now() / 1000 - entry.nodeStartTs) : 0;
  entry.el.querySelector(".elapsed").textContent = elapsed.toFixed(0) + "s";
  entry.el.querySelector(".unit-costs tbody").innerHTML =
    Array.from(entry.units.values()).map((u) =>
      `<tr><td>${esc(u.unit_id)}</td><td>${usd(u.cost_usd)}</td></tr>`).join("");
  entry.el.querySelector(".findings").innerHTML = entry.findings.map((f) =>
    `<li>${esc(f.severity || "?")}: ${esc(f.file)} — ${esc(f.detail)}</li>`).join("");
  if (currentPanelThread === threadId) renderRunPanel(threadId);
}

function showRunPanel(threadId) {
  currentPanelThread = threadId;
  panelStick = true;
  renderRunPanel(threadId);
  document.getElementById("run-panel").classList.remove("hidden");
}

function renderStep(entry, s, isLast, running, nowS) {
  const active = running && isLast && s.endTs == null;
  const dur = s.duration != null ? s.duration : (active ? nowS - s.startTs : 0);
  const toolCount = Array.from(s.tools.values()).reduce((a, b) => a + b, 0);
  const chips = Array.from(s.tools.entries())
    .map(([t, c]) => `<span class="chip">${esc(t)} ×${c}</span>`).join("");
  const parts = [];
  if (s.turns) parts.push(s.turns + " turn" + (s.turns === 1 ? "" : "s"));
  if (toolCount) parts.push(toolCount + " tool call" + (toolCount === 1 ? "" : "s"));
  const sub = parts.length ? `<div class="step-sub">${parts.join(" · ")}</div>` : "";
  const live = active && s.last ? `<div class="step-live">▸ ${esc(s.last)}</div>` : "";
  return `<div class="step ${active ? "active" : "done"}">
      <div class="step-head"><span>${active ? "⚙" : "✓"}</span>` +
      `<span class="step-node">${esc(s.node)}</span>` +
      `<span class="step-dur">${fmtDur(dur)}</span></div>
      ${sub}${chips ? `<div class="chips">${chips}</div>` : ""}${live}</div>`;
}

function renderRunPanel(threadId) {
  const entry = threads.get(threadId);
  const body = document.getElementById("run-panel-body");
  if (!entry) { body.innerHTML = ""; return; }
  const running = entry.status === "running";
  const nowS = Date.now() / 1000;

  let banner;
  if (running) {
    banner = `<div class="run-status running">● running · ${esc(entry.node || "starting")}</div>`;
  } else if (entry.status === "done") {
    const pr = entry.prUrl
      ? ` · <a href="${esc(entry.prUrl)}" target="_blank" rel="noreferrer">PR</a>` : "";
    banner = `<div class="run-status done">✓ done${pr}</div>`;
  } else {
    banner = `<div class="run-status halted">✕ ${esc(entry.status || "halted")}</div>`;
  }

  const steps = entry.steps
    .map((s, i) => renderStep(entry, s, i === entry.steps.length - 1, running, nowS)).join("");

  const unitRows = Array.from(entry.units.values()).map((u) =>
    `<tr><td>${esc(u.unit_id)}</td>` +
    `<td><span class="badge ${esc(u.gate_result)}">${esc(u.gate_result || "")}</span></td>` +
    `<td>${usd(u.cost_usd)}</td></tr>`).join("");
  const units = unitRows ? `<h3>Units</h3><table><tbody>${unitRows}</tbody></table>` : "";

  const findings = entry.findings.length
    ? `<h3>Reviewer findings</h3><ul class="findings">` + entry.findings.map((f) =>
        `<li>${esc(f.severity || "?")}: ${esc(f.file)} — ${esc(f.detail)}</li>`).join("") + `</ul>`
    : "";

  body.innerHTML = `<h2>Run ${esc(threadId)}</h2>${banner}` +
    `<h3>Steps</h3>${steps || "<div class='step-sub'>waiting…</div>"}${units}${findings}`;
  if (panelStick) body.scrollTop = body.scrollHeight; // stick to the newest activity
}

async function refreshFleet() {
  try {
    const resp = await fetch(FLEET_URL, { headers: { "Accept": "application/json" } });
    const active = resp.ok ? await resp.json() : [];
    const fleet = document.getElementById("fleet");
    const empty = document.getElementById("fleet-empty");
    if (Array.isArray(active) && active.length) {
      empty.classList.add("hidden");
      fleet.classList.remove("hidden");
      active.forEach((row) => ensureThread(row.thread_id));
      // Convenience: with a single run and nothing open, auto-open its detail panel so you
      // don't have to click to start watching.
      if (active.length === 1 && currentPanelThread === null) showRunPanel(active[0].thread_id);
    } else if (threads.size === 0) {
      empty.classList.remove("hidden");
      fleet.classList.add("hidden");
    }
  } catch (e) {
    // Best-effort polling: a transient fetch failure just retries on the next tick.
  }
}

// Auto-scroll that yields to the user: stick to the bottom, but stop if they scroll up, and
// re-engage when they scroll back down.
document.getElementById("run-panel-body").addEventListener("scroll", (e) => {
  const b = e.target;
  panelStick = b.scrollHeight - b.scrollTop - b.clientHeight < 40;
});

document.getElementById("run-panel-close").addEventListener("click", () => {
  document.getElementById("run-panel").classList.add("hidden");
  currentPanelThread = null;
});

refreshFleet();
setInterval(refreshFleet, REFRESH_MS);
setInterval(() => threads.forEach((_entry, id) => renderThread(id)), 1000);
</script>
</body>
</html>
""".replace("__REFRESH_MS__", str(REFRESH_MS))


def _open_ro(db_path: str | Path) -> sqlite3.Connection | None:
    """Open the metrics SQLite in READ-ONLY mode, or ``None`` if the file is absent.

    An absent store is not an error — the API reports it as an empty history. The
    ``file:...?mode=ro`` URI guarantees the connection can never write the metrics DB.
    """
    path = Path(db_path)
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _fetch_runs(db_path: str | Path, limit: int) -> list[dict]:
    """Return recorded run rows (most-recent first), or ``[]`` for an empty/absent store."""
    store = _open_ro(db_path)
    if store is None:
        return []
    try:
        return list_runs(store, limit)
    except sqlite3.OperationalError:
        # A file that exists but has no metrics schema yet reads as empty, not an error.
        return []
    finally:
        store.close()


def _fetch_run(db_path: str | Path, thread_id: str) -> tuple[dict | None, list[dict]]:
    """Return ``(run_row | None, [unit_rows])`` for one thread (read-only)."""
    store = _open_ro(db_path)
    if store is None:
        return None, []
    try:
        return get_run(store, thread_id)
    except sqlite3.OperationalError:
        return None, []
    finally:
        store.close()


def _fetch_active_runs(live_db_path: str | Path | None, *, now: float | None = None) -> list[dict]:
    """Return one row ``{"thread_id", "status", "last_ts"}`` per IN-FLIGHT run in the sink.

    "In-flight" is the live fleet, not run history, so a run is EXCLUDED when either:
    - it has **concluded** — its most recent event is a terminal ``run_status`` (done /
      halted / awaiting_qa); those belong to the metrics dashboard, not the live view; or
    - it has gone **silent** — no event for ``ACTIVE_WINDOW_S`` (e.g. a process killed before
      it could emit ``run_status``), so a dead run doesn't linger in the fleet forever.

    An in-flight run reports ``status="running"``. Sorted by ``last_ts`` descending. Mirrors
    the metrics API: no configured/absent/schema-less sink yields ``[]``, never an error.
    ``now`` is injectable for tests; it defaults to wall-clock time.
    """
    if live_db_path is None:
        return []
    store = _open_ro(live_db_path)
    if store is None:
        return []
    now = time.time() if now is None else now
    try:
        thread_ids = [row[0] for row in store.execute(
            "SELECT DISTINCT thread_id FROM run_events"
        ).fetchall()]
        active = []
        for thread_id in thread_ids:
            events = read_events(store, thread_id)
            if not events:
                continue
            last = events[-1]
            if last.kind == RUN_STATUS:
                continue  # concluded — it lives in run history now, not the live fleet
            if now - last.ts > ACTIVE_WINDOW_S:
                continue  # silent too long — treat as no longer in-flight (e.g. killed)
            active.append({
                "thread_id": thread_id,
                "status": "running",
                "last_ts": last.ts,
            })
        active.sort(key=lambda row: row["last_ts"], reverse=True)
        return active
    except sqlite3.OperationalError:
        # A file that exists but has no live-events schema yet reads as empty.
        return []
    finally:
        store.close()


def _fetch_new_events(
    live_db_path: str | Path | None, thread_id: str, after_seq: int
) -> list[RunEvent]:
    """Return one thread's events with ``seq > after_seq``, in seq order (READ-ONLY)."""
    if live_db_path is None:
        return []
    store = _open_ro(live_db_path)
    if store is None:
        return []
    try:
        return [e for e in read_events(store, thread_id) if e.seq > after_seq]
    except sqlite3.OperationalError:
        return []
    finally:
        store.close()


def _sse_frame(event: RunEvent) -> bytes:
    """Render one ``RunEvent`` as an SSE frame: an ``id:`` line plus a ``data:`` line."""
    body = {
        "thread_id": event.thread_id,
        "seq": event.seq,
        "ts": event.ts,
        "kind": event.kind,
        "payload": event.payload,
    }
    return f"id: {event.seq}\ndata: {json.dumps(body)}\n\n".encode()


def _parse_limit(query: str) -> int:
    """Parse an optional ``?limit=`` integer, falling back to ``DEFAULT_LIMIT``."""
    values = parse_qs(query).get("limit")
    if not values:
        return DEFAULT_LIMIT
    try:
        limit = int(values[0])
    except ValueError:
        return DEFAULT_LIMIT
    return limit if limit > 0 else DEFAULT_LIMIT


def make_handler(
    db_path: str | Path, *, live_db_path: str | Path | None = None
) -> type[BaseHTTPRequestHandler]:
    """Build a GET-only request handler class bound to ``db_path`` (read-only).

    ``live_db_path`` optionally binds the ADDITIVE live-events sink (``[live] db_path``)
    that backs ``/api/runs/active`` and ``/live/<thread_id>``. Omitting it (the default)
    keeps those two routes present but reporting empty — the existing metrics-only
    dashboard routes are unaffected either way.
    """

    class _DashboardHandler(BaseHTTPRequestHandler):
        # Keep the server quiet by default — request logging would leak to stderr.
        def log_message(self, *args, **kwargs):
            pass

        def _send_html(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_live(self, thread_id: str) -> None:
            """Serve ``/live/<thread_id>``: replay then a bounded new-event poll.

            Sends SSE headers, replays whatever is already in the sink for
            ``thread_id`` as ``data:`` frames in seq order, then polls a bounded
            number of times (``SSE_MAX_EMPTY_POLLS``) for newly-appended events
            before closing the stream — additive OBSERVATION only, never writes and
            never affects the graph. Bounded so this always terminates, in tests and
            in real use.
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            last_seq = -1
            empty_polls = 0
            while empty_polls <= SSE_MAX_EMPTY_POLLS:
                events = _fetch_new_events(live_db_path, thread_id, last_seq)
                if not events:
                    empty_polls += 1
                    time.sleep(SSE_POLL_INTERVAL_S)
                    continue
                empty_polls = 0
                try:
                    for event in events:
                        self.wfile.write(_sse_frame(event))
                        last_seq = event.seq
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _method_not_allowed(self) -> None:
            """Any mutating/non-GET method is rejected — this API is read-only."""
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # Every non-GET verb maps to 405 (not 501): the API only reads.
        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_HEAD = _method_not_allowed
        do_OPTIONS = _method_not_allowed

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if not parts:
                self._send_html(INDEX_HTML)
                return
            if parts == ["live"]:
                self._send_html(LIVE_HTML)
                return
            if parts == ["api", "runs"]:
                self._send_json(_fetch_runs(db_path, _parse_limit(parsed.query)))
                return
            if parts == ["api", "runs", "active"]:
                self._send_json(_fetch_active_runs(live_db_path))
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "runs":
                run, units = _fetch_run(db_path, unquote(parts[2]))
                self._send_json({"run": run, "units": units})
                return
            if len(parts) == 2 and parts[0] == "live":
                self._stream_live(unquote(parts[1]))
                return
            self._send_json({"error": "not found"}, status=404)

    return _DashboardHandler


def build_server(
    db_path: str | Path, *, port: int = 0, live_db_path: str | Path | None = None
) -> ThreadingHTTPServer:
    """Build a ``ThreadingHTTPServer`` bound to ``127.0.0.1`` on ``port`` (0 = ephemeral).

    The server is bound on construction; read the chosen port from
    ``server.server_address[1]``. ``live_db_path`` optionally binds the additive live-events
    sink for ``/api/runs/active`` and ``/live/<thread_id>`` (see ``make_handler``).
    """
    return ThreadingHTTPServer((HOST, port), make_handler(db_path, live_db_path=live_db_path))


def serve(
    db_path: str | Path, *, port: int = 0, live_db_path: str | Path | None = None
) -> int:
    """Serve the read-only dashboard (HTML page + JSON API) on ``127.0.0.1``.

    Binds an ephemeral port by default (``port=0``), prints the chosen
    ``http://127.0.0.1:<port>`` URL, and serves forever until Ctrl-C. The root path serves
    a single self-contained page; the ``/api/...`` paths serve JSON. Returns 0.
    """
    server = build_server(db_path, port=port, live_db_path=live_db_path)
    host, chosen_port = server.server_address[0], server.server_address[1]
    print(f"http://{host}:{chosen_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
