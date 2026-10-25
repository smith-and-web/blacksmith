# S15 acceptance audit

Goal22/30: **accepted locally**. Complete full03/44404 finished **1325 passed,
6 SDK advisory warnings in3373.29s (56:13), exit0**. All165 Python sources,
config/input hashes, complete installed distribution set, Python3.12.13 and
SQLite3.53.1 match heavy-prd-turn-21-environment-03.json. Fresh primary ruff22
passed. No skips/xfails or warning filtering. Live release gates remain unrun.

| Specification requirement | Production wiring | Named regression evidence |
| --- | --- | --- |
| Finite defaults and explicit unlimited choice | LimitsConfig defaults, fresh CLI --unlimited-cost and immutable budget policy | test_budget_admission: settled_spec_defaults_are_finite_and_per_call_bounded, uncapped_run_requires_explicit_recorded_choice_and_still_reserves_per_call; test_admission_recovery: unlimited_cost_is_explicit_and_frozen_in_real_cli_policy |
| Atomic min(call cap, unreserved funds) before transport | BudgetLedger.reserve uses BEGIN IMMEDIATE, validated model identities and a committed grant; Executor replaces the exact SDK max_budget_usd before model intent/transport | test_budget_admission: concurrent_reservations_sum_within_run_cap_and_denials_start_no_model, same_call_reservation_is_idempotent_and_survives_new_ledger_owner; test_executor_admission: each_role_receives_reserved_sdk_budget_and_persists_exact_call_charge |
| Every default SDK role has immutable admission context | Executor._run refuses absent context before build_options/credentials; all six role wrappers share it | test_executor_admission: default_sdk_without_context_refuses_before_options_transport_or_journal_change, each_role_is_denied_before_model_intent_and_transport_when_cap_exhausted, real_cli_both_shapes_and_publications_bind_every_paid_role_to_grants |
| Completed cost controls subsequent admission even before settlement | Balance derives charges from critical model outcomes, validates matching reservation/budget and keeps original cached grant/options | test_budget_admission: recorded_actual_cost_controls_next_admission_before_separate_settlement; test_executor_admission: cached_partial_cap_keeps_original_option_and_seals_a_lost_settlement |
| Unknown usage never becomes free or an estimate | Missing/nonterminal usage consumes full grant, preserves measured cost separately and sets incomplete accounting | test_budget_admission: missing_cost_consumes_full_reservation_and_remains_incomplete_across_restart, known_zero_cost_is_measured_zero_and_refunds_the_full_reservation; test_executor_admission: nonterminal_partial_cost_is_still_charged_at_full_reservation |
| Overrun is visible and stops all later paid roles | Reservation's admitted cap and measured usage preserve unacknowledged overrun; even advisory summary/critic paths propagate ModelCallStopped | test_budget_admission: overrun_is_visible_before_settlement_and_denies_every_later_call; test_executor_admission: actual_overrun_stops_every_role_including_advisory_paths |
| New-unit admission and durable recovery cannot reset funds | Shared unit runner checks authoritative balance; sealed budget amendments distinguish denied, started, unknown and completed-overrun calls | test_unit_admission: next_unit_budget_denial_preserves_work_and_budget_only_resume_does_not_replay_siblings; test_admission_recovery: budget_only_resume_retries_denied_call_without_continuation_or_replay; test_started_call_recovery: started_call_resume_preserves_original_evidence_and_uses_only_required_allowance |
| Unique partial call transcripts and critical usage identities | UUID/call-key transcript paths, captured partial turns/usage and terminal/interrupted/unknown journal outcomes | test_interrupted_accounting: cancelled_paid_call_retains_known_progress_usage_and_unique_transcript; tests/test_transcript.py and tests/test_receipts.py retain original replay/changed-input assertions |
| Active time persists and excludes approval/offline waiting | One shared execution_scope, pre-reserved bounded journal slices, monotonic time, conservative crashed-slice cleanup allowance | test_run_clock: clock_excludes_pause_and_nested_parallel_work_counts_wall_time_once, deadline_is_persistent_and_cannot_reset_on_resume; test_clock_recovery: fresh_process_resume_conservatively_charges_crashed_slice_without_offline_pause; test_clock_forwarding: real_cli_approval_wait_has_no_execution_clock_and_saved_limits_reach_sdk |
| Remaining run time bounds model/native operations | Executor._bounded_collect and checked native commands use remaining execution time as an upper deadline | test_sdk_deadlines: remaining_run_deadline_bounds_actual_sdk_before_longer_model_timeout; test_clock_forwarding: native_command_uses_remaining_run_time_and_reaps_before_return |
| Owned SDK processes stop before recovery capture | Both SDK entry APIs use owned POSIX session transport; TERM,5-second grace,KILL/reap; signal/critical-failure cancellation reaches sibling workers before graph waits | test_sdk_deadlines: both_real_sdk_entry_paths_timeout_and_kill_descendants, raw_asyncio_cancellation_finishes_owned_child_cleanup; test_parallel_cancellation: parallel_sdk_writers_are_stopped_before_cli_recovery_capture; test_clock_recovery: critical_timer_failure_stops_sdk_writers_before_return_and_preserves_partial_receipt |
| Cleanup permission failures do not falsely prove termination | EPERM reconciles only with complete bounded positive no-live-group inventory; unavailable/live evidence stays critical | tests/test_sdk_transport_signals.py: live-group denial, positive empty inventory and unavailable-inventory cases |
| Legacy/corrupt policies stop before new effects | Resume checks saved inputs/budget/clock policy before native scope or executor construction; missing historical usage/time is never invented | test_admission_migration: policy_migration_refuses_before_native_executor_or_journal_mutation; test_budget_admission and test_run_clock missing-policy/corruption cases |
| Honest CLI, PR and metric accounting | accounting.snapshot/cost_line/clock_line, nullable total plus separate measured/charged/reserved/overrun fields, additive metrics migration | tests/test_accounting_reports.py: unknown usage, visible overrun and legacy metric migration; CLI/render/transcript regression files |
| Critical write failure stops before a new effect | Required policy/grant/model/clock storage is fail-closed; optional observability is separate | test_budget_admission: critical_insert_failure_rolls_back_and_allocates_no_grant_or_model; test_executor_admission: critical_reservation_failure_stops_before_sdk_transport; test_run_clock: clock_critical_reservation_failure_prevents_active_work |

The named functions above omit their common test_ prefix for readability. They
exist in the current source; execution evidence is the retained scoped/full logs,
not function existence alone.

## Current proof and unresolved boundaries

- Old full02 completed10 failed/1315 passed/6 SDK warnings in3013.29s, exit1.
  All ten failures are the recorded path/requirements offline-adapter constructors;
  the production immutable-context guard was preserved. Exact final source and log
  identity is saved in turn20 environment-02. This failed suite is not acceptance.
- Corrected adapters/all-role admission:76 passed/119.04s. Context-free guard plus
  surrounding scope:65 passed/189.14s. Corrected SDK/policy receipt scope:96 passed/
  126.74s. Primary ruff21/22 passed. Exact frozen candidatef6113bb full03/44404
  finished green with exact source/config/input/runtime identity and refreshed
  ruff. The original failure receipts remain retained; S15 is accepted locally.
- Installed SDK/local subprocess tests exercise actual process groups and late-write
  sentinels with billed transport replaced. They do not prove exact provider billing
  or physical termination of a remote provider session. Admission plus SDK stopping
  is explicitly not an exact invoice ceiling.
- Legacy respond is still an unaccepted entry point pending S18's required common
  integration or permitted explicit unsupported-command rejection. Sandbox ownership
  or enabled-config rejection remains S17; global optional observability/display
  behavior remains S19. S16 owns default2 workers, derived recursion and conflict
  batches; its isolated candidate needs integrated full proof after S15 acceptance.
- Paid disposable-canary, human contract.py review and real Docker enablement smoke
  remain unperformed release gates. No mocked/scoped test closes them.
