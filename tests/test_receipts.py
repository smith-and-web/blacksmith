"""Atomic critical receipts, concurrent writers and SDK replay without live transport."""

import asyncio
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from blacksmith.config import BlacksmithConfig, TranscriptsConfig
from blacksmith.executor import Executor, ExecutorError
from blacksmith.nodes.implement import _stage_and_commit
from blacksmith.receipts import (
    CallContext,
    OperationJournal,
    OperationKey,
    ReceiptError,
    merge_events,
)


@dataclass(frozen=True)
class JournalContext:
    directory: Path
    run_id: str

    def model_dump(self):
        return {"run_dir": str(self.directory), "run_id": self.run_id, "state_version": 2}


@pytest.fixture
def journal(tmp_path):
    return OperationJournal.create(JournalContext(tmp_path, str(uuid4())))


def key(journal, unit="U1", attempt="attempt-1", kind="model.plan"):
    return OperationKey(journal.run_id, unit, attempt, kind)


def call_context(journal, attempt="attempt-1"):
    return CallContext(str(journal.path), journal.run_id, journal.context_hash, "U1", attempt)


def sdk_result(*, error=False, cost=0.25):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=error,
        num_turns=2,
        session_id="offline-provider-session",
        result="retained model response",
        total_cost_usd=cost,
        usage={"input_tokens": 12, "output_tokens": 3},
    )


def executor(monkeypatch, query):
    monkeypatch.setenv("BLACKSMITH_ANTHROPIC_API_KEY", "unused-offline-test-key")
    config = BlacksmithConfig(transcripts=TranscriptsConfig(enabled=False))
    return Executor(config, query_fn=query)


def test_journal_is_separate_wal_full_and_survives_reopen(journal):
    operation = key(journal)
    first = journal.intent(operation, {"prompt_sha": "abc"})
    assert first.created and first.status == "intent"
    reopened = OperationJournal(journal.path, journal.run_id, journal.context_hash)
    assert reopened.get(operation).intent == {"prompt_sha": "abc"}
    assert not reopened.intent(operation, {"prompt_sha": "abc"}).created
    reopened.finish(
        operation,
        "completed",
        {"response": "saved"},
        events=[("cost-1", "cost", {"cost_usd": 0.25})],
    )
    assert journal.get(operation).outcome == {"response": "saved"}
    assert len(journal.events()) == 1
    with journal._connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    journal.verify()


def test_outcome_and_event_commit_or_rollback_together(journal):
    operation = key(journal)
    journal.intent(operation, {"task": "model"})
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_cost BEFORE INSERT ON events BEGIN "
            "SELECT RAISE(ABORT, 'injected receipt failure'); END"
        )
    with pytest.raises(ReceiptError, match="read/write failed"):
        journal.finish(
            operation,
            "completed",
            {"known_cost": 0.25},
            events=[("cost-1", "cost", {"cost_usd": 0.25})],
        )
    assert journal.get(operation).status == "intent"
    assert journal.get(operation).outcome is None
    assert journal.events() == []
    with sqlite3.connect(journal.path) as connection:
        connection.execute("DROP TRIGGER fail_cost")
    journal.finish(
        operation,
        "completed",
        {"known_cost": 0.25},
        events=[("cost-1", "cost", {"cost_usd": 0.25})],
    )
    assert journal.get(operation).status == "completed" and len(journal.events()) == 1


def test_conflicting_intent_outcome_and_event_are_never_silently_overwritten(journal):
    operation = key(journal)
    journal.intent(operation, {"input": "original"})
    with pytest.raises(ReceiptError, match="intent changed"):
        journal.intent(operation, {"input": "changed"})
    journal.finish(
        operation,
        "unknown",
        {"reason": "interrupted"},
        events=[("cost", "cost", {"cost_usd": None})],
    )
    with pytest.raises(ReceiptError, match="conflicting operation outcome"):
        journal.finish(operation, "completed", {"response": "invented"})
    with pytest.raises(ReceiptError, match="conflicting event"):
        journal.record_event(operation, "cost", "cost", {"cost_usd": 0})
    assert journal.get(operation).status == "unknown"
    assert journal.get(operation).outcome == {"reason": "interrupted"}
    assert journal.events()[0]["payload"]["cost_usd"] is None


def test_repeated_finish_and_event_insert_are_idempotent(journal):
    operation = key(journal)
    journal.intent(operation, {"task": "same"})
    for _ in range(3):
        journal.finish(
            operation,
            "completed",
            {"commit": "saved"},
            events=[("cost", "cost", {"cost_usd": 0.5})],
        )
        journal.record_event(operation, "cost", "cost", {"cost_usd": 0.5})
    assert len(journal.events()) == 1


def test_operation_ids_hash_tuple_boundaries_and_raw_ids(journal):
    assert key(journal, "a:b", "c").id != key(journal, "a", "b:c").id
    assert key(journal, "A+B").id != key(journal, "A_B").id
    assert key(journal, attempt="1").id != key(journal, attempt="2").id
    assert key(journal, kind="model.plan").id != key(journal, kind="model.critic").id
    assert len(key(journal).id) == 64
    assert key(journal).trailer == "Blacksmith-Operation-Id: " + key(journal).id


def test_concurrent_writers_have_one_intent_and_one_event_per_operation(journal):
    def write(index):
        operation = key(journal, unit=f"unit-{index % 5}")
        intent = journal.intent(operation, {"unit": operation.unit_id})
        journal.finish(
            operation,
            "completed",
            {"unit": operation.unit_id},
            events=[(operation.id + ":cost", "cost", {"cost_usd": 0.1})],
        )
        return intent.created

    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(write, range(40)))
    assert sum(created) == 5 and len(journal.events()) == 5
    assert sum(e["payload"]["cost_usd"] for e in journal.events()) == pytest.approx(0.5)
    journal.verify()


def test_real_process_exit_after_intent_preserves_pending_receipt(journal):
    operation = key(journal)
    script = (
        "import os,sys\nfrom blacksmith.receipts import OperationJournal,OperationKey\n"
        "j=OperationJournal(sys.argv[1],sys.argv[2],sys.argv[3])\n"
        "j.intent(OperationKey(sys.argv[2],'U1','attempt-1','model.plan'),{'input':'durable'})\n"
        "os._exit(19)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(journal.path), journal.run_id, journal.context_hash],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 19
    assert journal.get(operation).status == "intent"
    assert journal.get(operation).intent == {"input": "durable"}


@pytest.mark.parametrize("failure", ["missing", "corrupt", "wrong-run", "wrong-context", "schema"])
def test_journal_integrity_failures_never_create_or_replace_recovery_data(journal, failure):
    if failure == "missing":
        journal.path.unlink()
    elif failure == "corrupt":
        journal.path.write_bytes(b"retained corrupt evidence")
    elif failure == "wrong-run":
        journal = OperationJournal(journal.path, str(uuid4()), journal.context_hash)
    elif failure == "wrong-context":
        journal = OperationJournal(journal.path, journal.run_id, "0" * 64)
    else:
        with sqlite3.connect(journal.path) as connection:
            connection.execute("ALTER TABLE operations ADD COLUMN unexpected TEXT")
    with pytest.raises(ReceiptError):
        journal.verify()
    if failure == "missing":
        assert not journal.path.exists()
    if failure == "corrupt":
        assert journal.path.read_bytes() == b"retained corrupt evidence"


def test_corrupt_receipt_payload_stops_even_when_sqlite_is_valid(journal):
    operation = key(journal)
    journal.intent(operation, {"input": "original"})
    with sqlite3.connect(journal.path) as connection:
        connection.execute("UPDATE operations SET intent_json='{}'")
    with pytest.raises(ReceiptError, match="corrupt operation"):
        journal.get(operation)
    with pytest.raises(ReceiptError, match="corrupt operation"):
        journal.verify()


def test_event_reduction_deduplicates_ids_but_keeps_distinct_calls_and_legacy_events():
    a = {"event_id": "a", "cost_usd": 1.0}
    b = {"event_id": "b", "cost_usd": 1.0}
    legacy = {"cost_usd": 1.0}
    assert merge_events([a, legacy], [a, b, legacy]) == [a, legacy, b, legacy]
    with pytest.raises(ReceiptError, match="conflicting reduced"):
        merge_events([a], [{**a, "cost_usd": 0.0}])


@pytest.mark.parametrize(
    "method,kind",
    [
        ("run_plan", "model.plan"),
        ("run_plan_critique", "model.critic"),
        ("run_implement", "model.implement"),
        ("run_implement_escalate", "model.implement"),
        ("run_review", "model.review"),
        ("run_summary", "model.summary"),
    ],
)
def test_every_sdk_role_persists_intent_before_transport_and_reuses_completed_result(
    journal,
    monkeypatch,
    method,
    kind,
):
    context = call_context(journal)
    calls = []

    async def query(*, prompt, options):
        assert journal.get(context.key(kind)).status == "intent"
        assert options.setting_sources == [] and options.strict_mcp_config is True
        if method == "run_summary":
            assert options.tools == [] and options.mcp_servers == {}
        calls.append((prompt, options))
        yield sdk_result()

    first = getattr(executor(monkeypatch, query), method)("original request", call_context=context)
    reopened = OperationJournal(journal.path, journal.run_id, journal.context_hash)
    again = getattr(executor(monkeypatch, query), method)("original request", call_context=context)
    assert len(calls) == 1 and first == again
    assert first.call_id == context.key(kind).id
    assert reopened.get(context.key(kind)).status == "completed"
    assert len(reopened.events()) == 1
    assert reopened.events()[0]["payload"]["cost_usd"] == 0.25


def test_sdk_failed_terminal_result_is_retained_and_not_rebilled(journal, monkeypatch):
    context = call_context(journal)
    calls = []

    async def query(*, prompt, options):
        calls.append(prompt)
        yield sdk_result(error=True)

    ex = executor(monkeypatch, query)
    for _ in range(2):
        with pytest.raises(ExecutorError):
            ex.run_plan("same task", call_context=context)
    assert calls == ["same task"]
    assert journal.get(context.key("model.plan")).status == "failed"
    assert len(journal.events()) == 1


@pytest.mark.parametrize("ending", ["missing-terminal", "stream-error", "cancellation"])
def test_uncertain_sdk_call_retains_progress_and_cannot_repeat_automatically(
    journal,
    monkeypatch,
    ending,
):
    context = call_context(journal)
    calls = []

    async def query(*, prompt, options):
        calls.append(prompt)
        yield AssistantMessage(content=[TextBlock(text="partial")], model=options.model)
        yield AssistantMessage(content=[TextBlock(text="more partial")], model=options.model)
        if ending == "stream-error":
            raise RuntimeError("SDK stream lost")
        if ending == "cancellation":
            raise asyncio.CancelledError()

    ex = executor(monkeypatch, query)
    if ending == "cancellation":
        with pytest.raises(asyncio.CancelledError):
            ex.run_plan("work", call_context=context)
    else:
        with pytest.raises(ReceiptError, match="interrupted_attempt_requires_resume"):
            ex.run_plan("work", call_context=context)
        receipt = journal.get(context.key("model.plan"))
        assert receipt.outcome["result"]["num_turns"] == 2
        assert receipt.outcome["result"]["cost_usd"] is None
        assert not receipt.outcome["result"]["terminal_received"]
    with pytest.raises(ReceiptError, match="interrupted_attempt_requires_resume"):
        ex.run_plan("work", call_context=context)
    assert calls == ["work"]
    assert journal.get(context.key("model.plan")).status in {"unknown", "interrupted"}


def test_changed_prompt_and_critical_intent_failure_cannot_reach_sdk(journal, monkeypatch):
    context = call_context(journal)
    journal.intent(context.key("model.plan"), {"different": "intent"})

    async def forbidden_query(*, prompt, options):
        pytest.fail("conflicting or corrupt receipt must prevent SDK transport")
        yield sdk_result()

    ex = executor(monkeypatch, forbidden_query)
    with pytest.raises(ReceiptError, match="intent changed"):
        ex.run_plan("changed request", call_context=context)
    journal.path.unlink()
    with pytest.raises(ReceiptError):
        ex.run_plan("changed request", call_context=context)
    assert not journal.path.exists()


def test_real_git_commit_has_operation_trailer_and_replay_cannot_duplicate_it(journal, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (repo / "base.txt").write_text("base")
    git("add", "-A")
    git("commit", "-m", "base")
    (repo / "feature.txt").write_text("recoverable change")
    context = call_context(journal)
    first = _stage_and_commit(str(repo), "feat: implement", call_context=context)
    candidate = git("rev-parse", "HEAD")
    assert context.key("git.commit").trailer in git("log", "-1", "--format=%B")
    second = _stage_and_commit(str(repo), "feat: implement", call_context=context)
    assert first == second and git("rev-parse", "HEAD") == candidate
    assert git("rev-list", "--count", "HEAD") == "2"
    (repo / "unexpected.txt").write_text("unknown extra change")
    with pytest.raises(ReceiptError, match="unknown extra changes"):
        _stage_and_commit(str(repo), "feat: implement", call_context=context)
    assert git("rev-parse", "HEAD") == candidate
    assert (repo / "unexpected.txt").read_text() == "unknown extra change"
