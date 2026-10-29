"""Collect OpenCode Go subscription status.

The primary source is ``GET https://opencode.ai/zen/go/v1/usage`` with each
OpenCode Go API key as a bearer token, one row per key. ``oc-go-key use``
switches which of those keys the local servers spend; the console session
cookie does not follow that switch.

When no API key is configured, the collector falls back to the console
session cookie and ``/console/api/go/status``. A lapsed plan has no
``access`` object (or the route 404s). Cookie meters are micro-cents; the
usage API reports percent used directly.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import requests

from aiuse.models import AccountUsage, BillingKind, QuotaWindow

from .base import CollectorError
from .opencode_keys import resolve_opencode_api_keys
from .opencode_zen import (
    _fetch_console,
    _resolve_cookie,
    _workspace_id,
    list_workspaces,
    micro_cents_to_usd,
)

_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
_USAGE_USER_AGENT = "aiuse OpenCode Go collector"
_USAGE_NOTE = "Live data fetched from the OpenCode Go usage API."

_WORKSPACE_ENV = "AIUSE_OPENCODE_ZEN_WORKSPACE_ID"
_GO_STATUS_PATH = "/go/status"
_LABEL = "OpenCode Go status"
_EXPIRED_DESCRIPTION = "subscription expired"
_LIVE_NOTE = "Live data fetched directly from the OpenCode Go console API."

_METER_SPECS: tuple[tuple[str, str, int], ...] = (
    ("fiveHour", "OpenCode Go 5-hour", 300),
    ("week", "OpenCode Go weekly", 10080),
    ("month", "OpenCode Go monthly", 43200),
)
# /zen/go/v1/usage names the same three windows differently from /go/status.
_USAGE_SPECS: tuple[tuple[str, str, int], ...] = (
    ("rolling", "OpenCode Go 5-hour", 300),
    ("weekly", "OpenCode Go weekly", 10080),
    ("monthly", "OpenCode Go monthly", 43200),
)


def collect_opencode_go(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
) -> list[AccountUsage]:
    """Return one Go row per API key, or the console cookie when no key is set."""
    env = os.environ if environ is None else environ
    keyed = resolve_opencode_api_keys(env, timeout, allow_vault=environ is None)
    if keyed is not None:
        return [_account_for_api_key(label, api_key, timeout) for label, api_key in keyed]
    cookie = _resolve_cookie(env, timeout, allow_secretspec=environ is None)
    if not cookie:
        return []
    override = _workspace_id(str(env.get(_WORKSPACE_ENV) or ""))
    workspaces = [override] if override else list_workspaces(cookie, timeout, label=_LABEL)
    if not workspaces:
        raise CollectorError(f"{_LABEL}: no workspace for this session")

    payloads: list[Any] = []
    errors: list[str] = []
    for workspace in workspaces:
        try:
            payloads.append(_fetch_go_status(workspace, cookie, timeout))
        except CollectorError as exc:
            errors.append(str(exc))

    if not payloads:
        raise CollectorError(errors[0] if errors else f"{_LABEL}: workspace status unavailable")

    expired = False
    for payload in payloads:
        account = _account_from_go_status(payload)
        if account is None:
            continue
        if account.plan == "expired":
            expired = True
            continue
        return [account]
    if expired:
        return [_expired_account()]
    return []


def _account_for_api_key(label: str, api_key: str, timeout: float) -> AccountUsage:
    """One account from the usage API. The key is not copied onto the row."""
    try:
        payload = _fetch_usage(api_key, timeout)
    except CollectorError as exc:
        return _keyed_error(label, str(exc))
    if payload is None:
        account = _expired_account()
        account.account = label
        account.notes = [*account.notes, _USAGE_NOTE]
        return account
    account = _account_from_usage(payload, account=label)
    if account is None:
        return _keyed_error(label, "OpenCode Go usage response had no windows")
    return account


def _keyed_error(label: str, message: str) -> AccountUsage:
    return AccountUsage(
        source="opencode_go",
        provider="opencode-go",
        account=label,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        error=message,
        notes=[_USAGE_NOTE],
    )


def _fetch_usage(api_key: str, timeout: float) -> Any | None:
    """Return the usage JSON, or ``None`` when the key has no Go subscription.

    Redirects are not followed: the bearer token must not be sent to another host.
    """
    try:
        response = requests.get(
            _USAGE_URL,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": _USAGE_USER_AGENT,
            },
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise CollectorError(f"OpenCode Go usage request failed: {exc.__class__.__name__}") from exc
    if response.status_code == 401:
        raise CollectorError("OpenCode Go API key was rejected")
    if response.status_code == 403:
        return None
    if response.status_code >= 300:
        raise CollectorError(f"OpenCode Go usage returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise CollectorError("OpenCode Go usage response was not JSON") from exc


def _account_from_usage(payload: Any, *, account: str) -> AccountUsage | None:
    """Build one account from ``/zen/go/v1/usage``. ``None`` when no meter parses."""
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    windows: list[QuotaWindow] = []
    notes = [_USAGE_NOTE]
    if account.startswith("*"):
        notes.append("Active OpenCode credential (oc-go-key).")
    for key, label, minutes in _USAGE_SPECS:
        meter = usage.get(key)
        if not isinstance(meter, Mapping):
            continue
        percent = _usage_percent(meter.get("percent"))
        if percent is None:
            continue
        status = meter.get("status")
        if isinstance(status, str) and status.strip() and status.strip() != "ok":
            text = status.strip()
            if len(text) <= 40 and "oc_" not in text:
                notes.append(f"{label} is {text}.")
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=percent,
                remaining_percent=max(0.0, 100.0 - percent),
                resets_at=_parse_timestamp(meter.get("resetsAt")),
                window_minutes=minutes,
                raw={"meter": key},
            )
        )
    if not windows:
        return None
    return AccountUsage(
        source="opencode_go",
        provider="opencode-go",
        account=account,
        plan="go",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=windows,
        notes=notes,
        raw={"subscription_active": True},
    )


def _usage_percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return min(100.0, max(0.0, float(value)))


def _fetch_go_status(workspace: str, cookie: str, timeout: float) -> Any:
    return _fetch_console(
        _GO_STATUS_PATH,
        cookie,
        timeout,
        org=workspace,
        label=_LABEL,
        allow_missing=True,
    )


def _account_from_go_status(payload: Any) -> AccountUsage | None:
    """Build one account from ``/go/status``; ``None`` when the shape is unknown."""
    if payload is None:
        return _expired_account()
    if not isinstance(payload, dict):
        return None
    access = payload.get("access")
    if not isinstance(access, dict):
        return _expired_account()
    windows = _windows_from_access(access)
    if not windows:
        return _expired_account()
    plan = payload.get("product")
    return AccountUsage(
        source="opencode_go",
        provider="opencode-go",
        plan=plan if isinstance(plan, str) and plan else "go",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=windows,
        notes=[_LIVE_NOTE],
        raw={"subscription_active": True},
    )


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
            _LIVE_NOTE,
            "OpenCode Go has no active subscription (expired or not renewed).",
        ],
        raw={"subscription_active": False},
    )


def _windows_from_access(access: Mapping[str, Any]) -> list[QuotaWindow]:
    meters = access.get("meters")
    if not isinstance(meters, Mapping):
        return []
    period_end = _parse_timestamp(access.get("endsAt"))
    windows: list[QuotaWindow] = []
    for key, label, minutes in _METER_SPECS:
        meter = meters.get(key)
        if not isinstance(meter, Mapping):
            continue
        limit = micro_cents_to_usd(meter.get("limitMicroCents"))
        used = micro_cents_to_usd(meter.get("usedMicroCents"))
        if limit is None or used is None or limit <= 0:
            continue
        percent = min(100.0, max(0.0, used / limit * 100.0))
        resets_at = _parse_timestamp(meter.get("resetsAt")) or (period_end if key == "month" else None)
        if key == "month" and resets_at is not None:
            starts_at = _parse_timestamp(access.get("startsAt"))
            if starts_at is not None:
                period_minutes = int((resets_at - starts_at).total_seconds() / 60)
                if period_minutes > 0:
                    minutes = period_minutes
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=percent,
                remaining_percent=max(0.0, 100.0 - percent),
                resets_at=resets_at,
                window_minutes=minutes,
                raw={"meter": key, "limit_usd": limit, "used_usd": used},
            )
        )
    return windows


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
