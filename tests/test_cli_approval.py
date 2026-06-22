"""Headless approval modes for the CLI (dogfood feedback: gates must be scriptable).

The drive loop already accepts an injected approver; these cover the flag-driven
approvers that make non-interactive / CI runs possible without a terminal.
"""

from __future__ import annotations

import argparse

from blacksmith.cli import _auto_approver, _cli_approver, _select_approver


def _args(*, auto_approve=False, approve=None):
    return argparse.Namespace(auto_approve=auto_approve, approve=approve)


def test_auto_approver_all_approves_every_gate():
    approve = _auto_approver(None)
    assert approve({"gate": "plan"}, {}) is True
    assert approve({"gate": "pr"}, {}) is True


def test_auto_approver_subset_denies_unlisted_gate():
    approve = _auto_approver({"plan"})
    assert approve({"gate": "plan"}, {}) is True
    assert approve({"gate": "pr"}, {}) is False  # denial halts before the PR gate


def test_select_approver_auto_approve_flag():
    approve = _select_approver(_args(auto_approve=True))
    assert approve({"gate": "pr"}, {}) is True


def test_select_approver_approve_list_trims_and_filters():
    approve = _select_approver(_args(approve=" plan , "))
    assert approve({"gate": "plan"}, {}) is True
    assert approve({"gate": "pr"}, {}) is False


def test_select_approver_defaults_to_interactive():
    assert _select_approver(_args()) is _cli_approver
