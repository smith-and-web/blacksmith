# Heavy-PRD implementation progress

- Goal status: complete (local implementation); S01-S19 accepted locally. The fresh post-goal live canary published the exact candidate at AWAITING_QA; human QA and remaining release gates are separate. The original failed run is preserved.
- Goal turn: 28 / 30. Never reset this counter automatically.
- Initial interrupted goal turn: turn 1 (no implementation or tool evidence).
- Previous-turn classification: progress; compatibility and exact21-row acceptance audits retained, full1419 observed on frozen1d68a64 through both100-chain/20-root passes; same full terminal0 now independently confirmed with all before/after identities unchanged.
- Current turn: 28; automatic27->28 increment exactly once. Full1419 terminal0/7 retained SDK warnings/5207.92s and Ruff pass; exact176-source/runtime/input identity unchanged. All19 slices/21 matrix rows accepted locally, docs refreshed and brain lint/index/retrieval/exact readback verified. Final local evidence commit/post-hook audit then goal completion; no live release gate claimed. Never reset/increment on tools, status questions or compaction.
- Turn 13 boundary exception: a missed test crash injection reached a real push attempt to a synthetic GitHub remote; it failed before authentication (128), with no gh creation command. Receipt: `heavy-prd-turn-13-unexpected-push.json`. Child publication guard and native offline transport/auth isolation added before further testing.
- Stop rule: at turn 30 stop implementation, write a precise handoff, and pause the goal using the user's explicit authorization. Resume only on instruction.
- Branch: `feature/heavy-prd-readiness`.
- S01-S02 implementation commit: `88070ac` (local only).
- S03 implementation commit: `e8650ba` (local only).
- S04 implementation commit: `1b0d63f` (local only).
- S05 implementation commit: `ec0aceb` (local only).
- S06 implementation commit: `c13fc34` (local only).
- S07 implementation commit: `f3a1312` (local only).
- S08 implementation commit: `b694e06` (local only).
- S09 implementation commit: `adc56d2` (local only).
- S10 implementation commit: `07965ca` (local only).
- S11 foundation commit: `68a80c3` (local only; partial at turn 11).
- S11 controller commit: `03d4308` (local only; partial at turn 12).
- S11 completion commit: `2e97459` (local only; turn 13 full proof passed).
- S12 completion commit: `5f483e7` (local only; turn 14 full proof passed).
- S13 completion commit: `3581d289088a1f3ec4010bd1a62a40bea005aaee` (local only; turn16 full1181/ruff proof).
- S14 completion commit: `8652ac237f3f10ab2ac10f37ab40eec15b4de00d` (local only; turn18 full1219/ruff/146-source identity proof).
- S15 completion commit: `44f0108bbe38acdd638e16cea13d37865a23c885` (local only; turn22 full1325/ruff/165-source identity proof).
- Starting revision: `dcc3bb56940c3907b2e0fcaf13c254e1c55f9767` (verified).
- Existing untracked inputs preserved: specification and `2026-10-24-readiness-probes.py`.
- Initial implementation scope excluded push, merge, publication and paid canaries. Later private-target setup and the owner's manual canary are recorded below; they do not resume the completed goal.

## Input preservation

- `docs/heavy-prd-readiness-spec.md` initial SHA-256: `4aecbe39632dd6aa1060b95b1319b4ec40a62824f1f094745d36dd463cd3a4a3`.
- `docs/reviews/2026-10-24-readiness-probes.py` initial SHA-256: `70e36e96aba1a449244bddca13150eeeda7fe0874e663f8b75a8f44ad0e2901c`.

## Implementation checklist (dependency order)

- [x] S01: Path policy and implement guard.
- [x] S02: Shared requirements and plan context, including full immutable PR-summary context.
- [x] S03: Immutable run context, paths, preflight and locks.
- [x] S04: Transactional operation journal.
- [x] S05: Checked bounded command and Git execution.
- [x] S06: Trusted multi-layer exact-candidate gates.
- [x] S07: Typed per-unit state and reset factory.
- [x] S08: Shared checkpointed unit subgraph.
- [x] S09: Per-unit checkpointed planning and approved hashes.
- [x] S10: Join reconciliation and integration gates.
- [x] S11: Artifacts and recovery CLI.
- [x] S12: Honest patch reviews and receipts.
- [x] S13: Human-unit validation and exact-SHA approval.
- [x] S14: Publication reconciliation.
- [x] S15: Run-wide admission and cancellation.
- [x] S16: Bounded scheduling and recursion.
- [x] S17: Sandbox ownership or explicit disabled-feature rejection.
- [x] S18: Hardened respond or explicit unsupported-command error.
- [x] S19: Observability, docs and complete acceptance matrix.

## Acceptance and verification

See [heavy-prd-evidence.md](heavy-prd-evidence.md) for commands, scoped results, requirement coverage and release gates. A checked slice requires production wiring and regression evidence; partial modules are not completion.

## Post-goal human approval

The repository owner explicitly approved the reviewed diff through36885ee,
including contract.py, after the source walkthrough. See heavy-prd-human-approval.json.
The implementation and its1419/Ruff-tested identity are unchanged. Human review is
satisfied for that source. The owner subsequently approved private setup and ran
the canary manually; it stopped incomplete. The conditional Docker enablement gate
remains unrun. The completed goal counter stays28/30.

## Post-goal live-canary review, 2026-10-26

Run `19277790-18ab-4792-8e45-be61a8cccd9b` stopped before test gates, reviews or
publication. A custom transport options mismatch in SDK permission routing denied
85 Write requests; neither utility exists. A zero-cost probe reproduces the exact
missing flag. Original/shared/unit baseline hashes and remote refs remain unchanged.
Known SDK estimates total about $1.426663; policy charge is about $3.426663 including
two unknown-cost reservations. Accounting and all subsequent live gates remain
incomplete. See [the review and next action](heavy-prd-canary-run-19277790.md).
This review changes documentation only and makes no new paid call or goal-counter
change. The runtime fix is outstanding; offline acceptance is not live acceptance.

## Post-goal permission fix, 2026-10-26

The owner requested a fix for writes and permissions. `OwnedCLITransport` now
supplies stdio permission routing on a copy of guarded options. The real SDK/local
CLI regression reproduced the old missing flag, then passed allowed Write/Edit and
seven fail-closed denial checks after the fix. Unguarded routing remains compatible.
Ruff passes. Full01 finished 1 failed/1421 passed on unchanged177-source identity:
the 100-unit chain hit a separate native cleanup PermissionError after its unit
commits. Failed log, recovery and fixture are retained. Native/SDK cleanup now share
a bounded fail-closed observer, including prevention of recursive probes. Broader
46-test command/SDK/deadline/cancellation verification passes. Full02 is terminal0:
1431 passed/6 retained SDK warnings/7209.51s, with all178 source/test/runtime/input
identities unchanged. The actual100-unit chain and20-root/default-two-worker cases
pass. Final Ruff and separate fixed-source/private-target preflight pass; see
[the fix record](heavy-prd-permissions-fix.md). No paid retry, allowance change or
publication occurred. The original goal counter stays28/30. Requested urgent disk
cleanup is complete: current verified full1431 archive retained; raw successful
full1419/full1431 and passed full01 fixture siblings removed. Failed100-unit case,
paid canary and original receipts remain. Source/input identity still matches full02;
about12GiB available in the final observation. See the disk-cleanup JSON receipt.

## Fresh live canary verified, 2026-10-26

Operational rollout check: normal `/Users/josh.smith/.local/bin/blacksmith` still
uses the older separate uv tool installation; it lacks remediation modules and
does not contain the tested fixes. The canary used the checkout interpreter.
`/Users/josh.smith/Code/blacksmith/.venv/bin/python -m blacksmith.cli` resolves the
tested checkout even from outside this repository. Use that explicit interpreter
until the installed tool is updated and verified. The final permission/cleanup
delta and its two regression files are included in the readiness feature branch.
The status check did not install or start paid work. All178 source/test hashes
still match passing full02. Sandbox
and respond remain disabled under the specification's permitted alternatives.

Run `c36c7939-b2e1-45f4-97b1-aebd0a915d45` completed the automated path and
published candidate `6996ab55beac70a7999bf1c6dd587c660d933ae0` as private canary
PR #2. Both utility gates/reviews, final12-test/Ruff gate, independent rerun/smoke,
source/baseline/sentinel identities, saved-call reuse and two concurrent model
calls in distinct clones were verified. Seven paid calls total, SDK estimate
$0.29278945, no uncertain operations. Exactly one push and one PR-create effect.
The original failed run remains unchanged. No paid call or GitHub mutation was
performed during this review. Goal counter remains28/30.

GitHub records the PR being marked ready for review after draft creation; current
`03-check.sh` correctly refuses the changed draft flag. The SHA still matches all
gates/approval/remote/PR marker. Human QA is still pending in Blacksmith; the owner
reports the PR looks good. Provider billing, original unknown costs and conditional
Docker/crash-recovery follow-ups remain separate. See [live review](heavy-prd-canary-run-c36c7939.md)
and its JSON evidence. PR #1 is the closed setup probe, not this canary.

## Final handoff: turn28; S01-S19 locally accepted

Counter **28/30**, incremented27->28 once on this actual automatic continuation.
Final implementation **1d68a6492e53898c5011a24b9d14d771bad6275d** on feature/heavy-prd-readiness.
Full61391 is terminal0: **1419 passed, 7 warnings in 5207.92s (1:26:47)**. No skipped/xfail/filtering;
actual100-chain and20-root/default2 cases pass. All176-source/JS/helper/config/
original input/65-distribution/Python3.12.13/SQLite3.53.1 identities match before,
after and current audit. All full test/archive/cleanup processes are terminal.
Ruff passed on unchanged source. Exact command/terminal/environment/log evidence
is in turn26-full-command-01.json, turn26-environment-01.json and
turn26-pytest-full-01.log; final audit turn28-final-audit.json matches12 requirements,
19 slices,21 exact spec rows and89 hashed production/regression references.

All19 checklist entries are accepted locally. S17/S18 use only the explicitly
permitted disabled alternatives; enabled sandbox/revision workflows remain
unimplemented/unaccepted. Human contract.py review is now approved for36885ee
(heavy-prd-human-approval.json). General release awaits the authorized fixed10-dollar
paid disposable canary; real Docker smoke is required before future sandbox enablement.
Original spec/probe remain unchanged/untracked/unstaged; original worktree untouched.
Historical turn13 failed external-push exception remains disclosed with receipt and
subsequent guards; no successful external publication or paid canary is claimed.

Brain closeout is verified in turn28-brain-proof.json: lint0 errors/0 warnings,
clean index after more than70 seconds, latest checkpoint retrieved at rank1, exact
whole-note readback and positive feedback once. Dashboard/probe bytes match the
actual tested baseline in turn28-dashboard-identity.json. This record is included in the local evidence closeout commit; the post-hook
identity audit must match it before the goal status transition. No further
implementation remains.

Next release action (outside this goal): review the proposed disposable-target
canary plan, then separately authorize its fixed10-dollar spend and draft publication.
Sandbox stays disabled until separately authorized real Docker proof.
At30 stop/save/pause any unfinished goal; never reset/resume automatically.

## Historical turn27 handoff; final1419 was live on frozen1d68a64

Counter **27/30**, incremented26->27 once on this automatic continuation. Previous
turn was progress. Implementation **1d68a6492e53898c5011a24b9d14d771bad6275d**;
documentation/evidence HEAD6e7d683de99cc23cf02f94c6ff63872f05aa0eff. Original spec/
probe unchanged/untracked; all176 Python/JS/helper/config/input/65 distributions/
Python3.12.13/SQLite3.53.1 match **turn26-environment-01.json** on current recheck.
Full **61391** positively polled live; PIDs9007/9008 confirmed at13:50 elapsed.
Command `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t26-full-01`;
log **turn26-pytest-full-01.log**; durable command/PID/terminal/after-identity receipt
**turn26-full-command-01.json**. Observe same handle; missing observer means inspect
PIDs/receipt/log, never restart after timeout. Keep Python/JS/config/tests/helper/
dependencies frozen. All prior tests/archive/cleanup handles terminal; this full
is the only live job. Source-only compatibility audit confirms Contract v1 field
names and uv.lock/pyproject.toml unchanged from dcc3bb5. Receipt
turn27-compatibility-audit.json and exact turn27-contract-review.patch retained;
human contract review remains NOT RUN. No whole-goal acceptance yet; S02/S19 remain unchecked.

Next: observe full to terminal, verify exact1419 pass/no hidden skips/xfails plus
unchanged exact identities, inspect final acceptance inventory's12 requirements/
19 slices/21 matrix rows and5 summary cases against the result, then close checklist/
docs/brain and commit local evidence. Existing Ruff pass is valid for unchanged
source. R10/R11 use only explicit permitted disabled alternatives. Human contract
review, paid10-dollar disposable canary and Docker enablement smoke remain unrun.
At30 stop implementation, save exact completed/outstanding/verification/live-job/
next-action handoff and pause unfinished; never reset or resume automatically.

## Historical turn26 final launch; final corrected full1419 live on frozen1d68a64

Counter **26/30**, incremented25->26 once only. Both user's status questions and
compaction/tools do not increment it. Current implementation
**1d68a6492e53898c5011a24b9d14d771bad6275d**, documentation/evidence HEAD2911530;
source176/config/original inputs/runtime exactly match stage
**66beb2ab347ca4ad265d273db90135f108f0b855** after hooks. Original spec/probe remain
unchanged and untracked/unstaged; original .claude worktree intact. S01 and S03-S18
accepted locally; S02 source corrected and S19 closeout pending final full.

**Final corrected full1419 RUNNING, handle61391**, supervisorPID9007/uvPID9008,
launched exactly once via /private/tmp/blacksmith-heavy-prd-turn-26-full-supervisor.py.
Command `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t26-full-01`.
Log **turn26-pytest-full-01.log**, durable command/PID/terminal/after-identity receipt
**turn26-full-command-01.json**. Complete176 Python sources/JS/helper/config/original
inputs/65 distributions/Python3.12.13/SQLite3.53.1 frozen in
**turn26-environment-01.json**, including supervisor source for recovery. Every
before identity matches. Observe same handle; missing observer -> authoritative
receipt/PIDs/log, never restart after timeout. Do not edit Python/JS/config/tests/
helper/dependencies while full runs. Documentation-only commits do not change
frozen implementation. No pass claimed; terminal1419/unchanged identities required.

Old full20455 terminal0 on85fb50e: **1414 passed,4 retained SDK advisories/5466.74s
(1:31:06)**, no skips/xfails/filtering; actual100-chain and20-root/default2 pass,
all175-source/config/input/JS/helper/runtime identities unchanged. Terminal proof
turn26-full1414-terminal-proof.json; logSHA
3eb0a4c7b8223f4cfdc6315341f8df79cba36f1661e2f9715045209ccd599b47.
This does not prove subsequent summary code. Baseline negative summary1 failed/
18.93s; checked2-file correction has broad72/854.27s then exact-final25/113.42s,
Ruff passes,176 post-hook matching sources and primary1419 collection/0.66s.
Source/patch/integration records turn26-summary-source-01.json,
turn26-summary-feature.patch and turn26-summary-integration.json. Every production
paid role now has full immutable prose/contract; summary stays tool-free, cached
prompt identity stable, manual QA explicitly pending. No added call class/cap;
input tokens may increase. S02/S19 remain unchecked until final full and closeout.

Completed old1414 fixture archive/cleanup all terminal: prep10052 exit0; fresh
owner42810 exit1/no process records (only validated unrelated-device HFS warnings);
cleanup31683 exit0. Every935878 entry/regular byte/mode/symlink rechecked, protected0.
Archive346740898 bytes/SHAc708f844e323fecc53f7eed3a185d7db35bacba61514287bfb17c6eb460ea900,
compressed manifestSHA3b30c1f526145eca4391c05f2a2af46cf89f0dda1d471ba7660f7274a3eae2be.
Proof turn26-archive-full1414-proof.json has restore prerequisites; source removed,
only10441 owned directory modes changed, never regular/shared-hardlink
modes. Failed roots untouched by this agent. Free16766197760 bytes at new freeze.
No archive/cleanup job live; final full61391 is the only live job.
Latest brain checkpoint now matches this launch: Projects/blacksmith.md updated
through MCP, targeted lint03 terminal0/0 errors/0 warnings; after70s index errors0/
invalid0, rank1 retrieval and exact whole-note equality, one feedback. Initial
lint02 checksum false positive retained; duplicate digest removed from note with
repository-proof reference, no linter weakening. Proof turn26-brain-proof-02.json.

Current final-source inventory **turn26-final-acceptance-inventory.json** maps
all12 requirements/19 slices/21 acceptance rows, including5 summary cases, to
current source176/1419 collected cases. Still pending terminal full; R10/R11 use
only permitted disabled alternatives. Next: observe same full61391 to terminal,
verify exact1419 pass with no hidden skips/xfails and unchanged source/runtime,
then complete requirement-by-requirement audit, update S02/S19/checklist/docs/brain,
commit local evidence and mark goal complete only when no required local work
remains. Existing Ruff remains valid if source is unchanged. Human contract review,
authorized10-dollar disposable paid canary and real Docker enablement smoke remain
unrun; never claim mocked tests passed them. At30 stop implementation, save exact
completed/outstanding/verification/live-job/next-action handoff and pause unfinished;
never reset/resume automatically. No push/merge/publication/paid canary authorized.
Historical turn13 failed synthetic-remote push attempt remains disclosed above.

## Historical turn26 integration/archive handoff; final source1d68a64 committed, full1419 awaiting archive headroom

Counter **26/30**, incremented25->26 once only. Current implementation
**1d68a6492e53898c5011a24b9d14d771bad6275d** committed on feature/heavy-prd-readiness.
All176 Python sources/config/original inputs/runtime match isolated final candidate
**66beb2ab347ca4ad265d273db90135f108f0b855** after both commit hooks. Only nodes/pr.py
and new test_summary_requirements.py transferred; the remaining174 baseline Python
files are unchanged. Checked delta/complete identity in turn26-summary-source-01.json,
turn26-summary-feature.patch and turn26-summary-integration.json. Source is corrected
but S02 and S19 remain unchecked until final corrected full1419 and closeout.

Full **20455 terminal0** on old85fb50e: **1414 passed,4 retained SDK warnings/
5466.74s (1:31:06)**, no failures/skips/xfails/filtering. Actual100-chain and20-root/
default2 included and passed. Every175 Python/JS/helper/config/input/65 distributions/
Python3.12.13/SQLite3.53.1 identity unchanged before/after. Frozen record
turn25-environment-01.json; terminal turn25-full-command-01.json; terminal proof
turn26-full1414-terminal-proof.json; logSHA
3eb0a4c7b8223f4cfdc6315341f8df79cba36f1661e2f9715045209ccd599b47.
No supervisor/uv/pytest PIDs remain; observer ps exit1 is expected, not test failure.
This green baseline cannot prove the subsequent summary correction.

R02 audit found summary missing full contract and reading mutable PRD state.
Real CLI baseline1 failed/18.93s, retained. Shared renderer now gives exact frozen
prose/full contract to the existing tool-free summary call; pending human QA is
explicitly not completed checks. No new call class/cap; input tokens may increase.
Broad scope72/854.27s then exact-final25/113.42s, no warnings/skips/xfails, and Ruff
pass. New5 cases cover both shapes/normal+QA publication, changed original PRD,
mutated in-memory PRD and stable cached-prompt identity. Conflicting old SDK prompt
intents fail closed, never silently rebilled. Primary Ruff-final01 passes;
collection-primary-final01 **1419/0.66s**. No new dependency/config/schema change.
Original spec/probe unchanged and untracked/unstaged; original other worktree intact.

Completed old full fixture /private/tmp/blacksmith-heavy-prd-t25-full-01 is being
archived, not deleted yet. Fresh owner8504 terminal1/no process records; only
validated unrelated-device HFS warnings in turn26-archive-full1414-lsof-01.log.
Preparation **10052 terminal0** via /private/tmp/blacksmith-heavy-prd-turn-26-archive-full1414.py,
log turn26-archive-full1414-prepare-01.log. Fully verified935878 entries/1762288473
logical payload bytes, protected0; archive346740898 bytes/SHAc708f844e323fecc53f7eed3a185d7db35bacba61514287bfb17c6eb460ea900,
compressed manifestSHA3b30c1f526145eca4391c05f2a2af46cf89f0dda1d471ba7660f7274a3eae2be.
Proof turn26-archive-full1414-proof.json; source retained, cleanup not started.
Fresh post-archive owner42810 terminal1/no process records; only validated
unrelated-device HFS warnings. Safe cleanup now running; do not launch it again
after timeout. Observe same handle/PID and completion proof. It requires terminal0/unchanged identities
and validates every archived byte/mode/symlink, skipping protected .env/special/
inode aliases. Do not clean until verified archive/proof, protected0 and another
fresh no-owner check. Cleanup helper /private/tmp/blacksmith-heavy-prd-
turn-26-archive-full1414-cleanup.py now running as31683, log turn26-archive-full1414-cleanup-01.log.
It revalidates archive/manifest/source identity and changes only owned directory
modes before removing this verified closed root. Failed roots untouched. No test
job live; safe cleanup is the only possible live job.

Next: observe archive handle to terminal, verify proof/hashes/restore prerequisites,
fresh owner check, safe owned-directory-only cleanup, then execute prepared
/private/tmp/blacksmith-heavy-prd-turn-26-freeze.py. It requires completed archive/
cleanup, source176 exactly matching stage66, configs/original inputs/runtime stable,
primary1419 collection/Ruff and >=4GB free. Prepared full supervisor
/private/tmp/blacksmith-heavy-prd-turn-26-full-supervisor.py expects fresh root
/private/tmp/blacksmith-heavy-prd-t26-full-01 and1419 tests; launch exactly once only
after valid frozen turn26-environment-01.json. Final full NOT launched yet. Then
observe same handle/PIDs/log and retain failure evidence. Requirement inventory
must be refreshed to final176-source identity and5 summary cases; each row still
needs exact final full proof. Human contract review, paid10-dollar canary and real
Docker enablement smoke remain unrun/prohibited here. At30 stop implementation,
save precise completed/outstanding/verification/live-job/next-action handoff and
pause unfinished goal; never reset/resume automatically.

## Historical turn26 audit and full1414 wait; full1414 live, S02 summary-context gap reopened in isolation

Counter **26/30**, incremented25->26 once on this automatic continuation.
Previous turn was progress. Primary implementation85fb50e5f6da462ee5c51962c51af994fa100e8c;
documentation/evidence HEADd3ebcd4f6229491dbcfd65315d6f38c7290d3384, original spec/
probe unchanged/untracked. S01 and S03-S18 accepted locally; S02 reopened and S19
unchecked. Requirement audit found paid PR-summary still reads mutable state prose
and omits full contract. Actual CLI/Git/gate/SQLite baseline78646 terminal1,
1 failed/18.93s at exact full-contract assertion; raw receipt turn26-summary-
baseline-01.log retained. Isolated /private/tmp/blacksmith-heavy-prd-summary-stage,
branch feature/heavy-prd-summary-context-staging, baseline85fb50e; only nodes/pr.py
plus new test_summary_requirements.py change. Summary now uses shared immutable
renderer; same call class/caps, possibly more input tokens, no duplicated prose.
Five new cases cover both shapes/normal+QA publication and mutated in-memory PRD.
Scope43628 terminal0:72 passed/854.27s, no warnings/skips/xfails. Final prompt
wording clarifies pending human QA and strengthened cached-prompt identity check;
final25 scope72617 terminal0:25 passed/113.42s, Ruff-final02 passed.
Stage collection confirms1419 tests,176 Python sources; final post-hook identity
record turn26-summary-source-01.json proves all sources/config/original inputs/
65 distributions/Python3.12.13/SQLite3.53.1. Candidate66beb2ab347ca4ad265d273db90135f108f0b855
is committed in isolation,2-file delta baseline85fb50e exported to
turn26-summary-feature.patch. Primary source remains frozen/unmodified. Every production
paid-role call site was inspected: planner/critic reuse the full-context system
prompt; both implement tiers/retries and review append the shared renderer.
PR summary was the missing role. Do not transfer/edit primary source until
full20455 terminal. Even a passing1414 cannot
complete goal: correction needs checked transfer, fresh identity and full1419
(collection still to verify). Remaining required work stays in original scope.
 Full **20455** positively polled live and
PIDs25554/25555 confirmed at4:29 elapsed this turn. Source/config/runtime remain
frozen in **turn25-environment-01.json**; command/PIDs/terminal/after-identity receipt
**turn25-full-command-01.json**, log **turn25-pytest-full-01.log**. Same command/root:
`uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t25-full-01`.
Observe the same handle; missing handle -> authoritative PIDs/receipt/log, never
restart after timeout. Do not edit Python/JS/config/tests/helper/dependencies while
full runs. Documentation/audit reads may continue. Turn26 acceptance inventory
maps all12 requirements/19 slices/21 matrix rows to frozen sources and collected
regressions; each row still requires the terminal full result. Existing test bodies
were sampled for actual sentinels, refusal before effects, cumulative patches,
immutable recovery, owned process cleanup and exact candidate publication.
Prepared but NOT executed: /private/tmp/blacksmith-heavy-prd-turn-26-archive-
full1414.py and corresponding cleanup helper (require terminal0/unchanged old full,
fresh no-owner proof, protected0/complete archive verification before cleanup);
/private/tmp/blacksmith-heavy-prd-turn-26-full-supervisor.py expects later1419.
No current fixture archive/cleanup starts while full20455 remains live.
Latest09:36 observation:1313 passing markers/no failures; both actual100-chain
and20-root/default2 now pass. Remaining cases/terminal whole-full result still
required; no later-summary1419 acceptance inferred.
Latest09:33 observation:1312 passing markers/no failure markers; actual100-chain
now passed on this exact frozen source.20-root/default2 is next; whole full is
still live. Earlier artifact counts below are historical progress observations.
Full observation at09:07: positively live20455/25554/25555,1311 passing markers,
100 per-unit plan artifacts and14 workspace unit files. Artifact counts are not
passing unit/test outcomes. All source175/config/input/JS/helper/dependency
identities remain frozen; current full cannot accept later summary66beb2a.
All archive/cleanup jobs terminal;
full is the only live job. At30 stop implementation, retain live-job details and
precise completed/outstanding/verification/next action, pause unfinished and do not
resume without instruction. No live release gate or terminal full pass claimed.

## Historical handoff: turn25; final corrected full1414 live on frozen85fb50e

Counter **25/30**, incremented24->25 once; user's status question, tools and
compaction do not increment it. S01-S18 accepted locally; S19 unchecked. Current
implementation **85fb50e5f6da462ee5c51962c51af994fa100e8c** is committed on
feature/heavy-prd-readiness. All175 Python sources/config/original inputs/runtime
match isolated final candidate **24dc05b1ad105461e77aae4be71cbcde4f656a35** after
both commit hooks. Source record turn25-terminal-source-03.json; checked seven-file
migration delta and complete identity in turn25-terminal-migration-integration.json.
Original spec/probe unchanged and untracked/unstaged. Dependencies unchanged.

Accepted full02 **80505 terminal0**: **1398 passed,5 retained SDK advisory warnings/
5047.97s (1:24:07)** on539508be01b498749f6cb49c33c80b9d35047a04. Every174
source/JS/helper/config/input/65 installed distributions/Python3.12.13/SQLite3.53.1
identity unchanged before/after; turn24-environment-02.json and
turn24-full-command-02.json/logSHA27cb1c2e37e1690ed045657c3bd496fc59381fdec244e3ce81f41aef9a4a2db7.
No skips/xfails/filtering. Actual100-chain and20-root/default2 pass in this full,
accepting S16. S17 enabled sandbox refusal and S18 explicit unsupported respond
are permitted deferrals accepted in the same full. No Docker/revision/live claim.

S19 acceptance audit reproduced two gaps; neither is hidden by the old full pass:
- Error/unknown SDK subtype with is_error=false could become a clean result.
  Require positive subtype=success and non-error flag; preserve text/cost/transcript
  and known error kinds. Original real CLI negative baseline failed1/6.23s;
  corrected scopes67/43.64s, SDK50/163.29s, parallel-safe fixture9/38.51s passed.
- Old cached clean model results bypass fresh normalization. Model intents now
  commit exact integer terminal_policy_version1 before transport. CLI resume/
  dispatch, library Executor and retained-review verification refuse absent,
  boolean or future versions before native execution/options/credentials/clock/
  amendments/new effects. Read-only inspect reports unsupported model policy,
  preserving original checkpoints/receipts/events/inputs. No invented historical
  tag. Baseline05 failed on an invalid new-test flag (retained, no behavioral claim);
  corrected baseline06 proves native execution before refusal,1 failed/6.02s.
  Initial scope07 passed16/180.91s. Final scope08 includes direct-dispatch/review
  refusal and pre-transport intent proof for every paid role: **134 passed/347.66s,
  terminal0**, no warnings/skips/xfails. Source175 unchanged after stage/primary
  hooks; primary Ruff-final03 passed, collection-primary03 **1414/0.70s**.

Normal successful SDK calls/caps unchanged; terminal max-turn recovery uses only
existing saved continuation/admission allowances. Migration adds no paid call or
allowance. **Final corrected-source full1414 is RUNNING, not yet accepted.**
Handle **20455**, supervisorPID25554/uvPID25555, launched exactly once via
/private/tmp/blacksmith-heavy-prd-turn-25-full-supervisor.py. Command
`uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t25-full-01`;
log turn25-pytest-full-01.log; durable PID/command/terminal/after-identity receipt
turn25-full-command-01.json. Complete175 Python sources/JS/helper/config/original
inputs/65 distributions/Python3.12.13/SQLite3.53.1 frozen after hooks in
**turn25-environment-01.json**, which also retains supervisor source for recovery.
Observe same handle; missing observer -> authoritative receipt/PIDs/log, never
restart after timeout. Do not edit Python/JS/config/tests/helper/dependencies while
full runs. Documentation-only commits do not change frozen implementation identity.
Passing terminal full1414/unchanged identities plus acceptance closeout are required
before S19 or whole-goal completion. All old test/archive/cleanup handles terminal.

Completed adjacent S16 fixture t21-scheduling-regression-03 archived and safely
removed: attempt02 preparation65900 terminal0, fresh owner39026 terminal1/no process
records and cleanup54777 terminal0. Every209207 entry/regular byte/mode/symlink
rechecked; protected0. Archive57095657 bytes/SHA1d34643dad10898701988b9c7c72c94a7d4eed7b5f62e6e4c5e295c9cc2a5ca0,
compressed manifestSHA8a0875d64c679255d0d2138272d8fdfcbf014f86beae1cb191e8d0f1c8c8723a;
proof turn25-archive-s16-proof.json. Only1621 owned directory modes changed, never
regular/shared-hardlink file modes. Earlier ENOSPC archive attempt45361 terminal1
partial/log retained; source was untouched before verified retry/cleanup.

Accepted full1398 root /private/tmp/blacksmith-heavy-prd-t24-full-02 archived and
removed: preparation54485 terminal0, fresh owner40442 terminal1/no records, cleanup
98983 terminal0. Every905063 entry/regular byte/mode/symlink rechecked, protected0.
Archive337886632 bytes/SHA299b03f94b7396175501607d5b7469554fdd224186d9b0b7fad62a353621bfde,
compressed manifestSHA2ecd5e7d6000c6fa7b7238220f615cb9aca8a89736f265c6f3204c0d332ca109;
proof turn25-archive-full1398-proof.json contains restore prerequisites. Only
10152 owned directory modes changed, never regular/shared-hardlink modes.
Failed roots remain untouched; free5469110272 bytes at final full freeze. No archive
or cleanup job live; final full20455 is the only live job.

Brain Projects/blacksmith.md updated via MCP, lint0/0, indexing errors0/invalid0
after70s, rank1 semantic retrieval/whole-note equality/one feedback; proof
turn25-brain-proof-01.json. Latest launch checkpoint now verified by targeted lint,
index errors0/invalid0 after70s and exact whole-note read/retrieval;
turn25-brain-proof-02.json. Top snippet is historical; fresh note matches live20455.
Next: observe full20455 to terminal; verify all exact identities/receipts and1414
pass with no hidden failures/skips/xfails. If it fails, preserve failed root/log and
fix the cause without weakening assertions; rerun only after terminal evidence and
new frozen identity. Then final Ruff if source changes, update acceptance matrix/
checklist, commit local evidence and complete goal only when required local work
is done. Live human contract review/paid canary/Docker enablement gates remain unrun.
Implementation85fb50e and documentation/evidence14374e1 committed locally; original
inputs remain untracked. Full20455 is the only live job. At30 stop/save exact
completed/outstanding/results/live-job/next-action handoff and
pause unfinished goal; never reset/resume automatically.

## Historical turn25 verified wait and terminal-status discovery

Counter **25/30**, incremented24->25 once after objective/instructions/memory/brain
and exact frozen source/runtime checks. Previous turn was progress. S01–S15
accepted; S16–S19 unchecked. Primary docs HEAD3520cea; implementation539508b.
Full02 **80505** positively polled live, supervisor42902/child42905 observed at
5:13 elapsed; log5% plus46 cases with no reported failure at turn entry. All174
sources/JS/supervisor/config/original inputs/65 distributions/Python3.12.13/SQLite
3.53.1 match **turn24-environment-02.json**. Keep source/config/tests/JS/helper
frozen; continue same handle, missing handle→receipt/PIDs/log, never restart
on observer timeout. Exact command/log/receipt below remain authoritative.
Turn25 observations: former config drift case passed;1311 test progress markers
without reported failure, then the actual100-chain began. All100 plan artifacts
present;40 unit files observed in the retained workspace at49:15 elapsed. These
artifact counts are progress only, not passing unit outcomes. Original full80505
still positively live at42902/42905. Detailed timestamp/PID/free-space observations
are in turn25-full-observations.jsonl. Prior accepted100 fixture allocation was
778592KiB (metadata-only size check; no replay/change). Current headroom remains
above that footprint. Keep all source/config/tests/JS/helper frozen.
Need final terminal full1398 including100/20, refreshed Ruff/identity audit and
complete acceptance closeout. No live gate claimed. At30 stop/save precise
handoff/pause unfinished goal; never reset/resume automatically.

Turn25 acceptance audit found an additional R09/S19 terminal-status gap: actual
Executor offline probe on primary539508 accepts error_max_turns,
error_during_execution and unknown terminal subtypes as successful if SDK
is_error=false, allowing clean review text. Probe turn25-terminal-status-probe.json.
Primary sources remain frozen for full80505. Actual100-chain now has a passing
progress marker in that full run;20-root is next, no failure markers so far.

Isolated correction lives in /private/tmp/blacksmith-heavy-prd-terminal-stage,
feature/heavy-prd-terminal-staging, baseline539508b, existing pinned .venv symlink.
Only executor.py plus new test_terminal_status_integrity.py change. Require both
positive subtype=success and non-error flag; known failure kinds/cost/transcript
evidence preserved. No added paid calls; valid successful calls unchanged.
New actual CLI/Git/SQLite negative test reproduced the old false-clean outcome
(baseline23930 terminal1,1 failed/6.23s; original effect assertions retained).
Staged scope **45417** is live, log turn25-terminal-scope-02.log;9 new direct/
sequential/parallel review tests plus adjacent Executor/review/activity cases.
Staging ruff01 passed. Do not transfer/edit primary until its full is terminal;
then checked feature-only delta, final full/Ruff on corrected source required.
Even a green full02 alone cannot accept S19 while this reproduced gap remains.
Both actual100-chain and20-root/max2 now have passing markers in primary full02;
whole full remains live and must finish. Isolated scope45417 terminal0 **67 passed/
43.64s**, ruff01 passed; imported Executor path verified inside terminal-stage.
SDK/admission/interrupted-accounting/transcript scope **25990** is now live,
log turn25-terminal-sdk-03.log. No primary source transfer yet. Normal successes
and caps unchanged; failing implement turn caps use existing bounded continuation
allowance, so any recovery spend remains governed by original admission/caps.

## Historical handoff: turn24; corrected full02 live on frozen candidate539508b

Counter **24/30**; S01–S15 accepted locally, S16–S19 unchecked. Current immutable
implementation **539508be01b498749f6cb49c33c80b9d35047a04**, all174 Python sources,
JS probe, supervisor helper, config/original inputs/65 installed distributions/
Python3.12.13/SQLite3.53.1 frozen in **turn24-environment-02.json** after hooks.
Ruff24 final02 passed;1398 tests collected. Saved inspection correction passed60
without changing the original config-drift assertion or parsed config values.

**Live full02 handle80505**, supervisorPID42902/childPID42905, started exactly once
via /private/tmp/blacksmith-heavy-prd-turn-24-full-supervisor.py. Command
`uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t24-full-02`.
Log **turn24-pytest-full-02.log**; durable command/PID/terminal/identity receipt
**turn24-full-command-02.json**. Observe same handle; if missing use receipt/PIDs/
log. Never restart after observation timeout. No Python/JS/config/test/helper edits
while full runs. Actual100-chain and20-root/max2 included; no terminal pass claimed.
All earlier test/lint/archive/cleanup handles terminal. New full is the only live job.

Accepted S12 archive preparation25075 and cleanup18206 both terminal0. Every
374157 entry/regular byte/mode/symlink checked; protected entries0. Archive
/Users/josh.smith/.codex/artifacts/blacksmith-heavy-prd-readiness/turn14-full01.tar.gz
115092916 bytes/SHA499a74d54b7749e825465fc5fae7b011bce42fae90e0ae8ac6d34c3238a52658;
manifestSHA66bbae4fb97a24b5c84f8b9a5a3deedf5ab2aee3385a95003f6b56e2cb7f1509.
Cleanup revalidated source/archives and no owners, changed5700 owned directory
modes only, removed completed t14-full01 root; regular/shared-hardlink file modes
unchanged. Proof **turn24-archive-s12-proof.json** contains restore preconditions.
Failed roots untouched. Free4124516352 bytes at full02 freeze. Do not repeat cleanup.

Full01 failure/proof and precise correction follow below as historical details.
Next: wait for full02 terminal, verify all exact identities/receipts, fix any real
failure without weakened assertions, refresh Ruff and acceptance records. At30
stop/save precise completed/outstanding/results/live-job/next-action handoff and
pause unfinished goal. No live gate/paid canary/Docker/human review claimed.

Turn24 brain handoff verified: Projects/blacksmith.md updated via MCP; targeted
lint0/0. After more than91 seconds index errors0/invalidFrontmatter0, fresh semantic
retrieval plus exact entire-note readback matched; feedback once
(5462042a-ec36-40f8-919e-932d6afe8ff7). Receipt turn24-brain-verification.json.
No memory-folder write. Full80505 remains live on exact frozen candidate539508b.
Counter stays24/30 through status/tools/compaction; next actual automatic25.

## Historical turn24 correction and resource preparation

Counter **24/30**; incremented23->24 once after objective/instructions/memory/brain
and exact source/runtime checks. Previous turn was progress. S01–S15 accepted;
S16–S19 remain unchecked. Full01 **77877** deliberately interrupted after confirmed
config-leaf failure; terminal **2**,1 failed/329 passed/480.25s. Supervisor receipt
proves every174-source/config/input/JS/installed-runtime identity unchanged during
that run. Failed root/log retained; actual100/20 not reached. No passing-full claim.

S18 reserved max_attempts was unread after unsafe flow removal. Read-only recovery
inspection now displays its saved value and sandbox flag explicitly unsupported.
The original config drift assertion is unchanged. New real historical Git/SQLite/
journal test proves saved7/local2 and no checkpoint/input/journal mutation or
executor/native calls. Targeted60/53.32s and ruff24 pass. Only recovery.py and
test_feature_deferrals.py differ fromfbbd16c; sample config changes correct two
CWD comments only, parsed values unchanged. Exact identity turn24-environment-01.

Before new full: accepted S12 completed fixture t14-full01 uses1.17GiB. Its
1138-pass/1136.16s accepted receipt matches turn14-environment.json. Owner scan8434
terminal1/no owners, known other-device HFS warnings retained. Archive prepare
**25075** is terminal0,374157 entries/499152982 logical bytes,115092916-byte
archive fully byte/mode/symlink verified; protected entries0.
ArchiveSHA499a74d54b7749e825465fc5fae7b011bce42fae90e0ae8ac6d34c3238a52658;
manifestSHA66bbae4fb97a24b5c84f8b9a5a3deedf5ab2aee3385a95003f6b56e2cb7f1509.
Fresh owner31413 terminal1/no owners/only verified other-device warnings.
Cleanup **18206**, PID41382, is live via
/private/tmp/blacksmith-heavy-prd-turn-24-archive-s12-cleanup.py; log
turn24-archive-s12-cleanup-01.log. It rechecked archive/manifest and every unchanged
source entry/regular byte, changed only owned directory modes and is removing the
verified completed root. Poll same cleanup; do not restart. Protected inputs
excluded unread; failed roots untouched. Wait for terminal0/proof before full02.
Then post-hook freeze corrected174 sources/config/input/runtime and final full02
including100-chain/20-root/max2. No new full has launched yet. At30 stop/save/pause
unfinished work; never reset or resume automatically. No live release gate claimed.

## Historical handoff: turn23; S19 scoped proof, final combined verification outstanding

Counter **23/30**, unchanged by the user's status request. S01–S15 accepted locally;
S16–S18 integrated with scoped proof; S19 remains unchecked. Primary branch
feature/heavy-prd-readiness has documentation base401a9a2 and final S19 source.
Original spec/probe unchanged and untracked. Do not stage them or replay old stage
baselines. Final combined full01 is running; no terminal acceptance result yet.

**Frozen S19 candidate fbbd16c9a3617fd2cbb061b377ad3cc833d94899.** Post-commit
hook174-source/config/input/JS/all-distribution/Python/SQLite identity matches
turn23-environment-02.json. Ruff08 passed and collection1397 succeeded. Full01
handle **77877**, supervisorPID18729/childPID18730, launched exactly once via
/private/tmp/blacksmith-heavy-prd-turn-23-full-supervisor.py. Command:
`uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t23-full-01`.
Log turn23-pytest-full-01.log; durable running/terminal PID/command/exit/source
receipt turn23-full-command-01.json. The supervisor rechecks all source/config/
input/runtime identities after terminal. **Do not edit Python/JS/config/tests
while it runs. Poll the same handle; missing handle → receipt/PIDs/log, never
restart after an observation timeout.** Expected1397 is not a pass. Actual100/20
remain part of this full invocation. All earlier test/lint handles are terminal.

S19 scoped results: counter recovery sequential/parallel **2 passed/29.90s**;
optional store/lookup/live/metrics/progress faults **9 passed/55.26s**; strict
activity plus actual parallel call/parent identity **11 passed/11.88s**; bounded
presentation **13 passed/0.29s**; adjacent presentation/events/dashboard batch had
87 passing cases and two new fixture-signature failures, corrected in the 13-case
target. Broader adjacent final01 finished **1 failed/162 passed/160.32s, exit1**:
new context-free result call IDs broke the existing transcript enabled/disabled
equality assertion. Restored legacy result behavior without changing that assertion;
activity/transcripts keep unique IDs and production calls retain critical IDs.
Correction02 finished **40 passed/41.35s, exit0**, including that original test,
strict activity, both recovery shapes and real parallel identity. Final ruff08
passed. Collection1397 succeeded; expected count is not a passing suite. Final
combined full still required. All failed receipts are retained.

Actual defects reproduced and repaired: recovered cumulative revision counted
twice; optional memory lookup aborted plan and printed private exception detail;
child model activity was absent from the parent live stream. New strict tests keep
their original effect/cost/identity assertions. Complete verdict/terminal checks,
real call/task ownership and bounded previews preserve full critical evidence.
The actual inline-JS offline probe **passed** after correcting a constant placement
error it exposed; it verifies task association, concurrent completion, history and
finding caps, unfinished status and replay deduplication. No browser-layout claim.

Accepted S15 archive cleanup11475 completed exit0. Archive turn21-full03.tar.gz
is209907938 bytes/SHA256556b59a103c9077b2879708187a6ff49c4ea16da159e132d85a5512d395bb76b;
manifestSHA610d9e67980843e9ad1a67777cfb71bdee7b13e7b0d677a24f439c6ec6ebc750.
752885 entries were verified;175489 owned dirs made writable without changing
regular-file/shared-hardlink modes. No protected entries; failed roots preserved.
Source t21-full03 removed; restore only after archive identity verification into
an absent safe destination. Exact proof: turn22-archive-s15-proof.json, cleanup23.
Latest free3.8GiB. Stable archive directory and restore instructions are in that
proof. Do not repeat the original expensive fixture or cleanup.

Next: observe the one running full invocation, audit terminal status/exact identities
and complete the acceptance matrix, fix any real failures without weakening
assertions, then accept/commit S16–S19 only with final full/Ruff proof. Keep all
source frozen during the full run. If the goal reaches30, stop implementation,
save exact results/live handle/outstanding work/next action and pause unfinished.
No paid canary/live release gate/Docker smoke/human contract review is claimed.
Historical turn13 failed external-push attempt remains disclosed. Goal active23/30;
next actual automatic24; at30 stop/save precise handoff/pause unfinished work.

Turn23 brain handoff verified: Projects/blacksmith.md updated via MCP; targeted
lint0 errors/0 warnings. After more than87 seconds, index errors0 and invalid
frontmatter0; fresh semantic retrieval and exact whole-note readback matched,
positive feedback once (56e04a91-1739-4c9e-b5e4-b0f3c20f479b). Receipt
turn23-brain-verification.json. No memory-folder writes. Code/config/tests remain
frozen at fbbd16c while full77877 runs. Counter remains23/30; next automatic24.

## Historical handoff: turn22; S15 accepted, S16-S18 scoped candidates ready

Counter **22/30**. S01-S15 accepted locally; S16-S19 unchecked. All test/lint handles are terminal. S15 accepted-fixture archive prepare4182
is live after owner scan3566 completed exit1; see the pending archive block below. Primary full03/44404 finished **1325 passed,
6 installed-SDK advisory warnings in3373.29s (56:13), exit0**. No skips/xfails or
warning filtering. All165 Python sources/config, both original inputs, complete
installed distribution set, Python3.12.13 and SQLite3.53.1 match frozen **turn21-
environment-03.json**. Fresh primary ruff22 passed. Final source matches durable
admission-stage **f6113bb301c46aaef4a9ca85ae5976fa3a054e2f**. No source changed
while the full suite ran. S15 local implementation committed 44f0108bbe38acdd638e16cea13d37865a23c885; post-hook source/input/config/receipt identity unchanged.

S16 integrated locally as **fc94de26f23d9f7d8d762791a4c148df881263a6** from
**a9ff632b263358a00d5ebb7f9344492d6876bde8**,168-source isolated stage:
final adjacent93-case regression passed **93/1061.65s**, complete terminal summary
and absent supervisor51538/pytest51552 verified, source/config/input hashes unchanged.
Launch handle was truncated; no independent supervisor exit-code claim. Final ruff22
passed. Actual100-chain passed on4d89e01;20-root/default2 and all4 cancellation modes
passed after owned-node pool, final max1 CLI/checkpointer/queued-cancel scope22 passed.
**Final integrated full must rerun100/20 on final code.** S16 remains unchecked.
Read-only13-file transfer check **turn22-transfer-check.json**, patch SHA256
fcdd90ad1c7823cce72d2727c1cd3e9960f95a153ffbd1c8f70b8bf99bf2eff4, passes on primary.

S17/S18 are prepared in /private/tmp/blacksmith-heavy-prd-final-stage,
feature/heavy-prd-final-staging. Baseline **780bd457889c7271e346366a99b8c10d0f81f677**
is an integrated S15/S16 snapshot preserving all six later S15 files. S17 candidate
**2c197feabbc4bf7f2ef9e6dbaa6d9299579d7578** uses the specification's allowed disabled
sandbox alternative: config/execution/parent/child/model/native/workspace boundaries
reject enabled sandbox before effects; no startup/tool advertisement; historical
settings/journal/checkpoint/bundles remain readable. New capture rejects before an
intent. Scoped227/190.45s, final boundary30/47.36s, ruff passed. Initial146-pass/1-fail
fixture omitted recovery_version1; strong valid:true assertion retained and marker
restored, target1/1.28s passed. Original enabled behavior assertions intentionally
change to explicit no-effect refusal under the settled spec exception; disabled,
untouchable/index/tool-surface/primitive tests remain covered. No Docker smoke.

S18 candidate **e51d65371510bb552fa1ab039449af616fccb138** uses the permitted explicit
unsupported-command alternative. CLI help/parsing remain; execution returns1 before
environment/config/PRD/GitHub/model/clone access; wrapper returns1 and library flow
raises RespondUnsupportedError. Unsafe legacy execution body removed. Real scratch
remote histories stay unchanged. Scoped31/4.63s and final ruff pass. Future actual
revision services/race/push recovery remain unimplemented and cannot be enabled by
reserved settings. Final complete169-source/config/input/runtime/terminal-receipt
record is **turn22-deferrals-source-03.json**, tracked stage clean/spec/.venv excluded.
S17 integrated as **24e52e1b143208982b552e0a8c4a32903fa803aa**, S18 as
**33eba45e451b77d0c71c4f3f062dc557c1348fcd**. All169 primary sources/config exactly
match final-stage e51d653. No S17/S18 global acceptance yet.

Integration **completed in order**, with no copied-baseline replay. S16's13-file
feature-only delta preserved all SIX accepted S15 files: executor.py,
test_executor_admission.py, test_receipts.py, test_receipt_wiring.py,
test_path_policy.py, test_requirements_context.py. The168-source result matched S16
plus those six hashes; record **turn22-scheduling-integration.json**. S17 then S18
feature deltas applied after780bd45; each complete169-source/config/input set matched
the exact corresponding stage record. Phase commits and patch hashes:
**turn22-deferrals-integration.json**. Fresh integrated primary ruff22 passed.

**Next: S19**, using primary169-source HEAD33eba45, or a checked delta from identical
final-stage e51d653. Fix keyed review revision accounting, strict terminal/identity-
bound activity findings, bounded presentation and visible optional-sink diagnostics;
refresh stale descriptions and audit every matrix row. The concrete trace below is
unfinished work, not evidence of implementation. Then run final combined full
pytest/ruff including100-chain and20-root/max2 on final source before checking
S16-S19. No per-slice full repetition is required by the spec; the final full must
cover every integrated slice and all acceptance requirements.

Disk: only accepted completed S13/S14 full fixtures were fully archived and removed;
failed/running roots untouched. New S13 archive **turn15-full01.tar.gz**,150464975 bytes,
SHA256 **7ee5dfbe69d62473673c27b55b0f8b10ec4c1d26c865958d7f360d0f4d6d5c64**;
manifest SHA256 **a055aee69a0eed3d7c887b88d620369aa5f61a1bd48f0f22750d1b4b2e6c1ab1**.
Stable directory /Users/josh.smith/.codex/artifacts/blacksmith-heavy-prd-readiness;
proof/restore **turn22-archive-proof.json**,518060 entries, protected.env inputs unread.
Cleanup86330 terminal0;122766 owned dirs made writable without changing file/shared
hardlink modes. Last free2.0GiB; accepted full03 archive preparation is now live before final scale.

No external push/merge/live publication/paid canary/Docker smoke this turn. Preserve
historical turn13 failed external-push disclosure. Paid disposable-canary, explicit
human contract.py review and real Docker enablement smoke remain unperformed gates.
Goal active22/30; next automatic23. At30 stop implementation/save precise completed,
outstanding, verification and next-action handoff/pause; never reset/resume unaided.

## Historical turn21 handoff; final S15 adapters and S16 owned-node scheduling

Counter **21/30**. Status questions, tools and compaction do not increment it. S01-S14 accepted locally; S15/S16 unchecked; S17-S19 outstanding. Primary branch feature/heavy-prd-readiness at documentation base525dedf contains uncommitted S15 source durably saved as admission-stage **f6113bb301c46aaef4a9ca85ae5976fa3a054e2f**. All165 primary Python sources/config match that candidate. Original specification/probe unchanged/untracked/unstaged. No primary source edits after full03 launch.

**Live primary full03 handle44404**: `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t21-full-03 > docs/reviews/heavy-prd-turn-21-pytest-03.log 2>&1`. Frozen complete165-source/config/input/Python/dependency/SQLite record: **heavy-prd-turn-21-environment-03.json**. Poll the same handle; do not restart after an observation timeout. No full S15 pass yet. Last accepted global proof remains S14's1219 tests/ruff. Fresh primary ruff21 passed; corrected path/requirements/admission scope passed **76/119.04s**.

Old primary full02/3281 is terminal **10 failed/1315 passed/6 SDK advisory warnings/3013.29s, exit1**. Exact frozen165-source identity/runtime/config/input and final log hash were audited before edits and saved in turn20 environment-02. Three path-policy and seven requirements-context tests inherited the default SDK query while overriding only collection; the new immutable-context guard correctly refused before their fake callbacks. Both offline adapters now explicitly inject an exact-signature forbidden query, which fails if SDK transport is ever reached. All original policy/context/option assertions remain; path cases additionally prove the callback ran and the protected edit really occurred. Production admission guards were not weakened. Initial path failures are retained; final76 scope includes all-role no-context refusals. Old full01/26162's13-failure/910-pass intentional-stop receipt and earlier corrections remain retained.

**Isolated final S16 candidate a9ff632b263358a00d5ebb7f9344492d6876bde8**, /private/tmp/blacksmith-heavy-prd-scheduling-stage, feature/heavy-prd-scheduling-staging; baselinec4c18cd. Complete168-source/config/input/runtime/SQLite identity: **heavy-prd-turn-21-scheduling-source-03.json**. Tracked staging clean; copied spec/.venv excluded. The new93-case adjacent regression is live against that frozen identity: **heavy-prd-turn-21-scheduling-command-03.json**, log **heavy-prd-turn-21-scheduling-regression-03.log**. Launch handle was lost from truncated tool output; verified supervisorPID51538/pytestPID51552 own that log. Observe the authoritative PIDs/log without restarting. The original first30 and remaining63 cases are included; scale tests have separate scoped results and must also pass the final integrated full suite.

Old S16 scale/remaining65567 is terminal **1 passed/1 failed/2231.83s, exit1**. The actual100-unit chain passed under4d89e01: real CLI/shared child/Git/gates/review/ledger/checkpointer/scratch remote and301 fake model calls/exact3.01 fixture cost, retaining all source/approval/publication/cleanliness assertions. The20-root/default2 test failed its original20-second barrier because LangGraph1.2.9 queued its custom-stream waiter in the same bounded pool, leaving only one actual worker. Remaining63 cases were not run in that invocation. Source/terminal-log identity retained in turn20 scheduling-source-02.

S16 now owns a separate bounded node executor via a runtime LangGraph runner-submit hook, with queued-node cancellation on scope exit and active cleanup drained before recovery capture. Coordination/checkpoint streaming has at least two slots; real node concurrency remains the saved configuredN, includingN=1. Immutable contexts persist limits only, never the hook. No additional paid role/call is introduced. The original20-root barrier/max2 assertions and all four real-local-SDK cancellation modes passed in workers01 (**5 passed/1 failed/494.69s**); its single-worker case timed out because coordination itself still had one slot. The final minimum-two coordination fix passed **22/25.22s**, including actual max1 CLI, custom progress delivered before node completion with SQLite synchronous checkpoints, and queued cancellation/drain. All actual-worker maxima and timing assertions remain strict; child invocation configs retain original savedN. Intermediate failures/probes are retained. Fresh final staging ruff passed. Final integrated full including100/20 remains required; none of these scopes accepts S16.

After S15 is accepted, transfer **only c4c18cd..final S16 feature delta**. Preserve SIX newer primary S15 files absent from that older baseline/delta: blacksmith/executor.py; tests/test_executor_admission.py; tests/test_receipts.py; tests/test_receipt_wiring.py; tests/test_path_policy.py; tests/test_requirements_context.py. The integrated168-source set must match final S16 except those six differences, each matching accepted S15 hashes. Never replay/merge the copied baseline. Capture a fresh integrated environment/source manifest and run full pytest/ruff before S16 acceptance.

Disk pressure: only the completed accepted S14 full02 fixture was compressed/fully byte-and-mode verified, then removed after rechecking archive identity and no live owner. Archive **/Users/josh.smith/.codex/artifacts/blacksmith-heavy-prd-readiness/turn17-full02.tar.gz**,176850479 bytes, SHA256 **9c352f0b62c9d593c4a0d3ed4575bdb575c838b88019e877c0b5ce239648dc8e**. Manifest SHA256 **61fda4d83820bfdf566bb18878507ae30d3118d0bc64f0e1ced71375b879c52d**. Proof **heavy-prd-turn-21-archive-proof.json** includes637053 entries, modes/symlinks/content verification and exact restore command. Failed/running roots untouched; no protected .env input read. Initial archive-helper precondition failures retained. Fresh free-space check3.1GiB; monitor before another full run. Use pinned venv Python/SQLite3.53.1 for journal probes: host Python's different SQLite returned CANTOPEN, which is not proven corruption.

Only primary44404 and isolated93-case supervisor51538/pytest51552 are live at this update. Other named test handles are terminal. Next: observe exact live runs; audit frozen identities and final receipts, fix actual failures without weakening assertions, accept/locally commit S15 only after full green. Then checked S16 transfer/full proof; S17 allowed disabled sandbox/config rejection; S18 allowed explicit unsupported respond; S19 observability/docs/full matrix. Paid disposable-canary, human contract review and real Docker enablement smoke remain unperformed release gates. No external push/merge/live publication/paid canary this turn; preserve turn13 failed external-push disclosure. Goal active21/30; at30 stop/save precise handoff/pause, never automatically reset/resume.

## Turn19 handoff (historical; current turn20 block supersedes live-source status)

Counter **19/30**, never increment on compaction or a status question. S01-S14 remain accepted locally; primary base1afc9c2/S14 implementation8652ac2 has1219 full tests/ruff and146-source proof. S15 is implemented but **unchecked pending full verification**. S16-S19 remain outstanding.

Final source-only S15 staging commit **7a48358eebf855bd48a02ab2d0422b76fd0284ba** contains165 Python files at /private/tmp/blacksmith-heavy-prd-admission-stage, branch feature/heavy-prd-admission-staging. Its tracked tree is clean; copied spec/.venv excluded. The checked binary delta23ad7ff..7a48358 was transferred to primary without replaying the baseline. All165 source hashes match except tests/test_receipt_wiring.py, whose strict accepted S14 fixture is deliberately preserved and verified against turn17 environment-02. Both original input hashes are unchanged/untracked. Final source/config/installed dependency/Python/patch/receipt identity is in **heavy-prd-turn-19-environment-01.json**. Primary ruff and whitespace checks passed.

**Live full pytest handle26162**: `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t19-full-01 > docs/reviews/heavy-prd-turn-19-pytest-01.log 2>&1`. No primary Python edits after launch. Poll this same handle; never restart because an observation timed out. No S15 full-suite pass is claimed yet. All other test handles are terminal. Final transport17 passed **11 tests/7 retained SDK advisory warnings/108.67s**, including all four actual parallel cancellation modes and installed SDK deadlines. Prior failed and intentionally stopped batches remain retained in the evidence record.

S15 includes atomic all-role SDK grants, unknown-spend charging, explicit unlimited policy, started/denied/overrun recovery, unique partial transcripts, honest reports/metrics/PR accounting, persistent execution time excluding approval waits, conservative crash/cleanup timing, native/model deadlines and owned SDK cleanup. Migration guards run before native observations. Real graph/CLI/Git/installed-SDK local-process tests prove cancellation before LangGraph waits and recovery capture, including SIGINT/SIGTERM/critical scope-entry faults. The normal-exit Darwin EPERM race is reconciled only with complete positive PID/PGID/state evidence of no live group; live/unknown observations fail critically. All trace/failure/repeat receipts are retained. No live provider billing or physical remote orphan guarantee is inferred.

Next: wait for full26162 terminal result; audit exact165-source/config/input/dependency identity, fix actual failures without weakening assertions, then commit/check S15 only when green. Finish S16 scheduling/recursion, S17 explicit disabled-feature rejection or real sandbox lifecycle, S18 hardened respond or explicit unsupported error, and S19 observability/docs/full matrix in dependency order. Live paid disposable-canary, explicit human contract review and real Docker enablement gates remain unperformed. No paid/publish/merge authorization. Preserve turn13 failed external-push disclosure. At30 stop, precise handoff and pause; full goal remains active.

Turn18 authoritative update (historical): counter18/30. S14 full02 handle34910 is now explicitly terminal exit0: **1219 passed in2297.64s (38:17)**, no warnings/skips/xfails. Complete146-source set/hashes, both original inputs, Python3.12.13 and installed dependency versions match environment-02 before/after. Full/ruff receipt hashes saved there; fresh primary ruff18 passed. S14 is accepted locally and committed8652ac237f3f10ab2ac10f37ab40eec15b4de00d; host pre-commit and post-commit146-source/input/full/ruff hash/whitespace checks passed. Prior failed full01 and all crash/scoped receipts remain retained. No live publication/canary evidence is claimed.

S15 remains isolated and unchecked. Latest source-only WIP **1d9841f5e99368b5f92ef01cd9aab0a2fd10f01a** (followsf2ff68b/fec4d88) in /private/tmp/blacksmith-heavy-prd-admission-stage contains156 Python sources. Exact identity/receipt hashes: heavy-prd-turn-18-admission-source.json. Tracked staging source is clean; .venv/copied spec excluded. Primary S14 source stayed frozen. Transfer only the delta against23ad7ff after the S15 audit, never baseline replay or overwrite the primary receipt fixture fix.

Turn18 S15 adds explicit CLI unlimited policy, typed review denial, budget-only critic/implement/review/summary denied-before-start recovery preserving identities/counters/valid seats, authoritative ledger routing, unique call-based transcripts/metrics refs, and owned SDK POSIX process-session cleanup. SDK49/152.18s; expanded admission/SDK/transcript74/181.34s; actual CLI recovery02=5/100.10s. Clock foundation01=5/5.99s; policy and graph-step/model/native integration02=36/119.94s. Further actual CLI approval-wait exclusion/nondefault call grant and installed SDK remaining-run deadline passed in forwarding03 (5 passed/1 failed/54.13s); native receipt-kind fixture corrected from command. to command:, target04=1/2.57s. Ruff08 passed. All test handles are terminal. Failed fixture/ruff receipts retained; no assertions weakened. No full S15 suite or local global acceptance yet.

S15 remaining: started SDK budget-stop/unknown summary and planner/critic recovery (new explicit identity under allowance); completed-overrun recovery by budget amendment and cache reuse; cancellation/KeyboardInterrupt partial known turns/cost/transcript retention; critical timer-failure and real restart clock proof; final before-new-unit budget audit; honest actual-overrun/incomplete-accounting/deadline reports; config sample/runbook updates and complete matrix/full pytest/ruff with fresh source/environment proof. No live provider/billing guarantee. See admission-plan.md for exact source and integration details. S15-S19 remain required. Goal stays active18/30; do not increment on compaction; stop/save/pause at30.


Turn17 authoritative handoff (historical entry, do not increment on compaction):

- Primary feature/heavy-prd-readiness remains on accepted S13/cab3050 with uncommitted S14 source/docs. Original spec/probe unchanged/untracked. Checked transfer initially matched146 saved WIP hashes; primary audits added remote refresh after summary and strict terminal URL/status/QA/approval checks. Audit-02 passed11/150.98s; saved WIP consolidated-01 passed67/925.74s, both handles terminal. Runtime/docs/R07 acceptance mapping is implemented but S14 stays unchecked until final full proof.
- Full01 handle75173 was deliberately SIGINT-stopped after a diagnosed legacy fixture seam failure: terminal1 failed/869 passed/1915.63s. Test mock targeted old open_pull_request and never reached the intended model.summary journal fault because S14 queries first. Strict query-only fake preserves all original call-count/critical-error assertions and adds zero-effect assertions. Stage receipt-03 passed2/11.46s; primary receipt-04 passed2/8.41s. No assertions weakened. Source/failed receipt identity retained in environment.json. Fixture fix is saved separately on S14 staging branch as7a1af22, already copied to primary; do not overwrite primary audit changes with the old staging publisher.
- **Live final full02 handle34910**: `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t17-full-02 > docs/reviews/heavy-prd-turn-17-pytest-02.log 2>&1`. Exact146 primary source set/hashes, basecab3050, versions and unchanged inputs captured in turn17 environment-02.json before launch; final-02 ruff passed. No primary Python edit after launch. Expected1219 is not a test result. Poll the same handle, never restart on timeout. All other test handles are terminal.
- Isolated S15 partial: /private/tmp/blacksmith-heavy-prd-admission-stage, branch feature/heavy-prd-admission-staging. Baseline23ad7ff copied all146 frozen S14 sources before the one legacy fixture correction. Latest WIP **fec4d88c216ff9b3b8edb700b491ae5c256f7120** follows bc11a18/a10683e;149 Python source hashes are in budget-source.json. Transfer only its delta against23ad7ff after S14 acceptance; do not merge/replay the baseline. Exclude copied untracked spec and .venv symlink. Source not in primary.
- S15 prototype includes atomic critical ledger, policy bootstrap, CallContext immutable run identity, every Executor role's reservation/SDK max_budget_usd/settlement and cached original option reuse, a pre-executor resume policy check and initial config defaults50/5/1800/21600. Unknown/nonterminal partial usage consumes full reservation; known actual cost is read inside admission transaction before settlement, with overrun refusal. Foundation04 passed18/11.58s; combined ledger/Executor roles01 passed33/19.88s; actual CLI both shapes normal/draft02 passed4/117.04s with all7 calls bound to grants and .07 settled cost. Focused ruff10 passed. No full S15/local global acceptance claim; model transport remains replaced.
- S15 still needs CLI --unlimited-cost wiring, typed denied-before-start/unavailable handling, budget-only recovery without consuming continuations and valid-seat option preservation, budget-aware graph/new-unit decisions, SDK timeout/cancel/owned child cleanup, persistent run clock excluding approvals, native remaining-deadline/cancel propagation, unique call-based transcripts, reporting/docs/full matrix/full pytest/ruff. Budget overrun/SDK cap-stop must halt optional summary/critic publication paths. Exact remaining map: heavy-prd-admission-plan.md. Partial config declarations are not completion.
- No subagents, push/merge/live publication/paid canary/Docker smoke in turn17. Scratch bare pushes are offline fixtures. Keep the turn13 failed unintended push disclosure through final goal report. S14-S19 and explicit human contract.py review before merge/live gates remain outstanding. Goal remains active17/30; stop/save/pause at30 if unfinished.
- Status-request verification (same turn17): full02 handle34910 remains live, log reached59% plus31 cases without a reported failure. All146 primary Python sources still exactly match environment-02 and both original inputs are unchanged. Final brain update to Projects/blacksmith.md passed targeted lint0/0, index errors0/invalidFrontmatter0 after more than70 seconds, fresh semantic retrieval and exact whole-note readback (544da5de-c999-408b-83e6-7f5ddf93faa1); positive feedback recorded once. S14 remains unchecked until terminal full proof. The status request does not reset/increment the counter or cancel the active goal.

Historical turn16 details follow for receipts; current live handle/source is the block above.

Counter **17/30**, incremented16->17 once after objective/repository/ledger/brain/current-source checks. All previous handles verified terminal. Turn16 closeout details follow: Do not increment on compaction or tool calls. Previous turn was progress; this turn completed S13 local acceptance/commit/brain proof and implemented unaccepted S14 in isolation. The full S01-S19 goal remains active. At turn30 stop implementation, save the precise handoff and pause under the explicit user authorization.

**Accepted primary:** feature/heavy-prd-readiness, S13 implementation3581d289088a1f3ec4010bd1a62a40bea005aaee. S01-S13 are complete locally. Full S13 command `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t15-full-01` finished **1181 passed in2097.72s (34:57), exit0, no warnings/skips/xfails**; handle59474 explicitly terminal. Ruff15 and refreshed primary ruff16 passed. Complete143-source set/hashes, original inputs, dependencies and final receipt hashes match turn15 environment JSON before and after commit. Host pre-commit and source/docs git-show whitespace checks passed, excluding only raw log receipts. Original spec/probe remain unchanged/untracked/unstaged. No source changed in the primary while its full suite ran.

S13 production proof: common normal/terminal-QA final gate -> deterministic prepared proposal checkpoint -> real PR interrupt -> normal/draft route. Immutable per-round critical proposal/decision artifacts bind exact clean known candidate, branch/base/origin/draft, automatic unit/final gate/review references and pending QA. Both publisher entries guard before summary and each effect; exact SHA refspec without force. Plan-only/stale/forged/missing evidence refuses with retained work. Full cumulative PR patch is private and independent of capped native stdout; preview is explicitly bounded. Restart reuses completed decisions, holds incomplete intent, makes a fresh round after explicit rejection recovery, and refuses legacy/corrupt files before executor. CLI0 DONE/2 AWAITING_QA/1 failure with QA/check/draft URL. Runtime docs: heavy-prd-approval.md. Failed development receipts remain retained.

**Unaccepted S14:** separate local worktree `/private/tmp/blacksmith-heavy-prd-publication-stage`, branch feature/heavy-prd-publication-staging. Source-only S13 snapshotf14580b matched all143 primary sources. WIP c3286f0ef5f1caf6ea00f9c6c46f6434612da979 durably saves only the10-file S14 delta. Do not merge the branch or replay its baseline. Transfer only f14580b..c3286f0 (or later fixes) using checked git-apply after this S13 closeout. Staging .venv symlink and copied untracked spec are excluded; never stage either. The WIP contains146 Python source files; manifest/receipt hashes are in `heavy-prd-turn-16-staging-source.json` in the primary. All finalized staging logs were copied into primary docs/reviews.

S14 WIP wiring: new publication service validates explicit remote ref and paginated API repository/base/head/SHA/draft/run marker before effects, checks both fetch and push endpoint identity, halts conflicts/multiple/foreign matches without summary or force, persists summary content/cost plus critical local seals, observes after push/create including error exits, reuses exact positive remote effects and persists a validated publication result. Unknown native create without positive PR observation holds. Normal/draft nodes, state markers, pre-executor migration/evidence validation and sealed explicit publication-stage retry are wired. Summary cost identity is __run__; prepared PR metadata includes its summary event idempotently. No S14 acceptance or live-publication claim.

All test handles are terminal. Scoped S14: integrity-02=17 passed/4 failed/265.88s (all13 protocol and4 migration passed; four exact exit assertions caught an argv-position error in the new crash harness). Corrected recovery-03=4 passed/4 deselected/69.12s proves actual exits106-109 after push/create/result/content seal with original cost/candidate/intent reuse and no duplicate effects. Edges-05=2 passed/17 deselected/74.94s proves unknown-native hold/positive reconciliation and missing completed branch refusal. Boundaries-06=4 passed/43 deselected/120.60s proves critical push-intent/outcome faults and two foreign push-URL cases. Wiring-02=36 passed/579.29s against the original S13 approval/recovery cases; later-added push-URL cases separately covered. Staging ruff-12 passed. Earlier known accounting-normalization failures, explicit SIGINT stops, transient insertion-metadata comparison failure and failed crash-injection receipts remain retained. No assertions weakened/skips/xfails.

**S14 remaining:** fresh consolidated67-case run against the exact current146-source WIP, then primary transfer, final full pytest/ruff with a fresh source/dependency record, publication runbook/README/architecture/migration/recovery docs, final R07 acceptance audit and brain/local commit. Expected full total1212 includes31 new cases, but this is not a test result. Validate artifact seals, complete receipt invariants, no duplicate effects/costs and terminal no-op resume. Preserve legacy explicit-approved library seams only; versioned missing metadata refuses. Do not manufacture historical receipts.

Brain: existing Projects/blacksmith.md updated through MCP with S13 commit/completion/full1181/143-source proof, turn16/30, isolated unaccepted S14, S14-S19/live holds and historical failed-push disclosure. Target lint0 errors/0 warnings. After89 seconds, index lastRun.errors=0 and invalidFrontmatter.count=0. Semantic retrieval and exact full-note readback verified all written content (`bd694404-7749-4e8b-8701-67b1c5534cb1`); positive feedback once. No memory-folder write.

No push, merge, live publication, paid canary or Docker smoke in turn16. Scratch bare-remote pushes are offline fixtures. Preserve the historical turn13 unintended failed push disclosure below through the final goal report. S14-S19, explicit contract.py human review before merge and all live gates remain outstanding. This closeout is recorded by a separate docs-only commit; no S13 implementation work remains.

## Historical turn 14 closeout

S01-S12 are complete under the scoped offline acceptance audit in `heavy-prd-evidence.md`. Full S01-S19 goal stays **active at 14/30**, with S13-S19 and all live gates outstanding. Do not increment on compaction. No subagents. Original spec/probe remain unchanged/untracked. All turn-14 source changes were verified against the pre-run source hashes; local commit/brain proof is recorded below.

S12 production wiring: `review_patch.py` captures full cumulative unit-base/candidate binary/rename patches through Git --output, independent of native command-log caps; <=128 KiB inline, larger explicit manifest plus bounded read-only read_diff MCP. `review_receipts.py` strictly validates one complete JSON verdict, finding fields/severities and typed panel/receipt evidence, with critical intent/seal/artifact/outcome and primary-key model-cost references. Any panel blocker blocks; unavailable halts without automatic retry; disabled has a typed disabled receipt and no reviewer call. Shared child graph halts exhausted blockers. Receipts cross keyed outcomes/joins/final/publication entry and validate before CLI executor on resume. Saved enablement/panel size and review_version=1 are enforced; old active metadata refuses without inventing hashes or changing checkpoints. Explicit unavailable recovery replaces only bad/unknown seat identities under remaining allowance, preserving valid seats, original candidate/gate/counters/costs/failure receipts. An unchanged blocked candidate cannot seek another vote; a budget addition can permit a real remaining code revision. Latest typed receipts drive current status while historical findings remain; revision attempts never count resolved findings.

## Turn 14 verification

- Final full command: `uv run pytest -q --basetemp /private/tmp/blacksmith-heavy-prd-t14-full-01 > docs/reviews/heavy-prd-turn-14-pytest-01.log 2>&1`, **exit 0: 1138 passed in 1136.16 seconds (18:56)**. Handle `14689` explicitly polled terminal. No warnings/skips/xfails. All 54 new S12 cases and existing regressions included. Fresh explicit temporary roots avoid repeated old read-only snapshot cleanup; no warning suppression or skipped checks.
- `uv run ruff check`, exit 0. All **140 Python source hashes and the complete source-file set** match the pre-run record. Environment records installed versions, base `123b4ed`, exact source/input and final full/ruff hashes. Source/docs whitespace excludes only verbatim raw `.log` receipts. No dependency/lockfile change or weakened assertions.
- Import-01: 2 collection errors (cycle fixed by local atomic_write import). Wiring-01: 20 passed/3 failed/56.20 s (two invalid `blocked` protocol fakes corrected to needs_changes, plus obsolete exhausted-publication expectation changed to strict halt/no effects).
- Integrity-01: 32 passed/1 failed/64.62 s. Full large reviewer evidence passed, then unrelated optional PR diff display hit native-log truncation; failure retained for S13. Large-patch-02: 1/36.50 s, actual registered RPC plus full bounded >1 MiB reconstruction/hash under 4096-byte command cap, optional PR display disabled only to isolate S12.
- Review-regression-01: 62 passed/2 failed/104.07 s (late obsolete pass assertion and disabled no-PRD library seam); corrected regression-02: 13/44.82 s. Optional writer, consumed state/counters and source assertions preserved.
- Recovery-01: 10/52.48 s, including real process exits before seal/after seal-before artifact/after receipt-before checkpoint, no completed-SDK replay, valid-seat reuse and unchanged-blocker refusal. Integrity/recovery-02: 56/190.37 s. Render-regression-01: 62 passed/10 failed/38.41 s (old false-clean/resolved/publication assertions); corrected render-regression-02: 81/38.11 s. Blocker-budget-01: 1/7.37 s, actual revision/new SHA/latest clean reporting without erasing old blockers. All failed receipts retained.
- All test/lint handles are terminal. Current local verification is not general readiness, exactly-once provider billing, live cancellation, global admission or successful publication evidence. No paid canary/merge/successful publication/Docker smoke ran in turn 14.

## Turn 14 durable closeout

- S12 implementation committed locally as `5f483e71af1f02106875d5385b24715a4f90fea4`; host pre-commit checks passed. Post-commit Python-source set/hashes and full/ruff receipt hashes match final proof. `git show --check` passed excluding only verbatim raw `.log` receipts. Only the two original unchanged untracked inputs remained.
- Existing `Projects/blacksmith.md` updated through MCP with S12 completion, turn 14/30, exact 1138-test proof, S13 approval/display handoff, S13-S19/live boundaries and preserved turn-13 failed-push disclosure. Targeted lint: 0 errors/0 warnings. After 83 seconds, index lastRun.errors=0 and invalidFrontmatter.count=0. Semantic retrieval and exact full-note readback verified every scope/counter/proof item (`b3344637-d931-4b46-8865-c452caae5251`); positive feedback recorded once.
- All test/lint/implementation-commit handles are terminal. No push/merge/publication/paid canary/Docker smoke performed in turn 14. The full native goal stays active; next automatic continuation is turn 15, S13. A user status question during closeout did not cancel or replace the objective.

## Historical boundary exception (retain through final goal report)

Turn 13's missed test crash injection patched impl._git instead of the actual impl.run_command, reached legacy publication and attempted a real push to a synthetic GitHub fixture remote. The critical receipt failed 128 before authentication, with verified username-unavailable stderr; no gh creation command or successful publication occurred. Exact receipt/hash/stderr: `heavy-prd-turn-13-unexpected-push.json`. Do not claim no push attempt occurred across the goal.

Correction preceded all later tests: fixed injection, plan-only approval and child publication guard; pytest denies native network Git protocols (scratch file transport allowed), isolates gh config and empties publication tokens so dotenv cannot restore credentials. Host Git settings unchanged. Python socket guards alone had not protected native Git children.

## Next action after turn22

Current counter stays **22/30**. S15 accepted and locally committed44f0108 under full
1325/ruff/165-source proof. S16/S17/S18 implemented and source-integrated as
fc94de2/24e52e1/33eba45 with exact staged-scope proof; remain unchecked pending final
combined full. All test/lint handles terminal; archive prepare4182 remains live. Observe that same
handle/proof before cleanup and implement S19 from current169-source
primary or identical final-stage e51d653, then final full pytest/ruff including100/20
with fresh source/runtime proof. Archive only completed accepted fixtures if space is
needed; preserve failed/running roots and original inputs. Next automatic23 increments
once; compaction/status/tools never increment. At30 save precise handoff and pause.

Later interactions: S15 must keep budget denial typed unavailable, distinguish denial-before-start from admitted/uncertain calls, and permit budget-only recovery without consuming a continuation for an unstarted call. Keep valid cached seat options/reservations when remaining cap changes. S19 must fix the keyed review-revision display tally (recovered child totals can still add twice), bounded finding/error/history presentation, validation/identity of best-effort streamed findings, and stale ReviewConfig/legacy helper/test docstrings describing earlier advisory behavior. Unit-owned counters/caps and current strict-review routes are already authoritative. No global invoice ceiling or physical orphan-termination claim.

At turn 30 stop implementation, save precise completed/outstanding/verification/next-action handoff, call update_goal(paused) under explicit user authorization and stop. Never mark unfinished work complete, reset the counter or resume automatically.


Turn18 durable closeout: S14 implementation8652ac2 remains accepted with final1219/ruff/146-source/input/dependency/receipt identity. Isolated S15 WIP1d9841f has156 exact sources and a clean tracked tree; no transfer/full S15 claim. All test/lint/commit handles are terminal. Projects/blacksmith.md updated through MCP; targeted lint0 errors/0 warnings. After more than130 seconds, index lastRun.errors=0 and invalidFrontmatter.count=0; fresh semantic retrieval and exact entire-note readback verified every written item (2f77f504-aead-4c31-bb32-8d26e01d7152), with one positive feedback. Original spec/probe unchanged/untracked/unstaged; no memory-folder write. Full S01-S19 goal remains active18/30, next automatic continuation19, with S15-S19 and live gates incomplete. Historical turn13 failed push exception retained. No push/merge/live publication/paid canary/Docker smoke in turn18.


Turn19 verification handoff: full primary pytest26162 remains running; S15 unaccepted, sources frozen. Source-only staging7a48358 holds the exact165-file candidate and primary preserves the strict accepted fixture. Projects/blacksmith.md updated through MCP, targeted lint0 errors/0 warnings. After80 seconds, index lastRun.errors=0 and invalidFrontmatter.count=0; fresh semantic retrieval and exact entire-note readback verified all written content (c461886e-55bb-4a6e-a30f-168516340d2b), one positive feedback. Original inputs unchanged/untracked; no memory-folder write. Goal active19/30, next automatic continuation20, with S15-S19/live gates incomplete. Historical turn13 failed external-push exception retained. No external push, merge, live publication, paid canary or Docker smoke in turn19.


Turn19 local documentation/evidence commit **8883fdc** saves the handoff and terminal receipts; S15 source remains uncommitted in primary while full26162 runs, with source-only staging7a48358 as durable candidate. The all-file cached whitespace check reported original whitespace in raw failed pytest receipts. Those bytes were preserved; scoped Python/config and Markdown/JSON whitespace checks passed, as did host precommit. This is not an all-receipt whitespace-clean claim. Post-commit all165 sources, configs, inputs and recorded raw-receipt hashes are unchanged; no staged files. Full26162 remains live; do not restart on timeout.


Turn20 durable verification handoff: primary full26162 and isolated consolidated49029 are both authoritatively live, all other handles terminal. Exact primary165 and isolated168 source/config/input/Python/dependency identities remain unchanged. S15/S16 remain unchecked; S01-S14 accepted. Additional actual library invalid-input probe halted before planning/workspace/spend/approval/publication; receipt turn20-scheduling-invalid-library.json. Projects/blacksmith.md updated via MCP; targeted lint0 errors/0 warnings. After135 seconds index errors0/invalidFrontmatter0, fresh semantic retrieval and exact whole-note readback verified all written content (f1611cba-cbe3-4ef6-969f-80796d538d7f), one positive feedback. No memory-folder write. Full goal active20/30; next automatic continuation21; stop/save/pause at30 if unfinished. Historical turn13 failed external-push exception retained. No external push/merge/live publication/paid canary/Docker smoke in turn20.


Turn20 final durable closeout: S01-S14 accepted, S15/S16 unchecked. Corrected primary165-source S15 candidateb123b50/full02 live3281; isolated168-source S16 candidate4d89e01/scale+remaining live65567. All other handles terminal, including failed full26162 (13 failed/910 passed/6 warnings/2181.76s), stopped S16 consolidated49029 (30 passed/223.38s), final SDK guard65/189.14s and primary combined96/126.74s. Exact source/config/input/runtime/dependency identities rechecked unchanged after launch; ruff/source whitespace passed. Projects/blacksmith.md corrected via MCP to these authoritative terminal/new-live states. Final targeted lint0 errors/0 warnings; after83 seconds index lastRun.errors0/invalidFrontmatter0. Fresh semantic retrieval and exact whole-note readback verified every written item (88c81828-92b5-49c4-a9bb-0edc1b53c42f), one positive feedback. Earlier interim brain/handoff claims about old live handles are explicitly superseded. No memory-folder write; original spec/probe preserved/untracked/unstaged. Goal active20/30, next automatic continuation21. Stop/save/pause unfinished work at30. No external push/merge/live publication/paid canary/Docker smoke this turn; historical turn13 failed external-push exception retained.


Turn21 durable handoff validation: Projects/blacksmith.md updated through MCP; targeted lint0 errors/0 warnings. After99 seconds index lastRun.errors0/invalidFrontmatter0, fresh semantic retrieval and exact entire-note readback matched every byte (368e239d-03a8-4fde-876b-368f31f48186); positive feedback once. Receipt heavy-prd-turn-21-brain-verification.json. No memory-folder write. Exact165/168 source/input/config/runtime audits passed while primary44404 and isolated regression supervisor51538/pytest51552 stayed live. S15/S16 remain unchecked; S01-S14 accepted; S17-S19/live gates outstanding. Counter21/30 unchanged by status question/compaction/tools; next automatic22; stop/save/pause at30. Historical turn13 failed-push exception retained.

## Turn22 continuation audit

Counter21->22 persisted once on the actual automatic continuation. Previous turn was progress. Re-read objective and repository instructions, refreshed memory/brain handoff and matched complete165/168 source/config/input identities. Primary44404 and isolated supervisor51538/pytest51552 are authoritatively live. No restart or source edit. S01-S14 accepted; S15/S16 unchecked; S17-S19/live gates outstanding. Current-turn entries below supersede historical handoffs. At30 stop/save/pause unfinished work.


### Turn22 remaining observability trace (future S19, not implemented)

Read-only inspection on current S15/S16 source confirms these remaining S19 changes: unit_graph.unit_runner returns a recovered child's cumulative review_revisions_total into an additive parent reducer (state.py), while each latest keyed unit_outcome already contains the exact per-unit review_revisions. Emit only each unit's new delta and derive presentation totals from current keyed outcomes so historical inflated reducer values are not promoted to exact counts. Keep attempt/cap counters unchanged; add an actual blocking->revision->unavailable->explicit recovery case asserting total1 rather than2 and preserved previous receipts/costs.

Renderer.report and PR/gate finding/error loops remain unbounded; bound presentation with explicit omitted counts/full artifact references, while preserving full critical receipts/history/immutable requirements. Executor._extract_review_findings currently accepts arbitrary dictionary findings and can emit findings after missing terminal because is_error stays false; reuse strict parse_verdict validation and require a successful terminal result before optional finding emission. Bind activity to call/run/unit/attempt/task identity and bound text at presentation. Optional live-store creation/event and metric failures are swallowed silently; add bounded diagnostics while retaining best-effort continuation. ReviewConfig and some legacy state/helper docstrings still describe advisory/proceed-after-blocker behavior; update to actual strict receipts/halts. Existing enabled-sandbox lifecycle assertions are intentionally replaced by S17 no-effect rejection under the spec's explicit disabled alternative; low-level primitive tests are not Docker enablement proof.


### Turn22 pending accepted-S15 fixture archive

Free2.0GiB. Owner scan3566 for /private/tmp/blacksmith-heavy-prd-t21-full-03 completed
exit1, no process/file records; known other-device HFS warnings were strictly parsed
by the helper before scanning. **Prepare handle4182 is live**, command pinned venv
Python /private/tmp/blacksmith-heavy-prd-turn-22-archive-s15.py prepare; log
heavy-prd-turn-22-archive-s15-prepare-01.log. Poll same handle; no restart. Helper
verifies accepted1325-test receipt hash, excludes protected.env/special entries and
hardlink aliases, archives without following symlinks, then verifies every regular
byte/mode/symlink before replacing partial archive/writing proof. Destination stable
artifacts directory, turn21-full03.tar.gz and turn21-full03-manifest.json; proof
heavy-prd-turn-22-archive-s15-proof.json when complete. **No cleanup started**.

After terminal exit0, reverify archive/manifest SHA/proof/root identity and no protected
entries; only then make owned directories writable by O_NOFOLLOW/O_DIRECTORY fd
(no file/shared-hardlink chmod), use symlink-resistant rmtree on that completed root,
record cleanup status/results/restore command and free space. All failed/running test
roots stay untouched. If protected entries exist, retain them and do not blanket
remove the root. Existing S13/S14 archives remain verified; restore only after SHA
verification and safe destination checks. Next actual automatic continuation23;
compaction/tool calls never change the counter22.


Turn22 durable brain validation: Projects/blacksmith.md updated via MCP with S15 accepted1325/44f0108, exact S16-S18 source integration at33eba45, S19 concrete remaining work, archive4182 resource handoff and limit22/30. Targeted lint0 errors/0 warnings. After more than148 seconds, index lastRun.errors0/invalidFrontmatter0; fresh semantic retrieval and exact entire-note readback verified every written byte (64530a99-cca0-4678-a27e-0b00af24bc91), one positive feedback. Receipt turn22-brain-verification.json. No memory-folder write. Test/lint handles terminal; accepted S15 archive prepare4182 remains live at last authoritative poll. Goal active22/30; next actual automatic23. Original inputs preserved/untracked/unstaged. Stop/save/pause unfinished work at30; historical turn13 failed external-push exception retained.

## Turn23 continuation audit

Counter22->23 persisted once after objective, repository instructions, memory/brain and exact169-source/config/input/all-distribution/Python/SQLite checks. Previous turn was progress. Archive4182 authoritatively terminal0; proof says archive verified and cleanup not_started. Other test/lint handles terminal. Source HEAD401a9a2; S01-S15 accepted, S16-S18 integrated/scoped but final full pending; S19 required. Free1.5GiB before cleanup; no new full run until sufficient space. Stop/save/pause unfinished work at30.
