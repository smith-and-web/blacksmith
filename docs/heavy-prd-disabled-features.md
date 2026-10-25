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

S18's respond integration or explicit unsupported-command alternative is still
pending. This document does not establish that command's safety or availability.
