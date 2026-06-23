"""WU-VALIDATE: ``blacksmith validate <prd>`` dry-runs the PRD contract.

The validate path is a zero-spend, fully offline check: it parses the PRD with
``parse_prd`` and reports field-level errors, building no Executor and making no
network calls. These tests drive the CLI through ``main(["validate", ...])`` and
guard those invariants.
"""

from __future__ import annotations

import copy
import socket

import yaml

import blacksmith.cli as cli
from blacksmith.cli import main

# A minimal conforming contract (mirrors tests/test_contract.py), mutated per-test.
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


def test_validate_conforming_prd_exits_zero_with_summary(tmp_path, capsys):
    code = main(["validate", str(_write_prd(tmp_path))])
    assert code == 0
    out = capsys.readouterr().out
    assert "demo" in out  # names the component
    assert "2" in out  # names the work-unit count


def test_validate_unknown_key_exits_nonzero_with_field_path(tmp_path, capsys):
    contract = copy.deepcopy(BASE_CONTRACT)
    contract["surprise"] = "nope"
    code = main(["validate", str(_write_prd(tmp_path, contract))])
    assert code != 0
    captured = capsys.readouterr()
    # The same field-level message parse_prd raises, naming the offending field.
    assert "surprise" in (captured.out + captured.err)


def test_validate_missing_file_exits_nonzero_with_clear_message(tmp_path, capsys):
    code = main(["validate", str(tmp_path / "nope.md")])
    assert code != 0
    captured = capsys.readouterr()
    assert "not found" in (captured.out + captured.err)


def test_validate_builds_no_executor_and_makes_no_network_call(tmp_path, monkeypatch):
    """The validate path must construct no Executor and open no socket."""

    def _no_executor(*args, **kwargs):
        raise AssertionError("validate must not construct an Executor (zero model spend)")

    def _no_socket(*args, **kwargs):
        raise AssertionError("validate must make no network calls")

    monkeypatch.setattr(cli, "Executor", _no_executor)
    monkeypatch.setattr(socket, "socket", _no_socket)

    assert main(["validate", str(_write_prd(tmp_path))]) == 0
