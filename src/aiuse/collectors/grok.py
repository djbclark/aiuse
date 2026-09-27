"""Fetch Grok Extra Usage Credits and the plan reset window from xAI billing.

CodexBar's ``grok-cli-proxy`` path exports the weekly SuperGrok pool only.
Purchased Extra Usage Credits live in ``config.prepaidBalance`` on
``GET https://cli-chat-proxy.grok.com/v1/billing?format=credits`` (same
surface CodexBar uses internally), and the same payload carries the plan
period itself — ``config.currentPeriod`` (type/start/end) plus
``config.creditUsagePercent``. This collector supplements CodexBar rows with
the prepaid wallet as ``usage_credits.remaining`` and the plan reset as a
``QuotaWindow``, so the time until the plan resets survives even when
CodexBar is disabled.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, UsageCredits, coerce_float, parse_dt

from .base import CollectorError

_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
# ``prepaidBalance.val`` is USD cents (live-checked against ~$20 purchases).
_PREPAID_SCALE = 100.0

# ``currentPeriod.type`` → (display label, nominal window minutes). The label
# text deliberately names the period so ``clock_from_label`` can bucket the
# window onto the matrix clock without guessing from the reset distance.
_PERIOD_SHAPES: dict[str, tuple[str, int | None]] = {
    "USAGE_PERIOD_TYPE_HOURLY": ("hourly plan", 60),
    "USAGE_PERIOD_TYPE_DAILY": ("daily plan", 1440),
    "USAGE_PERIOD_TYPE_WEEKLY": ("weekly plan", 10080),
    "USAGE_PERIOD_TYPE_MONTHLY": ("monthly plan", 43200),
}


def collect_grok(*, timeout: float = 15.0) -> list[AccountUsage]:
    """Return Extra Usage Credits and the plan window when auth has a bearer token."""
    token = _read_bearer_token()
    if not token:
        return []
    data = _fetch_billing(token, timeout)
    raw_config = data.get("config")
    config = raw_config if isinstance(raw_config, dict) else {}
    balance_usd = _prepaid_balance_usd(config.get("prepaidBalance"))
    window = _plan_window(config)
    product_note = _product_usage_note(config)
    if balance_usd is None and window is None:
        return []
    notes = ["Live data fetched from Grok billing (Extra Usage Credits)."]
    if product_note:
        notes.append(product_note)
    return [
        AccountUsage(
            source="grok_billing",
            provider="grok",
            account=_email_from_auth(),
            billing_kind=BillingKind.SUBSCRIPTION_WINDOW if window is not None else BillingKind.UNKNOWN,
            windows=[window] if window is not None else [],
            usage_credits=UsageCredits(remaining=balance_usd, currency="USD") if balance_usd is not None else None,
            notes=notes,
            raw={"prepaidBalance": config.get("prepaidBalance"), "currentPeriod": config.get("currentPeriod")},
        )
    ]


def _plan_window(config: dict[str, Any]) -> QuotaWindow | None:
    """The plan period as a window, when the billing payload describes one."""
    period = config.get("currentPeriod")
    if not isinstance(period, dict):
        return None
    period_type = str(period.get("type") or "")
    label, minutes = _PERIOD_SHAPES.get(period_type, ("plan", None))
    used_percent = coerce_float(config.get("creditUsagePercent"))
    resets_at = parse_dt(period.get("end"))
    if used_percent is None and resets_at is None:
        return None
    return QuotaWindow(
        label=label,
        used_percent=used_percent,
        remaining_percent=None if used_percent is None else max(0.0, 100.0 - used_percent),
        resets_at=resets_at,
        window_minutes=minutes,
        raw={"currentPeriod": period},
    )


def _product_usage_note(config: dict[str, Any]) -> str | None:
    """Per-product plan usage as one note ("GrokBuild 93% used · …")."""
    products = config.get("productUsage")
    if not isinstance(products, list):
        return None
    parts: list[str] = []
    for item in products:
        if not isinstance(item, dict):
            continue
        name = str(item.get("product") or "").strip()
        percent = coerce_float(item.get("usagePercent"))
        if name and percent is not None:
            parts.append(f"{name} {percent:.0f}% used")
    if not parts:
        return None
    return "Plan pools: " + " · ".join(parts)


def _grok_home() -> Path:
    override = os.environ.get("GROK_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".grok"


def _auth_path() -> Path:
    return _grok_home() / "auth.json"


def _read_auth() -> dict[str, Any] | None:
    path = _auth_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_bearer_token() -> str | None:
    auth = _read_auth()
    if not auth:
        return None
    preferred_scopes = (
        "https://auth.x.ai::",
        "https://accounts.x.ai/sign-in",
    )
    for scope in preferred_scopes:
        entry = auth.get(scope)
        if isinstance(entry, dict):
            token = entry.get("key")
            if isinstance(token, str) and token.strip():
                return token.strip()
    for entry in auth.values():
        if isinstance(entry, dict):
            token = entry.get("key")
            if isinstance(token, str) and token.strip():
                return token.strip()
    return None


def _email_from_auth() -> str | None:
    auth = _read_auth()
    if not auth:
        return None
    for entry in auth.values():
        if isinstance(entry, dict):
            email = entry.get("email")
            if isinstance(email, str) and email.strip():
                return email.strip()
    return None


def _prepaid_balance_usd(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None
    val = raw.get("val")
    if val is None:
        return None
    try:
        cents = float(val)
    except (TypeError, ValueError):
        return None
    return cents / _PREPAID_SCALE


def _fetch_billing(token: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        _BILLING_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "x-xai-token-auth": "xai-grok-cli",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # nosemgrep
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise CollectorError(f"Grok billing HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise CollectorError(f"Grok billing request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CollectorError("Grok billing request timed out") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CollectorError("Grok billing returned non-JSON") from exc
    if not isinstance(payload, dict):
        raise CollectorError("Grok billing JSON is not an object")
    return payload
