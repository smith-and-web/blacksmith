# Prepared canary scripts, 2026-10-26

The owner requested runnable scripts for a fresh canary after the permissions
repair. Preparation made **no paid call, push, merge or publication**. The original
implementation goal remains complete at **28/30**. Original failed run
`19277790-18ab-4792-8e45-be61a8cccd9b`, its receipts and launchers remain intact.

Local script directory:
`/Users/josh.smith/.codex/artifacts/blacksmith-heavy-prd-readiness/private-canary-setup/fixed-source-canary/`

- `01-run-plan.sh`: fresh fixed thread, plan approval only. Expected exit1 at
  deliberate PR denial after both utilities, gates and reviews finish.
- `03-check.sh`: inspect the retained candidate/pause; print SHA, workspace and
  diff command. After publication, verify call reuse, one summary, exact remote
  SHA/marker, draft/private status, baseline hashes and independent utility smoke.
- `02-publish-draft.sh`: explicitly approve publication of that same candidate,
  push its branch and create a draft. Expected exit2/AWAITING_QA. Run only after
  the expected pause and candidate review; then repeat `03-check.sh`.
- `README.md`: complete copy/paste commands, expected exits, failure handling and
  remaining gates. Each script supports `--dry-run` without model/GitHub calls.

Fresh thread: `heavy-prd-fixed-20261026T182630Z-9da0dc6e` (unused at preparation).
The scripts use the existing frozen runtime/PRD and exact repaired application
identity from successful full02: **1431 passed**, Ruff passed. They require
at least5GiB free, preserve one-shot start markers, refuse unresolved model usage
and make no automatic retry or budget increase. Existing $10 run-admission and
$1 per-call settings remain; provider invoice reconciliation is separate.

Preparation verification passed Python/shell syntax, isolated helper Ruff, all
three dry runs, exact argv checks, pre-step1 refusal for publication/check,
expected-pause negative checks, one-shot refusal and tiny offline known/unknown
usage fixtures. The repaired-source/private-target preflight passed again with
unchanged full-suite source/runtime/input identity, clean baseline, private remote
and setup refs. No launch markers or fresh-thread checkpoint were created. Free
space was about12.25GiB. These operational scripts are outside the application
source; no application/test changes or repeat full pytest run were needed.

File hashes and checks: local `preparation-verification.json` and repository
`heavy-prd-canary-scripts-verification.json`. A successful script check does not
establish human QA, measured concurrency, the complete live report, authoritative
provider billing, real Docker lifecycle or crash-recovery release acceptance.
The informational connectors-warning suppression remains an unimplemented
follow-up. No live gate is claimed from preparation tests.
