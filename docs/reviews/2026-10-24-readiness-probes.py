import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path

from blacksmith.cli import drive
from blacksmith.gate import run_gate
from blacksmith.graph import route_after_test_gate
from blacksmith.nodes.implement import make_pre_edit_guard
from blacksmith.state import Status

spec = importlib.util.spec_from_file_location("helpers", Path.cwd() / "tests/test_parallel.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
out = {}
with tempfile.TemporaryDirectory(prefix="blacksmith-review-") as td:
    root = Path(td)
    guard = make_pre_edit_guard(worktree_root=root)
    for name, args in [
        ("traversal", {"file_path": "../outside.txt"}),
        ("notebook", {"notebook_path": "/tmp/outside.ipynb"}),
        ("git_config", {"file_path": str(root / ".git/config")}),
    ]:
        result = asyncio.run(guard("NotebookEdit" if name == "notebook" else "Write", args, None))
        out[name] = type(result).__name__
    case = root / "combined"
    case.mkdir()
    repo = h._target_repo(case, "test ! -f wu-x.txt || test ! -f wu-y.txt")
    gh = h._recording_gh("https://example.test/pull/1")
    at_push = []

    def runner(argv, cwd=None):
        if argv[:2] == ["git", "push"]:
            at_push.append(run_gate(cwd).passed)
        return gh(argv, cwd)

    graph, _ = h._wire(case, repo, executor=h.FakeExecutor(), gh=runner)
    final = drive(
        graph,
        h._write_prd(case, h._TWO_INDEPENDENT),
        approver=lambda *_: True,
        thread_id="combined",
    )
    out["combined"] = {"status": final.values["status"].value, "actual_gate_at_push": at_push}
    case = root / "context"
    case.mkdir()
    repo = h._target_repo(case, "true")

    class Capture(h.FakeExecutor):
        def run_plan(self, prompt, **kwargs):
            out["plan_has_prose"] = "PROSE_SENTINEL" in prompt + kwargs["system_prompt"]
            out["plan_cwd"] = kwargs.get("cwd")
            return h._result("APPROVED_PLAN_SENTINEL")

        def run_implement(self, prompt, **kwargs):
            out["implement_has_prose"] = "PROSE_SENTINEL" in prompt + kwargs["system_prompt"]
            out["implement_has_plan"] = "APPROVED_PLAN_SENTINEL" in prompt + kwargs["system_prompt"]
            return super().run_implement(prompt, **kwargs)

    graph, _ = h._wire(
        case, repo, executor=Capture(), gh=h._recording_gh("https://example.test/pull/2")
    )
    prd = h._write_prd(case, h._ONE_UNIT)
    prd.write_text(prd.read_text() + "\nPROSE_SENTINEL\n")
    drive(graph, prd, approver=lambda *_: True, thread_id="context")
    case = root / "qa"
    case.mkdir()
    repo = h._target_repo(case, "true")
    gh = h._recording_gh("https://example.test/pull/3")
    graph, _ = h._wire(case, repo, executor=h.FakeExecutor(), gh=gh)
    prd = h._write_prd(
        case,
        h._unit("A", "auto", "a.txt", [])
        + "\n"
        + h._unit("QA", "manual", "qa.txt", ["A"]).replace("[py-logic]", "[qa]"),
    )
    prd.write_text(prd.read_text().replace("  py-logic: auto", "  py-logic: auto\n  qa: human"))
    gates = []

    def plan_only(payload, _):
        gates.append(payload.get("gate"))
        return payload.get("gate") == "plan"

    final = drive(graph, prd, approver=plan_only, thread_id="qa")
    out["qa_plan_only"] = {
        "status": final.values["status"].value,
        "gates": gates,
        "pushes": len(h._git_pushes(gh)),
    }
    case = root / "failed"
    case.mkdir()
    repo = h._target_repo(case, "false")
    graph, _ = h._wire(
        case, repo, executor=h.FakeExecutor(), gh=h._recording_gh("https://example.test/pull/4")
    )
    final = drive(
        graph, h._write_prd(case, h._ONE_UNIT), approver=lambda *_: True, thread_id="failed"
    )
    out["failed_workspace_exists"] = Path(final.values["worktree_path"]).exists()
    out["stale_gate_route"] = route_after_test_gate(
        {"status": Status.HALTED, "test_results": {"passed": True}}
    )
print(json.dumps(out, indent=2))
