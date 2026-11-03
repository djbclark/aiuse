"""Pi's independent OpenAI API grant accounting (never Codex subscription quota).

The organization Costs endpoint reports spend, not a prepaid balance. A verified
operator grant and console reading are required. Daily buckets overlapping the
console reading are charged in full, conservatively, rather than inventing an
intra-day allocation. Costs can lag; this remains a grant-derived estimate.

Reference: https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs
Keys arrive only through managed environment injection; no response body or key
is included in error messages. Network transport refuses credential redirects.
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.parse
from datetime import datetime
from typing import Any

from aiuse import credits_override as co
from aiuse.anthropic_cost import CostReportError, Fetch, _http_get
from aiuse.models import utcnow

COST_REPORT_URL = "https://api.openai.com/v1/organization/costs"
KEY_ENV = "OPENAI_ADMIN_KEY"
MAX_PAGES = 20


def fetch_spend_usd(
    api_key: str,
    starting_at: datetime,
    *,
    ending_at: datetime,
    after: datetime,
    fetch: Fetch | None = None,
) -> tuple[float, float]:
    """Return total USD cost and cost in buckets ending after the console reading."""
    fetch = fetch or _http_get
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    params = {
        "start_time": str(int(starting_at.timestamp())),
        "end_time": str(int(ending_at.timestamp())),
        "bucket_width": "1d",
        "limit": "180",
    }
    total = post_console = 0.0
    pages: set[str] = set()
    buckets: set[tuple[int, int]] = set()
    for _ in range(MAX_PAGES):
        try:
            status, body = fetch(COST_REPORT_URL + "?" + urllib.parse.urlencode(params), headers, 20.0)
        except CostReportError:
            raise
        except Exception as exc:
            raise CostReportError(f"request failed ({exc.__class__.__name__})") from None
        if status != 200:
            raise CostReportError(f"HTTP {status}")
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            raise CostReportError("invalid JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise CostReportError("invalid cost response")
        for bucket in payload["data"]:
            if not isinstance(bucket, dict) or not isinstance(bucket.get("results"), list):
                raise CostReportError("invalid cost bucket")
            start, end = bucket.get("start_time"), bucket.get("end_time")
            if type(start) is not int or type(end) is not int or start >= end:
                raise CostReportError("invalid bucket timestamps")
            if (start, end) in buckets:
                raise CostReportError("duplicate cost bucket")
            buckets.add((start, end))
            for result in bucket["results"]:
                amount = result.get("amount") if isinstance(result, dict) else None
                value = amount.get("value") if isinstance(amount, dict) else None
                if (
                    not isinstance(amount, dict)
                    or amount.get("currency") != "usd"
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                ):
                    raise CostReportError("missing or non-USD cost")
                try:
                    cost = float(value)
                except (ValueError, OverflowError):
                    raise CostReportError("invalid cost") from None
                if not math.isfinite(cost) or cost < 0:
                    raise CostReportError("invalid or negative cost; manual reconciliation required")
                total += cost
                if end > after.timestamp():
                    post_console += cost
        if type(payload.get("has_more")) is not bool:
            raise CostReportError("missing pagination status")
        if not payload["has_more"]:
            if not buckets or not math.isfinite(total):
                raise CostReportError("empty or invalid cost report")
            return total, post_console
        page = payload.get("next_page")
        if not isinstance(page, str) or not page or page in pages:
            raise CostReportError("invalid or repeated pagination cursor")
        pages.add(page)
        params["page"] = page
    raise CostReportError("cost report pagination limit reached")


def refresh(*, now: datetime | None = None, fetch: Fetch | None = None) -> dict[str, Any]:
    """Read costs only with an admin key and operator-verified grant parameters."""
    now = now or utcnow()
    key = os.environ.get(KEY_ENV)
    if not key:
        raise CostReportError(f"missing {KEY_ENV}; use managed credential injection")
    grant = co.load_grant_params("pi")
    console = co.load_file_entry(co.overrides_path(), co.OVERRIDE_SOURCE, "pi")
    if grant is None or console is None:
        raise CostReportError("no verified Pi grant; record total, granted-at and console balance first")
    total, granted, expires = grant
    observed = co.parse_dt(console.get("observed_at"))
    if granted >= now or observed is None or observed > now or (expires is not None and expires <= now):
        raise CostReportError("grant or console timestamps are not usable")
    spent, post_console = fetch_spend_usd(key, granted, ending_at=now, after=observed, fetch=fetch)
    remaining = max(0.0, min(total - spent, float(console["remaining_usd"]) - post_console))
    entry = {
        "total_usd": total,
        "remaining_usd": remaining,
        "granted_at": granted.isoformat(),
        "observed_at": now.isoformat(),
        "expires_at": expires.isoformat() if expires else None,
        "source": "admin-api",
        "note": "OpenAI organization costs; conservative grant-derived estimate, not a live cash balance; costs may lag",
    }
    co.save_credit_entry(co.readings_file(), "pi", entry, "admin-api")
    return entry


def run_refresh_credits() -> int:
    try:
        entry = refresh()
    except (CostReportError, OSError) as exc:
        # Only our controlled network/validation messages, never arbitrary exception text.
        message = str(exc) if isinstance(exc, CostReportError) else exc.__class__.__name__
        print(f"aiuse: OpenAI cost refresh unavailable: {message}", file=sys.stderr)
        return 1
    print(f"pi: ${entry['remaining_usd']:.2f} of ${entry['total_usd']:.2f} grant-derived estimate")
    return 0
