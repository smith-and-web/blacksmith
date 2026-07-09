# Full-codebase review prompt

A reusable prompt for having a senior agentic engineer do a **whole-repo** review of
blacksmith — tuned to the failure *classes* this codebase has actually exhibited
(wired-but-dark features, tests that pass by not reproducing prod, config drift), not a
generic "review my code."

## How to run it

- **For the full review this prompt describes:** launch a fresh agentic session pointed at
  this repo and paste the prompt below. Either a new `claude` (Claude Code) session in the
  repo root, or a `general-purpose` sub-agent. It needs file read, grep, and the ability to
  run `uv run pytest` / `uv run ruff check`. Give it a generous effort/thinking budget — this
  is a deep, cross-cutting sweep, not a quick pass.

- **`/code-review` is NOT the right tool for the whole-repo sweep.** The built-in
  `/code-review` skill reviews the **current git diff** (pending changes on the branch), not
  the entire codebase — so it's for *ongoing work*, not a standing audit. Use it that way:
  run `/code-review high` on a feature branch before merging to catch correctness/cleanup
  issues in that change. To exercise the failure-mode checklist below on a *change*, append a
  note to the `/code-review` invocation, e.g.:

  > `/code-review high` — and specifically verify this change isn't "wired-but-dark": trace
  > any new config-gated feature end-to-end (config → build_graph_for → compile_graph/
  > build_graph node injection → node body → executor build_options), and check any new
  > granted tool is actually steered in a prompt. Flag any test that passes only because a
  > fake hides the real integration (e.g. a fake swallowing **kwargs).

- **Scope reminder:** the prompt below assumes read/verify access to the running code. Have
  the reviewer confirm findings against `file:line` and by running checks — not from memory.

---

## The prompt

```
You are a senior software engineer specializing in agentic systems (LLM-driven
autonomous coding agents) and LangGraph. Do a thorough, adversarial, fresh-eyes review
of the `blacksmith` codebase. Surface real correctness bugs, cross-cutting
inconsistencies, and high-leverage improvements — not style nits.

## What blacksmith is
A LangGraph autonomous coding agent: a Contract-v1 PRD → work-unit DAG → plan → implement
→ deterministic test gate → PR, with human approval gates, per-run throwaway git-clone
isolation, a SQLite checkpointer plus separate metrics / long-term-store / live-events /
transcript sinks, a live web dashboard, recovery loops (gate self-heal, model escalation,
turn-cap continuation), a post-gate model reviewer, codebase indexing (a repo map + a
search_code tool), an optional sandboxed-exec self-verify container, and a `respond`
command that revises an open PR from its review comments. Python, uv, ruff, pytest.

## How to work
- Read broadly before concluding. Start with: blacksmith/graph.py (the graph +
  build_graph/compile_graph), blacksmith/cli.py (build_graph_for, _step, the drive loop,
  the subcommands), blacksmith/nodes/*, blacksmith/executor.py, config.py, state.py, and
  the sinks. Trace at least two full paths end-to-end: a normal PRD run and a `respond` run.
- VERIFY every finding against the code (cite file:line); where practical, confirm by
  running something or writing a scratch check. No speculation — mark each CONFIRMED or
  PLAUSIBLE. Run `uv run pytest` and `uv run ruff check` and note what the tests do and
  do NOT actually exercise.
- Rank by severity (correctness bug > cross-cutting inconsistency > maintainability >
  style). Prefer fewer high-confidence findings over a long noisy list.

## Prioritize these failure modes — this codebase has a track record of each
1. Wired-but-dark features. An opt-in feature is fully built and unit-tested but never
   reaches the running graph. Trace EVERY config-gated feature end-to-end:
   config.<x> → does build_graph_for forward it? → does compile_graph/build_graph inject
   it into the node's _node_with(...)? → does the node body read it? → does the executor's
   build_options forward every option a node passes it? Flag any feature whose flag can be
   set with no effect, and any executor kwarg silently dropped. Then check whether the
   existing wiring-guard tests actually cover every feature or just some.
2. Wired-but-unadopted. A tool is granted (allowed_tools / an MCP server) but no prompt
   steers the agent to use it, so it doesn't. Check every granted tool is actually pushed
   in the relevant prompt.
3. Tests that pass by not reproducing reality. Fakes/harnesses that hide integration gaps
   — a fake that swallows **kwargs (masking a missing parameter), a harness that seeds
   state locally that only exists remotely in prod, a mock that never exercises the real
   failure. For each critical fake, ask: what would break in production that this test
   cannot see?
4. Config → behavior drift. Dead config fields (declared, read nowhere); duplicated or
   parallel config types that should be unified; inconsistent application of the "additive,
   off-by-default, best-effort" pattern across the sinks and opt-in features.
5. State-machine consistency. The per-unit recovery state (self-heal / escalation /
   turn-cap continuation / review-revision counters and flags): are they seeded and reset
   symmetrically per unit? Any state key set-but-never-read, read-but-never-seeded, or a
   reducer-vs-last-write-wins choice that leaks stale or accumulated data across units/runs?
6. Duplication that will drift. Logic mirrored in two places (verdict parsing, prompt
   sections, recovery-reset code) destined to diverge — flag for consolidation.
7. Best-effort discipline. Every observability/additive path (metrics, live events,
   transcripts, memory) must never crash or alter a run. Find any that can raise into the
   graph, or any error shown as a raw traceback where a clean message is expected.

Do an open-ended pass beyond this list too — you have fresh eyes; use them.

## Also assess the root cause
Several of the above are symptoms of one design choice: some features are wired via node
dependencies, others via state seeded in prepare_worktree, and the graph is assembled by
hand in build_graph. Is that split the root cause of the recurring wired-but-dark bugs,
and is there a structural change (a single feature-registration seam, an exhaustiveness
check) that would make a whole class of these impossible?

## Output
Per finding: one-line summary · severity · file:line · a concrete failure scenario
(inputs → wrong behavior) · CONFIRMED/PLAUSIBLE · a specific recommended fix. Finish with
the 3–5 highest-leverage changes you'd make if you owned this codebase for a week.
```
