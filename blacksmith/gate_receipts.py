"""Immutable exact-candidate verification evidence, separate from graph state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

Outcome = Literal["passed", "failed", "error", "timeout", "dirty"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GateCommandReceipt(_Record):
    stage: Literal["setup", "test", "lint", "fix"]
    command: str
    layers: tuple[str, ...]
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    output_artifact_paths: tuple[str, ...] = ()
    output_artifact_sha256: tuple[str, ...] = ()
    outcome: Literal["passed", "failed", "error", "timeout", "not_run"] = "not_run"


class GateLayerReceipt(_Record):
    layer: str
    covered_by: str
    command_indices: tuple[int, ...]
    outcome: Literal["passed", "failed", "error", "timeout", "dirty", "not_run"] = "not_run"


class GateReceipt(_Record):
    schema_version: Literal[1] = 1
    receipt_id: str
    run_id: str
    scope: Literal["unit", "level", "final"]
    scope_id: str
    attempt_id: str
    candidate_commit: str | None
    candidate_tree: str | None
    toolchain_hash: str
    layers: tuple[str, ...]
    layer_results: tuple[GateLayerReceipt, ...]
    commands: tuple[GateCommandReceipt, ...]
    started_at: str
    finished_at: str
    output_artifact_paths: tuple[str, ...]
    artifact_path: str
    outcome: Outcome
    message: str = ""
    output: str = ""
    fix_receipt_id: str | None = None

    @model_validator(mode="after")
    def passing_evidence_is_complete(self):
        if tuple(row.layer for row in self.layer_results) != self.layers:
            raise ValueError("layer evidence does not match ordered required layers")
        if self.outcome == "passed":
            if not self.candidate_commit or not self.candidate_tree or not self.layers:
                raise ValueError("passing gate lacks candidate/layers")
            if not self.commands or any(row.outcome != "passed" for row in self.commands):
                raise ValueError("passing gate has incomplete command evidence")
            if any(row.outcome != "passed" for row in self.layer_results):
                raise ValueError("passing gate has incomplete layer evidence")
            for layer in self.layer_results:
                if not layer.command_indices or any(
                    i < 0 or i >= len(self.commands) or layer.layer not in self.commands[i].layers
                    for i in layer.command_indices
                ):
                    raise ValueError("passing layer lacks matching command coverage")
            if set(self.layers) != {layer for row in self.commands for layer in row.layers}:
                raise ValueError("passing gate command coverage differs from required layers")
            if any(
                row.exit_code != 0
                or not row.started_at
                or not row.finished_at
                or not row.output_artifact_paths
                or len(row.output_artifact_paths) != len(row.output_artifact_sha256)
                for row in self.commands
            ):
                raise ValueError("passing gate lacks command exit/timing/artifact evidence")
        return self


class FixReceipt(_Record):
    receipt_id: str
    run_id: str
    unit_id: str
    attempt_id: str
    before_commit: str
    candidate_commit: str | None
    toolchain_hash: str
    commands: tuple[GateCommandReceipt, ...]
    outcome: Literal["passed", "clean_failure", "error", "timeout", "dirty"]
    started_at: str
    finished_at: str
    artifact_path: str
    message: str = ""

    @property
    def eligible(self):
        return self.outcome in {"passed", "clean_failure"}
