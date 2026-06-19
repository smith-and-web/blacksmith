---
contract_version: 1
component: blacksmith
version: v0
primary_target_repo: smith-and-web/kindling
layers:
  py-logic: auto
  integration: human
  cross-cutting: human
untouchables:
  - "No AI, cloud, or subscription code introduced into Kindling's product"
  - "Kindling SQLite migrations"
  - "Kindling brand tokens / brand files (Space Grotesk, Inter, Ember/Flame orange, background colors, lowercase kindling footer rules)"
  - "Cargo.lock (no unsupervised dependency changes)"
  - "The .kindling.yaml sidecar config schema"
  - "blacksmith/contract.py (the PRD contract schema)"
work_units:
  - id: WU-01
    title: "Project scaffold + config loader"
    layers: [py-logic]
    target_modules: ["pyproject.toml", "blacksmith/config.py"]
    test_contract: "pytest: config parses; missing keys raise"
    depends_on: []
  - id: WU-02
    title: "PRD contract schema + validator"
    layers: [py-logic]
    target_modules: ["blacksmith/contract.py"]
    test_contract: "pytest: valid fixture passes, invalid fixture rejected with field-level error"
    depends_on: [WU-01]
  - id: WU-03
    title: "State schema + graph skeleton + checkpointer"
    layers: [py-logic]
    target_modules: ["blacksmith/graph.py", "blacksmith/state.py"]
    test_contract: "pytest: graph compiles; checkpointer persists + resumes a dummy state"
    depends_on: [WU-01]
  - id: WU-04
    title: "Claude Agent SDK executor wrapper"
    layers: [integration]
    target_modules: ["blacksmith/executor.py"]
    test_contract: "mocked unit test for wrapper logic + one manual live smoke call; prompt caching verified on static context"
    depends_on: [WU-01]
  - id: WU-05
    title: "Worktree manager"
    layers: [integration]
    target_modules: ["blacksmith/worktree.py"]
    test_contract: "integration test against a scratch git repo (create + cleanup)"
    depends_on: [WU-01]
  - id: WU-06
    title: "Toolchain-aware test gate"
    layers: [py-logic, integration]
    target_modules: ["blacksmith/gate.py"]
    test_contract: "run against worktree fixtures: passing repo -> pass, failing repo -> fail; reads blacksmith.toml"
    depends_on: [WU-05]
  - id: WU-07
    title: "HITL interrupt nodes (plan + PR)"
    layers: [py-logic]
    target_modules: ["blacksmith/nodes/hitl.py"]
    test_contract: "pytest: graph halts at interrupt, resumes on injected approval"
    depends_on: [WU-03]
  - id: WU-08
    title: "PR node"
    layers: [integration]
    target_modules: ["blacksmith/nodes/pr.py"]
    test_contract: "integration against scratch repo / mocked gh"
    depends_on: [WU-05]
  - id: WU-09
    title: "Plan node"
    layers: [integration]
    target_modules: ["blacksmith/nodes/plan.py"]
    test_contract: "mocked decomposition + manual smoke; selects exactly one unit"
    depends_on: [WU-04]
  - id: WU-10
    title: "Implement node"
    layers: [integration]
    target_modules: ["blacksmith/nodes/implement.py"]
    test_contract: "manual smoke on a trivial unit in a worktree"
    depends_on: [WU-04, WU-05]
  - id: WU-11
    title: "End-to-end wiring (happy path + human-halt-on-fail)"
    layers: [cross-cutting]
    target_modules: ["blacksmith/graph.py", "blacksmith/cli.py"]
    test_contract: "e2e run on a trivial unit: pass -> PR-approval halt; fail -> human_halt"
    depends_on: [WU-01, WU-02, WU-03, WU-04, WU-05, WU-06, WU-07, WU-08, WU-09, WU-10]
---
# blacksmith — Product Requirements Document

| | |
|---|---|
| **Component** | `blacksmith` — agentic development orchestrator |
| **Version** | v0 (MVP spine) |
| **Status** | Draft — pending Josh confirmation on §12 open decisions |
| **Conforms to** | PRD Contract v1 (this PRD is its own first dogfood) |
| **Owner repo** | `smith-and-web/blacksmith` (sibling to Kindling; not public until v0 acceptance criteria pass) |
| **Primary target repo** | `smith-and-web/kindling` |
| **Stack** | Python 3.12 · LangGraph · Claude Agent SDK · SQLite (checkpointer) |

---

## 1. Purpose

`blacksmith` is a LangGraph-based orchestrator that ingests a contract-conforming PRD and drives a single work unit through a development pipeline — plan, implement, test-gate, review, PR — with durable state and human approval gates. It uses the Claude Agent SDK as the per-node execution engine and operates on a target Rust/Tauri repository (Kindling) via git worktrees, `cargo`, and `gh`.

The point of v0 is **not** to ship Kindling features faster. It is to bootstrap a working, inspectable understanding of LangGraph's state-machine / checkpoint / human-in-the-loop (HITL) model — the same class of framework used to orchestrate production release pipelines — using a low-stakes codebase under sole ownership. Kindling is the dogfood; LangGraph fluency is the deliverable.

### Why a framework instead of a CLI script
The four properties below are hard to replicate in a bare CLI loop and are the entire reason for the LangGraph layer. v0 must demonstrate the first three; the fourth arrives in v1.
- **Durable, checkpointed state** — the run can be paused, inspected, and resumed from a known point after a process restart.
- **First-class HITL gates** — execution halts at a real interrupt node and waits for approval with state preserved across the wait.
- **Deterministic conditional routing** — test-gate outcome routes via a graph edge, not a model decision.
- **Failure recovery and audit trail** — bounded retry and a capturable record of every step (v1).

---

## 2. Scope fences

### In bounds (v0)
- A LangGraph graph that runs **exactly one** work unit end to end.
- Happy path: validate PRD → plan → human-approve plan → prepare worktree → implement → test gate → human-approve PR → open PR.
- Failure path: on test-gate failure, **halt to human** (no crash, no auto-retry, no auto-merge).
- SQLite checkpointer with pause/resume across process restart.
- Toolchain-aware test gate (Kindling: `cargo test` + `cargo clippy`; blacksmith self-target: `pytest` + `ruff`).
- Claude calls via the Claude Agent SDK on a **dedicated API key**, with prompt caching on static context and per-node model tiering.

### Out of bounds (deferred — do not build in v0)
- Parallel / multi-unit execution (v1).
- Bounded retry feeding test failures back to the implementer (v1).
- LangSmith tracing / observability (v1).
- `ui`-layer human-QA routing exercised against a real Kindling UI unit (v1 — the *routing branch* is defined in v0 but only the logic path is exercised).
- Cross-model review via Codex `/review` (v2).
- Rollback / multi-repo targeting (v2).
- Any change to Kindling's product behavior beyond the single trivial unit used to validate the pipeline.

---

## 3. Architecture overview

```
                 ┌─────────────────────── blacksmith (Python / LangGraph) ───────────────────────┐
   PRD (md) ───► │ ingest_prd → plan → [HITL: approve_plan] → prepare_worktree → implement →      │
                 │              test_gate ──(pass)──► [HITL: approve_pr] → open_pr → END           │
                 │                  └────(fail)────► human_halt → END                              │
                 │  state: SQLite checkpointer (pause/resume)     executor: Claude Agent SDK       │
                 └───────────────────────────────────────────────────────────────────────────────┘
                                              │ operates on
                                              ▼
                        target repo (smith-and-web/kindling): git worktree · cargo · gh
```

`blacksmith` is Python and operates *on* Kindling's repo; it is never compiled into Kindling and never alters Kindling's product to depend on it. The executor is a thin wrapper around the Claude Agent SDK so each node gets structured returns, the agent loop, subagents, and MCP natively rather than parsing CLI stdout.

---

## 4. The v0 graph

### State schema (`BlacksmithState`)
A typed dict carried through the graph and persisted by the checkpointer:
- `prd`: parsed, validated PRD object
- `work_units`: list of units extracted by the planner
- `selected_unit`: the single unit chosen for v0
- `plan`: planner output (steps, target modules, test contract)
- `worktree_path`: filesystem path of the isolated worktree
- `implementation`: executor result (diff summary, files touched)
- `test_results`: `{passed: bool, output: str, command: str}`
- `pr_url`: created PR URL (or null)
- `approvals`: `{plan: bool, pr: bool}`
- `status`: enum (`pending | awaiting_plan_approval | implementing | testing | awaiting_pr_approval | halted | done`)
- `errors`: list of structured error records

### Nodes
1. **`ingest_prd`** — load the PRD, validate against the contract schema, fail fast with a clear message if non-conforming.
2. **`plan`** — Agent SDK call: decompose the PRD into work units; v0 selects exactly one (lowest in the dependency DAG with no unmet deps).
3. **`approve_plan`** (HITL interrupt) — surface the plan; halt until approval.
4. **`prepare_worktree`** — create an isolated git worktree in the target repo for the unit's branch.
5. **`implement`** — Agent SDK call: implement the selected unit inside the worktree, honoring the constitution and untouchables.
6. **`test_gate`** — run the target repo's configured `test_cmd` + `lint_cmd` in the worktree; record pass/fail.
7. **`approve_pr`** (HITL interrupt) — surface the diff + test results; halt until approval.
8. **`open_pr`** — `gh pr create` with a generated summary; record `pr_url`.
9. **`human_halt`** — terminal node for the failure path; preserves state for inspection.

### Edges
- Linear through `ingest_prd → plan → approve_plan → prepare_worktree → implement → test_gate`.
- **Conditional** at `test_gate`: `passed == true` → `approve_pr → open_pr → END`; `passed == false` → `human_halt → END`.
- The `selected_unit.layer` value also gates routing: `integration` / `ui` units must route through `human_halt`-style human verification rather than the automated gate (defined in v0; only the auto-gated `logic` path is exercised).

### Checkpointer
LangGraph `SqliteSaver` (or equivalent), thread-scoped per run, so `approve_plan` / `approve_pr` interrupts survive a full process restart and resume on injected input.

---

## 5. Toolchain integration requirements

- **Git worktrees** against `smith-and-web/kindling`, one per unit, cleaned up on completion or halt.
- **Test gate is toolchain-aware and per-repo-configurable.** Commands are read from a `blacksmith.toml` in the *target* repo, keyed by layer:
  - Kindling: `test_cmd = "cargo test"`, `lint_cmd = "cargo clippy -- -D warnings"`
  - blacksmith self-target: `test_cmd = "pytest"`, `lint_cmd = "ruff check"`
- **PRs** created on `smith-and-web/kindling` via `gh` (or GitHub API), never auto-merged.
- **No interactive Claude dependency at runtime** — every model call is programmatic through the Agent SDK executor.

---

## 6. Work units (build plan for v0)

Each unit maps to one testable outcome and carries a layer tag, target modules, a test contract, and dependencies. Layer taxonomy is blacksmith's generalization of the contract's `rust-logic | ui | cross-cutting`: here it is **`py-logic`** (auto-gated by pytest/ruff, agent-owned), **`integration`** (touches the live API / real git / gh / cargo — requires human smoke verification), and **`cross-cutting`**. The fixed concept is *auto-gateable vs human-gated*; the vocabulary is per-project — which is itself a portability test of the contract.

| ID | Unit | Layer | Target modules | Test contract | Depends on |
|----|------|-------|----------------|---------------|------------|
| WU-01 | Project scaffold + config loader | py-logic | `pyproject.toml`, `blacksmith/config.py` | pytest: config parses; missing keys raise | — |
| WU-02 | PRD contract schema + validator | py-logic | `blacksmith/contract.py` | pytest: valid fixture passes, invalid fixture rejected with field-level error | WU-01 |
| WU-03 | State schema + graph skeleton + checkpointer | py-logic | `blacksmith/graph.py`, `blacksmith/state.py` | pytest: graph compiles; checkpointer persists + resumes a dummy state | WU-01 |
| WU-04 | Claude Agent SDK executor wrapper | integration | `blacksmith/executor.py` | mocked unit test for wrapper logic + one manual live smoke call; prompt caching verified on static context | WU-01 |
| WU-05 | Worktree manager | integration | `blacksmith/worktree.py` | integration test against a scratch git repo (create + cleanup) | WU-01 |
| WU-06 | Toolchain-aware test gate | py-logic + integration | `blacksmith/gate.py` | run against worktree fixtures: passing repo → pass, failing repo → fail; reads `blacksmith.toml` | WU-05 |
| WU-07 | HITL interrupt nodes (plan + PR) | py-logic | `blacksmith/nodes/hitl.py` | pytest: graph halts at interrupt, resumes on injected approval | WU-03 |
| WU-08 | PR node | integration | `blacksmith/nodes/pr.py` | integration against scratch repo / mocked `gh` | WU-05 |
| WU-09 | Plan node | integration | `blacksmith/nodes/plan.py` | mocked decomposition + manual smoke; selects exactly one unit | WU-04 |
| WU-10 | Implement node | integration | `blacksmith/nodes/implement.py` | manual smoke on a trivial unit in a worktree | WU-04, WU-05 |
| WU-11 | End-to-end wiring (happy path + human-halt-on-fail) | cross-cutting | `blacksmith/graph.py`, `blacksmith/cli.py` | e2e run on a trivial unit: pass → PR-approval halt; fail → human_halt | all above |

---

## 7. Untouchables

Files and behaviors `blacksmith` may **never** modify without explicit human sign-off, enforced as a constitutional rule in the executor's system context and (where feasible) as a pre-edit guard:

- **No AI, cloud, or subscription code introduced into Kindling's product, ever.** This is Kindling's core brand pillar. blacksmith *uses* AI to develop Kindling; it must never make Kindling itself depend on AI/cloud/subscription. An agent "helpfully" adding an AI feature is the single highest-priority failure to prevent.
- Kindling SQLite migrations.
- Kindling brand tokens / brand files (Space Grotesk, Inter, Ember/Flame orange, background colors, lowercase "kindling" footer rules).
- `Cargo.lock` (no unsupervised dependency changes).
- The `.kindling.yaml` sidecar config schema.
- In blacksmith's own repo: `blacksmith/contract.py` (the PRD contract schema) — changes require human review, since it is the interface every future PRD depends on.

---

## 8. Cost & auth model

`blacksmith` is programmatic Claude usage by definition. As of June 15, 2026, Agent SDK / `claude -p` usage meters against a separate monthly credit (~$20 Pro / $100 Max 5x / $200 Max 20x) at standard API rates with no rollover, and stops when exhausted unless overflow billing is enabled. Interactive Claude Code (i.e. building blacksmith by hand in the desktop app) is unaffected and stays on the subscription. Requirements:

- **Dedicated Anthropic API key** for blacksmith — not subscription auth — to isolate and make its spend predictable and decoupled from interactive usage.
- **Prompt caching mandatory** on static context (constitution, PRD, repo map). This is the highest-ROI lever for blacksmith's repetitive profile.
- **Model tiering:** a cheaper model on `plan` / triage, a stronger model on `implement`. Pin **current** model IDs (Opus 4.8 / Sonnet 4.6 / Haiku 4.5 family) — the legacy `claude-*-4-20250514` IDs are retired and will error.
- Verify the current per-plan credit figures and overflow behavior against the official help center before relying on them for budgeting; the figures above are widely reported but should be confirmed in the billing console.

---

## 9. Phasing

- **v0 (this PRD):** single unit, full spine, happy path + human-halt on failure, SQLite checkpointer, HITL at plan + PR, dedicated key + prompt caching + model tiering. Private until acceptance criteria pass.
- **v1:** parallel multi-unit execution (fan-out / fan-in), bounded retry feeding test failures back to `implement` (max N attempts), LangSmith tracing, and the `ui`-layer human-QA routing exercised against a real Kindling UI unit.
- **v2:** optional Codex `/review` cross-model gate before `approve_pr`; richer recovery / rollback points; multi-repo targeting.

---

## 10. Acceptance criteria (v0)

1. **AC-1** — blacksmith validates a conforming PRD and rejects a non-conforming one with a clear, field-level error.
2. **AC-2** — a run persists state to the checkpointer and resumes correctly after a full process restart.
3. **AC-3** — the graph halts at `approve_plan` and proceeds only on injected approval.
4. **AC-4** — for a single `logic` unit, blacksmith creates a worktree, invokes the implement executor, and the test gate runs the target repo's configured commands and reports pass/fail.
5. **AC-5** — on test pass, the graph halts at `approve_pr`; on approval, it opens a PR on the target repo.
6. **AC-6** — on test fail, the graph routes to `human_halt` (no crash, no auto-merge).
7. **AC-7** — no untouchable path is modified; an attempt is blocked or surfaced for sign-off.
8. **AC-8** — every Claude call routes through the dedicated API key with prompt caching enabled on static context.

---

## 11. Dogfood sequence

1. **Hand-build v0** in Claude Code desktop (interactive → subscription → unmetered).
2. **First dogfood run:** feed the working v0 graph *this PRD* to exercise AC-1 and the contract, using a trivial `py-logic` unit from blacksmith's own v1 backlog as the implemented unit — validating the pytest gate, worktree, and `gh` flow on blacksmith's own repo.
3. **Second dogfood run:** a trivial Kindling `rust-logic` unit, to validate the `cargo` / `clippy` gate against the real target toolchain.
4. **Push to GitHub** once AC-1 through AC-8 pass — so early visitors see a working spine, not scaffolding.

---

## 12. Open decisions (confirm or override before build)

1. **Checkpointer backend** — SQLite (simple, local; recommended for v0) vs Postgres (a heavier alternative, overkill here).
2. **HITL surface** — a CLI prompt for plan/PR approval (simplest; recommended for v0) vs a minimal local web view.
3. **API key** — provision a new dedicated key for blacksmith (recommended) vs reuse an existing one.
4. **Target-repo config location** — `blacksmith.toml` committed in the target repo (recommended; keeps toolchain definition next to the code it runs against) vs a central config inside blacksmith.
