"""Collect OpenCode Go subscription status from the official workspace page.

CodexBar's local path sums SQLite costs against hardcoded $12/$30/$60 caps and
cannot see that a Go plan has lapsed. This collector reuses the same OpenCode
console cookie as the Zen collector and reads ``/workspace/<id>/go``.

A lapsed plan has no ``rollingUsage`` / ``weeklyUsage`` / ``monthlyUsage``
objects. After a renew those objects come back (often still with
``subscription: null``) — treat the window objects as the live allotment.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlparse

import requests

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, utcnow

from .base import CollectorError
from .opencode_zen import (
    _BASE_URL,
    _WORKSPACES_SERVER_ID,
    _fetch_server,
    _resolve_cookie,
    _workspace_id,
)

_WORKSPACE_ENV = "AIUSE_OPENCODE_ZEN_WORKSPACE_ID"
_USER_AGENT = "aiuse OpenCode Go collector"
_EXPIRED_DESCRIPTION = "subscription expired"

_SUBSCRIPTION_NULL = re.compile(r"""["']?subscription["']?\s*:\s*null\b""", re.I)
_SUBSCRIPTION_ID_NULL = re.compile(r"""["']?subscriptionID["']?\s*:\s*null\b""", re.I)
_SUBSCRIPTION_PRESENT = re.compile(
    r"""["']?subscription["']?\s*:\s*(?:\{|"[^"\n]*"|sub_[A-Za-z0-9]+)""",
    re.I,
)

_WINDOW_SPECS: tuple[tuple[str, str, int], ...] = (
    ("rollingUsage", "OpenCode Go 5-hour", 300),
    ("weeklyUsage", "OpenCode Go weekly", 10080),
    ("monthlyUsage", "OpenCode Go monthly", 43200),
)


def collect_opencode_go(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
) -> list[AccountUsage]:
    """Return native Go status, or nothing until a session cookie is supplied."""
    env = os.environ if environ is None else environ
    cookie = _resolve_cookie(env, timeout, allow_secretspec=environ is None)
    if not cookie:
        return []
    override = _workspace_id(str(env.get(_WORKSPACE_ENV) or ""))
    workspace_ids = [override] if override else _workspace_ids(_fetch_workspaces(cookie, timeout))
    if not workspace_ids:
        raise CollectorError("OpenCode Go: workspace id missing from authenticated response")

    pages: list[str] = []
    errors: list[str] = []
    for workspace in workspace_ids:
        try:
            pages.append(_fetch_go_page(workspace, cookie, timeout))
        except CollectorError as exc:
            errors.append(str(exc))

    if not pages:
        raise CollectorError(errors[0] if errors else "OpenCode Go: workspace page unavailable")

    expired = False
    for text in pages:
        account = _account_from_go_page(text)
        if account is None:
            continue
        if account.plan == "expired":
            expired = True
            continue
        return [account]
    if expired:
        return [_expired_account()]
    return []


def _fetch_workspaces(cookie: str, timeout: float) -> str:
    try:
        return _fetch_server(_WORKSPACES_SERVER_ID, None, cookie, timeout)
    except CollectorError as exc:
        raise CollectorError(str(exc).replace("Zen billing", "Go workspace lookup")) from exc


def _workspace_ids(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\bw(?:rk|ork)_[A-Za-z0-9]+\b", text):
        workspace = match.group(0)
        if workspace not in seen:
            seen.add(workspace)
            found.append(workspace)
    return found


def _fetch_go_page(workspace: str, cookie: str, timeout: float) -> str:
    url = f"{_BASE_URL}/workspace/{workspace}/go"
    try:
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/json;q=0.9, */*;q=0.8",
                "Cookie": cookie,
                "Origin": _BASE_URL,
                "Referer": _BASE_URL,
                "User-Agent": _USER_AGENT,
            },
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"OpenCode Go usage page returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise CollectorError(f"OpenCode Go usage page request failed: {exc.__class__.__name__}") from exc
    if not _host_is_opencode(response.url):
        raise CollectorError("OpenCode Go usage page redirected off opencode.ai")
    return response.text


def _host_is_opencode(url: str) -> bool:
    host = urlparse(url).hostname or ""
    host = host.lower()
    return host == "opencode.ai" or host.endswith(".opencode.ai")


def _account_from_go_page(text: str) -> AccountUsage | None:
    windows = _windows_from_go_page(text)
    if windows:
        return AccountUsage(
            source="opencode_go",
            provider="opencode-go",
            plan=_subscription_plan(text) or "go",
            billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
            windows=windows,
            notes=["Live data fetched directly from the OpenCode Go workspace page."],
            raw={"subscription_active": True},
        )
    if _subscription_inactive(text) or not text.strip():
        return _expired_account()
    return None


def _expired_account() -> AccountUsage:
    return AccountUsage(
        source="opencode_go",
        provider="opencode-go",
        plan="expired",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="OpenCode Go",
                used_percent=None,
                remaining_percent=0.0,
                reset_description=_EXPIRED_DESCRIPTION,
                raw={"subscription": None, "subscription_id": None, "subscription_plan": None},
            )
        ],
        notes=[
            "Live data fetched directly from the OpenCode Go workspace page.",
            "OpenCode Go has no active subscription (expired or not renewed).",
        ],
        raw={"subscription_active": False},
    )


def _subscription_inactive(text: str) -> bool:
    return bool(_SUBSCRIPTION_NULL.search(text) and _SUBSCRIPTION_ID_NULL.search(text))


def _subscription_plan(text: str) -> str | None:
    match = re.search(r"""["']?subscriptionPlan["']?\s*:\s*["']([^"'\n]+)["']""", text)
    if match:
        return match.group(1)
    if _SUBSCRIPTION_PRESENT.search(text):
        return "active"
    return None


def _windows_from_go_page(text: str) -> list[QuotaWindow]:
    now = utcnow()
    windows: list[QuotaWindow] = []
    for key, label, minutes in _WINDOW_SPECS:
        parsed = _named_usage_window(text, key)
        if parsed is None:
            continue
        used, reset_in = parsed
        remaining = max(0.0, 100.0 - used)
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used,
                remaining_percent=remaining,
                resets_at=now + timedelta(seconds=reset_in) if reset_in is not None else None,
                window_minutes=minutes,
                raw={"field": key, "usage_percent": used, "reset_in_sec": reset_in},
            )
        )
    return windows


def _named_usage_window(text: str, key: str) -> tuple[float, int | None] | None:
    """Parse a usage object, including Solid ``$R[n]=`` wrappers.

    Matches ``rollingUsage:{usagePercent:12}`` and the live page form
    ``rollingUsage:$R[34]={status:"ok",resetInSec:18000,usagePercent:0}``.
    Does not treat a scalar ``monthlyUsage:0`` as a window.
    """
    percent = _extract_float(
        rf"{re.escape(key)}[^}}]*?usagePercent\s*:\s*([0-9]+(?:\.[0-9]+)?)",
        text,
    )
    if percent is None:
        percent = _extract_float(
            rf""""{re.escape(key)}"\s*:\s*\{{[^}}]*?"usagePercent"\s*:\s*([0-9]+(?:\.[0-9]+)?)""",
            text,
        )
    if percent is None:
        return None
    if 0.0 < percent <= 1.0:
        percent *= 100.0
    reset_in = _extract_int(rf"{re.escape(key)}[^}}]*?resetInSec\s*:\s*([0-9]+)", text)
    if reset_in is None:
        reset_in = _extract_int(rf""""{re.escape(key)}"\s*:\s*\{{[^}}]*?"resetInSec"\s*:\s*([0-9]+)""", text)
    return percent, reset_in


def _extract_float(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _extract_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None
