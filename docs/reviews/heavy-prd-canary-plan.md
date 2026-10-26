# Heavy-PRD live canary plan and private setup

Status: the repository owner approved the plan and requested private setup. The
private target, baseline and launch inputs are prepared; paid execution has not
started. Human source approval is recorded separately in
[heavy-prd-human-approval.json](heavy-prd-human-approval.json).
The implementation goal remains complete at turn28/30; this proposal does not
resume it or reset its counter.

## Disposable target and work

The disposable `blacksmith-readiness-canary` repository is now PRIVATE, with a
minimal Python package, locked pytest/Ruff dependencies and trusted toolchain.
The latest request requires private visibility; there is no public fallback.
Private draft support was verified with a setup-only draft and that draft was
closed without merging. The main checkout and protected sentinel remain unchanged.
Operational inputs, private setup receipts and logs are retained locally outside
this source repository under
`/Users/josh.smith/.codex/artifacts/blacksmith-heavy-prd-readiness/private-canary-setup/`.

Commit the minimal package scaffold, a passing smoke test, locked development
dependencies and a trusted `blacksmith.toml` first. Prewarm the gate environment.
The two auto units add distinct module/test files, so either worker's full pytest
gate can pass independently before joining. No runtime dependency is needed.

| Unit | Work | Dependencies |
| --- | --- | --- |
| U01, auto | `canary/labels.py`: `normalize_label(text)` collapses whitespace and lowercases text; tests in `tests/test_labels.py` | none |
| U02, auto | `canary/limits.py`: `clamp(value, lower, upper)` clamps inclusively and rejects inverted bounds with ValueError; tests in `tests/test_limits.py` | none |
| QA, human only | Inspect the draft, compare exact gated/approved/published SHA, and independently check both utilities | U01 and U02 |

Put one small requirement only in the PRD prose: normalization preserves hyphens.
Manual acceptance must check `normalize_label("  API-Client\t STATUS ")` returns
`"api-client status"`, whitespace-only text returns `""`, and clamp covers below,
inside and above the range plus inverted bounds. These independent checks supplement
the model-authored tests; a canary pass requires the actual outcomes.

Add `protected/sentinel.txt` and a hard `path:protected` untouchable. Record its
contents/hash and the original target checkout's HEAD/status before and after.
Retain the same committed toolchain throughout. Sandbox and respond stay disabled.

## Proposed settings

These are overlays for a complete runtime configuration with the disposable target
and separate storage paths. Keep the approved checkout's model IDs and retry policy.

```toml
[limits]
max_run_cost_usd = 10.0
max_call_cost_usd = 1.0
max_parallel_units = 2
model_timeout_s = 300
command_timeout_s = 120
max_run_seconds = 1200

[review]
enabled = true
panel_size = 1

[critic]
enabled = false

[sandbox]
enabled = false
```

The nominal path has seven paid calls: two plans, two implementations, two reviews
and one summary. Retries can add calls, but consume the same saved allowance.
The $10 setting is an admission budget, not an exact invoice guarantee. SDK budget
stopping can exceed a call allowance; retain SDK-reported estimates and provider
billing separately. No automatic budget increase, uncapped fallback or canary retry.

## Two process invocations

1. Run from the approved feature checkout using its source environment, a unique
   thread ID and `--approve plan`. Both auto units should implement, gate and review.
   Expect exit1 at denied PR approval, retained workspaces/recovery manifest and
   no paid summary or canary-run-owned remote branch/PR. The closed setup probe
   and bootstrap refs are pre-existing setup evidence, not canary publication.
   Save the exact candidate, final gate,
   review receipts, approval proposal and cost/call IDs.
2. Start a fresh process and resume the same thread/config with `--approve pr`.
   Expect the same candidate and consumed budget, no repeated plan/implementation/
   review calls, one new summary, one owned remote branch and exactly one draft PR.
   Expect exit2/AWAITING_QA with the manual check still pending.
3. Perform read-only closeout: compare the final gate, approval, remote head and
   PR marker SHAs; inspect the combined patch, strict review status, accounting,
   distinct worker paths and measured concurrency (never above2). Perform the
   independent utility/sentinel/source-checkout checks. Do not merge the draft or
   mark human QA passed without doing those checks.

## Evidence and stop rules

Record Blacksmith source revision, target baseline, full PRD/config/toolchain
hashes, run/thread IDs, commands/exit codes, receipts, transcripts, approval rounds,
remote observations, draft URL and SDK/provider usage in a new live-canary report.
Keep live results separate from the existing offline acceptance evidence.

Any policy denial, malformed/unavailable review, unexpected target mutation,
unknown cost, deadline/budget stop, duplicate call/effect or SHA mismatch is a failed
or incomplete canary. Stop and preserve the original receipts; diagnose before
separately authorizing additional spend. Crash injection and real Docker lifecycle
testing are separate follow-ups, not part of this first canary.

## Primary references

- [Specification release procedure](../heavy-prd-readiness-spec.md): fixed $10,
  enabled review, two independent auto units, terminal QA, plan-only refusal then
  approved exact-SHA draft publication.
- [GitHub draft PR availability](https://docs.github.com/en/rest/pulls/pulls).
- [SDK budget example and overshoot semantics](https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/max_budget_usd.py).
- [SDK estimates versus authoritative billing](https://code.claude.com/docs/en/agent-sdk/cost-tracking).

## Prepared setup evidence

The actual offline validate command accepted the three-unit PRD and compiled hard
path rules. Baseline pytest/Ruff passed, dependencies are warmed, private visibility
and source/runtime/input hashes were checked, and the original target checkout is
clean. The two utility implementations and their new tests are intentionally left
for Blacksmith. Prepared launchers verify these identities before invoking the CLI.
The implementation goal stays complete at28/30; setup does not reset/resume it.
No paid call or canary-run publication occurred during setup.
