import json
import sys

from blacksmith.sbfl import (
    collect_suspicious_locations,
    format_suspicious_locations,
    rank_suspicious_locations,
)


def _write_coverage(tmp_path, files: dict) -> str:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"files": files}))
    return str(path)


def _write_junit(tmp_path, testcases: list[tuple[str, str, bool]]) -> str:
    """testcases: list of (classname, name, failed)."""
    body = []
    for classname, name, failed in testcases:
        if failed:
            body.append(
                f'<testcase classname="{classname}" name="{name}">'
                f'<failure message="boom">trace</failure></testcase>'
            )
        else:
            body.append(f'<testcase classname="{classname}" name="{name}"></testcase>')
    xml = f'<testsuite>{"".join(body)}</testsuite>'
    path = tmp_path / "junit.xml"
    path.write_text(xml)
    return str(path)


def _passing_ids(n: int) -> list[str]:
    return [f"tests/test_foo.py::test_pass_{i}|run" for i in range(n)]


def _passing_cases(n: int) -> list[tuple[str, str, bool]]:
    return [("tests.test_foo", f"test_pass_{i}", False) for i in range(n)]


def test_line_covered_only_by_failing_test_outranks_line_shared_with_many_passes(tmp_path):
    coverage = _write_coverage(
        tmp_path,
        {
            "pkg/mod.py": {
                "contexts": {
                    "10": ["tests/test_foo.py::test_fails|run"],
                    "20": ["tests/test_foo.py::test_fails|run", *_passing_ids(5)],
                }
            }
        },
    )
    junit = _write_junit(
        tmp_path,
        [
            ("tests.test_foo", "test_fails", True),
            *_passing_cases(5),
        ],
    )

    results = rank_suspicious_locations(coverage, junit)

    assert [r["line"] for r in results] == [10, 20]
    assert results[0]["score"] > results[1]["score"]
    assert results[0] == {"file": "pkg/mod.py", "line": 10, "score": 1.0, "failed": 1, "passed": 0}


def test_line_covered_only_by_passing_tests_scores_zero_and_is_excluded(tmp_path):
    coverage = _write_coverage(
        tmp_path,
        {
            "pkg/mod.py": {
                "contexts": {
                    "10": ["tests/test_foo.py::test_fails|run"],
                    "30": _passing_ids(2),
                }
            }
        },
    )
    junit = _write_junit(
        tmp_path,
        [
            ("tests.test_foo", "test_fails", True),
            *_passing_cases(2),
        ],
    )

    results = rank_suspicious_locations(coverage, junit)

    assert [r["line"] for r in results] == [10]
    assert all(r["line"] != 30 for r in results)


def test_results_are_capped_at_limit_and_deterministically_ordered(tmp_path):
    contexts = {
        str(line): [f"tests/test_foo.py::test_fail_{line}|run"] for line in range(1, 11)
    }
    coverage = _write_coverage(tmp_path, {"pkg/mod.py": {"contexts": contexts}})
    junit = _write_junit(
        tmp_path,
        [("tests.test_foo", f"test_fail_{line}", True) for line in range(1, 11)],
    )

    results = rank_suspicious_locations(coverage, junit, limit=3)

    assert len(results) == 3
    # Every location here has an identical score (each line covered by exactly
    # one distinct failing test out of ten total failing tests), so ties break
    # on (file, line) ascending.
    assert [r["line"] for r in results] == [1, 2, 3]


def test_missing_coverage_file_yields_empty_list_without_raising(tmp_path):
    junit = _write_junit(tmp_path, [("tests.test_foo", "test_fails", True)])

    results = rank_suspicious_locations(str(tmp_path / "nope.json"), junit)

    assert results == []


def test_malformed_coverage_json_yields_empty_list_without_raising(tmp_path):
    bad_path = tmp_path / "coverage.json"
    bad_path.write_text("{not valid json")
    junit = _write_junit(tmp_path, [("tests.test_foo", "test_fails", True)])

    results = rank_suspicious_locations(str(bad_path), junit)

    assert results == []


def test_missing_junit_file_yields_empty_list_without_raising(tmp_path):
    coverage = _write_coverage(
        tmp_path, {"pkg/mod.py": {"contexts": {"10": ["tests/test_foo.py::test_fails|run"]}}}
    )

    results = rank_suspicious_locations(coverage, str(tmp_path / "nope.xml"))

    assert results == []


def test_malformed_junit_xml_yields_empty_list_without_raising(tmp_path):
    coverage = _write_coverage(
        tmp_path, {"pkg/mod.py": {"contexts": {"10": ["tests/test_foo.py::test_fails|run"]}}}
    )
    bad_path = tmp_path / "junit.xml"
    bad_path.write_text("<not-closed>")

    results = rank_suspicious_locations(coverage, str(bad_path))

    assert results == []


def test_empty_junit_with_no_failures_yields_empty_list(tmp_path):
    coverage = _write_coverage(
        tmp_path, {"pkg/mod.py": {"contexts": {"10": ["tests/test_foo.py::test_pass_0|run"]}}}
    )
    junit = _write_junit(tmp_path, _passing_cases(1))

    results = rank_suspicious_locations(coverage, junit)

    assert results == []


def test_junit_test_class_normalizes_to_same_id_space_as_coverage_context(tmp_path):
    coverage = _write_coverage(
        tmp_path,
        {"pkg/mod.py": {"contexts": {"10": ["tests/test_foo.py::TestThing::test_it|run"]}}},
    )
    junit = _write_junit(tmp_path, [("tests.test_foo.TestThing", "test_it", True)])

    results = rank_suspicious_locations(coverage, junit)

    assert results == [
        {"file": "pkg/mod.py", "line": 10, "score": 1.0, "failed": 1, "passed": 0}
    ]


def _write_fixture_script(tmp_path) -> str:
    """A tiny script that writes coverage.json + junit.xml into its cwd.

    Standing in for a real ``coverage_cmd`` — it writes the same shape of
    artifacts a `pytest --cov ... --cov-context=test --junit-xml=...` run
    would, into whatever directory it is invoked from (the worktree).
    """
    script_path = tmp_path / "write_fixtures.py"
    script_path.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "Path('coverage.json').write_text(json.dumps({'files': {'pkg/mod.py': "
        "{'contexts': {'10': ['tests/test_foo.py::test_fails|run']}}}}))\n"
        "Path('junit.xml').write_text("
        "'<testsuite><testcase classname=\"tests.test_foo\" name=\"test_fails\">"
        "<failure message=\"boom\">trace</failure></testcase></testsuite>')\n"
    )
    return str(script_path)


def test_collect_runs_coverage_cmd_and_returns_ranked_locations(tmp_path):
    script = _write_fixture_script(tmp_path)

    results = collect_suspicious_locations(
        str(tmp_path),
        coverage_cmd=f"{sys.executable} {script}",
        coverage_json="coverage.json",
        junit_xml="junit.xml",
    )

    assert results == [
        {"file": "pkg/mod.py", "line": 10, "score": 1.0, "failed": 1, "passed": 0}
    ]


def test_collect_empty_coverage_cmd_returns_empty_without_running(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called for an empty coverage_cmd")

    monkeypatch.setattr("blacksmith.sbfl.subprocess.run", _boom)

    results = collect_suspicious_locations(
        str(tmp_path), coverage_cmd="", coverage_json="coverage.json", junit_xml="junit.xml"
    )

    assert results == []


def test_collect_blank_coverage_cmd_returns_empty_without_running(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called for a blank coverage_cmd")

    monkeypatch.setattr("blacksmith.sbfl.subprocess.run", _boom)

    results = collect_suspicious_locations(
        str(tmp_path), coverage_cmd="   ", coverage_json="coverage.json", junit_xml="junit.xml"
    )

    assert results == []


def test_collect_command_writing_no_artifacts_returns_empty_without_raising(tmp_path):
    results = collect_suspicious_locations(
        str(tmp_path),
        coverage_cmd=f'{sys.executable} -c "pass"',
        coverage_json="coverage.json",
        junit_xml="junit.xml",
    )

    assert results == []


def test_collect_failing_command_is_ignored_and_only_artifacts_matter(tmp_path):
    script = _write_fixture_script(tmp_path)

    results = collect_suspicious_locations(
        str(tmp_path),
        coverage_cmd=f"{sys.executable} {script} && exit 1",
        coverage_json="coverage.json",
        junit_xml="junit.xml",
    )

    assert results == [
        {"file": "pkg/mod.py", "line": 10, "score": 1.0, "failed": 1, "passed": 0}
    ]


def test_collect_raising_subprocess_returns_empty_without_raising(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("no such executable")

    monkeypatch.setattr("blacksmith.sbfl.subprocess.run", _boom)

    results = collect_suspicious_locations(
        str(tmp_path),
        coverage_cmd="does-not-matter",
        coverage_json="coverage.json",
        junit_xml="junit.xml",
    )

    assert results == []


def test_format_suspicious_locations_renders_labelled_block():
    locations = [
        {"file": "pkg/mod.py", "line": 10, "score": 1.0, "failed": 2, "passed": 1},
        {"file": "pkg/mod.py", "line": 20, "score": 0.5, "failed": 1, "passed": 3},
    ]

    rendered = format_suspicious_locations(locations)

    assert rendered == (
        "SUSPICIOUS LOCATIONS (fault localization, most-suspicious first):\n"
        "pkg/mod.py:10 (score 1.00, 2 failing / 1 passing tests)\n"
        "pkg/mod.py:20 (score 0.50, 1 failing / 3 passing tests)"
    )


def test_format_suspicious_locations_empty_list_is_empty_string():
    assert format_suspicious_locations([]) == ""
