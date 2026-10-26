# CI host-tool isolation and feedback, 2026-10-26

PR #117's first Linux run reported **1 failed, 1430 passed, 5 warnings in
3167.14 seconds (52m47s)**. The workflow cancellation request raced with its
completion; the retained test-step log contains the complete failed result.
It is not a passing CI result. Original run: `40385425940`, head `21806a7`.

The only failure was
`tests/test_repo_consistency.py::test_main_exits_nonzero_on_mismatch`.
The test expected a repository-mismatch diagnostic, but host-tool preflight
stopped first with `required host tool is unavailable: claude`. A Mac with the
CLI installed passed that test; the runner did not have the prerequisite.

A local reproduction that hides `claude` fails with the identical assertion and
diagnostic. The fixture now supplies the prerequisite tool-discovery result,
while its Git observations remain real. Existing exit-code, both repo-name and
no-workspace assertions are unchanged. The missing-CLI reproduction now passes.
No production Python, runtime configuration, model allowance or assertion changed.

## Verification and CI changes

- The CLI/native-cleanup/SDK-permission preflight group: **26 passed**, one
  existing SDK advisory, in **3.21s**. Ruff and source/workflow whitespace pass.
- CI runs that fast group before expensive regression work. Each pytest run now
  stops on its first failure, names active tests and reports slowest durations.
- The standard partition collects **1422 tests**; the real scheduling partition
  collects **9**, including the unchanged 100-unit and 20-root tests. Independent
  collection proves their disjoint union is the complete **1431-test** suite.
  They run concurrently on separate runners with a bound of two jobs. No test is
  skipped or replaced with a permissive fake to improve elapsed time.
- The existing required `test` check is preserved as an aggregate. All16 pairs
  of success/failure/cancelled/skipped prerequisite statuses were checked; only
  two successful prerequisites make it pass. Older CI runs are superseded when
  new commits arrive. Per-job deadlines prevent an indefinite run.

The full suite remains substantial. Partitioning removes serial scheduling-file
time from the standard job's path; the actual elapsed-time improvement is not
claimed until the new CI run completes. Timing reports enable the next focused
performance investigation. No additional multi-hour local suite or paid model
run was started for this fixture/workflow change. Fresh partitioned CI remains
required; the prior 1431-pass Mac result describes the pre-fix fixture identity.

Evidence: `heavy-prd-ci-followup.json`; local red/green reproductions and complete
old CI log are under the existing readiness artifact directory. Original spec,
probe, failed canary and historical receipts remain intact. Goal counter stays28/30.
