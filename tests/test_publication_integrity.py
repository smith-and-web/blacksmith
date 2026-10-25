"""Publication uses real scratch Git refs and strict persistent GitHub API responses."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_publication_approval import COMPILE, install, run
from test_run_context import git, saved_values
from test_run_context import project as project

from blacksmith import cli
from blacksmith.nodes.pr import CommandResult, subprocess_runner
from blacksmith.publication import marker, target_for
from blacksmith.run_context import context_from_state
from blacksmith.state import Status


class Remote:
    """Only GitHub is faked; ref reads/pushes use an actual local bare remote."""

    def __init__(self, project, *, thread="publication", mode="fresh"):
        self.project, self.thread, self.mode = project, thread, mode
        self.root = project[0].parent
        self.repo = self.root / "publication.git"
        self.data = self.root / "github.json"
        self.log = self.root / "remote-calls.jsonl"
        self.target = None
        if not self.repo.exists():
            subprocess.run(["git", "clone", "--bare", str(project[0]), str(self.repo)],
                           check=True, capture_output=True)
        if not self.data.exists():
            self.data.write_text("[]")

    def rows(self):
        return json.loads(self.data.read_text())

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def effects(self):
        return [c for c in self.calls() if c[:2] == ["git", "push"]
                or c[:3] == ["gh", "pr", "create"]]

    def pr(self, target, *, body=None):
        repo = {"full_name": target["origin"].split("/", 1)[1],
                "html_url": "https://" + target["origin"]}
        return {"number": 1, "html_url": "https://" + target["origin"] + "/pull/1",
            "state": "open", "draft": target["draft"], "body": body or marker(target),
            "head": {"ref": target["branch"], "sha": target["candidate_commit"], "repo": repo},
            "base": {"ref": target["base_branch"], "repo": repo}}

    def __call__(self, argv, cwd=None):
        argv, cwd = list(argv), Path(cwd)
        with self.log.open("a") as stream:
            stream.write(json.dumps(argv) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if self.target is None:
            self.target = target_for(saved_values(self.project[1], self.thread))
            if self.mode in {"existing", "branch_only", "wrong_base", "multiple", "marker",
                             "wrong_sha", "wrong_draft", "foreign_repo", "wrong_url"}:
                subprocess_runner(["git", "push", str(self.repo), self.target["candidate_commit"]
                    + ":refs/heads/" + self.target["branch"]], cwd)
                if self.mode != "branch_only":
                    row = self.pr(self.target)
                    if self.mode == "wrong_base":
                        row["base"]["ref"] = "unapproved-base"
                    elif self.mode == "marker":
                        row["body"] = "foreign PR without run marker"
                    elif self.mode == "wrong_sha":
                        row["head"]["sha"] = "f" * 40
                    elif self.mode == "wrong_draft":
                        row["draft"] = not row["draft"]
                    elif self.mode == "foreign_repo":
                        row["head"]["repo"] = {"full_name": "foreign/repo",
                                               "html_url": "https://github.com/foreign/repo"}
                    elif self.mode == "wrong_url":
                        row["html_url"] = "https://foreign.invalid/example/context-demo/pull/1"
                    self.data.write_text(json.dumps([row, row] if self.mode == "multiple"
                                                    else [row]))
            elif self.mode == "remote_conflict":
                base = git(cwd, "rev-list", "--max-parents=0", "HEAD")
                subprocess_runner(["git", "push", str(self.repo), base + ":refs/heads/"
                                   + self.target["branch"]], cwd)
        if argv[:2] == ["git", "ls-remote"]:
            if self.mode == "branch_error":
                return CommandResult(1, "", "query failed")
            return subprocess_runner(["git", "ls-remote", "--refs", str(self.repo), argv[-1]], cwd)
        if argv[:2] == ["gh", "api"]:
            assert argv[2] == "repos/example/context-demo/pulls"
            assert argv[argv.index("--hostname") + 1] == "github.com"
            assert argv[argv.index("--method") + 1] == "GET" and "--paginate" in argv
            assert "--slurp" in argv and "state=all" in argv
            if self.mode == "api_error":
                return CommandResult(1, "", "API unavailable")
            return CommandResult(0, "{" if self.mode == "malformed" else json.dumps([self.rows()]),
                                 "")
        if argv[:2] == ["git", "push"]:
            assert "--force" not in argv and not any(a.startswith("+") for a in argv)
            result = subprocess_runner([*argv[:3], str(self.repo), argv[-1]], cwd)
            return CommandResult(1, "", "connection lost after push") \
                if self.mode == "push_error" else result
        assert argv[:3] == ["gh", "pr", "create"], argv
        assert argv[argv.index("--repo") + 1] == self.target["origin"]
        assert argv[argv.index("--head") + 1] == self.target["branch"]
        assert argv[argv.index("--base") + 1] == self.target["base_branch"]
        if self.mode == "create_failed":
            return CommandResult(1, "", "create failed before effect")
        self.data.write_text(json.dumps([self.pr(self.target,
            body=argv[argv.index("--body") + 1])]))
        return CommandResult(1 if self.mode == "create_error" else 0,
                             "lost URL" if self.mode == "create_error" else
                             "https://github.com/example/context-demo/pull/1\n", "")


def setup(project, monkeypatch, *, mode="fresh", on_summary=None):
    calls, _, payloads = install(monkeypatch, on_summary=on_summary)
    remote = Remote(project, mode=mode)
    monkeypatch.setattr(cli, "compile_graph", lambda saver, **kwargs:
                        COMPILE(saver, **{**kwargs, "pr_runner": remote}))
    return calls, remote, payloads


@pytest.mark.parametrize("change", ["matching_pr", "matching_branch", "conflicting_branch"])
def test_remote_changes_during_summary_are_observed_before_any_later_effect(
    project, monkeypatch, change,
):
    holder = {}

    def advance(workspace):
        remote = holder["remote"]
        target = remote.target
        if change == "conflicting_branch":
            base = git(remote.repo, "rev-parse", "refs/heads/main")
            git(remote.repo, "update-ref", "refs/heads/" + target["branch"], base)
        else:
            subprocess_runner(["git", "push", str(remote.repo), target["candidate_commit"]
                               + ":refs/heads/" + target["branch"]], workspace)
            if change == "matching_pr":
                remote.data.write_text(json.dumps([remote.pr(target)]))

    calls, remote, _ = setup(project, monkeypatch, on_summary=advance)
    holder["remote"] = remote
    assert run(project, "publication") == (1 if change == "conflicting_branch" else 0)
    state = saved_values(project[1], "publication")
    assert len([c for c in calls if c[0] == "summary"]) == 1
    assert not any(c[:2] == ["git", "push"] for c in remote.effects())
    assert len([c for c in remote.effects() if c[:3] == ["gh", "pr", "create"]]) == (
        1 if change == "matching_branch" else 0)
    assert len([e for e in state["cost_events"] if e["node"] == "summary"]) == 1
    assert bool(state.get("publication_receipt")) is (change != "conflicting_branch")
    assert Path(state["worktree_path"]).is_dir()


@pytest.mark.parametrize("mode", ["fresh", "existing", "branch_only", "push_error", "create_error"])
def test_exact_observed_effect_reconciles_without_duplicate_summary_push_or_create(
    project, monkeypatch, mode,
):
    calls, remote, _ = setup(project, monkeypatch, mode=mode)
    assert run(project, "publication") == 0
    state = saved_values(project[1], "publication")
    assert state["status"] == Status.DONE and state["publication_receipt"]["pr"]["number"] == 1
    assert len([c for c in remote.effects() if c[:2] == ["git", "push"]]) == (
        0 if mode in {"existing", "branch_only"} else 1)
    assert len([c for c in remote.effects() if c[:3] == ["gh", "pr", "create"]]) == (
        0 if mode == "existing" else 1)
    assert len([c for c in calls if c[0] == "summary"]) == (0 if mode == "existing" else 1)
    assert remote.calls()[0][:2] == ["git", "ls-remote"]
    assert marker(remote.target) in remote.rows()[0]["body"]
    assert git(remote.repo, "rev-parse", "refs/heads/" + state["branch"]) == remote.target[
        "candidate_commit"]
    before = list(remote.calls()), list(calls)
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "publication"]) == 0
    assert (remote.calls(), calls) == before


@pytest.mark.parametrize("mode", ["remote_conflict", "wrong_base", "multiple", "marker",
                                  "wrong_sha", "wrong_draft", "foreign_repo", "wrong_url",
                                  "branch_error", "api_error", "malformed"])
def test_conflicting_or_unavailable_remote_observation_halts_before_paid_summary_and_effects(
    project, monkeypatch, mode,
):
    calls, remote, _ = setup(project, monkeypatch, mode=mode)
    assert run(project, "publication") == 1
    state = saved_values(project[1], "publication")
    assert state["status"] == Status.HALTED and not state.get("pr_url")
    assert remote.effects() == [] and not any(c[0] == "summary" for c in calls)
    assert Path(state["worktree_path"]).is_dir()
    assert all(c[:2] != ["git", "push"] for c in remote.calls())


def test_failed_create_retries_only_creation_with_original_summary_cost_and_push(
    project, monkeypatch,
):
    calls, remote, payloads = setup(project, monkeypatch, mode="create_failed")
    assert run(project, "publication") == 1
    before = saved_values(project[1], "publication")
    context = context_from_state(before)
    original = next(o for o in context.journal().operations() if o.key.kind == "publication.create")
    assert original.status == "failed"
    cost_ids = {e["event_id"] for e in before["cost_events"]}
    remote.mode = "fresh"
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "publication",
                     "--retry-stage"]) == 0
    after = saved_values(project[1], "publication")
    assert context.journal().get(original.key) == original
    assert {e["event_id"] for e in after["cost_events"]} == cost_ids
    assert len([c for c in calls if c[0] == "summary"]) == 1
    assert len([c for c in remote.effects() if c[:2] == ["git", "push"]]) == 1
    assert len([c for c in remote.effects() if c[:3] == ["gh", "pr", "create"]]) == 2
    assert [p["gate"] for p in payloads] == ["plan", "pr"]


def test_unknown_native_create_holds_until_exact_remote_positive_without_repeating_effect(
    project, monkeypatch,
):
    calls, remote, _ = setup(project, monkeypatch, mode="create_failed")
    assert run(project, "publication") == 1
    state = saved_values(project[1], "publication")
    context = context_from_state(state)
    create = next(o for o in context.journal().operations()
                  if o.key.kind == "publication.create")
    pending = context.journal().command_intent("__run__", create.key.attempt_id,
        "publication.create:gh", {"argv": ["gh", "pr", "create"], "stage": "publication.create"})
    # Compare persisted fields; created=True describes only the insertion call.
    pending = context.journal().get(pending.key)
    effects, paid = list(remote.effects()), list(calls)
    remote.mode = "fresh"
    args = ["resume", "--config", str(project[1]), "--thread-id", "publication", "--retry-stage"]
    assert cli.main(args) == 1
    assert remote.effects() == effects and calls == paid
    assert context.journal().get(pending.key) == pending
    remote.data.write_text(json.dumps([remote.pr(remote.target)]))
    assert cli.main(args) == 0
    assert remote.effects() == effects and calls == paid
    assert context.journal().get(pending.key) == pending


def test_missing_branch_after_completed_push_is_conflict_and_never_recreated(project, monkeypatch):
    calls, remote, _ = setup(project, monkeypatch, mode="create_failed")
    assert run(project, "publication") == 1
    before = list(remote.effects()), list(calls)
    git(remote.repo, "update-ref", "-d", "refs/heads/" + remote.target["branch"])
    remote.mode = "fresh"
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "publication",
                     "--retry-stage"]) == 1
    assert (remote.effects(), calls) == before


@pytest.mark.parametrize("boundary", ["before_push", "after_push"])
def test_critical_push_boundary_fault_stops_then_observes_without_summary_or_effect_replay(
    project, monkeypatch, boundary,
):
    from blacksmith.receipts import OperationJournal, ReceiptError
    calls, remote, _ = setup(project, monkeypatch)
    original = OperationJournal.intent if boundary == "before_push" else OperationJournal.finish

    def fail(self, key, *args, **kwargs):
        if key.kind == "publication.push":
            raise ReceiptError("injected critical publication persistence failure")
        return original(self, key, *args, **kwargs)
    name = "intent" if boundary == "before_push" else "finish"
    monkeypatch.setattr(OperationJournal, name, fail)
    assert run(project, "publication") == 1
    state = saved_values(project[1], "publication")
    assert not state.get("pr_url") and Path(state["worktree_path"]).is_dir()
    assert len(remote.effects()) == (0 if boundary == "before_push" else 1)
    assert not any(c[:3] == ["gh", "pr", "create"] for c in remote.effects())
    assert len([c for c in calls if c[0] == "summary"]) == 1
    monkeypatch.setattr(OperationJournal, name, original)
    assert cli.main(["resume", "--config", str(project[1]), "--thread-id", "publication"]) == 0
    after = saved_values(project[1], "publication")
    assert len(remote.effects()) == 2
    assert len([c for c in calls if c[0] == "summary"]) == 1
    assert len(after["cost_events"]) == 4 and after["publication_receipt"]
