"""blacksmith/remote/spike.py — runnable CLI entrypoint for the WU-REMOTE-* spike.

STANDALONE SPIKE — see ``blacksmith/remote/__init__.py``. This module is not imported by
anything on blacksmith's production path.

Drives :func:`blacksmith.remote.client.run_remote_command` against a running LangGraph
dev server (``uv run --with langgraph-cli langgraph dev``, per the repo-root
``langgraph.json``) and prints the resulting stdout/stderr/exit_code. See
``docs/remote-node-spike.md`` for the manual round-trip.

Usage::

    uv run python -m blacksmith.remote.spike --command "echo hello from the remote node"
"""

from __future__ import annotations

import argparse
import sys

from blacksmith.remote.client import run_remote_command

DEFAULT_SERVER_URL = "http://127.0.0.1:2024"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m blacksmith.remote.spike",
        description=(
            "Send a command to the standalone 'workspace' LangGraph over RemoteGraph, "
            "against a locally running LangGraph dev server."
        ),
    )
    parser.add_argument(
        "--command",
        required=True,
        help="Shell command to run on the remote workspace graph.",
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help=f"URL of the local LangGraph dev server (default: {DEFAULT_SERVER_URL}).",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for the command on the remote side (default: server's own cwd).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the command remotely, print the result, return its exit code."""
    args = _build_parser().parse_args(argv)

    result = run_remote_command(args.server_url, args.command, cwd=args.cwd)

    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    exit_code = result.get("exit_code", 1)

    print("stdout:")
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    print("stderr:", file=sys.stderr)
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    print(f"exit_code: {exit_code}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
