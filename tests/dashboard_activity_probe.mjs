// Standalone offline behavior regression: run against freshly exported dashboard.LIVE_HTML.
// Node is a verification tool here, not a Blacksmith runtime or pytest dependency.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync(process.argv[2], "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(match, "actual served live HTML has no inline program");
function element() {
  const children = new Map();
  return { dataset: {}, innerHTML: "", textContent: "", scrollTop: 0, scrollHeight: 10,
    classList: { add() {}, remove() {} }, addEventListener() {}, appendChild() {},
    querySelector(key) {
      if (!children.has(key)) children.set(key, element());
      return children.get(key);
    }, content: { firstElementChild: { cloneNode: element } } };
}
const mounts = new Map();
const context = vm.createContext({ assert, console,
  document: { getElementById(key) {
    if (!mounts.has(key)) mounts.set(key, element());
    return mounts.get(key);
  } },
  EventSource: class { close() {} },
  fetch: () => new Promise(() => {}), setInterval() {},
});
vm.runInContext(match[1], context);
vm.runInContext(`
const entry = ensureThread("parallel");
currentPanelThread = "parallel";
let seq = 0;
function frame(kind, payload, ts = seq + 1) {
  handleEvent("parallel", {seq: seq++, kind, payload, ts});
}
frame("node_start", {node: "execute_unit", task_id: "A", unit_id: "unit-A"}, 1);
frame("node_start", {node: "execute_unit", task_id: "B", unit_id: "unit-B"}, 2);
frame("node_activity", {node: "implement", task_id: "child-A", parent_task_id: "A",
  activity: "turn", turn: 1});
frame("node_activity", {node: "implement", task_id: "child-B", parent_task_id: "B",
  activity: "tool_use", tool: "Write"});
assert.equal(entry.steps[0].turns, 1);
assert.equal(entry.steps[1].turns, 0);
assert.equal(entry.steps[0].tools.size, 0);
assert.equal(entry.steps[1].tools.get("Write"), 1);
frame("node_end", {node: "execute_unit", task_id: "A", duration: 3}, 4);
assert.equal(entry.steps[0].endTs, 4);
assert.equal(entry.steps[1].endTs, null);
assert.equal(entry.nodeStartTs, 2, "ending A must leave still-active B in the header");
assert.equal((document.getElementById("run-panel-body").innerHTML.match(/step active/g) || []).length, 1);
frame("node_activity", {node: "execute_unit", task_id: "unknown", activity: "turn", turn: 99});
assert.equal(entry.steps[1].turns, 0, "unknown task cannot fall back to the current sibling");
for (let i = 0; i < 45; i++) frame("node_activity", {task_id: "child-B", parent_task_id: "B",
  activity: "finding", finding: {severity: "blocking", file: "file-" + i,
    detail: "<script>bad()</script>" + "D".repeat(5000)}});
frame("node_activity", {task_id: "child-B", parent_task_id: "B",
  activity: "findings_omitted", count: 30});
const findingHtml = entry.el.querySelector(".findings").innerHTML;
assert.ok(findingHtml.includes("55 more findings omitted"));
assert.ok(findingHtml.includes("file-19"));
assert.ok(!findingHtml.includes("file-20"));
assert.ok(findingHtml.includes("characters omitted"));
assert.ok(!findingHtml.includes("<script>"));
assert.ok(findingHtml.includes("&lt;script&gt;bad()&lt;/script&gt;"));
assert.equal(entry.findings.length, 45, "display cap must not delete observation history");
assert.ok(entry.findings[44].detail.includes("<script>bad()</script>"));
for (let i = 0; i < 105; i++) frame("unit_result", {unit_id: "unit-" + i, cost_usd: .01});
assert.equal(entry.units.size, 105);
assert.ok(entry.el.querySelector(".unit-costs tbody").innerHTML.includes("5 more units omitted"));
assert.ok(document.getElementById("run-panel-body").innerHTML.includes("5 more units omitted"));
frame("node_end", {node: "execute_unit", task_id: "B"}, 5);
for (let i = 0; i < 230; i++) {
  frame("node_start", {node: "phase-" + i, task_id: "task-" + i});
  frame("node_end", {node: "phase-" + i, task_id: "task-" + i});
}
const panel = document.getElementById("run-panel-body").innerHTML;
assert.equal(entry.steps.length, 232);
assert.ok(panel.includes("32 earlier steps omitted"));
assert.equal((panel.match(/step-head/g) || []).length, 200);
assert.ok(panel.includes("55 more findings omitted"));
frame("node_start", {node: "unfinished", task_id: "unfinished"});
frame("run_status", {status: "halted"});
assert.ok(document.getElementById("run-panel-body").innerHTML.includes("unfinished (unfinished)"));
const before = entry.steps.length;
handleEvent("parallel", {seq: 0, kind: "node_start", payload: {node: "replayed"}, ts: 1});
assert.equal(entry.steps.length, before, "replay deduplication must remain intact");
console.log("PASS: actual inline JS task association, concurrent completion, bounded findings/history, " +
  "full observation retention, unfinished status, and replay deduplication");
`, context);
