"""Tests for PRD Contract v1 (WU-02).

Test contract (PRD §6, WU-02): valid fixture passes; invalid fixture rejected with
a field-level error. We also validate the real vendored PRD — the contract's first
dogfood input (PRD §11) and acceptance criterion AC-1.
"""

import copy
from pathlib import Path

import pytest
import yaml

from blacksmith.contract import ContractError, parse_prd

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_PRD = REPO_ROOT / "blacksmith-v0-prd.md"

# A minimal conforming contract, mutated per-test to produce invalid variants.
BASE_CONTRACT = {
    "contract_version": 1,
    "component": "demo",
    "version": "v0",
    "primary_target_repo": "owner/demo",
    "layers": {"py-logic": "auto", "integration": "human"},
    "untouchables": ["do not touch the brand files"],
    "work_units": [
        {
            "id": "WU-01",
            "title": "scaffold",
            "layers": ["py-logic"],
            "target_modules": ["pyproject.toml"],
            "test_contract": "pytest: parses",
            "depends_on": [],
        },
        {
            "id": "WU-02",
            "title": "thing",
            "layers": ["py-logic", "integration"],
            "target_modules": ["x.py"],
            "test_contract": "pytest: works",
            "depends_on": ["WU-01"],
        },
    ],
}

VALID_BODY = """\
# Demo PRD

## 1. Purpose
why.

## 2. Scope fences
what.

## 7. Untouchables
no.

## 10. Acceptance criteria
done.
"""


def _write_prd(tmp_path, contract=None, body=VALID_BODY, name="prd.md"):
    contract = BASE_CONTRACT if contract is None else contract
    frontmatter = yaml.safe_dump(contract, sort_keys=False)
    path = tmp_path / name
    path.write_text(f"---\n{frontmatter}---\n{body}")
    return path


# --- valid -----------------------------------------------------------------


def test_valid_prd_parses(tmp_path):
    prd = parse_prd(_write_prd(tmp_path))
    assert prd.contract.component == "demo"
    assert [u.id for u in prd.contract.work_units] == ["WU-01", "WU-02"]


def test_vendored_prd_validates():
    """The real PRD must conform — it is the contract's first dogfood input (AC-1)."""
    prd = parse_prd(VENDORED_PRD)
    assert prd.contract.component == "blacksmith"
    assert len(prd.contract.work_units) == 11
    assert [u.id for u in prd.contract.roots()] == ["WU-01"]
    # WU-06 spans py-logic + integration, so it is human-gated (PRD §4).
    wu06 = prd.contract.work_unit_by_id("WU-06")
    assert wu06 is not None
    assert prd.contract.gate_for(wu06) == "human"
    # WU-01 is pure py-logic — auto-gated.
    wu01 = prd.contract.work_unit_by_id("WU-01")
    assert prd.contract.gate_for(wu01) == "auto"


# --- invalid: rejected with a field-level error ----------------------------


def test_missing_frontmatter_rejected(tmp_path):
    path = tmp_path / "no_fm.md"
    path.write_text(VALID_BODY)
    with pytest.raises(ContractError, match="frontmatter"):
        parse_prd(path)


def test_invalid_yaml_rejected(tmp_path):
    path = tmp_path / "bad_yaml.md"
    path.write_text("---\nfoo: : : bad\n---\n" + VALID_BODY)
    with pytest.raises(ContractError):
        parse_prd(path)


def test_missing_required_field_rejected(tmp_path):
    contract = copy.deepcopy(BASE_CONTRACT)
    del contract["work_units"]
    with pytest.raises(ContractError, match="work_units"):
        parse_prd(_write_prd(tmp_path, contract))


def test_unknown_key_rejected(tmp_path):
    contract = copy.deepcopy(BASE_CONTRACT)
    contract["surprise"] = "nope"
    with pytest.raises(ContractError, match="surprise"):
        parse_prd(_write_prd(tmp_path, contract))


def test_undeclared_layer_rejected(tmp_path):
    contract = copy.deepcopy(BASE_CONTRACT)
    contract["work_units"][0]["layers"] = ["ui"]  # not in declared layers
    with pytest.raises(ContractError, match="ui"):
        parse_prd(_write_prd(tmp_path, contract))


def test_dangling_dependency_rejected(tmp_path):
    contract = copy.deepcopy(BASE_CONTRACT)
    contract["work_units"][1]["depends_on"] = ["WU-99"]
    with pytest.raises(ContractError, match="WU-99"):
        parse_prd(_write_prd(tmp_path, contract))


def test_dependency_cycle_rejected(tmp_path):
    contract = copy.deepcopy(BASE_CONTRACT)
    contract["work_units"][0]["depends_on"] = ["WU-02"]  # WU-01 <-> WU-02
    with pytest.raises(ContractError, match="cycle"):
        parse_prd(_write_prd(tmp_path, contract))


def test_duplicate_unit_ids_rejected(tmp_path):
    contract = copy.deepcopy(BASE_CONTRACT)
    contract["work_units"][1]["id"] = "WU-01"
    with pytest.raises(ContractError, match="duplicate"):
        parse_prd(_write_prd(tmp_path, contract))


def test_wrong_contract_version_rejected(tmp_path):
    contract = copy.deepcopy(BASE_CONTRACT)
    contract["contract_version"] = 2
    with pytest.raises(ContractError, match="contract_version"):
        parse_prd(_write_prd(tmp_path, contract))


def test_missing_prose_section_rejected(tmp_path):
    body = VALID_BODY.replace("## 7. Untouchables\nno.\n", "")
    with pytest.raises(ContractError, match="untouchables"):
        parse_prd(_write_prd(tmp_path, body=body))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ContractError, match="not found"):
        parse_prd(tmp_path / "nope.md")
