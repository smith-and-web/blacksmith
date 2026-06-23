"""Tests for the topological execution-order planner (WU-PLANNER).

Test contract: ``execution_order(contract)`` returns every work unit after the
units it ``depends_on`` (a valid topological order), is deterministic and stable
(ties break by PRD declaration order), handles a single-unit PRD and a diamond
DAG, and raises a clear error on a cyclic graph rather than looping.
"""

import pytest

from blacksmith.contract import PRDContract, WorkUnit
from blacksmith.planner import PlannerError, execution_order


def _unit(uid, depends_on=None):
    return WorkUnit(
        id=uid,
        title=uid,
        layers=["py-logic"],
        target_modules=[f"{uid}.py"],
        test_contract="pytest: works",
        depends_on=depends_on or [],
    )


def _contract(units):
    return PRDContract(
        contract_version=1,
        component="demo",
        version="v0",
        primary_target_repo="owner/demo",
        layers={"py-logic": "auto"},
        untouchables=["do not touch the brand files"],
        work_units=units,
    )


def _ids(units):
    return [unit.id for unit in units]


def _appears_after_deps(ordered):
    positions = {unit.id: i for i, unit in enumerate(ordered)}
    for unit in ordered:
        for dep in unit.depends_on:
            assert positions[dep] < positions[unit.id], f"{unit.id} before its dep {dep}"


def test_every_unit_after_its_dependencies():
    units = [
        _unit("WU-01"),
        _unit("WU-02", ["WU-01"]),
        _unit("WU-03", ["WU-02"]),
        _unit("WU-04", ["WU-01", "WU-03"]),
    ]
    ordered = execution_order(_contract(units))
    assert len(ordered) == len(units)
    _appears_after_deps(ordered)


def test_single_unit_returns_just_that_unit():
    units = [_unit("WU-01")]
    ordered = execution_order(_contract(units))
    assert _ids(ordered) == ["WU-01"]


def test_deterministic_and_stable_for_independent_units():
    units = [_unit("WU-A"), _unit("WU-B"), _unit("WU-C")]
    contract = _contract(units)
    first = _ids(execution_order(contract))
    # No interdependency -> ties break by declaration order.
    assert first == ["WU-A", "WU-B", "WU-C"]
    # Stable across repeated calls.
    assert first == _ids(execution_order(contract))


def test_independent_units_keep_declaration_order_under_shared_dep():
    # X and Y both depend only on R; they must follow R in declaration order.
    units = [_unit("R"), _unit("X", ["R"]), _unit("Y", ["R"])]
    ordered = execution_order(_contract(units))
    assert _ids(ordered) == ["R", "X", "Y"]


def test_diamond_dag():
    # D depends on B and C, both depend on A.
    units = [
        _unit("A"),
        _unit("B", ["A"]),
        _unit("C", ["A"]),
        _unit("D", ["B", "C"]),
    ]
    ordered = execution_order(_contract(units))
    pos = {unit.id: i for i, unit in enumerate(ordered)}
    assert pos["A"] < pos["B"]
    assert pos["A"] < pos["C"]
    assert pos["B"] < pos["D"]
    assert pos["C"] < pos["D"]
    _appears_after_deps(ordered)


def test_cyclic_graph_raises_rather_than_looping():
    # parse_prd rejects cycles, so bypass validation to hand a cyclic graph directly.
    cyclic = [_unit("WU-01", ["WU-02"]), _unit("WU-02", ["WU-01"])]
    contract = PRDContract.model_construct(work_units=cyclic)
    with pytest.raises(PlannerError):
        execution_order(contract)
