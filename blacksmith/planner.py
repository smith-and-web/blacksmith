"""Topological execution-order planner (WU-PLANNER).

``PRDContract.roots`` answers "which units have no dependencies?" but blacksmith
needs to order *every* work unit so that each one runs only after the units it
``depends_on``. This module provides that ordering as a pure function over a parsed
contract — it READS the contract and never mutates it, keeping the schema/validator
(``blacksmith/contract.py``, an untouchable) untouched.

The ordering is a stable topological sort: a deterministic Kahn's algorithm where
ties between units with no interdependency break by their declaration order in the
PRD. ``parse_prd`` already rejects cycles, but ``execution_order`` re-checks so that
a contract handed in directly cannot make it loop silently.
"""

from __future__ import annotations

from blacksmith.contract import PRDContract, WorkUnit


class PlannerError(Exception):
    """Raised when a contract cannot be ordered (e.g. it contains a dependency cycle)."""


def execution_order(contract: PRDContract) -> list[WorkUnit]:
    """Return all work units in a valid topological order by ``depends_on``.

    Every unit appears after all units it depends on. Units with no
    interdependency keep their PRD declaration order, making the result
    deterministic and stable. Raises ``PlannerError`` if the dependency graph
    contains a cycle.
    """
    units = list(contract.work_units)
    order = {unit.id: index for index, unit in enumerate(units)}

    # Outstanding dependency count per unit, and reverse edges (dep -> dependents).
    remaining = {unit.id: len(unit.depends_on) for unit in units}
    dependents: dict[str, list[str]] = {unit.id: [] for unit in units}
    for unit in units:
        for dep in unit.depends_on:
            dependents[dep].append(unit.id)

    # Ready set: units with no outstanding dependencies, drained in declaration order.
    ready = sorted((uid for uid, count in remaining.items() if count == 0), key=order.get)
    by_id = {unit.id: unit for unit in units}
    result: list[WorkUnit] = []

    while ready:
        current = ready.pop(0)
        result.append(by_id[current])
        newly_ready: list[str] = []
        for dependent in dependents[current]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                newly_ready.append(dependent)
        if newly_ready:
            ready = sorted(ready + newly_ready, key=order.get)

    if len(result) != len(units):
        unplaced = sorted((uid for uid in remaining if remaining[uid] > 0), key=order.get)
        raise PlannerError(
            "dependency cycle among work units: " + ", ".join(unplaced)
        )
    return result
