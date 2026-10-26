# Deferred sandbox and revision features

S17 uses the specification's explicit disabled-feature alternative. Sandbox
execution remains disabled. Runtime loading of [sandbox].enabled=true returns
sandbox_unsupported_until_hardened before execution dependencies or new spend.
Direct graph, model, native-command and workspace entry points enforce the same
policy. The implementer never receives or advertises a sandbox run_command tool.

Default disabled settings keep the existing tools, prompts and host verification
gates. Low-level Docker primitives remain as experimental code with fake-transport
unit coverage; they are not an enabled Blacksmith execution feature or isolation
proof. Enabling requires per-workspace containers and verified mounts/labels,
reliable in-container timeout termination, restart/cleanup ownership and a real
Docker smoke test. That release gate has not run.

Historical enabled settings can still be structurally parsed. Existing checkpoints,
journals and recovery bundles remain readable through recovery inspect with a
disabled local runtime configuration. Resume, paid calls, native execution and new
capture refuse without rewriting the original evidence. The disabled feature does
not stop/delete historical containers whose ownership is unproven; inspect those
separately before any manual cleanup. Do not edit saved settings to bypass refusal.

S18 also uses the explicit disabled-command alternative. respond --help remains
available (exit0). A well-formed execution request returns exit1 with
respond_unsupported_until_hardened before loading environment/configuration,
reading a supplied PRD, querying GitHub, constructing an executor or cloning.
Missing/invalid required arguments retain argparse's exit2. The programmatic
run_respond wrapper returns1 with the same diagnostic; respond_to_pr raises
RespondUnsupportedError before any dependency/effect. Existing branches and
workspaces are untouched. Reserved [respond] settings do not enable the command.

Future enablement requires immutable original feedback and addressed comment IDs,
a correctly scoped synthetic unit, full supplied requirements/untouchables,
common admission/attempt/review/approval/gate services, exact fetched-head checks,
non-force publication and retained recovery evidence for races or failed pushes.
The disabled variant does not claim that those revision behaviors were implemented
or verified. It provides an explicit refusal under the specification's exception.

Recovery inspection reports a `disabled_features` object from the immutable saved
configuration: sandbox's saved enabled flag and respond's reserved `max_attempts`
value, each explicitly marked unsupported. Changed local settings do not replace
these historical values. This read-only report does not enable either feature or
write checkpoints, input snapshots, journal operations or events.
