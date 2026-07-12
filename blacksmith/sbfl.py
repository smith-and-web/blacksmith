"""SBFL — Spectrum-Based Fault Localization (Ochiai ranking).

This module is PURE, READ-ONLY, and stdlib-only: it parses artifacts that a
target repo's own ``coverage_cmd`` already produced (a coverage.py
``--show-contexts`` JSON report and a JUnit XML test-outcome report) and
ranks source (file, line) locations by Ochiai suspiciousness. It never runs a
subprocess, never writes anything, and never raises — any failure (a
missing/malformed file, an unparseable format, zero failing tests, zero
covered lines) degrades to an empty result.

SBFL is ADVISORY-ONLY and OFF by default (PRD constitution): this module
never touches the test gate's pass/fail decision. It only ever feeds
suspicious-locations into the fix-retry feedback on a gate failure that is
already headed to a retry.
"""

from __future__ import annotations

import json
import math
from xml.etree import ElementTree


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
