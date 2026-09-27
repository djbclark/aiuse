"""Fetch Grok Extra Usage Credits from the xAI CLI billing API.

CodexBar's ``grok-cli-proxy`` path exports the weekly SuperGrok pool only.
Purchased Extra Usage Credits live in ``config.prepaidBalance`` on
``GET https://cli-chat-proxy.grok.com/v1/billing?format=credits`` (same
surface CodexBar uses internally). This collector supplements CodexBar rows
with that prepaid wallet as ``usage_credits.remaining``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from aiuse.models import AccountUsage, UsageCredits

from .base import CollectorError

_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
# ``prepaidBalance.val`` is USD cents (live-checked against ~$20 purchases).
_PREPAID_SCALE = 100.0


def collect_grok(*, timeout: float = 15.0) -> list[AccountUsage]:
    """Return Extra Usage Credits when ``~/.grok/auth.json`` has a bearer token."""
    token = _read_bearer_token()
    if not token:
        return []
    data = _fetch_billing(token, timeout)
    raw_config = data.get("config")
    config = raw_config if isinstance(raw_config, dict) else {}
    balance_usd = _prepaid_balance_usd(config.get("prepaidBalance"))
    if balance_usd is None:
        return []
    return [
        AccountUsage(
            source="grok_billing",
            provider="grok",
            account=_email_from_auth(),
            usage_credits=UsageCredits(remaining=balance_usd, currency="USD"),
            notes=["Live data fetched from Grok billing (Extra Usage Credits)."],
            raw={"prepaidBalance": config.get("prepaidBalance")},
        )
    ]


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
