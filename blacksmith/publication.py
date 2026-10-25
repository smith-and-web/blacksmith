"""Observed remote publication with immutable effects and restart reconciliation."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit

from blacksmith.commands import CommandError, command_scope
from blacksmith.contract import ContractError
from blacksmith.gate import GateError
from blacksmith.publication_approval import ApprovalError, hold
from blacksmith.publication_approval import verify as verify_approval
from blacksmith.receipts import OperationKey, ReceiptError, digest, encoded
from blacksmith.run_context import RunContextError, atomic_write, context_from_state
from blacksmith.state import Status


class PublicationError(ValueError):
    """A failed observation, remote conflict or unresolved publication effect."""


def target_for(state):
    approved = verify_approval(state, draft=state.get("publication_draft", False))
    if not approved:
        raise PublicationError("publication requires a versioned exact-candidate approval")
    return {"version": 1, **{k: approved[k] for k in (
        "run_id", "candidate_commit", "candidate_tree", "origin", "branch", "base_branch",
        "draft")}}


def marker(target):
    return "<!-- blacksmith-publication: " + encoded(target) + " -->"


def _attempt(target):
    return "publication:" + digest(encoded(target))


def _key(context, target, kind, suffix=""):
    return OperationKey(context.run_id, "__run__", _attempt(target) + suffix, kind)


def _path(context, target, name):
    path = context.directory / "publication" / (digest(encoded(target)) + "-" + name + ".json")
    if path.resolve() != path or path.is_symlink():
        raise ReceiptError("foreign publication artifact")
    return path


def _persist(path, data):
    content = encoded(data) + "\n"
    if path.exists():
        if path.read_text() != content:
            raise ReceiptError("publication artifact changed")
    else:
        atomic_write(path, content)


def _artifact(context, target, kind, name, data):
    key = _key(context, target, kind)
    context.journal().intent(key, {"target": target})
    seal = _key(context, target, kind + ".seal")
    context.journal().intent(seal, {"sha256": digest(encoded(data)), "data": data})
    context.journal().finish(seal, "completed", {"sha256": digest(encoded(data))})
    path = _path(context, target, name)
    _persist(path, data)
    context.journal().finish(key, "completed", {
        "artifact_path": str(path), "sha256": digest(encoded(data)), "data": data})
    return key.id


def _load(context, target, kind, name):
    row = context.journal().get(_key(context, target, kind))
    path = _path(context, target, name)
    if row is None:
        if path.exists():
            raise ReceiptError("publication artifact lacks critical ownership")
        return None
    if row.intent != {"target": target}:
        raise ReceiptError("publication target differs from critical evidence")
    if row.status != "completed":
        seal = context.journal().get(_key(context, target, kind + ".seal"))
        if row.status != "intent":
            raise ReceiptError("incomplete publication artifact; inspect retained evidence")
        if seal is None:
            if path.exists():
                raise ReceiptError("unsealed publication artifact; inspect retained evidence")
            return None
        try:
            data = seal.intent["data"]
            if (seal.intent != {"sha256": digest(encoded(data)), "data": data}
                    or seal.status not in {"intent", "completed"}
                    or (seal.status == "completed"
                        and seal.outcome != {"sha256": digest(encoded(data))})
                    or (path.exists() and json.loads(path.read_text()) != data)):
                raise ReceiptError("changed publication persistence seal/artifact")
        except (OSError, ValueError) as exc:
            raise ReceiptError("malformed incomplete publication artifact") from exc
        return data  # Caller validates before sealing or admitting effects.
    try:
        data = row.outcome["data"]
        if (row.outcome != {"artifact_path": str(path), "sha256": digest(encoded(data)),
                           "data": data} or not path.is_file()
                or json.loads(path.read_text()) != data):
            raise ReceiptError("missing/corrupt publication artifact")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ReceiptError("missing/corrupt publication artifact") from exc
    return data


def _repo(target):
    host, *parts = target["origin"].split("/")
    if len(parts) != 2:
        raise PublicationError("publication requires a GitHub host/owner/repository identity")
    return host, "/".join(parts), parts[0]


def _pr(target, row):
    """Validate API identities and marker, then retain a small exact observation."""
    try:
        host, repo, _ = _repo(target)
        number = row["number"]
        if type(number) is not int or number <= 0 or type(row["draft"]) is not bool:
            raise ValueError("invalid number/draft")
        expected_repo = "https://" + target["origin"]
        for side in ("head", "base"):
            remote = row[side]["repo"]
            if (remote["full_name"].lower() != repo
                    or remote["html_url"].rstrip("/").lower() != expected_repo):
                raise ValueError("foreign repository")
        url = urlsplit(row["html_url"])
        body = row["body"]
        if (url.scheme != "https" or url.netloc.lower() != host or url.query or url.fragment
                or url.path.lower() != "/" + repo + "/pull/" + str(number)
                or row["head"]["ref"] != target["branch"]
                or row["head"]["sha"] != target["candidate_commit"]
                or row["base"]["ref"] != target["base_branch"]
                or row["draft"] is not target["draft"]
                or row["state"] not in {"open", "closed"}
                or not isinstance(body, str) or body.count("<!-- blacksmith-publication:") != 1
                or re.findall(r"<!-- blacksmith-publication: .*? -->", body) != [marker(target)]):
            raise ValueError("mismatched PR identity/marker")
        return {"url": row["html_url"], "number": number, "state": row["state"],
                "draft": row["draft"], "marker": marker(target)}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PublicationError(
            "remote PR does not match exact run/repository/base/head/SHA/draft") from exc


def _operations(context, target, kind):
    return [r for r in context.journal().operations()
            if r.key.kind == kind and r.key.attempt_id.startswith(_attempt(target))]


def _check(state, target):
    if target_for(state) != target:
        raise PublicationError("publication approval target changed")


def observe(state, context, target, runner):
    _check(state, target)
    index = len(_operations(context, target, "publication.observe"))
    key = _key(context, target, "publication.observe", f":observe:{index}")
    context.journal().intent(key, {"target": target})
    try:
        with command_scope(context, stage="publication.observe", attempt_id=key.attempt_id):
            ref = "refs/heads/" + target["branch"]
            branch = runner(["git", "ls-remote", "--refs", "origin", ref],
                            context.workspace_path)
            if branch.returncode:
                raise PublicationError("remote branch query failed; inspect retained command logs")
            lines = branch.stdout.splitlines()
            remote = None
            if lines:
                if len(lines) != 1 or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\t"
                                                     + re.escape(ref), lines[0]):
                    raise PublicationError("malformed/duplicate remote branch observation")
                remote = lines[0].split("\t")[0]
            if remote is not None and remote != target["candidate_commit"]:
                raise PublicationError("remote branch conflict; refusing to force-push")
            host, repo, owner = _repo(target)
            result = runner(["gh", "api", "repos/" + quote(repo, safe="/") + "/pulls",
                "--hostname", host, "--method", "GET", "--paginate", "--slurp",
                "-f", "state=all", "-f", "head=" + owner + ":" + target["branch"],
                "-f", "per_page=100"], context.workspace_path)
            if result.returncode:
                raise PublicationError("remote PR query failed; inspect retained command logs")
            try:
                pages = json.loads(result.stdout)
                if not isinstance(pages, list) or any(not isinstance(p, list) for p in pages):
                    raise ValueError("invalid paginated array")
                matches = [row for page in pages for row in page]
            except (TypeError, ValueError) as exc:
                raise PublicationError("malformed remote PR query response") from exc
            if len(matches) > 1:
                raise PublicationError("multiple remote PR matches; refusing arbitrary selection")
            pr = _pr(target, matches[0]) if matches else None
            if pr and remote is None:
                raise PublicationError("matching PR has no exact remote branch; inspect")
        data = {"remote_commit": remote, "pr": pr}
        context.journal().finish(key, "completed", {"observation": data})
        return data, key.id
    except (PublicationError, CommandError, OSError):
        context.journal().finish(key, "failed", {"error": "publication observation failed"})
        raise


def _effect(context, target, kind, argv, runner):
    index = len(_operations(context, target, kind))
    key = _key(context, target, kind, f":{kind}:{index}")
    context.journal().intent(key, {"target": target, "argv": argv})
    try:
        with command_scope(context, stage=kind, attempt_id=key.attempt_id):
            result = runner(argv, context.workspace_path)
    except (CommandError, OSError) as exc:
        context.journal().finish(key, "interrupted", {"error": type(exc).__name__})
        return False
    context.journal().finish(key, "completed" if result.returncode == 0 else "failed",
                             {"returncode": result.returncode})
    return result.returncode == 0


def _unconfirmed(context, target, kind):
    return any(context.journal().pending_commands("__run__", row.key.attempt_id)
               for row in _operations(context, target, kind))


def _content(context, target):
    data = _load(context, target, "publication.content", "content")
    if data is not None:
        if (set(data) != {"title", "body", "cost_events"} or not isinstance(data["title"], str)
                or not data["title"] or not isinstance(data["body"], str)
                or data["body"].count(marker(target)) != 1
                or not isinstance(data["cost_events"], list)):
            raise ReceiptError("invalid publication content evidence")
        from blacksmith.nodes.plan import usage_breakdown
        known = {e["event_id"]: {**{k: e["payload"][k] for k in (
            "unit_id", "model", "cost_usd", "num_turns", "session_id", "call_id")},
            "node": e["payload"]["node"].removeprefix("model."),
            "usage": usage_breakdown(e["payload"]["usage"]), "event_id": e["event_id"]}
            for e in context.journal().events() if e["kind"] == "model_cost"}
        if any(known.get(e.get("event_id")) != e for e in data["cost_events"]):
            raise ReceiptError("publication content cost differs from critical accounting")
    return data


def _receipt(context, target):
    data = _load(context, target, "publication.result", "result")
    if data is None:
        return None
    try:
        if (set(data) != {"version", "receipt_id", "target", "pr", "observation_id",
                         "effect_ids", "artifact_path"}
                or type(data["version"]) is not int or data["version"] != 1
                or data["target"] != target
                or data["receipt_id"] != _key(context, target, "publication.result").id
                or data["artifact_path"] != str(_path(context, target, "result"))):
            raise ValueError("invalid target")
        observed = context.journal().get_by_id(data["observation_id"])
        if (not observed or observed.status != "completed"
                or observed.key.kind != "publication.observe"
                or observed.intent != {"target": target}
                or observed.outcome != {"observation": {"remote_commit": target["candidate_commit"],
                                                       "pr": data["pr"]}}):
            raise ValueError("invalid observed effect")
        for identity in data["effect_ids"]:
            row = context.journal().get_by_id(identity)
            if (not row or row.key.kind not in {"publication.push", "publication.create"}
                    or row.intent.get("target") != target):
                raise ValueError("foreign effect")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ReceiptError("invalid publication receipt") from exc
    return data


def _require_unpublished(context, target, observed, previous):
    if observed["pr"]:
        return
    if (previous or any(r.status == "completed" for r in _operations(
            context, target, "publication.create"))):
        raise PublicationError("completed publication is missing remotely; refusing replay")
    if _unconfirmed(context, target, "publication.create"):
        raise PublicationError("native PR create outcome unconfirmed; inspect process")
    if observed["remote_commit"] is None and (
            _unconfirmed(context, target, "publication.push") or any(
                r.status == "completed" for r in _operations(context, target, "publication.push"))):
        raise PublicationError("previous push is missing/unconfirmed; inspect")


def publish(state, *, runner, prepare_content):
    if state.get("publication_version") != 1:
        raise ReceiptError("legacy_publication_requires_manual_recovery")
    context = context_from_state(state)
    target = target_for(state)
    content = _content(context, target)
    costs = content["cost_events"] if content else []
    try:
        previous = _receipt(context, target)
        observed, observation_id = observe(state, context, target, runner)
        if previous and (not observed["pr"] or {
                k: v for k, v in observed["pr"].items() if k != "state"} != {
                k: v for k, v in previous["pr"].items() if k != "state"}):
            raise PublicationError("previously published PR no longer matches; inspect")
        if not observed["pr"]:
            _require_unpublished(context, target, observed, previous)
            _check(state, target)
            if content is None:
                content = prepare_content()
                costs = content["cost_events"]
                content = {**content, "body": content["body"] + "\n\n" + marker(target)}
                _artifact(context, target, "publication.content", "content", content)
                _content(context, target)
            else:
                _artifact(context, target, "publication.content", "content", content)
            # Summary generation can take minutes. Never use its earlier remote
            # observation to authorize a later push or create.
            observed, observation_id = observe(state, context, target, runner)
            _require_unpublished(context, target, observed, previous)
            if not observed["pr"] and observed["remote_commit"] is None:
                _check(state, target)
                _effect(context, target, "publication.push", ["git", "push", "-u", "origin",
                    target["candidate_commit"] + ":refs/heads/" + target["branch"]], runner)
                observed, observation_id = observe(state, context, target, runner)
                if observed["remote_commit"] is None:
                    raise PublicationError("push not observed; retain candidate and retry-stage")
            if not observed["pr"]:
                _check(state, target)
                argv = ["gh", "pr", "create", "--repo", target["origin"], "--head",
                    target["branch"], "--base", target["base_branch"], "--title", content["title"],
                    "--body", content["body"]]
                if target["draft"]:
                    argv.append("--draft")
                _effect(context, target, "publication.create", argv, runner)
                observed, observation_id = observe(state, context, target, runner)
                if observed["pr"] is None:
                    raise PublicationError(
                        "PR not observed; retain branch and resume --retry-stage")
        _check(state, target)
        if previous is None:
            previous = {"version": 1,
                "receipt_id": _key(context, target, "publication.result").id,
                "target": target, "pr": observed["pr"], "observation_id": observation_id,
                "effect_ids": [r.key.id for kind in ("publication.push", "publication.create")
                               for r in _operations(context, target, kind)],
                "artifact_path": str(_path(context, target, "result"))}
        _artifact(context, target, "publication.result", "result", previous)
        _receipt(context, target)
        return {"status": Status.AWAITING_QA if target["draft"] else Status.DONE,
                "pr_url": previous["pr"]["url"], "publication_receipt": previous,
                "pending_qa_unit": state["pr_approval"]["proposal"]["pending_qa_unit"],
                "cost_events": costs}
    except (ApprovalError, GateError, ContractError, RunContextError) as exc:
        return {**hold(state, exc, node="open_draft_pr" if target["draft"] else "open_pr"),
                "cost_events": costs}
    except (PublicationError, CommandError, OSError) as exc:
        return {"status": Status.HALTED, "cost_events": costs,
                "errors": [{"node": "open_draft_pr" if target["draft"] else "open_pr",
                            "message": str(exc)}]}


def _validate_state(state):
    """Read-only pre-executor integrity checks; no remote access or invented success."""
    if not state.get("worktree_path"):
        return
    if state.get("publication_version") != 1:
        raise ReceiptError("legacy_publication_requires_manual_recovery")
    terminal = state.get("status") in {Status.DONE, Status.AWAITING_QA}
    if not state.get("pr_approval") or state["pr_approval"].get("approved") is not True:
        if terminal or state.get("publication_receipt") or state.get("pr_url"):
            raise ReceiptError("published state lacks exact approved publication evidence")
        return
    if state.get("approvals", {}).get("pr") is not True:
        raise ReceiptError("publication decision differs from approved state")
    context = context_from_state(state)
    target = target_for({**state, "status": Status.TESTING})
    _content(context, target)
    receipt = _receipt(context, target)
    if terminal and (not receipt or state.get("pr_url") != receipt["pr"]["url"]
            or state["status"] != (Status.AWAITING_QA if target["draft"] else Status.DONE)
            or state.get("pending_qa_unit") != state["pr_approval"]["proposal"]["pending_qa_unit"]):
        raise ReceiptError("terminal URL/status/QA differs from approved publication receipt")
    if state.get("publication_receipt") != receipt or (state.get("status") in {
            Status.DONE, Status.AWAITING_QA} and receipt is None):
        # Receipt-before-checkpoint is valid only for an interrupted nonterminal node.
        if not (not state.get("publication_receipt") and receipt
                and state.get("status") not in {Status.DONE, Status.AWAITING_QA}):
            raise ReceiptError("terminal publication lacks its exact critical receipt")


def validate_state(state):
    try:
        return _validate_state(state)
    except ReceiptError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        raise ReceiptError("invalid retained publication evidence") from exc


def recovery_target(context, state):
    """Select only an actually entered publication stage under the same approval."""
    if state.get("publication_version") != 1 or state.get("approvals", {}).get("pr") is not True:
        return None
    from blacksmith.publication_approval import ApprovalError
    try:
        target = target_for({**state, "status": Status.TESTING})
    except ApprovalError:
        return None
    if not _operations(context, target, "publication.observe"):
        return None
    return "open_draft_pr" if target["draft"] else "open_pr"
