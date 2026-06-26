# Authoring blacksmith-ready PRDs

> **Use this as project context.** Drop this file into a Claude Project (as project
> knowledge or instructions). When asked to write or revise a PRD for a build that
> blacksmith will execute, follow this contract exactly. A PRD that violates any
> **HARD rule** below will be rejected by `blacksmith/contract.py` at ingest with a
> field-level error before any work happens.

---

## 1. What "blacksmith-ready" means

blacksmith is a LangGraph orchestrator that ingests **one** PRD and drives its **work
units** — the whole `depends_on` DAG, in dependency order — each through
`plan → implement → test-gate → review → PR`, in an isolated git clone of a target repo.
A "blacksmith-ready" PRD is a single markdown file with
**two parts**, both of which are validated:

1. **YAML frontmatter** — a machine-readable contract, validated by pydantic
   (strict: unknown keys are rejected).
2. **Prose body** — human-readable sections; four specific headings must be present.

The frontmatter is the law. The prose explains intent to the human and seeds the
implementing agent's context.

---

## 2. Frontmatter contract (HARD — machine-validated)

The file **must begin** with a `---` fenced YAML block (the first `---` line is line 1).
Later `---` horizontal rules in the prose are fine; only the first fenced block is parsed.

### Top-level fields (all required)

| Field | Type | Rule |
|---|---|---|
| `contract_version` | int | **Must be `1`.** Anything else is rejected. |
| `component` | string | Name of the thing being built (e.g. `kindling`). |
| `version` | string | e.g. `v0`, `v1`. |
| `primary_target_repo` | string | `owner/repo` blacksmith operates on (e.g. `smith-and-web/kindling`). |
| `layers` | map<string, `auto`\|`human`> | ≥1 entry. Each value is **exactly** `auto` or `human`. |
| `untouchables` | list<string> | ≥1 entry. Free-text rules/paths the agent must never modify. |
| `work_units` | list<WorkUnit> | ≥1 entry. See below. |

### WorkUnit fields

| Field | Type | Rule |
|---|---|---|
| `id` | string | Unique across all units (e.g. `WU-01`). Duplicates are rejected. |
| `title` | string | One-line outcome. |
| `layers` | list<string> | ≥1, and **every name must be declared** in top-level `layers`. |
| `target_modules` | list<string> | ≥1. Real file paths the unit creates/edits, relative to the target repo. |
| `test_contract` | string | Non-empty. How this unit is proven (the gate / smoke check). |
| `depends_on` | list<string> | Optional (default `[]`). Each must be an existing unit `id`. |

### Validator rules (each one rejects the PRD if broken)

- `contract_version` must equal `1`.
- At least one `layers` entry; values restricted to `auto` / `human`.
- At least one `untouchables` entry and at least one `work_units` entry.
- No duplicate work-unit `id`s.
- Every `work_units[].layers` name must be declared in top-level `layers`.
- Every `depends_on` must reference a known unit `id`.
- The `depends_on` graph must be **acyclic** (a cycle is reported with the path).
- **No unknown keys, anywhere.** Both the contract and each work unit use
  `extra="forbid"` — a stray or misspelled key (e.g. `target_module` instead of
  `target_modules`, or an invented `priority:` field) is a hard error. Do **not** add
  fields that aren't in the tables above.

---

## 3. Required prose sections (HARD — presence-checked)

After the frontmatter, the body must contain markdown headings whose text contains
each of these keywords (case-insensitive substring match, any heading level):

- `purpose`
- `scope`
- `untouchables`
- `acceptance`

Numbered/decorated headings satisfy this: `## 1. Purpose`,
`## 2. Scope fences`, `## 7. Untouchables`, and `## 10. Acceptance criteria (v0)`
all match. Everything else in the body is free prose — write whatever helps the human
and the agent (architecture, edges, phasing, cost model, open decisions).

---

## 4. The layer & gating model (this decides what actually happens)

`layers` is a vocabulary **you define per project**; the only fixed concept is
**auto vs human**:

- **`auto`** — the unit is proven by the automated test gate (e.g. `pytest`/`ruff`,
  `cargo test`/`clippy`). On pass the run proceeds to the PR-approval gate; on fail it
  routes to `human_halt`. No human verification step is required for correctness.
- **`human`** — the unit requires human smoke/QA verification (touches a live API,
  real UI, external services). A unit is treated as human-gated if **any** of its
  layers is `human`.

**Consequence for a real run:** blacksmith executes the **whole `work_units` DAG** in
dependency order — independent units at the same level run in parallel, dependent units
in topological order — accumulating every unit's commits onto **one shared branch** that
opens a **single combined PR**. So:

- For a fully **autonomous** end-to-end run (implement → auto gate → one combined PR for
  the whole DAG), make every unit **`auto`** with `test_contract`s the gate can fully
  prove, and order them with `depends_on`.
- A `human`-layer unit **truncates** the DAG when reached: blacksmith builds up to and
  including it, opens a **draft PR** for that built subset, and ends `AWAITING_QA` with
  the branch preserved for manual review (units after the human gate are not built in that
  run). Continuing a DAG *past* a human gate is out of scope.
- A gate failure on any unit halts the run there with no PR, naming the failed unit.

Pick layer names that map cleanly to how things are tested. Typical taxonomies:
`rust-logic: auto`, `py-logic: auto`, `ui: human`, `integration: human`,
`cross-cutting: human`.

---

## 5. Pairing with the target repo's `blacksmith.toml`

The PRD declares *which* layer a unit is; the **target repo's `blacksmith.toml`**
declares *what commands* the gate runs. Keep them consistent.

```toml
# blacksmith.toml — lives in the TARGET repo root, read by the test gate.
test_cmd = "cargo test"
lint_cmd = "cargo clippy -- -D warnings"   # optional
fix_cmd = "cargo fmt --all"                # optional; deterministic auto-fix before the gate

# Optional per-layer overrides — keys should match PRD layer names:
[layers.py-logic]
test_cmd = "uv run pytest"
lint_cmd = "uv run ruff check"
fix_cmd = "uv run ruff format && uv run ruff check --fix"
```

- `test_cmd` is required; `lint_cmd` is optional and only runs if tests pass.
- A unit's layer name can match a `[layers.<name>]` section to override the default
  commands; otherwise the top-level commands run.
- **Commands must work from a fresh clone with no installed deps.** If the project needs
  an environment activated or deps installed, bake that into `setup_cmd`/the command
  (e.g. `uv run pytest`, not bare `pytest`; `npm ci` before `npm test`).
- **`fix_cmd` is optional and auto-fixes mechanical failures so they never burn a model
  retry.** It runs in the worktree right after the agent commits and *before* the gate, and
  any change it makes is `git commit --amend`ed into the unit's commit (so the cherry-picked
  unit is CI-clean). Use it for the formatter/lint-fixer the agent can't reproduce by hand —
  `cargo fmt --all`, `prettier --write`, `ruff format` — then you can safely keep the matching
  `--check` in `lint_cmd`. It's best-effort and self-contained (chain in deps it needs, e.g.
  `npm ci && npm run format`); a failing `fix_cmd` just falls through to the gate.

---

## 6. Untouchables (constitutional rules)

`untouchables` entries are injected into the implementing agent's system context as
hard "never modify without sign-off" rules, and some path patterns are additionally
enforced by a pre-edit guard (writes outside the clone, plus protected globs like
lockfiles, migrations, and the contract schema, are blocked outright). Author them as:

- The single most important product invariant first (for Kindling: *no AI/cloud/
  subscription code in the product, ever*).
- Lockfiles (`Cargo.lock`, etc.) — no unsupervised dependency changes.
- Schema/migration files and any config schema the product depends on.
- Brand/design tokens or other "house style" files that must not drift.

Be specific; these are the guardrails that stop an over-eager agent.

---

## 7. Authoring rules (do / don't)

**Do**
- Keep each work unit to **one testable outcome**. Small units gate cleanly.
- Make `target_modules` point at **real, buildable paths** — the implement step
  halts if it produces no file changes, so vague targets cause dead runs.
- Write `test_contract` as a concrete, checkable statement
  (*"pytest: valid fixture passes, invalid fixture rejected with field-level error"*),
  not a vibe (*"works well"*).
- Order units with a clean DAG; the unit you want built first should be a root.
- Mirror the target repo's real protected files in `untouchables`.

**Don't**
- Don't add frontmatter keys that aren't in §2 — strict validation rejects them.
- Don't reference an undeclared layer or an unknown `depends_on` id.
- Don't create dependency cycles.
- Don't put a `human` layer on a unit you want fully auto-gated — a `human` unit truncates
  the DAG into a draft PR (`AWAITING_QA`) rather than running to a merged-style PR.
- Don't put secrets, API keys, or absolute local paths in the PRD (it may be public).

---

## 8. Copy-paste template (valid against Contract v1)

```markdown
---
contract_version: 1
component: <component-name>
version: v0
primary_target_repo: <owner>/<repo>
layers:
  logic: auto
  integration: human
untouchables:
  - "<the #1 product invariant that must never be violated>"
  - "<lockfile / dependency manifest>"
  - "<schema or migration files>"
work_units:
  - id: WU-01
    title: "<one-line outcome>"
    layers: [logic]
    target_modules: ["path/to/file.ext"]
    test_contract: "<concrete pass/fail check the gate runs>"
    depends_on: []
  - id: WU-02
    title: "<next outcome>"
    layers: [logic]
    target_modules: ["path/to/other.ext"]
    test_contract: "<concrete check>"
    depends_on: [WU-01]
---
# <Component> — Product Requirements Document

## 1. Purpose
<Why this exists; what success looks like.>

## 2. Scope
**In bounds (v0):** <what's built>
**Out of bounds:** <explicitly deferred>

## 3. Untouchables
<Restate and justify the frontmatter untouchables in prose.>

## 4. Acceptance criteria
1. AC-1 — <observable, checkable outcome>
2. AC-2 — <...>
```

---

## 9. Pre-flight checklist (run before handing a PRD to blacksmith)

- [ ] File starts with `---` on line 1; frontmatter is valid YAML.
- [ ] `contract_version: 1`.
- [ ] `component`, `version`, `primary_target_repo` present and meaningful.
- [ ] `layers` has ≥1 entry; every value is `auto` or `human`.
- [ ] `untouchables` has ≥1 entry; mirrors the target repo's real protected paths.
- [ ] `work_units` has ≥1 entry; **no duplicate ids**.
- [ ] Every unit: `id`, `title`, ≥1 `layers` (all declared), ≥1 `target_modules`,
      non-empty `test_contract`.
- [ ] Every `depends_on` points at a real id; the DAG is acyclic.
- [ ] **No extra/unknown frontmatter keys.**
- [ ] Body has headings containing: `purpose`, `scope`, `untouchables`, `acceptance`.
- [ ] The DAG's root unit(s) have `depends_on: []` and the rest are ordered with
      `depends_on` (acyclic). For a fully autonomous run, every unit is `auto` and the
      target repo's `blacksmith.toml` runs commands that actually prove each one.
- [ ] `target_modules` are real paths; no secrets or absolute local paths anywhere.

---

## 10. Appendix — Kindling defaults

When the target is **Kindling** (`smith-and-web/kindling`), start from these:

```yaml
component: kindling
primary_target_repo: smith-and-web/kindling
layers:
  rust-logic: auto      # pure Rust logic, proven by cargo test + clippy
  ui: human             # Tauri/front-end — needs human smoke/QA
  integration: human    # touches real services / filesystem
untouchables:
  - "No AI, cloud, or subscription code introduced into Kindling's product, ever."
  - "Kindling SQLite migrations"
  - "Kindling brand tokens / brand files (Space Grotesk, Inter, Ember/Flame orange,
     background colors, lowercase 'kindling' footer rules)"
  - "Cargo.lock (no unsupervised dependency changes)"
  - "The .kindling.yaml sidecar config schema"
```

Kindling's `blacksmith.toml` (in the Kindling repo):

```toml
test_cmd = "cargo test"
lint_cmd = "cargo fmt --all -- --check && cargo clippy --all-targets -- -D warnings"
fix_cmd = "cargo fmt --all"   # auto-format before the gate, so the fmt --check above never halts on whitespace
```

With `fix_cmd` set you can safely keep `cargo fmt --all -- --check` in `lint_cmd`:
blacksmith runs `cargo fmt --all` and amends the result into the unit's commit before the
gate, so a whitespace-only diff is fixed deterministically (no Sonnet→Opus escalation on
trivia) instead of halting the run. `fix_cmd` must not touch `Cargo.lock` (untouchable #4),
so prefer `cargo fmt` over `cargo clippy --fix` unless you've confirmed the latter leaves the
lockfile alone.

The **prime directive** (untouchable #1) is non-negotiable: blacksmith *uses* AI to
build Kindling, but must **never** make Kindling's product depend on AI, cloud, or
subscription services. Any work unit that would introduce such a dependency is
out of scope by definition — do not author one.
```
