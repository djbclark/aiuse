"""Collect the DeepSeek prepaid API balance directly (issue #16).

DeepSeek is pay-as-you-go: purchased balance does not expire, so it is never
use-or-lose. Until now CodexBar was its only source. DeepSeek documents one
read-only endpoint for it (https://api-docs.deepseek.com/api/get-user-balance):

    GET https://api.deepseek.com/user/balance   (Authorization: Bearer <API key>)
    -> {"is_available": true,
        "balance_infos": [{"currency": "USD", "total_balance": "110.00",
                           "granted_balance": "10.00", "topped_up_balance": "100.00"}]}

An ordinary API key is enough; the call costs no tokens. Like the OpenRouter
collector it stays quiet until a key is supplied explicitly: the
``AIUSE_DEEPSEEK_API_KEY`` environment variable, else SecretSpec's
``DEEPSEEK_API_KEY``. A CNY balance is reported in ``credits_remaining`` with
its currency in the notes, never converted into ``balance_usd``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any

import requests

from aiuse.models import AccountUsage, BillingKind, coerce_float
from aiuse.secretspec import resolve_manifest_path

from .base import CollectorError

_API_URL = "https://api.deepseek.com/user/balance"
_KEY_ENV = "AIUSE_DEEPSEEK_API_KEY"
_KEY_SECRET = "DEEPSEEK_API_KEY"
_SECRETSPEC_TIMEOUT = 5.0
_USER_AGENT = "aiuse DeepSeek collector"


def collect_deepseek(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
) -> list[AccountUsage]:
    """Return the native DeepSeek balance, or nothing until an API key is supplied."""
    env = os.environ if environ is None else environ
    key = _resolve_key(env, timeout, allow_secretspec=environ is None)
    if not key:
        return []

    try:
        response = requests.get(
            _API_URL,
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": _USER_AGENT},
        )
        if response.status_code in (401, 403):
            return [
                AccountUsage(
                    source="deepseek",
                    provider="deepseek",
                    error=f"DeepSeek API rejected the key (HTTP {response.status_code}); check {_KEY_ENV} / {_KEY_SECRET}.",
                    billing_kind=BillingKind.PREPAID_BALANCE,
                )
            ]
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"DeepSeek API returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise CollectorError(f"DeepSeek API request failed: {exc.__class__.__name__}") from exc
    except ValueError as exc:
        raise CollectorError("DeepSeek API returned invalid JSON") from exc

    return [_account_from_payload(data)]


def _account_from_payload(data: Any) -> AccountUsage:
    if not isinstance(data, dict) or not isinstance(data.get("balance_infos"), list):
        raise CollectorError("DeepSeek API response missing 'balance_infos' list")
    by_currency: dict[str, dict[str, float | None]] = {}
    for info in data["balance_infos"]:
        if not isinstance(info, dict):
            continue
        currency = str(info.get("currency") or "").strip().upper()
        total = coerce_float(info.get("total_balance"))
        if not currency or total is None:
            continue
        by_currency[currency] = {
            "total": total,
            "granted": coerce_float(info.get("granted_balance")),
            "topped_up": coerce_float(info.get("topped_up_balance")),
        }
    if not by_currency:
        raise CollectorError("DeepSeek API response has no numeric total_balance")

    notes = ["Live data fetched directly from the DeepSeek API (GET /user/balance)."]
    for currency, amounts in sorted(by_currency.items()):
        parts = [f"{amounts['total']:.2f} {currency} total"]
        if amounts["topped_up"] is not None:
            parts.append(f"{amounts['topped_up']:.2f} topped up")
        if amounts["granted"] is not None:
            parts.append(f"{amounts['granted']:.2f} granted")
        notes.append("DeepSeek balance: " + ", ".join(parts) + ".")
    if data.get("is_available") is False:
        notes.append("DeepSeek reports the balance is insufficient for API calls (is_available: false).")

    usd = by_currency.get("USD")
    balance_usd = max(0.0, float(usd["total"] or 0.0)) if usd is not None else None
    other = next((amounts for currency, amounts in sorted(by_currency.items()) if currency != "USD"), None)
    credits_remaining = None
    if balance_usd is None and other is not None:
        # Not USD (DeepSeek's mainland accounts are CNY): keep the number, do not convert.
        credits_remaining = max(0.0, float(other["total"] or 0.0))
    return AccountUsage(
        source="deepseek",
        provider="deepseek",
        plan="DeepSeek (prepaid API)",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=balance_usd,
        credits_remaining=credits_remaining,
        notes=notes,
        raw={"is_available": data.get("is_available"), "balance_infos": data["balance_infos"]},
    )


def _resolve_key(env: Mapping[str, str], timeout: float, *, allow_secretspec: bool = True) -> str | None:
    """Return an explicit API key or a SecretSpec value without exposing either."""
    explicit = str(env.get(_KEY_ENV) or "").strip()
    if explicit:
        return explicit
    if not allow_secretspec:
        return None
    executable = shutil.which("secretspec")
    if executable is None:
        return None
    manifest = str(resolve_manifest_path(env))
    try:
        result = subprocess.run(
            [executable, "get", "--file", manifest, "--reason", "aiuse DeepSeek balance collection", _KEY_SECRET],
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
    key = result.stdout.strip()
    return key or None
