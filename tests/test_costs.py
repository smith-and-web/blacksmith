"""Tests for ``blacksmith costs`` (WU-COST-REPORT).

The reporter queries two Anthropic Admin API endpoints — cost_report and
usage_report/messages — through an injectable fetcher, so these tests supply canned
payloads in the REAL schemas with NO network, and assert the printed total cost +
per-model token totals (including a cache-read vs uncached split). The reporter is
strictly read-only: every request is a GET to api.anthropic.com. A missing admin key
exits non-zero with a message naming the env var.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest

import blacksmith.costs as costs
from blacksmith.config import BlacksmithConfig, ConfigError
from blacksmith.costs import (
    ADMIN_API_HOST,
    ANTHROPIC_VERSION,
    FetchRequest,
    fetch_cost_report,
    format_report,
    generate_report,
    per_model_usage,
    run_costs,
    total_cost,
)

FIXTURES = Path(__file__).parent / "fixtures"

# --- Canned payloads, in the REAL Admin-API schemas (validated live 2026-06-23). ---

COST_PAYLOAD = {
    "data": [
        {
            "starting_at": "2026-05-24T00:00:00Z",
            "results": [{"amount": "7.3227", "currency": "USD"}],
        },
        {
            "starting_at": "2026-05-25T00:00:00Z",
            "results": [{"amount": "2.6773", "currency": "USD"}],
        },
    ],
    "has_more": False,
    "next_page": None,
}

USAGE_PAYLOAD = {
    "data": [
        {
            "starting_at": "2026-05-24T00:00:00Z",
            "results": [
                {
                    "uncached_input_tokens": 1000,
                    "cache_read_input_tokens": 400,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 50,
                        "ephemeral_1h_input_tokens": 10,
                    },
                    "output_tokens": 300,
                    "model": "claude-sonnet-4-6",
                },
                {
                    "uncached_input_tokens": 200,
                    "cache_read_input_tokens": 0,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 0,
                        "ephemeral_1h_input_tokens": 0,
                    },
                    "output_tokens": 80,
                    "model": "claude-opus-4-8",
                },
            ],
        },
        {
            "starting_at": "2026-05-25T00:00:00Z",
            "results": [
                {
                    "uncached_input_tokens": 500,
                    "cache_read_input_tokens": 600,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 0,
                        "ephemeral_1h_input_tokens": 0,
                    },
                    "output_tokens": 100,
                    "model": "claude-sonnet-4-6",
                }
            ],
        },
    ],
    "has_more": False,
    "next_page": None,
}


class RecordingFetcher:
    """Canned fetcher that records every request and routes by endpoint path."""

    def __init__(self, *, cost=COST_PAYLOAD, usage=USAGE_PAYLOAD):
        self.cost = cost
        self.usage = usage
        self.requests: list[FetchRequest] = []

    def __call__(self, request: FetchRequest) -> dict:
        self.requests.append(request)
        if "cost_report" in request.url:
            return self.cost
        return self.usage


def _config() -> BlacksmithConfig:
    return BlacksmithConfig.load(FIXTURES / "valid_config.toml")


# --- Parsing the real schemas -------------------------------------------------------


def test_total_cost_sums_string_amounts():
    # ``amount`` is a STRING in the real schema; the reporter must coerce + sum.
    assert total_cost(COST_PAYLOAD["data"]) == pytest.approx(10.0)


def test_per_model_usage_splits_cache_read_vs_uncached():
    per_model = per_model_usage(USAGE_PAYLOAD["data"])

    sonnet = per_model["claude-sonnet-4-6"]
    # uncached: 1000 + 500; cache-read: 400 + 600; cache-creation: 50 + 10.
    assert sonnet.uncached_input_tokens == 1500
    assert sonnet.cache_read_input_tokens == 1000
    assert sonnet.cache_creation_input_tokens == 60
    assert sonnet.output_tokens == 400
    assert sonnet.input_tokens == 1500 + 1000 + 60

    opus = per_model["claude-opus-4-8"]
    assert opus.uncached_input_tokens == 200
    assert opus.cache_read_input_tokens == 0
    assert opus.output_tokens == 80


# --- The report end to end (injected fetcher, no network) ---------------------------


def test_generate_report_uses_get_only_to_anthropic_host():
    fetcher = RecordingFetcher()
    generate_report(
        admin_key="sk-ant-admin-xxx",
        starting_at="2026-05-24T00:00:00Z",
        ending_at="2026-06-23T00:00:00Z",
        fetcher=fetcher,
    )
    assert fetcher.requests  # both endpoints were hit
    for req in fetcher.requests:
        assert req.method == "GET"  # read-only: GET only
        assert urlparse(req.url).hostname == ADMIN_API_HOST
        assert req.headers["x-api-key"] == "sk-ant-admin-xxx"
        assert req.headers["anthropic-version"] == ANTHROPIC_VERSION
    # Both endpoints were queried.
    urls = " ".join(r.url for r in fetcher.requests)
    assert "cost_report" in urls
    assert "usage_report/messages" in urls
    assert "bucket_width=1d" in urls
    assert "group_by[]=model" in urls
    assert "limit=31" in urls  # capped at 31 for daily buckets


def test_format_report_prints_total_cost_and_per_model_tokens():
    report = generate_report(
        admin_key="sk-ant-admin-xxx",
        starting_at="2026-05-24T00:00:00Z",
        ending_at="2026-06-23T00:00:00Z",
        fetcher=RecordingFetcher(),
    )
    text = format_report(report)
    assert "total cost: $10.00 USD" in text
    assert "claude-sonnet-4-6" in text
    assert "claude-opus-4-8" in text
    # Per-model split is visible: sonnet uncached 1500, cache-read 1000.
    assert "uncached 1500" in text
    assert "cache-read 1000" in text


# --- Pagination: never silently truncate ---------------------------------------------


def test_fetcher_pages_through_has_more():
    page1 = {
        "data": [{"results": [{"amount": "1.00", "currency": "USD"}]}],
        "has_more": True,
        "next_page": "cursor-2",
    }
    page2 = {
        "data": [{"results": [{"amount": "2.50", "currency": "USD"}]}],
        "has_more": False,
        "next_page": None,
    }
    pages = [page1, page2]
    seen: list[str] = []

    def fetcher(request: FetchRequest) -> dict:
        seen.append(request.url)
        return pages[len(seen) - 1]

    buckets = fetch_cost_report(
        admin_key="k",
        starting_at="2026-05-24T00:00:00Z",
        ending_at="2026-06-30T00:00:00Z",
        fetcher=fetcher,
    )
    assert len(seen) == 2  # paged through both
    assert "page=cursor-2" in seen[1]  # followed next_page cursor
    assert total_cost(buckets) == pytest.approx(3.5)  # both pages counted


# --- Missing admin key ---------------------------------------------------------------


def test_run_costs_missing_admin_key_raises_naming_env_var(monkeypatch):
    cfg = _config()
    monkeypatch.delenv(cfg.api.admin_key_env_var, raising=False)
    with pytest.raises(ConfigError) as exc:
        run_costs(cfg, fetcher=RecordingFetcher())
    assert cfg.api.admin_key_env_var in str(exc.value)


def test_run_costs_prints_and_returns_report(monkeypatch, capsys):
    cfg = _config()
    monkeypatch.setenv(cfg.api.admin_key_env_var, "sk-ant-admin-xxx")
    fetcher = RecordingFetcher()
    report = run_costs(cfg, days=30, today=date(2026, 6, 23), fetcher=fetcher)
    out = capsys.readouterr().out
    assert "total cost: $10.00 USD" in out
    assert report.total_cost_usd == pytest.approx(10.0)
    # The default window is the last 30 days ending today.
    assert report.starting_at == "2026-05-24T00:00:00Z"
    assert report.ending_at == "2026-06-23T00:00:00Z"


def test_default_admin_key_env_var():
    assert _config().api.admin_key_env_var == "BLACKSMITH_ANTHROPIC_ADMIN_KEY"


# --- CLI integration -----------------------------------------------------------------


def test_cli_costs_missing_key_exits_nonzero(monkeypatch, capsys, tmp_path):
    from blacksmith import cli

    cfg = _config()
    monkeypatch.delenv(cfg.api.admin_key_env_var, raising=False)
    # cli.main calls load_dotenv(.env), which would re-populate the key from a real local
    # .env (setdefault) — undoing the delenv above and, worse, making a live billed Admin
    # API call. Neutralize the .env reload so the key is genuinely absent for this test.
    monkeypatch.setattr(cli, "load_dotenv", lambda _p: None)
    monkeypatch.setattr(cli, "_load_config", lambda _arg: cfg)
    code = cli.main(["costs"])
    assert code == 1
    err = capsys.readouterr().err
    assert cfg.api.admin_key_env_var in err


def test_cli_costs_reports(monkeypatch, capsys):
    from blacksmith import cli

    cfg = _config()
    monkeypatch.setenv(cfg.api.admin_key_env_var, "sk-ant-admin-xxx")
    monkeypatch.setattr(cli, "_load_config", lambda _arg: cfg)
    monkeypatch.setattr(costs, "urllib_fetcher", RecordingFetcher())
    code = cli.main(["costs"])
    assert code == 0
    out = capsys.readouterr().out
    assert "total cost: $10.00 USD" in out
