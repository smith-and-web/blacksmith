"""SBFL — Spectrum-Based Fault Localization (Ochiai ranking).

This module is READ-ONLY and stdlib-only: ``collect_suspicious_locations``
runs the target repo's own configured ``coverage_cmd`` as a plain subprocess
in the worktree (the same shell-parsed way ``gate.py``'s ``_run`` runs the
test command — no new dependency), then ``rank_suspicious_locations`` parses
the artifacts it produced (a coverage.py ``--show-contexts`` JSON report and
a JUnit XML test-outcome report) and ranks source (file, line) locations by
Ochiai suspiciousness. Everything here is best-effort and never raises — any
failure (an empty/blank/raising/timing-out coverage_cmd, a missing/malformed
artifact, an unparseable format, zero failing tests, zero covered lines)
degrades to an empty result, and nothing here ever writes to or otherwise
mutates the repo.

SBFL is ADVISORY-ONLY and OFF by default (PRD constitution): this module
never touches the test gate's pass/fail decision. It only ever feeds
suspicious-locations into the fix-retry feedback on a gate failure that is
already headed to a retry.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from xml.etree import ElementTree


def collect_suspicious_locations(
    worktree_path,
    *,
    coverage_cmd: str,
    coverage_json: str,
    junit_xml: str,
    limit: int = 5,
) -> list[dict]:
    """Run ``coverage_cmd`` in the worktree, then rank the artifacts it produced.

    Best-effort end to end: an empty ``coverage_cmd``, a command that raises,
    times out, or exits non-zero, or missing/malformed artifacts all yield
    ``[]`` rather than raising. The command's own exit code is deliberately
    ignored — it is expected to "fail" when the tests it runs fail; only the
    artifacts it wrote (if any) matter. Read-only over the repo: this function
    only reads the artifacts the command wrote, never writing or mutating the
    worktree itself.
    """
    if not coverage_cmd or not coverage_cmd.strip():
        return []

    worktree = Path(worktree_path)
    try:
        # shell=True mirrors gate.py's _run, the same way the gate's own test
        # command is parsed — no new subprocess-invocation dependency. The
        # return code is intentionally discarded: it is expected to be
        # non-zero when the tests it runs fail.
        subprocess.run(
            coverage_cmd, cwd=str(worktree), capture_output=True, text=True, shell=True
        )
    except Exception:
        return []

    coverage_json_path = worktree / coverage_json
    junit_xml_path = worktree / junit_xml
    return rank_suspicious_locations(coverage_json_path, junit_xml_path, limit=limit)


def format_suspicious_locations(locations: list[dict]) -> str:
    """Render ranked locations as a compact, labelled block; "" for an empty list."""
    if not locations:
        return ""
    lines = ["SUSPICIOUS LOCATIONS (fault localization, most-suspicious first):"]
    for loc in locations:
        lines.append(
            f"{loc['file']}:{loc['line']} (score {loc['score']:.2f}, "
            f"{loc['failed']} failing / {loc['passed']} passing tests)"
        )
    return "\n".join(lines)


def rank_suspicious_locations(coverage_json_path, junit_xml_path, *, limit: int = 5) -> list[dict]:
    """Rank (file, line) locations by Ochiai suspiciousness.

    Returns the top ``limit`` {file, line, score, failed, passed} dicts,
    sorted by score descending then (file, line) ascending. Best-effort:
    any parsing failure or the absence of failing tests / covered lines
    yields ``[]`` rather than raising.
    """
    try:
        contexts = _load_coverage_contexts(coverage_json_path)
        outcomes = _load_test_outcomes(junit_xml_path)

        failed_ids = {test_id for test_id, passed in outcomes.items() if not passed}
        total_failed = len(failed_ids)
        if total_failed == 0:
            return []

        locations = []
        for file_path, line_map in contexts.items():
            for line, test_ids in line_map.items():
                failed = sum(1 for test_id in test_ids if test_id in failed_ids)
                passed = sum(
                    1 for test_id in test_ids if test_id in outcomes and test_id not in failed_ids
                )
                denominator = total_failed * (failed + passed)
                score = failed / math.sqrt(denominator) if denominator > 0 else 0.0
                if score > 0.0:
                    locations.append(
                        {
                            "file": file_path,
                            "line": line,
                            "score": score,
                            "failed": failed,
                            "passed": passed,
                        }
                    )

        locations.sort(key=lambda loc: (-loc["score"], loc["file"], loc["line"]))
        return locations[:limit]
    except Exception:
        return []


def _load_coverage_contexts(coverage_json_path) -> dict[str, dict[int, set[str]]]:
    """Parse coverage.py --show-contexts JSON into {file: {line: {test_id, ...}}}."""
    with open(coverage_json_path, encoding="utf-8") as handle:
        data = json.load(handle)

    result: dict[str, dict[int, set[str]]] = {}
    files = data["files"]
    for file_path, file_data in files.items():
        contexts = file_data.get("contexts", {})
        line_map: dict[int, set[str]] = {}
        for line_str, raw_test_ids in contexts.items():
            test_ids = {_normalize_context_id(raw) for raw in raw_test_ids}
            test_ids.discard("")
            if test_ids:
                line_map[int(line_str)] = test_ids
        if line_map:
            result[file_path] = line_map
    return result


def _normalize_context_id(raw_test_id: str) -> str:
    """Strip coverage's dynamic-context "|run"/"|setup"/"|teardown" suffix."""
    return raw_test_id.split("|", 1)[0]


def _load_test_outcomes(junit_xml_path) -> dict[str, bool]:
    """Parse a JUnit XML report into {test_id: passed}."""
    tree = ElementTree.parse(junit_xml_path)
    root = tree.getroot()

    outcomes: dict[str, bool] = {}
    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        test_id = _junit_test_id(classname, name)
        failed = testcase.find("failure") is not None or testcase.find("error") is not None
        outcomes[test_id] = not failed
    return outcomes


def _junit_test_id(classname: str, name: str) -> str:
    """Normalize a JUnit classname/name into a pytest-nodeid-shaped test id.

    e.g. classname="tests.test_foo", name="test_bar" -> "tests/test_foo.py::test_bar"
    and classname="tests.test_foo.TestBar", name="test_baz"
      -> "tests/test_foo.py::TestBar::test_baz" (a trailing dotted segment that
    looks like a class name — starts with an uppercase letter — is treated as
    the enclosing test class, matching coverage's pytest-cov context ids).
    """
    parts = [part for part in classname.split(".") if part]
    if parts and parts[-1][:1].isupper():
        module_parts, cls = parts[:-1], parts[-1]
        module = "/".join(module_parts) + ".py"
        return f"{module}::{cls}::{name}"
    module = "/".join(parts) + ".py"
    return f"{module}::{name}"
