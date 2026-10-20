"""The omp credit balance from the Anthropic Admin API cost report.

``GET https://api.anthropic.com/v1/organizations/cost_report`` returns cost-to-date, not a
balance (https://platform.claude.com/docs/en/api/beta/organization/cost_report/retrieve).
The balance is derived: the grant's total (from the operator override, which keeps supplying
the grant parameters: ``total_usd``, ``granted_at``, ``expires_at``) minus the spend since the
grant date, clamped to ``[0, total]``. The result is stored beside the override as a reading
with ``source: admin-api`` and read back by ``credits_override.load_overrides``.

The admin key comes only from ``$ANTHROPIC_ADMIN_KEY``, supplied by
``sudo-secretspec run --reason "aiuse cost report" -- aiuse refresh-credits omp``. It is never
printed, logged or stored, and nothing runs without it. Any failure prints one stderr line (no
headers, no body) and leaves the previous reading in place, where it simply ages.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from aiuse import credits_override as co
from aiuse.models import utcnow

COST_REPORT_URL = "https://api.anthropic.com/v1/organizations/cost_report"
KEY_ENV = "ANTHROPIC_ADMIN_KEY"
TIMEOUT_SECONDS = 20.0
MAX_PAGES = 20
_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
_MAX_BODY = 4 * 1024 * 1024

# fetch(url, headers, timeout) -> (status, body bytes); injected by tests.
Fetch = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


class CostReportError(Exception):
    """A failed fetch; the message never carries headers, the body or the key."""


def readings_path() -> Path:
    return co.overrides_path().with_name("credit-readings.json")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would forward x-api-key to the new origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CostReportError("redirect refused")


def _http_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:  # nosec B310  # nosemgrep
            return response.status, response.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except CostReportError:
        raise
    except Exception as exc:  # never let exception text (it can echo a header) reach output
        raise CostReportError(f"request failed ({exc.__class__.__name__})") from None


def fetch_spend_usd(api_key: str, starting_at: datetime, *, fetch: Fetch | None = None) -> float:
    """Dollars spent from ``starting_at`` to now, summed over every page of the cost report."""
    fetch = fetch or _http_get
    buckets = 0
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "accept": "application/json"}
    start = starting_at.astimezone(co.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cents = 0.0
    page: str | None = None
    for _ in range(MAX_PAGES):
        params = {"starting_at": start, "bucket_width": "1d", "limit": "31"}
        if page:
            params["page"] = page
        status, body = fetch(f"{COST_REPORT_URL}?{urllib.parse.urlencode(params)}", headers, TIMEOUT_SECONDS)
        if status != 200:
            raise CostReportError(f"cost_report returned HTTP {status}")
        try:
            doc = json.loads(body.decode("utf-8"))
            for bucket in doc["data"]:
                buckets += 1
                for result in bucket["results"]:
                    amount = float(result["amount"])
                    if not math.isfinite(amount) or amount < 0:
                        raise ValueError("bad amount")
                    cents += amount
            page = doc.get("next_page") if doc.get("has_more") else None
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise CostReportError("cost_report response was not the documented shape") from None
        if not page:
            if buckets == 0:
                raise CostReportError("cost_report returned no buckets since the grant date")
            return cents / 100.0
    raise CostReportError(f"cost_report has more than {MAX_PAGES} pages")


def compute_remaining(total: float, spent: float) -> float:
    return max(0.0, min(total, total - spent))


def refresh(
    *,
    env: Mapping[str, str] | None = None,
    fetch: Fetch | None = None,
    now: datetime | None = None,
) -> tuple[int, str]:
    """One refresh. Returns ``(exit code, one status line)``: 0 ok, 1 failed, 2 not run."""
    fetch = fetch or _http_get
    key = ((env if env is not None else os.environ).get(KEY_ENV) or "").strip()
    if key and not _KEY_RE.fullmatch(key):
        return 2, f"{KEY_ENV} has characters outside [A-Za-z0-9_-]; not used (value not shown)"
    if not key:
        return (
            2,
            f"{KEY_ENV} is not set; run under: sudo-secretspec run --reason 'aiuse cost report' -- aiuse refresh-credits omp",
        )
    now = now or utcnow()
    grant = co.load_grant_params()
    if grant is None:
        return (
            2,
            "no grant parameters: run `aiuse set-credits omp --remaining N --total N --granted DATE --expires DATE` once",
        )
    total, granted_at, expires_at = grant
    try:
        spent = fetch_spend_usd(key, granted_at, fetch=fetch)
    except CostReportError as exc:
        return 1, f"aiuse: credits refresh failed, keeping the previous reading: {exc}"
    except Exception as exc:  # noqa: BLE001 - only the class name may reach output
        return 1, f"aiuse: credits refresh failed, keeping the previous reading ({exc.__class__.__name__})"
    remaining = compute_remaining(total, spent)
    # Spend since the grant only grows, so for the same grant a reading can only fall: cap it at
    # the operator's figure and at the previous reading (an empty or short report must not refill it).
    capped_by = None
    for label, ref in (
        ("operator override", co.load_file_entry(co.overrides_path(), co.OVERRIDE_SOURCE)),
        ("previous reading", co.load_file_entry(readings_path(), "admin-api")),
    ):
        if ref is None or abs(float(ref["total_usd"]) - total) > 0.005:
            continue
        granted = co.parse_dt(ref.get("granted_at"))
        if granted is None or granted != granted_at:
            continue
        if remaining > float(ref["remaining_usd"]) + 0.005:
            remaining, capped_by = float(ref["remaining_usd"]), label
    entry: dict[str, Any] = {
        "remaining_usd": round(remaining, 6),
        "total_usd": total,
        "granted_at": granted_at.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "note": f"admin api cost_report, a lower bound (counts all org spend since {granted_at.date().isoformat()}): ${spent:.2f}"
        + (f"; reading rose, capped at the {capped_by}'s figure" if capped_by else ""),
        "observed_at": now.isoformat(),
        "source": "admin-api",
    }
    path = readings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({co.OVERRIDE_PROVIDER: entry}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    suffix = f"; rose above the {capped_by}, capped" if capped_by else ""
    return (
        0,
        f"refreshed: omp ${remaining:.2f} of ${total:.2f} left (${spent:.2f} spent since {granted_at.date()}){suffix}",
    )


def run_refresh_credits(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="aiuse refresh-credits",
        description="Read the omp credit balance from the Anthropic Admin API cost report (needs ANTHROPIC_ADMIN_KEY).",
    )
    parser.add_argument("provider", choices=[co.OVERRIDE_PROVIDER])
    parser.parse_args(argv)
    code, line = refresh()
    print(line, file=sys.stdout if code == 0 else sys.stderr)
    return code
