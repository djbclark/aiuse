"""Collect Muse billing via Meta Model API (Bearer) and dev.meta.ai cookie (GraphQL).

Muse Code bills pay-as-you-go through https://dev.meta.ai / https://api.meta.ai/v1 .
There are two live transports with mutual failover:

  1. Bearer (AIUSE_MUSE_API_KEY / META_API_KEY / secretspec / ~/.config/muse/auth.json
     from `muse login`) → https://api.meta.ai/v1/*
  2. Cookie (AIUSE_MUSE_COOKIE / secretspec MUSE_COOKIE from `aiuse credential refresh muse --from chrome`)
     → GET https://dev.meta.ai/api/auth/me + /api/portal/teams/{id}/billing-banner and /usage.
       The pre-2026-10 Relay page (LSD + fb_dtsg on /usage) is only a fallback when the portal
       routes are absent. https://dev.meta.ai/usage now redirects to the public marketing page.

If one transport is absent or fails, the other is tried. Absent both → [] . 401/403 with a
credential present surfaces as AccountUsage(error=…) only after both transports fail.

As of 2026-08, api.meta.ai exposes /models and /status for the LLM| key but no billing
path (all candidates 404). Cookie path uses the portal billing banner plus
month-to-date USAGE_BILLABLE_COST: Muse's UI "balance" is MTD PAYG spend (counts up),
not prepaid remaining.

A monthly Muse Code plan does not publish a monthly used-percent. The plan meter is
``subs_usage`` on ``POST https://api.meta.ai/muse-code/key`` (Muse CLI keychain access
token): a rolling window (``window_duration_mins``, 300 for the 5-hour clock) and a
weekly window. No active subscription omits ``subs_usage``; the row stays spend-only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

import requests

from aiuse.keychain import MISSING, PROMPT, KeychainResult, read_generic_password
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
_DEV_HOME_URL = "https://dev.meta.ai/"
_DEV_USAGE_URL = "https://dev.meta.ai/usage"
_GRAPHQL_URL = "https://dev.meta.ai/api/graphql/"
_PORTAL_ORIGIN = "https://dev.meta.ai"
_SIGN_IN_URL = "https://dev.meta.ai/api/auth/login"
# Shown when Chrome still holds a dead llm_sess. /usage is a marketing redirect and
# no longer embeds fb_dtsg; muse.ai is the separate Muse chat app.
_SESSION_REJECTED = (
    "Muse cookie: dev.meta.ai rejected this Chrome session. "
    f"Open {_SIGN_IN_URL} in this Chrome profile and wait until the Model API "
    "dashboard loads, then re-run `aiuse credential refresh muse --from chrome`. "
    "Signing in at https://muse.ai does not create this session, and "
    "https://dev.meta.ai/usage no longer embeds fb_dtsg."
)
# Live Relay persisted query (LLMDCBillingBannerContainerQuery). Meta rotates these;
# free_money_* amounts use PECurrency DEFAULT_AMOUNT_OFFSET=100 (cents for USD).
_BILLING_DOC_ID = "28281552291474266"
_BILLING_FRIENDLY = "LLMDCBillingBannerContainerQuery"
# Home usage summary (LAST_90_DAYS spend_cost_metrics) — Muse UI "balance" is spend that counts up.
_SPEND_DOC_ID = "28692949813640152"
_SPEND_FRIENDLY = "LLMDCHomeContentUsageSummaryQuery"
_PE_AMOUNT_OFFSET = 100.0
# Muse Code plan meter. The API key cannot read it; the device-login access token can.
_MUSE_CODE_KEY_URL = "https://api.meta.ai/muse-code/key"
_KEYCHAIN_SERVICE = "ai.meta.dev.credentials"
_KEYCHAIN_ACCOUNT = "meta"
_SUBS_CACHE_TTL_S = 300.0
_subs_cache_lock = threading.Lock()
# ``payload`` None means "no cached read". A dict (possibly without subs_usage)
# is a fresh key response and is reused for five minutes.
_subs_cache: dict[str, Any] = {"at": 0.0, "payload": None, "keychain": None}
# A keychain read that timed out raised a SecurityAgent prompt; do not raise it
# again on every watch tick.
_PROMPT_BACKOFF_S = 3600.0


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
                    return _merge_subscription_windows(accounts, timeout, allow_local=allow_local)
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
            return _merge_subscription_windows(
                _collect_via_cookie(cookie, env, timeout, account=account),
                timeout,
                allow_local=allow_local,
            )
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
                return _merge_subscription_windows(soft_from_key, timeout, allow_local=allow_local)
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
        return _merge_subscription_windows(soft_from_key, timeout, allow_local=allow_local)

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
            "For live balance: sign into https://dev.meta.ai/api/auth/login in Chrome, then run "
            "`aiuse credential refresh muse --from chrome`.",
        ],
    )


class _PortalUnavailable(Exception):
    """Portal routes are missing, so the legacy HTML scrape may still work."""


def _collect_via_cookie(
    cookie: str,
    env: Mapping[str, str],
    timeout: float,
    *,
    account: str | None = None,
) -> list[AccountUsage]:
    """Read Model API billing. Portal JSON first; Relay HTML only if those routes are gone."""
    try:
        return _collect_via_portal(cookie, env, timeout, account=account)
    except _PortalUnavailable:
        return _collect_via_legacy_cookie(cookie, env, timeout, account=account)


def _collect_via_legacy_cookie(
    cookie: str,
    env: Mapping[str, str],
    timeout: float,
    *,
    account: str | None = None,
) -> list[AccountUsage]:
    # Prefer explicit team_id (env / operator URL). Bare /usage without team_id redirects to auth.
    team_id = str(env.get(_TEAM_ENV) or "").strip()
    html = _fetch_dev_session_html(cookie, timeout, team_id=team_id or None)
    if not team_id:
        team_id = _extract_team_id(html) or ""
    lsd = _extract_lsd(html)
    dtsg = _extract_dtsg(html)
    if not team_id:
        raise CollectorError(
            "Muse cookie: team_id not found in dev.meta.ai HTML; set AIUSE_MUSE_TEAM_ID "
            "(from the team_id= query param on https://dev.meta.ai/usage) or re-run "
            "`aiuse credential refresh muse --from chrome`"
        )
    if not dtsg:
        dtsg = lsd or ""
    if not dtsg:
        raise CollectorError(
            "Muse cookie: fb_dtsg not found in dev.meta.ai HTML; sign in to "
            "https://dev.meta.ai/usage in Chrome and re-run `aiuse credential refresh muse --from chrome`"
        )
    data = _post_muse_graphql(
        cookie,
        lsd or "",
        dtsg,
        team_id,
        timeout,
        doc_id=_BILLING_DOC_ID,
        friendly=_BILLING_FRIENDLY,
        variables={"team_id": team_id},
    )
    spend_usd: float | None = None
    spend_raw: Any = None
    try:
        spend_raw = _post_muse_graphql(
            cookie,
            lsd or "",
            dtsg,
            team_id,
            timeout,
            doc_id=_SPEND_DOC_ID,
            friendly=_SPEND_FRIENDLY,
            variables={
                "team_id": team_id,
                "team_id_is_null": False,
                "__relay_internal__pv__Usage_ShouldIncludeCostMetricsrelayprovider": True,
                "__relay_internal__pv__Usage_ShouldIncludeBatchMetricsrelayprovider": False,
            },
        )
        spend_usd = _mtd_total_spend_usd(spend_raw)
    except CollectorError:
        spend_usd = None
    accounts = _accounts_from_muse_cookie_payloads(
        data,
        spend_usd=spend_usd,
        spend_raw=spend_raw,
        url=_GRAPHQL_URL,
    )
    if account:
        for row in accounts:
            if not row.account:
                row.account = account
    return accounts


def _portal_headers(cookie: str) -> dict[str, str]:
    return {
        "Cookie": cookie,
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Referer": f"{_PORTAL_ORIGIN}/",
    }


def _portal_get(cookie: str, path: str, timeout: float, *, missing_ok: bool = False) -> Any | None:
    """GET a Model API portal route.

    401/403 and a login redirect are a dead Chrome session. 404 or a non-JSON
    body means this deployment has no portal (legacy Relay scrape may apply),
    unless ``missing_ok`` treats 404 as an absent optional route.
    """
    url = f"{_PORTAL_ORIGIN}{path}"
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers=_portal_headers(cookie),
        )
    except requests.RequestException as exc:
        raise CollectorError(f"Muse cookie: failed to fetch {path}: {exc.__class__.__name__}") from exc
    final = resp.url or ""
    if resp.status_code in (401, 403) or "auth.meta.com" in final:
        raise CollectorError(_SESSION_REJECTED)
    if resp.status_code == 404:
        if missing_ok:
            return None
        raise _PortalUnavailable()
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise CollectorError(f"Muse cookie: dev.meta.ai portal returned HTTP {resp.status_code} for {path}") from exc
    try:
        payload = resp.json()
    except (ValueError, AttributeError) as exc:
        if missing_ok:
            return None
        raise _PortalUnavailable() from exc
    if not isinstance(payload, (dict, list)):
        if missing_ok:
            return None
        raise _PortalUnavailable()
    return payload


def _team_id_of(team: Any) -> str | None:
    if not isinstance(team, dict):
        return None
    for key in ("id", "team_id", "teamId"):
        value = team.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _pick_team_id(teams: list[Any], wanted: str) -> str:
    ids = [team_id for team in teams if (team_id := _team_id_of(team))]
    if wanted:
        return wanted
    if not ids:
        raise CollectorError(
            "Muse cookie: Model API session has no team id. "
            f"Open {_SIGN_IN_URL} and finish signing in, or set AIUSE_MUSE_TEAM_ID."
        )
    return ids[0]


def _iana_timezone() -> str:
    try:
        parts = Path("/etc/localtime").resolve().parts
    except OSError:
        return "UTC"
    if "zoneinfo" not in parts:
        return "UTC"
    name = "/".join(parts[parts.index("zoneinfo") + 1 :])
    return name or "UTC"


def _minor_to_usd(raw: Any, *, offset: float = 100.0) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) / offset
    if isinstance(raw, str):
        try:
            return float(raw) / offset
        except ValueError:
            return None
    return None


def _free_remaining_usd(banner: Any) -> float | None:
    """Free-credit remaining from the portal billing banner, in dollars."""
    if not isinstance(banner, dict):
        return None
    kind = banner.get("kind")
    if kind == "free_untouched":
        return _minor_to_usd(banner.get("grant_minor_units"))
    if kind == "free_partial":
        return _minor_to_usd(banner.get("remaining_minor_units"))
    return None


def _usage_point_usd(point: dict[str, Any], offset: float) -> float | None:
    # Dashboard formula for USAGE_BILLABLE_COST: amount is minor units
    # (currency.offset, default 100). Missing amount falls back to value/1e8.
    if point.get("amount") is not None:
        return _minor_to_usd(point.get("amount"), offset=offset)
    if point.get("value") is not None:
        scaled = _minor_to_usd(point.get("value"), offset=1e8)
        if scaled is None:
            return None
        return scaled / offset
    return None


def _mtd_from_portal_usage(payload: Any) -> float | None:
    """Sum TOTAL billable cost for the current calendar month, in dollars."""
    if not isinstance(payload, dict):
        return None
    series = payload.get("series")
    if not isinstance(series, list):
        return None
    currency = payload.get("currency") if isinstance(payload.get("currency"), dict) else {}
    offset = currency.get("offset") if isinstance(currency, dict) else None
    if not isinstance(offset, (int, float)) or isinstance(offset, bool) or offset < 1:
        offset = 100.0
    prefix = date.today().strftime("%Y-%m")
    totals = [row for row in series if isinstance(row, dict) and row.get("type") == "TOTAL"]
    rows = totals or [row for row in series if isinstance(row, dict)]
    saw_point = False
    found = False
    mtd = 0.0
    for row in rows:
        points = row.get("data_points")
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            saw_point = True
            day = str(point.get("date") or "")
            if not day.startswith(prefix):
                continue
            amount = _usage_point_usd(point, float(offset))
            if amount is None:
                continue
            mtd += amount
            found = True
    if not saw_point:
        return None
    return mtd if found else 0.0


def _account_label_from_me(me: Any, account: str | None) -> str | None:
    if account:
        return account
    if not isinstance(me, dict):
        return None
    candidates: list[Any] = [me, me.get("user") if isinstance(me.get("user"), dict) else None]
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        for key in ("email", "user_email", "name"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _accounts_from_portal(
    banner: Any,
    spend_usd: float | None,
    *,
    team_id: str,
    usage_raw: Any,
) -> list[AccountUsage]:
    free_remaining = _free_remaining_usd(banner)
    notes = [
        "Live data fetched directly from Muse (dev.meta.ai portal).",
        f"Endpoint: {_PORTAL_ORIGIN}/api/portal/teams/{team_id}/usage",
        "Muse dashboard balance is cumulative PAYG spend (counts up from $0), "
        "not prepaid remaining (DeepSeek / oc-zen count down to $0).",
    ]
    raw: dict[str, Any] = {"team_id": team_id, "banner": banner}
    if usage_raw is not None:
        raw["usage"] = usage_raw
    if isinstance(banner, dict) and banner.get("kind"):
        notes.append(f"Muse billing banner: {banner.get('kind')}.")
    if spend_usd is not None:
        notes.append(f"Muse spend this month: ${spend_usd:.2f} (counts up).")
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                billing_kind=BillingKind.PAYG_API,
                balance_usd=None,
                usage_credits=UsageCredits(used=spend_usd, currency="USD"),
                notes=notes,
                raw=raw,
            )
        ]
    if free_remaining is not None and free_remaining > 0:
        notes.append(f"Muse free credits remaining: ${free_remaining:.2f} (counts down).")
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=free_remaining,
                notes=notes,
                raw=raw,
            )
        ]
    raise CollectorError(
        "Muse cookie: could not read MTD spend and free credits are empty; "
        "re-run `aiuse credential refresh muse --from chrome`"
    )


def _collect_via_portal(
    cookie: str,
    env: Mapping[str, str],
    timeout: float,
    *,
    account: str | None = None,
) -> list[AccountUsage]:
    me = _portal_get(cookie, "/api/auth/me", timeout)
    teams_payload = _portal_get(cookie, "/api/portal/teams", timeout)
    if isinstance(teams_payload, dict):
        teams = teams_payload.get("teams")
    elif isinstance(teams_payload, list):
        teams = teams_payload
    else:
        teams = None
    if not isinstance(teams, list) or not teams:
        raise CollectorError(
            "Muse cookie: Model API session returned no teams. "
            f"Open {_SIGN_IN_URL} until the dashboard loads, then re-run "
            "`aiuse credential refresh muse --from chrome`."
        )
    wanted = str(env.get(_TEAM_ENV) or "").strip()
    team_id = _pick_team_id(teams, wanted)
    team_path = quote(team_id, safe="")
    try:
        banner = _portal_get(
            cookie,
            f"/api/portal/teams/{team_path}/billing-banner",
            timeout,
            missing_ok=True,
        )
    except CollectorError as exc:
        if str(exc) == _SESSION_REJECTED:
            raise
        banner = None
    today = date.today()
    query = urlencode(
        {
            "metric": "USAGE_BILLABLE_COST",
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.isoformat(),
            "timezone": _iana_timezone(),
        }
    )
    usage_raw: Any = None
    spend_usd: float | None = None
    try:
        usage_raw = _portal_get(
            cookie,
            f"/api/portal/teams/{team_path}/usage?{query}",
            timeout,
            missing_ok=True,
        )
        spend_usd = _mtd_from_portal_usage(usage_raw)
    except CollectorError as exc:
        if str(exc) == _SESSION_REJECTED:
            raise
        spend_usd = None
    label = _account_label_from_me(me, account)
    accounts = _accounts_from_portal(banner, spend_usd, team_id=team_id, usage_raw=usage_raw)
    if label:
        for row in accounts:
            if not row.account:
                row.account = label
    return accounts


def _fetch_dev_session_html(cookie: str, timeout: float, *, team_id: str | None = None) -> str:
    """Load a logged-in Model API HTML shell (LSD/DTSG + optional team_id scrape)."""
    urls: list[str] = []
    if team_id:
        urls.append(f"{_DEV_USAGE_URL}/?team_id={team_id}")
        urls.append(f"{_DEV_HOME_URL}?team_id={team_id}")
    urls.extend([_DEV_HOME_URL, f"{_DEV_USAGE_URL}/"])
    last_error: CollectorError | None = None
    for url in urls:
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                headers={
                    "Cookie": cookie,
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            if resp.status_code in (401, 403):
                raise CollectorError(_SESSION_REJECTED)
            resp.raise_for_status()
            text = resp.text
            # auth.meta.com login waterfall is not usable
            if "auth.meta.com" in (resp.url or ""):
                last_error = CollectorError(_SESSION_REJECTED)
                continue
            if _extract_dtsg(text) or _extract_lsd(text):
                return text
            # Tiny Error shells without tokens are not usable
            if len(text) < 5000:
                last_error = CollectorError(_SESSION_REJECTED)
                continue
            last_error = CollectorError(_SESSION_REJECTED)
        except CollectorError as exc:
            last_error = exc
        except requests.RequestException as exc:
            last_error = CollectorError(f"Muse cookie: failed to fetch {url}: {exc.__class__.__name__}")
    if last_error is not None:
        raise last_error
    raise CollectorError("Muse cookie: failed to fetch a usable dev.meta.ai session page")


def _post_muse_graphql(
    cookie: str,
    lsd: str,
    dtsg: str,
    team_id: str,
    timeout: float,
    *,
    doc_id: str,
    friendly: str,
    variables: dict[str, Any],
) -> Any:
    headers = {
        "Cookie": cookie,
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Origin": "https://dev.meta.ai",
        "Referer": f"https://dev.meta.ai/usage/?team_id={team_id}",
        "X-FB-Friendly-Name": friendly,
        "X-ASBD-ID": "359341",
    }
    if lsd:
        headers["X-FB-LSD"] = lsd
    data = {
        "fb_dtsg": dtsg,
        "lsd": lsd,
        "doc_id": doc_id,
        "variables": json.dumps(variables),
    }
    try:
        resp = requests.post(_GRAPHQL_URL, headers=headers, data=data, timeout=timeout)
        if resp.status_code in (401, 403):
            raise CollectorError(
                f"Muse cookie rejected by dev.meta.ai GraphQL (HTTP {resp.status_code}); "
                "re-run `aiuse credential refresh muse --from chrome`"
            )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            if "<!DOCTYPE html" in resp.text or resp.text.lstrip().startswith("<"):
                raise CollectorError(
                    "Muse cookie: dev.meta.ai returned HTML (session expired); "
                    "re-run `aiuse credential refresh muse --from chrome`"
                ) from exc
            raise CollectorError("Muse cookie: dev.meta.ai GraphQL returned invalid JSON") from exc
        if isinstance(payload, dict) and payload.get("errors"):
            err0 = payload["errors"][0] if payload["errors"] else {}
            msg = err0.get("message") if isinstance(err0, dict) else payload["errors"]
            raise CollectorError(f"Muse cookie: dev.meta.ai GraphQL error: {msg}")
        return payload
    except requests.RequestException as exc:
        raise CollectorError(f"Muse cookie: dev.meta.ai GraphQL request failed: {exc.__class__.__name__}") from exc


def _pe_currency_usd(obj: Any) -> float | None:
    """Convert Meta CurrencyAmount / amount_with_offset (cents) to USD dollars."""
    if obj is None:
        return None
    raw: Any = obj
    if isinstance(obj, dict):
        raw = obj.get("amount_with_offset", obj.get("amount"))
    if isinstance(raw, (int, float)):
        return float(raw) / _PE_AMOUNT_OFFSET
    if isinstance(raw, str):
        try:
            return float(raw) / _PE_AMOUNT_OFFSET
        except ValueError:
            return None
    return None


def _mtd_total_spend_usd(payload: Any) -> float | None:
    """Sum TOTAL spend_cost_metrics for the current calendar month (USD)."""
    if not isinstance(payload, dict):
        return None
    team = payload.get("data", {}).get("team") if isinstance(payload.get("data"), dict) else None
    if not isinstance(team, dict):
        return None
    metrics = team.get("spend_cost_metrics")
    if not isinstance(metrics, list):
        return None
    total_series: dict[str, Any] | None = None
    for series in metrics:
        if isinstance(series, dict) and series.get("type") == "TOTAL":
            total_series = series
            break
    if total_series is None:
        points: list[Any] = []
        for series in metrics:
            if isinstance(series, dict):
                points.extend(series.get("categorical_data") or [])
    else:
        points = list(total_series.get("categorical_data") or [])
    if not points:
        return None
    from datetime import date

    prefix = date.today().strftime("%Y-%m")
    mtd = 0.0
    found = False
    for pt in points:
        if not isinstance(pt, dict):
            continue
        cat = str(pt.get("category") or "")
        if not cat.startswith(prefix):
            continue
        amt = _pe_currency_usd(pt.get("value"))
        if amt is None:
            continue
        mtd += amt
        found = True
    return mtd if found else 0.0


def _accounts_from_muse_cookie_payloads(
    data: Any,
    *,
    spend_usd: float | None,
    spend_raw: Any,
    url: str,
) -> list[AccountUsage]:
    """Map banner (+ optional MTD spend) into Muse row.

    Muse's dashboard "balance" is cumulative PAYG spend (counts up from $0).
    DeepSeek / oc-zen ``balance_usd`` is prepaid remaining (counts down to $0).
    Prefer spend as ``usage_credits.used`` with no ``balance_usd`` so the report
    can label it ``spent $X (counts up)`` instead of ``balance $X remaining``.
    """
    if not isinstance(data, dict):
        raise CollectorError(f"Muse cookie: unexpected GraphQL response at {url}")
    team = data.get("data", {}).get("team") if isinstance(data.get("data"), dict) else None
    if not isinstance(team, dict):
        team = data.get("team") if isinstance(data.get("team"), dict) else None
    if not isinstance(team, dict):
        raise CollectorError(f"Muse cookie: GraphQL response missing data.team at {url}")

    remaining = _pe_currency_usd(team.get("free_money_remaining_currency_amount"))
    granted = _pe_currency_usd(team.get("free_money_granted_currency_amount"))
    has_card = team.get("has_usable_payment_method") is True

    billing = team.get("billing_info")
    if not isinstance(billing, dict):
        for k in ("billingInfo", "billing", "balance_info"):
            if isinstance(team.get(k), dict):
                billing = team[k]
                break
    if isinstance(billing, dict) and "free_money_remaining_currency_amount" not in team:
        return _accounts_from_legacy_billing_info(billing, data, url)

    if remaining is None and "free_money_remaining_currency_amount" not in team and not isinstance(billing, dict):
        raise CollectorError(f"Muse cookie: GraphQL team missing free_money_remaining_currency_amount at {url}")

    free_remaining = 0.0 if remaining is None else remaining
    notes = [
        "Live data fetched directly from Muse (dev.meta.ai GraphQL).",
        f"Endpoint: {url} banner={_BILLING_DOC_ID} spend={_SPEND_DOC_ID}.",
        "Muse dashboard balance is cumulative PAYG spend (counts up from $0), "
        "not prepaid remaining (DeepSeek / oc-zen count down to $0).",
    ]
    if granted is not None:
        notes.append(f"Muse free credits granted: ${granted:.2f}.")
    if free_remaining > 0:
        notes.append(f"Muse free credits remaining: ${free_remaining:.2f}.")
    if has_card:
        notes.append("Payment method on file (pay-as-you-go).")

    raw: dict[str, Any] = {"banner": data}
    if spend_raw is not None:
        raw["spend"] = spend_raw

    if spend_usd is not None:
        notes.append(f"Muse spend this month: ${spend_usd:.2f} (counts up).")
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                billing_kind=BillingKind.PAYG_API,
                balance_usd=None,
                usage_credits=UsageCredits(used=spend_usd, currency="USD"),
                notes=notes,
                raw=raw,
            )
        ]

    if free_remaining > 0:
        notes.append(f"Muse free credits remaining: ${free_remaining:.2f} (counts down).")
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=free_remaining,
                notes=notes,
                raw=raw,
            )
        ]

    raise CollectorError(
        "Muse cookie: could not read MTD spend and free credits are empty; "
        "re-run `aiuse credential refresh muse --from chrome`"
    )


def _accounts_from_legacy_billing_info(billing: dict[str, Any], data: Any, url: str) -> list[AccountUsage]:
    def amt(obj: Any) -> float | None:
        if isinstance(obj, dict):
            v = obj.get("amount", obj.get("amount_with_offset"))
            if isinstance(v, (int, float)):
                if "amount_with_offset" in obj and "amount" not in obj:
                    return float(v) / _PE_AMOUNT_OFFSET
                return float(v)
            if isinstance(v, str):
                try:
                    n = float(v)
                except ValueError:
                    return None
                if "amount_with_offset" in obj and "amount" not in obj:
                    return n / _PE_AMOUNT_OFFSET
                return n
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
    rem = amt(billing.get("remaining_budget")) or amt(billing.get("remainingBudget")) or amt(billing.get("remaining"))
    balance_usd = rem if rem is not None else balance
    if balance_usd is None and credit_limit is not None:
        balance_usd = credit_limit
    if balance_usd is None and credit_limit is None and rem is None:
        raise CollectorError(f"Muse cookie: billing_info had no recognizable amount at {url}: {billing}")
    notes = [
        "Live data fetched directly from Muse (dev.meta.ai GraphQL).",
        f"Endpoint: {url} doc_id {_BILLING_DOC_ID}",
    ]
    if credit_limit is not None and rem is not None:
        used = max(0.0, credit_limit - rem)
        notes.append(f"Muse spend: ${used:.2f} of ${credit_limit:.2f} (remaining ${rem:.2f}).")
        credits = UsageCredits(
            used=used,
            limit=credit_limit,
            remaining=rem,
            currency="USD",
            used_percent=(used / credit_limit * 100.0 if credit_limit else None),
        )
        return [
            AccountUsage(
                source="muse",
                provider="muse",
                billing_kind=BillingKind.PAYG_API,
                balance_usd=None,
                usage_credits=credits,
                notes=notes,
                raw=data,
            )
        ]
    if balance_usd is None:
        raise CollectorError(f"Muse cookie: billing_info had no recognizable amount at {url}: {billing}")
    notes.append(f"Muse balance: ${float(balance_usd):.2f} remaining (counts down).")
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


def _accounts_from_billing_graphql(data: Any, url: str) -> list[AccountUsage]:
    """Back-compat wrapper; prefer ``_accounts_from_muse_cookie_payloads``."""
    return _accounts_from_muse_cookie_payloads(data, spend_usd=None, spend_raw=None, url=url)


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


def _epoch_to_utc(value: Any) -> datetime | None:
    """Muse sends ``resets_at`` as unix seconds (or an ISO string)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        if seconds <= 0:
            return None
        return datetime.fromtimestamp(seconds, timezone.utc)
    if isinstance(value, str):
        return parse_dt(value)
    return None


def _windows_from_subs_usage(payload: Any) -> list[QuotaWindow]:
    """Map ``subs_usage`` from ``POST /muse-code/key`` into clock windows.

    ``used_percent`` is the share consumed. The rolling window's
    ``window_duration_mins`` picks the clock (300 is the 5-hour plan window).
    The weekly object has no duration of its own.
    """
    if not isinstance(payload, dict):
        return []
    usage = payload.get("subs_usage")
    if not isinstance(usage, dict):
        return []
    windows: list[QuotaWindow] = []
    rolling = usage.get("window")
    if isinstance(rolling, dict):
        window = _quota_window_from_subs_spec(
            rolling,
            label="Muse 5-hour",
            minutes=300,
        )
        if window is not None:
            windows.append(window)
    weekly = usage.get("weekly")
    if isinstance(weekly, dict):
        window = _quota_window_from_subs_spec(
            weekly,
            label="Muse weekly",
            minutes=10080,
        )
        if window is not None:
            windows.append(window)
    return windows


def _quota_window_from_subs_spec(
    spec: dict[str, Any],
    *,
    label: str,
    minutes: int,
) -> QuotaWindow | None:
    percent = spec.get("used_percent")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    used = float(percent)
    duration = spec.get("window_duration_mins")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
        minutes = int(duration)
        if minutes == 300:
            label = "Muse 5-hour"
        elif minutes == 10080:
            label = "Muse weekly"
        else:
            label = f"Muse {minutes}m"
    resets = _epoch_to_utc(spec.get("resets_at"))
    raw = {
        "used_percent": used,
        "resets_at": spec.get("resets_at"),
        "window_duration_mins": spec.get("window_duration_mins"),
    }
    return QuotaWindow(
        label=label,
        used_percent=used,
        remaining_percent=max(0.0, 100.0 - used),
        resets_at=resets,
        window_minutes=minutes,
        raw={key: value for key, value in raw.items() if value is not None},
    )


def _plan_note_from_key_payload(payload: Any, windows: list[QuotaWindow]) -> str | None:
    if not isinstance(payload, dict):
        return None
    tier = payload.get("subs_tier_name")
    if isinstance(tier, str) and tier.strip():
        return f"Muse Code plan: {tier.strip()}."
    if payload.get("is_subs_active") is False and not windows:
        return (
            "Muse Code subscription is inactive, so Meta reports no plan usage percent. "
            "The row is pay-as-you-go spend only."
        )
    return None


def _read_muse_keychain_access_token(
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[KeychainResult, str | None]:
    """OAuth access token from the Muse CLI login. Never the API key.

    Returns the classified ``security`` result with the token, so a locked
    keychain (exit 152) or a raised prompt (timeout) is reported as such and
    never as a missing or invalid credential.
    """
    result, raw = read_generic_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT, timeout=5.0, run_fn=run_fn)
    if not result.ok or raw is None:
        return result, None
    not_login = KeychainResult(status=MISSING, returncode=0, detail="item holds no Muse login access_token")
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError:
        return not_login, None
    if not isinstance(payload, dict):
        return not_login, None
    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return not_login, None
    return result, token.strip()


def _fetch_muse_code_key(token: str, timeout: float) -> dict[str, Any] | None:
    try:
        response = requests.post(
            _MUSE_CODE_KEY_URL,
            json={},
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-api-version": "1.0.0",
                "User-Agent": _USER_AGENT,
            },
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    # The response repeats the API key. Do not keep it on the account.
    payload.pop("api_key", None)
    payload.pop("user_email", None)
    payload.pop("user_avatar_url", None)
    return payload


def _local_login_plan(timeout: float) -> tuple[list[QuotaWindow], str | None, KeychainResult | None]:
    """Plan windows, plan note and keychain problem from the local Muse CLI login.

    The key endpoint is rate-limited, and ``aiuse watch`` collects on a short
    interval, so a hit is reused for five minutes. A keychain read that raised
    a prompt is not retried for an hour. Unit tests must not read the operator
    keychain or call Meta.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return [], None, None
    now = time.monotonic()
    issue: KeychainResult | None = None
    with _subs_cache_lock:
        cached_at = float(_subs_cache.get("at") or 0.0)
        cached = _subs_cache.get("payload")
        cached_issue = _subs_cache.get("keychain")
        ttl = (
            _PROMPT_BACKOFF_S
            if isinstance(cached_issue, KeychainResult) and cached_issue.status == PROMPT
            else _SUBS_CACHE_TTL_S
        )
        if isinstance(cached, dict) and now - cached_at < ttl:
            payload = cached
            issue = cached_issue if isinstance(cached_issue, KeychainResult) else None
        else:
            payload = None
    if payload is None:
        result, token = _read_muse_keychain_access_token()
        issue = None if result.ok else result
        payload = _fetch_muse_code_key(token, timeout) if token else None
        # Cache a miss too, so a locked keychain or an inactive plan does not
        # hit the keychain or the rate-limited key endpoint on every watch tick.
        stored = payload if isinstance(payload, dict) else {}
        with _subs_cache_lock:
            _subs_cache["at"] = time.monotonic()
            _subs_cache["payload"] = stored
            _subs_cache["keychain"] = issue
        payload = stored
    windows = _windows_from_subs_usage(payload)
    return windows, _plan_note_from_key_payload(payload, windows), issue


def _keychain_issue_note(issue: KeychainResult) -> str:
    return issue.message(f"Muse plan windows not read; keychain item {_KEYCHAIN_SERVICE}")


def _merge_subscription_windows(
    accounts: list[AccountUsage],
    timeout: float,
    *,
    allow_local: bool,
) -> list[AccountUsage]:
    """Attach Muse Code plan windows when the local login reports them.

    When the keychain read fails, say why on each row (``credential_status``
    plus a note): locked, missing and prompt each need a different action.
    """
    if not accounts or not allow_local:
        return accounts
    windows, note, issue = _local_login_plan(timeout)
    if not windows and not note and issue is None:
        return accounts
    for row in accounts:
        if row.error:
            continue
        if windows:
            present = {window.label for window in row.windows}
            row.windows.extend(window for window in windows if window.label not in present)
            if row.billing_kind == BillingKind.PAYG_API:
                row.billing_kind = BillingKind.SUBSCRIPTION_WINDOW
        if note and note not in row.notes:
            row.notes.append(note)
        if issue is not None:
            issue_note = _keychain_issue_note(issue)
            if issue_note not in row.notes:
                row.notes.append(issue_note)
            row.credential_status = issue.to_dict(item=_KEYCHAIN_SERVICE)
    return accounts


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
