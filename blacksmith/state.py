"""BlacksmithState — the typed state carried through the graph and persisted by the
checkpointer (PRD §4).

LangGraph reads the annotations here to wire reducers: ``errors`` accumulates across
nodes (append), every other field is last-write-wins. The state is intentionally a
plain ``TypedDict`` (not a pydantic model) because that is what LangGraph's reducer /
serialization machinery expects; the rich types it references (PRD, WorkUnit) come
from the contract module.
"""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from blacksmith.contract import PRD, WorkUnit


class Status(StrEnum):
    """Run status (PRD §4). A str-enum so it serializes cleanly through the checkpointer."""

    PENDING = "pending"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    AWAITING_PR_APPROVAL = "awaiting_pr_approval"
    HALTED = "halted"
    DONE = "done"


class TestResults(TypedDict):
    passed: bool
    output: str
    command: str


class Approvals(TypedDict, total=False):
    plan: bool
    pr: bool


class ErrorRecord(TypedDict):
    node: str
    message: str


class BlacksmithState(TypedDict, total=False):
    """State persisted per-run by the checkpointer. All keys optional — the graph
    fills them in as it advances."""

    prd_path: str
    prd: PRD
    work_units: list[WorkUnit]
    selected_unit: WorkUnit
    # Cursor into ``work_units`` (topo order, from blacksmith.planner.execution_order):
    # which unit the sequential implement->gate loop is currently on (multi-unit run).
    unit_cursor: int
    plan: dict[str, Any]
    worktree_path: str
    # The single shared branch every unit's commits land on, so one combined PR can be
    # opened against it. Set once when the run's lone worktree is created.
    branch: str
    implementation: dict[str, Any]
    test_results: TestResults
    pr_url: str | None
    approvals: Approvals
    status: Status
    errors: Annotated[list[ErrorRecord], operator.add]
