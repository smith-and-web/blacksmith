# S14 publication implementation and acceptance map

Status: S14 accepted locally at goal turn18/30. Final full suite:1219 passed
in2297.64s; ruff passed; all146-source/input/dependency identities match the
pre-run environment record. Runtime wiring and recovery matrix below are covered.
No live publication evidence is claimed; the authorized-release canary remains unrun.

## Settled requirements

R07 requires fresh exact repository/base/head remote observations before effects,
run-owned branches, exact candidate reuse, remote-conflict refusal without force,
run/candidate body markers and no duplicate PR after ambiguous push/create.
S04 critical intents/outcomes and S11 retained recovery/amendment rules apply.
Normal and draft paths share S13's exact-SHA approval guard. DONE/AWAITING_QA
requires a durable validated publication receipt, not merely a parsed command URL.

## Production wiring under verification

- Add a publication service, used by both real normal/draft PR nodes when a
  versioned run context exists. Retain the explicit-approved pre-versioned helper
  API for library callers. Recheck S13 approval before queries, summary and effects.
- Derive the immutable target from saved run ID, origin/base/head, exact candidate
  commit/tree and draft intent. Use deterministic operation keys and private
  publication files. Preserve negative/uncertain originals; record observed
  reconciliation separately where an original outcome cannot be changed.
- Observe the exact remote ref using bounded `git ls-remote --refs origin
  refs/heads/<owned-branch>`. Empty success means absent; nonzero/malformed/duplicate
  output means an observation failure. Different SHA is a conflict.
- Query GitHub with explicit saved host/repository identity. Installed `gh api`
  supports `--method GET`, explicit `--hostname`, `--paginate --slurp` and string
  query fields. Pull-list filters support state=all, owner:head and base. Validate
  returned base/head repositories, refs, candidate SHA, draft, number/URL and the
  unique machine-readable marker; do not trust filters alone.
- A single matching PR plus exact branch is reused without summary/push/create.
  Multiple matches, foreign/missing/changed markers, wrong target/draft/SHA or a
  missing branch after a known completed push are inspection conflicts.
- If the remote branch is absent, critically record push intent and send the
  approved SHA refspec without force. Re-observe after success or ambiguous exit.
  If the branch already has that SHA, skip push and reconcile/create only the PR.
- Persist prepared title/body and summary cost identity before create. Existing
  completed summary receipts are candidate-keyed and reusable. Native create
  intent records exact body/marker before effect. Always query after ambiguous
  create and before any later create attempt.
- An unconfirmed native process/effect is not evidence it stopped. If no matching
  PR exists and prior create may still be running, hold for inspection instead of
  risking duplicate create. A fresh remote positive observation can reconcile a
  completed effect without replay. Critical storage failure prevents new effects.
- Persist a publication receipt with target, remote candidate, exact observed PR,
  original effect/observed-reconciliation references and artifact/hash identity.
  Validate before terminal reporting and CLI resume; capture with existing
  publication recovery artifacts. Retain all workspaces on failure.
- Extend explicit `resume --retry-stage` to the saved publication stage without
  rerunning units, reviews, gates or approvals unnecessarily. Preserve known
  costs/counters and recorded approval; do not create a new run to recover it.
  Missing legacy publication metadata must produce a migration diagnostic without
  inventing a success receipt or replaying an old ambiguous effect.

## Required offline evidence

All tests prohibit paid transport and real GitHub effects; native network Git
remains denied. Scratch local bare Git remotes and strict API-shaped fakes may be
used. Fake query responses must be stateful, reflect effects and reject unknown
commands, rather than succeeding on every argv.

| Scenario | Required evidence |
|---|---|
| Fresh approved normal/draft, both shapes | Query before push; exact refspec; one create; marker/receipt; terminal0/2; no QA model call |
| Plan-only/stale/forged approval | Zero remote query, summary, push or create; retained recovery |
| Existing exact branch + matching PR | Same URL, zero new summary/push/create; completed durable observed receipt |
| Existing exact branch, no PR | Zero push; one create after fresh exact query |
| Remote changed, absent after completed push, wrong/multiple PR | Halt; no force, summary or create; retain all candidates and original receipts |
| Query error/truncation/malformed/foreign identity | Halt before any new effect; precise retained observation failure |
| Push succeeded but return errored or process exited before checkpoint | New process observes exact branch; no repeated push; create once; no completed SDK replay |
| PR created but response errored/lost/unparseable or process exited | New process observes marker + exact identity; same URL; no duplicate create/push/summary |
| PR create definitely failed, branch remains | Explicit stage retry queries then creates only; old failure evidence unchanged |
| Create native outcome unconfirmed + no observed PR | Conservative hold; no duplicate; no claim process stopped |
| Critical intent/artifact/outcome fault | No subsequent effect; ambiguous success reconciles only through exact external observation |
| Corrupt/missing/foreign receipt or old active metadata | Refuse before executor and leave original checkpoint/budget/evidence unchanged |
| Terminal repeat resume | Same receipt/URL/exit; zero SDK or effect replay |

Final full pytest/ruff and exact source/dependency/crash identities are recorded in
heavy-prd-evidence.md and turn17 environment-02.json. No mocked result satisfies the
live disposable-target canary.

Protocol sources checked during preparation: installed `gh api --help` and
`gh pr list --help`; [GitHub pull-list API](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests)
and [Git ls-remote manual](https://git-scm.com/docs/git-ls-remote). This is a local
implementation map, not authorization to invoke GitHub against a live target.
