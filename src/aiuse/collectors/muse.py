"""Collect Muse billing via Meta Model API (Bearer) and dev.meta.ai cookie (GraphQL).

Muse Code bills pay-as-you-go through https://dev.meta.ai / https://api.meta.ai/v1 .
There are two live transports with mutual failover:

  1. Bearer (AIUSE_MUSE_API_KEY / META_API_KEY / secretspec / ~/.config/muse/auth.json
     from `muse login`) → https://api.meta.ai/v1/*
  2. Cookie (AIUSE_MUSE_COOKIE / secretspec MUSE_COOKIE from `aiuse credential refresh muse --from chrome`)
     → GET https://dev.meta.ai/usage (scrape LSD + fb_dtsg + team_id) → POST https://dev.meta.ai/api/graphql/
       doc_id 9128374650192834 (MuseDevBillingBalanceQuery) → billing_info {balance, credit_limit, remaining_budget}

If one transport is absent or fails, the other is tried. Absent both → [] . 401/403 with a
credential present surfaces as AccountUsage(error=…) only after both transports fail.

As of 2026-08, api.meta.ai exposes /models and /status for the LLM| key but no billing
path (all candidates 404). When the key validates and cookie balance is unavailable,
we still emit a visible muse row so login is not silent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, UsageCredits, parse_dt
from aiuse.secretspec import resolve_manifest_path

from .base import CollectorError

_API_BASE = "https://api.meta.ai/v1"
_CANDIDATE_PATHS: tuple[str, ...] = (
    "/usage",
    "/billing/usage",
    "/me/usage",
    "/credits",
    "/billing",
)
_KEY_ENV_PRIMARY = "AIUSE_MUSE_API_KEY"
_KEY_ENV_FALLBACK = "META_API_KEY"
_KEY_SECRET_PRIMARY = "MUSE_API_KEY"
_KEY_SECRET_FALLBACK = "META_API_KEY"
_SECRETSPEC_TIMEOUT = 5.0
_USER_AGENT = "aiuse Muse collector"
_URL_ENV = "AIUSE_MUSE_API_URL"
_AUTH_PATH_ENV = "MUSE_AUTH_PATH"
_MODELS_URL = f"{_API_BASE}/models"

# Cookie transport
_COOKIE_ENV = "AIUSE_MUSE_COOKIE"
_COOKIE_SECRET = "MUSE_COOKIE"
_TEAM_ENV = "AIUSE_MUSE_TEAM_ID"
_DEV_USAGE_URL = "https://dev.meta.ai/usage"
_GRAPHQL_URL = "https://dev.meta.ai/api/graphql/"
_BILLING_DOC_ID = "9128374650192834"
_BILLING_FRIENDLY = "MuseDevBillingBalanceQuery"


def collect_muse(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
) -> list[AccountUsage]:
    """Return the native Muse source via Bearer or cookie, with mutual failover."""
    env = os.environ if environ is None else environ
    allow_local = environ is None
    key, account = _resolve_key_and_account(env, timeout, allow_local=allow_local)
    cookie = _resolve_cookie(env, timeout, allow_secretspec=allow_local)

    if not key and not cookie:
        return []

    errors: list[str] = []
    soft_from_key: list[AccountUsage] | None = None
    # Try Bearer first (stable, no JS scrape)
    if key:
        try:
            accounts = _collect_via_api_key(key, env, timeout, account=account)
            if accounts:
                # Soft inventory (key OK, no billing JSON) yields to cookie when present.
                if cookie and _is_soft_inventory_row(accounts[0]):
                    soft_from_key = accounts
                else:
                    return accounts
        except CollectorError as exc:
            msg = str(exc)
            errors.append(msg)
            # If cookie absent, surface the Bearer error appropriately
            if not cookie:
                if "401" in msg or "403" in msg:
                    # Surface as error row (like original behavior)
                    return [
                        AccountUsage(
                            source="muse",
                            provider="muse",
                            account=account,
                            error=msg,
                            billing_kind=BillingKind.PAYG_API,
                            notes=[msg],
                        )
                    ]
                raise

    if cookie:
        try:
            return _collect_via_cookie(cookie, env, timeout, account=account)
        except CollectorError as exc:
            msg = str(exc)
            errors.append(msg)
            if soft_from_key is not None:
                # Prefer visible key-authenticated inventory over a cookie scrape failure.
                row = soft_from_key[0]
                row.notes = [
                    *row.notes,
                    f"Cookie balance unavailable: {msg}",
                ]
                return soft_from_key
            if key:
                # Both failed
                raise CollectorError("; ".join(errors)) from exc
            # No key, only cookie failed
            if "401" in msg or "403" in msg or "team_id" in msg.lower() or "fb_dtsg" in msg.lower():
                return [
                    AccountUsage(
                        source="muse",
                        provider="muse",
                        account=account,
                        error=msg,
                        billing_kind=BillingKind.PAYG_API,
                        notes=[msg],
                    )
                ]
            raise

    if soft_from_key is not None:
        return soft_from_key

    # One transport was tried and failed with non-401 without fallback
    if errors:
        raise CollectorError(errors[-1])
    return []


def _is_soft_inventory_row(account: AccountUsage) -> bool:
    return (
        account.balance_usd is None
        and account.credits_remaining is None
        and not account.windows
        and account.usage_credits is None
        and not account.error
    )


def _collect_via_api_key(
    key: str,
    env: Mapping[str, str],
    timeout: float,
    *,
    account: str | None = None,
) -> list[AccountUsage]:
    override_url = str(env.get(_URL_ENV) or "").strip()
    if override_url:
        data = _fetch_json(override_url, key, timeout)
        return _account_from_payload(data, override_url, account=account)
    last_error: CollectorError | None = None
    saw_only_404 = True
    for path in _CANDIDATE_PATHS:
        url = _API_BASE + path
        try:
            data = _fetch_json(url, key, timeout)
        except CollectorError as exc:
            if "401" in str(exc) or "403" in str(exc):
                raise
            last_error = exc
            if "404" not in str(exc):
                saw_only_404 = False
            continue
        try:
            return _account_from_payload(data, url, account=account)
        except CollectorError as exc:
            last_error = exc
            saw_only_404 = False
            continue
    if saw_only_404 and _api_key_validates(key, timeout):
        return [_soft_inventory_account(account=account)]
    if last_error is not None:
        raise last_error
    raise CollectorError("Muse API: no candidate endpoint returned usable JSON")


def _api_key_validates(key: str, timeout: float) -> bool:
    """True when /models accepts the Bearer key (billing may still be unavailable)."""
    try:
        response = requests.get(
            _MODELS_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "x-api-version": "1.0.0",
            },
        )
    except requests.RequestException:
        return False
    return response.status_code == 200


def _soft_inventory_account(*, account: str | None) -> AccountUsage:
    return AccountUsage(
        source="muse",
        provider="muse",
        account=account,
        billing_kind=BillingKind.PAYG_API,
        notes=[
            "Muse API key accepted (/models); Meta does not expose a billing endpoint on api.meta.ai yet.",
            "For live balance: sign into https://dev.meta.ai in Chrome, then run "
            "`aiuse credential refresh muse --from chrome`.",
        ],
    )


def _collect_via_cookie(
    cookie: str,
    env: Mapping[str, str],
    timeout: float,
    *,
    account: str | None = None,
) -> list[AccountUsage]:
    # Allow explicit team_id override for headless/CI
    team_id = str(env.get(_TEAM_ENV) or "").strip()
    html: str | None = None
    lsd: str | None = None
    dtsg: str | None = None
    if not team_id or True:  # always fetch HTML to get LSD/DTSG even if team_id overridden
        html = _fetch_dev_usage_html(cookie, timeout)
        if not team_id:
            team_id = _extract_team_id(html) or ""
        lsd = _extract_lsd(html)
        dtsg = _extract_dtsg(html)
    if not team_id:
        raise CollectorError(
            "Muse cookie: team_id not found in dev.meta.ai HTML; set AIUSE_MUSE_TEAM_ID or re-run `aiuse credential refresh muse --from chrome`"
        )
    if not dtsg:
        # Some sessions render DTSG async; try LSD as fallback; if still empty, fail with hint
        dtsg = lsd or ""
    if not dtsg:
        raise CollectorError(
            "Muse cookie: fb_dtsg not found in dev.meta.ai HTML; sign in to dev.meta.ai in Chrome and re-run `aiuse credential refresh muse --from chrome`"
        )
    data = _post_billing_graphql(cookie, lsd or "", dtsg, team_id, timeout)
    accounts = _accounts_from_billing_graphql(data, _GRAPHQL_URL)
    if account:
        for row in accounts:
            if not row.account:
                row.account = account
    return accounts


def _fetch_dev_usage_html(cookie: str, timeout: float) -> str:
    try:
        resp = requests.get(
            _DEV_USAGE_URL,
            timeout=timeout,
            headers={
                "Cookie": cookie,
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code in (401, 403):
            raise CollectorError(
                f"Muse cookie rejected by dev.meta.ai (HTTP {resp.status_code}); re-run `aiuse credential refresh muse --from chrome`"
            )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        raise CollectorError(f"Muse cookie: failed to fetch dev.meta.ai/usage: {exc.__class__.__name__}") from exc


def _post_billing_graphql(cookie: str, lsd: str, dtsg: str, team_id: str, timeout: float) -> Any:
    headers = {
        "Cookie": cookie,
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Origin": "https://dev.meta.ai",
        "Referer": f"https://dev.meta.ai/usage/?team_id={team_id}",
        "X-FB-Friendly-Name": _BILLING_FRIENDLY,
    }
    if lsd:
        headers["X-FB-LSD"] = lsd
    # X-ASBD-ID is static for this app (359341 seen in trace); include if available
    headers["X-ASBD-ID"] = "359341"
    data = {
        "fb_dtsg": dtsg,
        "doc_id": _BILLING_DOC_ID,
        "variables": json.dumps({"team_id": team_id}),
    }
    try:
        resp = requests.post(_GRAPHQL_URL, headers=headers, data=data, timeout=timeout)
        if resp.status_code in (401, 403):
            raise CollectorError(
                f"Muse cookie rejected by dev.meta.ai GraphQL (HTTP {resp.status_code}); re-run `aiuse credential refresh muse --from chrome`"
            )
        resp.raise_for_status()
        # GraphQL returns JSON even on logical errors
        try:
            payload = resp.json()
        except ValueError as exc:
            # HTML login shell means cookie expired
            if "<!DOCTYPE html" in resp.text:
                raise CollectorError(
                    "Muse cookie: dev.meta.ai returned HTML (session expired); re-run `aiuse credential refresh muse --from chrome`"
                ) from exc
            raise CollectorError("Muse cookie: dev.meta.ai GraphQL returned invalid JSON") from exc
        if isinstance(payload, dict) and payload.get("errors"):
            raise CollectorError(
                f"Muse cookie: dev.meta.ai GraphQL error: {payload['errors'][0].get('message') or payload['errors']}"
            )
        return payload
    except requests.RequestException as exc:
        raise CollectorError(f"Muse cookie: dev.meta.ai GraphQL request failed: {exc.__class__.__name__}") from exc


def _accounts_from_billing_graphql(data: Any, url: str) -> list[AccountUsage]:
    if not isinstance(data, dict):
        raise CollectorError(f"Muse cookie: unexpected GraphQL response at {url}")
    # Expected: data.team.billing_info {balance, credit_limit, remaining_budget} each {amount}
    team = data.get("data", {}).get("team") if isinstance(data.get("data"), dict) else None
    if not isinstance(team, dict):
        # Also try top-level team
        team = data.get("team") if isinstance(data.get("team"), dict) else None
    if not isinstance(team, dict):
        raise CollectorError(f"Muse cookie: GraphQL response missing data.team at {url}")
    billing = team.get("billing_info")
    if not isinstance(billing, dict):
        # Try alternative keys
        for k in ("billingInfo", "billing", "balance_info"):
            if isinstance(team.get(k), dict):
                billing = team[k]
                break
    if not isinstance(billing, dict):
        raise CollectorError(f"Muse cookie: GraphQL response missing billing_info at {url}")

    def amt(obj: Any) -> float | None:
        if isinstance(obj, dict):
            v = obj.get("amount")
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    return None
        if isinstance(obj, (int, float)):
            return float(obj)
        if isinstance(obj, str):
            try:
                return float(obj)
            except ValueError:
                return None
        return None

    balance = amt(billing.get("balance"))
    credit_limit = amt(billing.get("credit_limit")) or amt(billing.get("creditLimit")) or amt(billing.get("limit"))
    remaining = (
        amt(billing.get("remaining_budget")) or amt(billing.get("remainingBudget")) or amt(billing.get("remaining"))
    )
    # Fallbacks for amount_with_offset style nested value
    if (
        balance is None
        and isinstance(billing.get("balance"), dict)
        and isinstance(billing["balance"].get("value"), dict)
    ):
        balance = amt(billing["balance"]["value"].get("amount_with_offset"))
    # If only credit_limit + remaining, derive balance as remaining
    if remaining is None and balance is not None and credit_limit is not None:
        remaining = balance  # some APIs use balance as remaining
    if remaining is None and credit_limit is not None and balance is not None:
        remaining = max(0.0, credit_limit - balance)  # if balance is spend
    # Prefer remaining_budget as balance_usd
    balance_usd = remaining if remaining is not None else balance
    if balance_usd is None and credit_limit is not None:
        balance_usd = credit_limit

    if balance_usd is None and credit_limit is None and remaining is None:
        raise CollectorError(f"Muse cookie: billing_info had no recognizable amount at {url}: {billing}")

    notes = [
        "Live data fetched directly from Muse (dev.meta.ai GraphQL).",
        f"Endpoint: {url} doc_id {_BILLING_DOC_ID}",
    ]
    if credit_limit is not None and remaining is not None:
        used = max(0.0, credit_limit - remaining)
        notes.append(f"Muse spend: ${used:.2f} of ${credit_limit:.2f} (remaining ${remaining:.2f}).")
        credits = UsageCredits(
            used=used,
            limit=credit_limit,
            remaining=remaining,
            currency="USD",
            used_percent=(used / credit_limit * 100.0 if credit_limit else None),
        )
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                billing_kind=BillingKind.PAYG_API,
                balance_usd=remaining,
                usage_credits=credits,
                notes=notes,
                raw=data,
            )
        ]
    if balance_usd is not None:
        notes.append(f"Muse balance: ${balance_usd:.2f} remaining.")
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                billing_kind=BillingKind.PREPAID_BALANCE if credit_limit is None else BillingKind.PAYG_API,
                balance_usd=float(balance_usd),
                notes=notes,
                raw=data,
            )
        ]
    raise CollectorError(f"Muse cookie: could not map billing_info to balance at {url}")


def _extract_lsd(html: str) -> str | None:
    m = re.search(r'"LSD",\[[^\]]*\],\{"token":"([^"]+)"\}', html)
    if m:
        return m.group(1)
    m = re.search(r'"LSD"\s*:\s*\{"token"\s*:\s*"([^"]+)"', html)
    if m:
        return m.group(1)
    return None


def _extract_dtsg(html: str) -> str | None:
    # Preferred: DTSGInitialData or DTSGInitData
    for pat in [
        r'"DTSGInitialData",\[[^\]]*\],\{"token":"([^"]+)"',
        r'"DTSGInitData",\[[^\]]*\],\{"token":"([^"]+)"',
        r'"DTSG",\[[^\]]*\],\{"token":"([^"]+)"',
        r'"async_get_token"\s*:\s*"([^"]+)"',
        r'"fb_dtsg"\s*:\s*"([^"]+)"',
        r'fb_dtsg["\']?\s*:\s*["\']([^"\']+)["\']',
    ]:
        m = re.search(pat, html)
        if m and m.group(1):
            return m.group(1)
    # Fallback: NATh token anywhere (76-char)
    m = re.search(r"NATh[A-Za-z0-9_\-:]+", html)
    if m:
        return m.group(0)
    return None


def _extract_team_id(html: str) -> str | None:
    for pat in [
        r'active_team_id["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        r'"team_id"\s*:\s*"([^"]+)"',
        r"'team_id'\s*:\s*'([^']+)'",
        r'team_id=([^&"\'\s]+)',
        r'window\.__Config[^;]*team_id[^"\']*["\']([^"\']+)["\']',
    ]:
        m = re.search(pat, html)
        if m and m.group(1) and len(m.group(1)) > 2:
            return m.group(1)
    return None


# --- existing Bearer helpers ---


def _fetch_json(url: str, key: str, timeout: float) -> object:
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "x-api-version": "1.0.0",
            },
        )
        if response.status_code in (401, 403):
            raise CollectorError(
                f"Muse API rejected the key (HTTP {response.status_code}) at {url}. "
                "Check META_API_KEY / AIUSE_MUSE_API_KEY or run `muse login` / `muse auth set`."
            )
        if response.status_code == 404:
            raise CollectorError(f"Muse API returned HTTP 404 at {url}")
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise CollectorError(f"Muse API returned invalid JSON at {url}") from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"Muse API returned HTTP {status} at {url}") from exc
    except requests.RequestException as exc:
        raise CollectorError(f"Muse API request failed at {url}: {exc.__class__.__name__}") from exc


def _account_from_payload(data: object, url: str, *, account: str | None = None) -> list[AccountUsage]:
    if not isinstance(data, dict):
        raise CollectorError(f"Muse API response is not an object at {url}")

    # ClinePass-like limits array (subscription windows) — handle first so a
    # prepaid fallback does not swallow a real window pool.
    windows = _windows_from_payload(data)
    if windows:
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                account=account,
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=windows,
                notes=[
                    "Live data fetched directly from Muse (Meta Model API).",
                    f"Endpoint: {url}",
                ],
                raw=data if isinstance(data, dict) else {},
            )
        ]

    # OpenRouter-like prepaid balance
    prepaid = _balance_from_payload(data)
    if prepaid is not None:
        balance, total, used, raw = prepaid
        notes = [
            "Live data fetched directly from Muse (Meta Model API).",
            f"Endpoint: {url}",
        ]
        if total is not None and used is not None:
            notes.append(f"Muse credits: ${total:.2f} funded, ${used:.2f} spent, ${balance:.2f} remaining.")
        elif balance is not None:
            notes.append(f"Muse balance: ${balance:.2f} remaining.")
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                account=account,
                billing_kind=BillingKind.PREPAID_BALANCE
                if windows == [] and balance is not None
                else BillingKind.PAYG_API,
                balance_usd=balance,
                notes=notes,
                raw=data,
            )
        ]

    # Generic PAYG spend/limit (e.g. monthly spend vs $ cap)
    credits = _usage_credits_from_payload(data)
    if credits is not None:
        notes = [
            "Live data fetched directly from Muse (Meta Model API).",
            f"Endpoint: {url}",
        ]
        if credits.used is not None and credits.limit is not None:
            notes.append(
                f"Muse spend: ${credits.used:.2f} of ${credits.limit:.2f} (remaining ${credits.remaining:.2f})."
            )
        # Surface as PAYG with usage_credits (like Claude spend)
        balance = credits.remaining
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                account=account,
                billing_kind=BillingKind.PAYG_API,
                balance_usd=balance,
                usage_credits=credits,
                notes=notes,
                raw=data,
            )
        ]

    raise CollectorError(f"Muse API response at {url} had no recognizable balance or limits field")


def _balance_from_payload(data: dict) -> tuple[float, float | None, float | None, dict] | None:
    """Try OpenRouter-shaped and generic balance shapes. Returns (balance, total, used, raw) or None."""
    # OpenRouter: {"data":{"total_credits":100,"total_usage":42}}
    for container in (data.get("data"), data):
        if not isinstance(container, dict):
            continue
        total = container.get("total_credits")
        used = container.get("total_usage")
        if isinstance(total, (int, float)) and isinstance(used, (int, float)):
            balance = max(0.0, float(total) - float(used))
            return balance, float(total), float(used), container
        # Generic: {"balance":..} / {"credits":..} / {"remaining":..}
        for bal_key in ("balance", "remaining", "available", "credits_remaining"):
            bal = container.get(bal_key)
            if isinstance(bal, (int, float)):
                tot = container.get("total_credits") or container.get("limit") or container.get("total")
                tot_f = float(tot) if isinstance(tot, (int, float)) else None
                used_f = None
                if tot_f is not None:
                    used_f = tot_f - float(bal)
                return float(bal), tot_f, used_f, container
    return None


def _usage_credits_from_payload(data: dict) -> UsageCredits | None:
    for container in (data.get("data"), data):
        if not isinstance(container, dict):
            continue
        # Look for spend/limit shapes
        used = None
        limit = None
        for u_key in ("spend", "used", "total_usage", "current_spend", "month_spend", "spend_usd"):
            if isinstance(container.get(u_key), (int, float)):
                used = float(container[u_key])
                break
        for l_key in ("limit", "monthly_limit", "quota", "budget", "spend_limit", "cap"):
            if isinstance(container.get(l_key), (int, float)):
                limit = float(container[l_key])
                break
        if used is not None or limit is not None:
            remaining = None
            if used is not None and limit is not None:
                remaining = max(0.0, limit - used)
            elif isinstance(container.get("remaining"), (int, float)):
                remaining = float(container["remaining"])
            # Only return if at least one of used/limit/remaining is present and numeric
            if used is not None or limit is not None or remaining is not None:
                resets = None
                for r_key in ("resetsAt", "resets_at", "resetAt", "reset_at", "period_end", "billing_period_end"):
                    if isinstance(container.get(r_key), str):
                        resets = parse_dt(container[r_key])
                        if resets is not None:
                            break
                pct = None
                if used is not None and limit not in (None, 0):
                    pct = (used / limit) * 100.0
                return UsageCredits(
                    used=used,
                    limit=limit,
                    remaining=remaining,
                    currency=str(container.get("currency") or "USD"),
                    used_percent=pct,
                    resets_at=resets,
                )
    return None


_WINDOW_KEYS: dict[str, tuple[str, int]] = {
    "five_hour": ("Muse 5-hour", 300),
    "5h": ("Muse 5-hour", 300),
    "weekly": ("Muse weekly", 10080),
    "monthly": ("Muse monthly", 43200),
    "daily": ("Muse daily", 1440),
}


def _windows_from_payload(data: dict) -> list[QuotaWindow]:
    candidates: list[object] = []
    if isinstance(data.get("limits"), list):
        candidates = data["limits"]
    elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("limits"), list):
        candidates = data["data"]["limits"]
    elif isinstance(data.get("windows"), list):
        candidates = data["windows"]
    else:
        return []

    windows: list[QuotaWindow] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        # ClinePass shape: percentUsed + type + resetsAt
        percent = item.get("percentUsed")
        if percent is None:
            percent = item.get("percent_used")
        if percent is None:
            percent = item.get("usedPercent")
        if percent is None:
            # Try used/remaining pair
            used = item.get("used")
            remain = item.get("remaining")
            lim = item.get("limit")
            if isinstance(remain, (int, float)) and isinstance(lim, (int, float)) and lim > 0:
                percent = (1.0 - float(remain) / float(lim)) * 100.0
            elif isinstance(used, (int, float)) and isinstance(lim, (int, float)) and lim > 0:
                percent = (float(used) / float(lim)) * 100.0
        if percent is None:
            continue
        try:
            used_pct = float(percent)
        except (TypeError, ValueError):
            continue
        label_key = str(item.get("type") or item.get("kind") or item.get("window") or "unknown").strip().lower()
        label, minutes = _WINDOW_KEYS.get(label_key, (f"Muse {label_key}", None))
        # Allow per-item override
        if isinstance(item.get("window_minutes"), (int, float)):
            minutes = int(item["window_minutes"])
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used_pct,
                remaining_percent=max(0.0, 100.0 - used_pct),
                resets_at=parse_dt(
                    item.get("resetsAt") or item.get("resets_at") or item.get("resetAt") or item.get("reset_at")
                ),
                window_minutes=minutes,
                raw=item,
            )
        )
    return windows


def _resolve_key_and_account(
    env: Mapping[str, str],
    timeout: float,
    *,
    allow_local: bool = True,
) -> tuple[str | None, str | None]:
    """Return (api_key, account_email) without exposing the key.

    Precedence: AIUSE_MUSE_API_KEY → META_API_KEY → SecretSpec → ~/.config/muse/auth.json
    (from `muse login` / `muse auth set`). Local file/SecretSpec reads are skipped when
    ``allow_local`` is False (tests that pass an explicit environ).
    """
    explicit = str(env.get(_KEY_ENV_PRIMARY) or "").strip()
    if explicit:
        return explicit, None
    fallback = str(env.get(_KEY_ENV_FALLBACK) or "").strip()
    if fallback:
        return fallback, None
    if not allow_local:
        return None, None

    from_spec = _resolve_key_via_secretspec(env, timeout)
    if from_spec:
        return from_spec, None

    return _read_muse_cli_auth(env)


def _resolve_key_via_secretspec(env: Mapping[str, str], timeout: float) -> str | None:
    executable = shutil.which("secretspec")
    if executable is None:
        # Also try sudo-secretspec (used by clinepass on some hosts)
        executable = shutil.which("sudo-secretspec")
        if executable is None:
            return None
        return _resolve_via_sudo_secretspec(executable, timeout)
    manifest = str(resolve_manifest_path(env))
    for secret_name in (_KEY_SECRET_PRIMARY, _KEY_SECRET_FALLBACK):
        try:
            result = subprocess.run(
                [
                    executable,
                    "get",
                    "--file",
                    manifest,
                    "--reason",
                    "aiuse Muse balance collection",
                    secret_name,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=min(max(timeout, 0.1), _SECRETSPEC_TIMEOUT),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            key = result.stdout.strip()
            if key:
                return key
    return None


def _muse_cli_auth_path(env: Mapping[str, str]) -> Path:
    override = str(env.get(_AUTH_PATH_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    xdg = str(env.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "muse" / "auth.json"
    return Path.home() / ".config" / "muse" / "auth.json"


def _read_muse_cli_auth(env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Load providers.meta.api_key (+ email) from the Muse CLI auth file."""
    path = _muse_cli_auth_path(env)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None, None
    meta = providers.get("meta")
    if not isinstance(meta, dict):
        return None, None
    key = str(meta.get("api_key") or "").strip()
    email = str(meta.get("user_email") or "").strip() or None
    return (key or None), email


def _resolve_via_sudo_secretspec(executable: str, timeout: float) -> str | None:
    for secret_name in (_KEY_SECRET_PRIMARY, _KEY_SECRET_FALLBACK):
        try:
            result = subprocess.run(
                [executable, "get", secret_name, "--reason", "aiuse live quota collection"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=min(max(timeout, 0.1), _SECRETSPEC_TIMEOUT),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            key = result.stdout.strip()
            if key:
                return key
    return None


def _resolve_cookie(env: Mapping[str, str], timeout: float, *, allow_secretspec: bool = True) -> str | None:
    explicit = str(env.get(_COOKIE_ENV) or "").strip()
    if explicit:
        return explicit
    if not allow_secretspec:
        return None
    executable = shutil.which("secretspec")
    if executable is None:
        executable = shutil.which("sudo-secretspec")
        if executable is None:
            return None
        # sudo-secretspec path
        try:
            result = subprocess.run(
                [executable, "get", _COOKIE_SECRET, "--reason", "aiuse Muse cookie collection"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=min(max(timeout, 0.1), _SECRETSPEC_TIMEOUT),
                check=False,
            )
            if result.returncode == 0:
                c = result.stdout.strip()
                return c or None
        except (OSError, subprocess.SubprocessError):
            return None
        return None
    manifest = str(resolve_manifest_path(env))
    try:
        result = subprocess.run(
            [
                executable,
                "get",
                "--file",
                manifest,
                "--reason",
                "aiuse Muse cookie collection",
                _COOKIE_SECRET,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=min(max(timeout, 0.1), _SECRETSPEC_TIMEOUT),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    cookie = result.stdout.strip()
    return cookie or None
