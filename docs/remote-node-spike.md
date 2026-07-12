# Remote node spike: decoupled command execution over `RemoteGraph`

> **Status: standalone spike.** Everything described here lives under
> `blacksmith/remote/` plus the repo-root `langgraph.json` and this doc. Nothing on
> blacksmith's production path (`build_graph`, `build_graph_for`, the plan / implement /
> test_gate / review nodes, `CloneManager`) imports or calls any of it. A normal
> `blacksmith <prd>` run is byte-for-byte unchanged whether or not this spike exists.

## What this demonstrates

Today, every blacksmith node runs in-process, in the same Python interpreter, against a
local git clone on the same machine. This spike explores what it would look like to pull
a node's *execution* out of that process entirely, and run it against a separately
deployed graph reached over the network — without adopting any cloud service.

It does this with a deliberately minimal example:

- **`blacksmith/remote/workspace_graph.py`** defines a tiny standalone LangGraph, `workspace`,
  with one node (`run_command`) that runs a shell command and captures its
  stdout/stderr/exit_code. It is served, not imported — a LangGraph dev server loads it
  directly from `langgraph.json`.
- **`blacksmith/remote/client.py`** (`run_remote_command`) is the client side: it points
  `langgraph.pregel.remote.RemoteGraph` (already a pinned dependency, `langgraph` 1.2.9)
  at that server's URL and invokes the `workspace` graph over the network, getting back
  a structured result.
- **`blacksmith/remote/spike.py`** is a runnable CLI that ties the two together end to
  end: parse a `--command`, call `run_remote_command`, print what came back.

Conceptually, this is the **LangGraph-native analog of OpenHands' `RemoteAPIWorkspace`**:
an orchestrator-side client object that looks like it's running a command locally, but is
actually delegating that execution to a separate, remotely-reachable process. In
OpenHands, `RemoteAPIWorkspace` talks to a remote sandboxed runtime over HTTP; here,
`RemoteGraph` plays the same role, talking to a remotely-deployed LangGraph graph instead.

This is also the shape a future **container-first isolation feature** would build on:
instead of blacksmith's nodes running commands directly against a local worktree (as
`blacksmith/sandbox.py` does today), a node could delegate that execution to a graph
running inside its own container/VM, reached the same way this spike reaches its dev
server — decoupling *where code executes* from *where the orchestrator runs*, without
requiring a cloud platform. This spike proves the mechanics of that pattern in isolation,
without touching any real node.

## No new dependency, no cloud

- `RemoteGraph` (`langgraph.pregel.remote`) and the `langgraph-sdk` client are already
  pinned dependencies of the main project (via `langgraph>=1.2.9`) — the spike's code
  imports only those. `pyproject.toml` and `uv.lock` are untouched.
- `langgraph-cli` (the `langgraph dev` server command) is a *dev tool*, not a runtime
  dependency of blacksmith. It is invoked **ephemerally** via
  `uv run --with langgraph-cli langgraph dev` — the same ephemeral-tool pattern the SBFL
  feature uses for coverage collection — and is never added to `pyproject.toml`.
- Execution stays entirely **local**: `langgraph dev` runs a dev server on
  `127.0.0.1:2024`. There is no LangGraph Platform deployment and no LangSmith call
  involved.

## Manual round-trip

Run these from the repo root, in two separate shells.

**Shell 1 — start the local dev server** (serves the graph declared in the repo-root
`langgraph.json`, i.e. `blacksmith/remote/workspace_graph.py:graph` as `workspace`):

```sh
uv run --with langgraph-cli langgraph dev
```

Leave this running. It listens on `http://127.0.0.1:2024` by default.

**Shell 2 — drive it with the spike CLI:**

```sh
uv run python -m blacksmith.remote.spike --command "echo hello from the remote node"
```

You should see output along the lines of:

```
stdout:
hello from the remote node
stderr:
exit_code: 0
```

The command ran inside the `run_command` node of the `workspace` graph, in the dev
server's process — not in the shell you ran `python -m blacksmith.remote.spike` from.
`--server-url` (default `http://127.0.0.1:2024`) and `--cwd` (default: the server's own
working directory) can be used to point at a different server or working directory:

```sh
uv run python -m blacksmith.remote.spike \
  --command "pwd" \
  --server-url "http://127.0.0.1:2024" \
  --cwd "/tmp"
```

## Automated tests

`tests/test_remote_client.py`, `tests/test_remote_workspace_graph.py`, and
`tests/test_remote_spike.py` cover this package without ever starting a server or making
a network call: the workspace graph's tests invoke the compiled graph directly
in-process, and the client/CLI tests monkeypatch `RemoteGraph` /
`run_remote_command` respectively. The manual round-trip above is the only way to
exercise the real network path end to end.
