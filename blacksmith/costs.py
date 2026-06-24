"""``blacksmith costs`` — org-level usage + cost from the Anthropic Admin API (read-only).

Reports organization-wide spend and token usage by querying two Admin API endpoints:

  * GET /v1/organizations/cost_report          — total cost (USD)
  * GET /v1/organizations/usage_report/messages — per-model token usage

This reporter is strictly READ-ONLY: it issues GET requests only and never creates,
modifies, or deletes any org resource. It authenticates with a SEPARATE org-scoped
Admin API key (``config.api.admin_key_env_var``), never blacksmith's dedicated run key,
and never persists it. Admin keys are scoped to Anthropic, so calls target
``api.anthropic.com`` directly and are NOT routed through ``ANTHROPIC_BASE_URL`` (which
may be a gateway/proxy).

The HTTP layer is an injectable ``Fetcher`` (mirroring ``nodes/pr.py``'s ``Runner``):
the default issues a stdlib ``urllib`` GET, while tests inject canned payloads — in the
real Admin-API schemas — with no network. Both endpoints cap ``limit`` at 31 for
``bucket_width=1d`` and paginate via ``has_more`` / ``next_page``, so the reporter pages
through rather than silently truncating a window longer than 31 days.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlencode

# Admin keys are org-scoped to Anthropic — always hit the real host directly, never a
# gateway/proxy (do NOT consult ANTHROPIC_BASE_URL here).
ADMIN_API_HOST = "api.anthropic.com"
ADMIN_API_BASE = f"https://{ADMIN_API_HOST}"
ANTHROPIC_VERSION = "2023-06-01"

COST_REPORT_PATH = "/v1/organizations/cost_report"
USAGE_REPORT_PATH = "/v1/organizations/usage_report/messages"

# With ``bucket_width=1d`` the Admin API caps ``limit`` at 31 buckets per page; longer
# windows are covered by pagination (``has_more`` / ``next_page``).
MAX_DAILY_LIMIT = 31

# Backstop so a server that returns ``has_more`` forever can't loop unboundedly.
_MAX_PAGES = 1000


@dataclass(frozen=True)
class FetchRequest:
    """One read-only HTTP request handed to a ``Fetcher`` (always GET)."""

    method: str
    url: str
    headers: dict[str, str]


# An injectable fetcher: given a request, return the parsed JSON body as a dict.
Fetcher = Callable[[FetchRequest], dict]


def urllib_fetcher(request: FetchRequest) -> dict:
    """Default fetcher: issue the given GET via the stdlib and parse the JSON body.

    Read-only by construction — it performs exactly the request handed to it. Uses only
    the standard library (``urllib``); no third-party HTTP client is introduced.
    """
    req = urllib.request.Request(
        request.url, method=request.method, headers=dict(request.headers)
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - host fixed to api.anthropic.com
        return json.loads(resp.read().decode("utf-8"))


@dataclass
class ModelUsage:
    """Per-model token totals, keeping the cache-read vs uncached split distinct."""

    uncached_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def input_tokens(self) -> int:
        """All input tokens billed (uncached + cache-read + cache-creation)."""
        return (
            self.uncached_input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass(frozen=True)
class CostReport:
    """The assembled org cost + usage report for a date window."""

    starting_at: str
    ending_at: str
    total_cost_usd: float
    currency: str
    per_model: dict[str, ModelUsage] = field(default_factory=dict)


def _headers(admin_key: str) -> dict[str, str]:
    return {"x-api-key": admin_key, "anthropic-version": ANTHROPIC_VERSION}


def _build_url(path: str, params: dict) -> str:
    # ``safe="[]"`` keeps ``group_by[]`` literal rather than percent-encoding the brackets.
    return f"{ADMIN_API_BASE}{path}?{urlencode(params, safe='[]')}"


def _paginate(path: str, params: dict, *, admin_key: str, fetcher: Fetcher) -> list[dict]:
    """Fetch every page of ``path`` (GET only), returning the concatenated ``data`` buckets.

    Follows ``next_page`` while ``has_more`` is truthy so windows longer than a single
    capped page are never silently truncated.
    """
    headers = _headers(admin_key)
    buckets: list[dict] = []
    page: str | None = None
    for _ in range(_MAX_PAGES):
        query = dict(params)
        if page:
            query["page"] = page
        request = FetchRequest(method="GET", url=_build_url(path, query), headers=headers)
        payload = fetcher(request)
        buckets.extend(payload.get("data") or [])
        next_page = payload.get("next_page")
        if payload.get("has_more") and next_page:
            page = next_page
            continue
        break
    return buckets


def fetch_cost_report(
    *, admin_key: str, starting_at: str, ending_at: str, fetcher: Fetcher = urllib_fetcher
) -> list[dict]:
    """Page through the org cost_report endpoint, returning its ``data`` buckets."""
    params = {
        "starting_at": starting_at,
        "ending_at": ending_at,
        "limit": MAX_DAILY_LIMIT,
    }
    return _paginate(COST_REPORT_PATH, params, admin_key=admin_key, fetcher=fetcher)


def fetch_usage_report(
    *, admin_key: str, starting_at: str, ending_at: str, fetcher: Fetcher = urllib_fetcher
) -> list[dict]:
    """Page through the org usage_report/messages endpoint grouped by model (daily buckets)."""
    params = {
        "starting_at": starting_at,
        "ending_at": ending_at,
        "bucket_width": "1d",
        "group_by[]": "model",
        "limit": MAX_DAILY_LIMIT,
    }
    return _paginate(USAGE_REPORT_PATH, params, admin_key=admin_key, fetcher=fetcher)


def total_cost(buckets: list[dict]) -> float:
    """Sum cost across all buckets. ``amount`` arrives as a STRING (e.g. "7.3227")."""
    total = 0.0
    for bucket in buckets:
        for result in bucket.get("results") or []:
            total += float(result.get("amount") or 0)
    return total


def report_currency(buckets: list[dict], *, default: str = "USD") -> str:
    """Return the currency reported by the cost results (first seen), defaulting to USD."""
    for bucket in buckets:
        for result in bucket.get("results") or []:
            currency = result.get("currency")
            if currency:
                return currency
    return default


def per_model_usage(buckets: list[dict]) -> dict[str, ModelUsage]:
    """Aggregate per-model token totals across all daily buckets.

    Mirrors the real usage_report schema: top-level ``uncached_input_tokens`` and
    ``cache_read_input_tokens``, plus a NESTED ``cache_creation`` object holding
    ``ephemeral_5m_input_tokens`` and ``ephemeral_1h_input_tokens``.
    """
    totals: dict[str, ModelUsage] = {}
    for bucket in buckets:
        for result in bucket.get("results") or []:
            model = result.get("model") or "unknown"
            usage = totals.setdefault(model, ModelUsage())
            usage.uncached_input_tokens += int(result.get("uncached_input_tokens") or 0)
            usage.cache_read_input_tokens += int(result.get("cache_read_input_tokens") or 0)
            cache_creation = result.get("cache_creation") or {}
            usage.cache_creation_input_tokens += int(
                cache_creation.get("ephemeral_5m_input_tokens") or 0
            ) + int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
            usage.output_tokens += int(result.get("output_tokens") or 0)
    return totals


def _rfc3339(day: date) -> str:
    """Render a date as an RFC-3339 UTC timestamp at the start of that day."""
    return f"{day.isoformat()}T00:00:00Z"


def date_range(days: int = 30, *, today: date | None = None) -> tuple[date, date]:
    """Return the (start, end) dates for a window of ``days`` ending today (default 30)."""
    end = today if today is not None else date.today()
    return end - timedelta(days=days), end


def generate_report(
    *, admin_key: str, starting_at: str, ending_at: str, fetcher: Fetcher = urllib_fetcher
) -> CostReport:
    """Fetch + parse both endpoints into a ``CostReport`` (no printing)."""
    cost_buckets = fetch_cost_report(
        admin_key=admin_key, starting_at=starting_at, ending_at=ending_at, fetcher=fetcher
    )
    usage_buckets = fetch_usage_report(
        admin_key=admin_key, starting_at=starting_at, ending_at=ending_at, fetcher=fetcher
    )
    return CostReport(
        starting_at=starting_at,
        ending_at=ending_at,
        total_cost_usd=total_cost(cost_buckets),
        currency=report_currency(cost_buckets),
        per_model=per_model_usage(usage_buckets),
    )


def format_report(report: CostReport) -> str:
    """Render the report as the human-readable text printed by ``blacksmith costs``."""
    lines = [
        f"blacksmith costs — {report.starting_at} to {report.ending_at}",
        f"total cost: ${report.total_cost_usd:.2f} {report.currency}",
        "per-model tokens:",
    ]
    if not report.per_model:
        lines.append("  (no usage in this window)")
    for model, usage in sorted(report.per_model.items()):
        lines.append(
            f"  {model}: input {usage.input_tokens} "
            f"(uncached {usage.uncached_input_tokens}, "
            f"cache-read {usage.cache_read_input_tokens}, "
            f"cache-creation {usage.cache_creation_input_tokens}), "
            f"output {usage.output_tokens}"
        )
    return "\n".join(lines)


def run_costs(
    config,
    *,
    days: int = 30,
    today: date | None = None,
    fetcher: Fetcher | None = None,
    out: Callable[[str], None] = print,
) -> CostReport:
    """Resolve the admin key, fetch the report, print it, and return it.

    Raises ``ConfigError`` (from ``config.resolve_admin_key``) naming the env var when
    the admin key is unset.
    """
    admin_key = config.resolve_admin_key()
    start, end = date_range(days, today=today)
    report = generate_report(
        admin_key=admin_key,
        starting_at=_rfc3339(start),
        ending_at=_rfc3339(end),
        fetcher=fetcher or urllib_fetcher,
    )
    out(format_report(report))
    return report
