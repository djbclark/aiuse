"""Collect OpenCode Go subscription status from the official console API.

CodexBar's local path sums SQLite costs against hardcoded $12/$30/$60 caps and
cannot see that a Go plan has lapsed. This collector reuses the same OpenCode
console session cookie as the Zen collector and reads
``/console/api/go/status`` for the workspace.

A lapsed plan has no ``access`` object (or the route 404s); an active plan
returns ``access.meters`` with ``fiveHour`` / ``week`` / ``month`` limits and
usage in micro-cents — treat those meters as the live allotment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from aiuse.models import AccountUsage, BillingKind, QuotaWindow

from .base import CollectorError
from .opencode_zen import (
    _fetch_console,
    _resolve_cookie,
    _workspace_id,
    list_workspaces,
    micro_cents_to_usd,
)

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
