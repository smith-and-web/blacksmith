"""PRD Contract v1 — schema + validator.

A conforming PRD is a markdown document whose machine-readable contract lives in a
leading YAML frontmatter block (validated by pydantic, giving field-level errors),
and whose prose body contains a required set of sections (validated by presence).
This hybrid split keeps the human-facing document intact while making the parts
blacksmith depends on — work units, their dependency DAG, layer gating, and the
untouchables — unambiguous and machine-checkable.

This module is an UNTOUCHABLE (PRD §7): it is the interface every future PRD depends
on, so changes require explicit human review. The ``ingest_prd`` graph node uses
``parse_prd`` to satisfy acceptance criterion AC-1 (validate a conforming PRD; reject
a non-conforming one with a clear, field-level error).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SUPPORTED_CONTRACT_VERSION = 1

# Keywords that must appear in a markdown heading somewhere in the prose body.
# Matched case-insensitively as substrings, so numbered headings like
# "## 1. Purpose" or "## 10. Acceptance criteria (v0)" satisfy them.
REQUIRED_SECTIONS: tuple[str, ...] = ("purpose", "scope", "untouchables", "acceptance")

# Frontmatter is a leading '---' fenced YAML block. Non-greedy so the first closing
# fence terminates it (later '---' horizontal rules in the prose are left in the body).
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)

# A layer is either auto-gateable (pytest/clippy decide) or human-gated (smoke/QA).
# The fixed concept is auto-vs-human; the vocabulary is per-project (PRD §6).
GateKind = Literal["auto", "human"]


class ContractError(Exception):
    """Raised when a PRD is missing, malformed, or does not conform to the contract."""


class _Strict(BaseModel):
    """Base for contract models: reject unknown keys and freeze after load."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkUnit(_Strict):
    """One testable outcome (PRD §6)."""

    id: str
    title: str
    layers: list[str] = Field(min_length=1)
    target_modules: list[str] = Field(min_length=1)
    test_contract: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class PRDContract(_Strict):
    """The machine-readable contract carried in a PRD's YAML frontmatter."""

    contract_version: int
    component: str
    version: str
    primary_target_repo: str
    layers: dict[str, GateKind]
    untouchables: list[str] = Field(min_length=1)
    work_units: list[WorkUnit] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_structure(self) -> PRDContract:
        if self.contract_version != SUPPORTED_CONTRACT_VERSION:
            raise ValueError(
                f"contract_version: this validator supports v{SUPPORTED_CONTRACT_VERSION}, "
                f"got {self.contract_version}"
            )
        if not self.layers:
            raise ValueError("layers: at least one layer must be declared")

        ids = [unit.id for unit in self.work_units]
        duplicates = sorted({uid for uid in ids if ids.count(uid) > 1})
        if duplicates:
            raise ValueError(f"work_units: duplicate ids: {', '.join(duplicates)}")

        id_set = set(ids)
        declared_layers = set(self.layers)
        for unit in self.work_units:
            undeclared = [name for name in unit.layers if name not in declared_layers]
            if undeclared:
                raise ValueError(
                    f"work_units[{unit.id}].layers: undeclared layer(s) "
                    f"{', '.join(undeclared)}; declared: {', '.join(sorted(declared_layers))}"
                )
            unknown_deps = [dep for dep in unit.depends_on if dep not in id_set]
            if unknown_deps:
                raise ValueError(
                    f"work_units[{unit.id}].depends_on: references unknown unit(s): "
                    f"{', '.join(unknown_deps)}"
                )

        cycle = _find_cycle({unit.id: unit.depends_on for unit in self.work_units})
        if cycle:
            raise ValueError(f"work_units: dependency cycle: {' -> '.join(cycle)}")
        return self

    def work_unit_by_id(self, unit_id: str) -> WorkUnit | None:
        return next((unit for unit in self.work_units if unit.id == unit_id), None)

    def gate_for(self, unit: WorkUnit) -> GateKind:
        """A unit is human-gated if ANY of its layers is human-gated (PRD §4 routing)."""
        return "human" if any(self.layers[name] == "human" for name in unit.layers) else "auto"

    def roots(self) -> list[WorkUnit]:
        """Units with no dependencies — candidates for the v0 single-unit selection (PRD §6)."""
        return [unit for unit in self.work_units if not unit.depends_on]


@dataclass(frozen=True)
class PRD:
    """A parsed, validated PRD: its frontmatter contract plus the prose body."""

    path: Path
    contract: PRDContract
    body: str


def parse_prd(path: str | Path) -> PRD:
    """Parse and validate a PRD markdown file against PRD Contract v1.

    Raises ``ContractError`` with a clear, field-level message if the file is missing,
    lacks frontmatter, has invalid YAML, fails schema validation, or omits a required
    prose section.
    """
    path = Path(path)
    if not path.is_file():
        raise ContractError(f"PRD not found: {path}")

    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ContractError(
            f"{path}: missing YAML frontmatter. A conforming PRD (Contract v1) must begin "
            "with a '---' fenced block carrying the machine-readable contract."
        )
    raw_frontmatter, body = match.group(1), match.group(2)

    try:
        data = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        raise ContractError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"{path}: frontmatter must be a mapping of contract fields")

    try:
        contract = PRDContract.model_validate(data)
    except ValidationError as exc:
        raise ContractError(_format_validation_error(path, exc)) from exc

    missing = _missing_sections(body)
    if missing:
        raise ContractError(
            f"{path}: missing required prose section(s): {', '.join(missing)} "
            f"(Contract v1 requires headings for: {', '.join(REQUIRED_SECTIONS)})"
        )

    return PRD(path=path, contract=contract, body=body)


def _missing_sections(body: str) -> list[str]:
    headings = [
        line.lstrip("#").strip().lower()
        for line in body.splitlines()
        if line.lstrip().startswith("#")
    ]
    return [key for key in REQUIRED_SECTIONS if not any(key in heading for heading in headings)]


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return a cycle path through the depends_on edges, or None if the graph is acyclic."""
    white, gray, black = 0, 1, 2
    color = dict.fromkeys(graph, white)
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = gray
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                continue  # unknown deps are reported separately
            if color[dep] == gray:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == white and (found := visit(dep)):
                return found
        stack.pop()
        color[node] = black
        return None

    for start in graph:
        if color[start] == white and (found := visit(start)):
            return found
    return None


def _format_validation_error(path: Path, err: ValidationError) -> str:
    lines = [f"invalid PRD contract in {path}:"]
    for detail in err.errors():
        loc = ".".join(str(part) for part in detail["loc"]) or "<root>"
        lines.append(f"  - {loc}: {detail['msg']}")
    return "\n".join(lines)
