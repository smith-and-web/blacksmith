"""Tests for the human-gated QA terminal status (WU-QA-STATUS).

A human-GATED unit that implemented successfully parks its work behind a draft PR
for manual QA. That outcome needs its own terminal status, distinct from HALTED
(failure/rejection, work discarded) and DONE (fully approved). The status must also
survive checkpoint persistence, i.e. round-trip through ``blacksmith_serde``.
"""

import logging

from blacksmith.graph import blacksmith_serde
from blacksmith.state import Status

SERDE_LOGGER = "langgraph.checkpoint.serde.jsonplus"


def _serde_warnings(caplog):
    return [
        r for r in caplog.records if "unregistered" in r.message or "will be blocked" in r.message
    ]


def test_awaiting_qa_is_a_distinct_terminal_status():
    # Exists as a member...
    assert hasattr(Status, "AWAITING_QA")
    # ...and is a genuinely distinct terminal value, not an alias of the other terminals.
    assert Status.AWAITING_QA is not Status.HALTED
    assert Status.AWAITING_QA is not Status.DONE
    assert Status.AWAITING_QA != Status.HALTED
    assert Status.AWAITING_QA != Status.DONE
    assert Status.AWAITING_QA.value == "awaiting_qa"


def test_awaiting_qa_round_trips_through_checkpointer_serde(caplog):
    state = {"status": Status.AWAITING_QA}
    serde = blacksmith_serde()
    with caplog.at_level(logging.WARNING, logger=SERDE_LOGGER):
        restored = serde.loads_typed(serde.dumps_typed(state))

    assert restored["status"] == Status.AWAITING_QA
    assert restored["status"] is Status.AWAITING_QA
    assert _serde_warnings(caplog) == []
