# Security Policy

## Supported versions

blacksmith is at v0 (pre-1.0); only the latest `main` is supported.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, use GitHub's
private vulnerability reporting: go to the repository's **Security** tab →
**Report a vulnerability**. We'll acknowledge the report and work with you on a fix
before any public disclosure.

## Scope notes

blacksmith is an agentic orchestrator that executes a model-driven agent against a
target repository. Two safety properties are deliberate and worth reporting if you
can break them:

- The executor must route every model call through the configured **dedicated API
  key** and never fall back to other credentials.
- The implement step must stay inside its isolated **git worktree** and must not edit
  paths declared **untouchable** by the target's PRD (see `blacksmith-v0-prd.md` §7).
