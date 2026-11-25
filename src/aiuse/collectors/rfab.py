"""Collect the Reality Fabricator (rfab.ai) prepaid token balance directly.

rfab.ai bills from a prepaid token balance (50,000 tokens = $1.00 USD) that
does not expire on a subscription cycle:

    GET https://api.rfab.ai/api/tokens/balance   (X-API-Key: <API key>)
    -> {"success": true, "tokenBalance": 633020, "totalTokensUsed": 17980,
        "totalInteractions": 5, "recentTransactions": [...]}

Note: ``/api/tokens/balance`` requires ``X-API-Key`` (``Authorization: Bearer``
returns ``401 AUTH_REQUIRED``). Redirects are disabled so the credential header
cannot cross origins.

Like the DeepSeek and OpenRouter collectors, this stays quiet until an API key
is supplied via ``AIUSE_RFAB_API_KEY``, ``RFAB_API_KEY``, or
``sudo-secretspec get RFAB_API_KEY --reason aiuse-rfab-balance``. The key is
never written to config, snapshots, logs, tracebacks, or error strings.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any

import requests

from aiuse.models import AccountUsage, BillingKind, QuotaWindow

from .base import CollectorError

_API_URL = "https://api.rfab.ai/api/tokens/balance"
_KEY_ENV = "AIUSE_RFAB_API_KEY"
_KEY_ENV_FALLBACK = "RFAB_API_KEY"
_KEY_SECRET = "RFAB_API_KEY"
_SECRETSPEC_REASON = "aiuse-rfab-balance"
_SECRETSPEC_TIMEOUT = 10.0
_DEFAULT_TIMEOUT = 15.0
_TOKENS_PER_USD = 50_000.0
_USER_AGENT = "aiuse rfab collector"


def collect_rfab(
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    environ: Mapping[str, str] | None = None,
    requires_operator_approval: bool = False,
) -> list[AccountUsage]:
    """Return the native rfab.ai token balance, or nothing when no key is available."""
    env = os.environ if environ is None else environ
    key = _resolve_key(env, timeout, allow_secretspec=environ is None)
    if not key:
        return []
    _validate_api_key(key)

    try:
        response = requests.get(
            _API_URL,
            timeout=timeout,
            allow_redirects=False,
            headers={
                "X-API-Key": key,
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        if 300 <= response.status_code < 400:
            raise CollectorError(f"rfab.ai API returned unexpected redirect (HTTP {response.status_code})")
        if response.status_code in (401, 403):
            return [
                AccountUsage(
                    source="rfab",
                    provider="rfab",
                    error=(
                        f"rfab.ai API rejected the key (HTTP {response.status_code}); "
                        f"check {_KEY_ENV} / {_KEY_ENV_FALLBACK} / {_KEY_SECRET}."
                    ),
                    billing_kind=BillingKind.PREPAID_BALANCE,
                    requires_operator_approval=requires_operator_approval,
                )
            ]
        response.raise_for_status()
    except CollectorError:
        raise
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"rfab.ai API returned HTTP {status}") from None
    except requests.RequestException as exc:
        raise CollectorError(f"rfab.ai API request failed: {exc.__class__.__name__}") from None

    try:
        data = response.json()
    except (requests.exceptions.JSONDecodeError, ValueError):
        raise CollectorError("rfab.ai API returned invalid JSON") from None

    return [_account_from_payload(data, requires_operator_approval=requires_operator_approval)]


def _validate_api_key(key: str) -> None:
    """Reject control or non-ASCII characters before Requests can echo them or raise UnicodeEncodeError."""
    if key != key.strip() or any(ord(ch) < 0x20 or ord(ch) >= 0x7F for ch in key):
        raise CollectorError("rfab.ai API key contains invalid header characters")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        num = float(value)
    except OverflowError:
        return None
    return num if math.isfinite(num) else None


def _account_from_payload(
    data: Any,
    *,
    requires_operator_approval: bool = False,
) -> AccountUsage:
    if not isinstance(data, dict):
        raise CollectorError("rfab.ai API response must be a JSON object")
    if data.get("success") is not True:
        raise CollectorError("rfab.ai API returned success=false")

    raw_balance = _finite_number(data.get("tokenBalance"))
    if raw_balance is None:
        raise CollectorError("rfab.ai API response missing numeric tokenBalance")

    tokens_remaining = max(0.0, raw_balance)
    balance_usd = tokens_remaining / _TOKENS_PER_USD
    tokens_int = int(round(tokens_remaining))
    used_tokens = _finite_number(data.get("totalTokensUsed"))
    interactions = _finite_number(data.get("totalInteractions"))

    reset_description = f"↓ ${balance_usd:.2f} prepaid ({tokens_int:,} tokens; no reset)"
    exhausted = tokens_remaining <= 0.0
    window = QuotaWindow(
        label="rfab token balance",
        window_id="rfab-token-balance",
        used_percent=100.0 if exhausted else 0.0,
        remaining_percent=0.0 if exhausted else 100.0,
        reset_description=reset_description,
        raw={
            "tokenBalance": raw_balance,
            "tokens_per_usd": _TOKENS_PER_USD,
            "balance_usd": balance_usd,
        },
    )

    notes = ["Live data fetched directly from the rfab.ai API (GET /api/tokens/balance)."]
    summary = f"rfab.ai balance: {tokens_int:,} tokens (↓ ${balance_usd:.2f} prepaid at 50,000 tokens/$1)"
    if used_tokens is not None and used_tokens >= 0:
        summary += f"; {int(round(used_tokens)):,} tokens used"
        if interactions is not None and interactions >= 0:
            summary += f" across {int(round(interactions)):,} interactions"
    notes.append(summary + ".")
    if exhausted:
        notes.append("rfab.ai token balance is exhausted (0 tokens remaining).")

    raw_meta: dict[str, Any] = {
        "success": True,
        "tokenBalance": raw_balance,
        "tokens_per_usd": _TOKENS_PER_USD,
    }
    if used_tokens is not None:
        raw_meta["totalTokensUsed"] = used_tokens
    if interactions is not None:
        raw_meta["totalInteractions"] = interactions

    return AccountUsage(
        source="rfab",
        provider="rfab",
        plan="rfab.ai (prepaid tokens)",
        billing_kind=BillingKind.PREPAID_BALANCE,
        windows=[window],
        balance_usd=balance_usd,
        credits_remaining=tokens_remaining,
        requires_operator_approval=requires_operator_approval,
        notes=notes,
        raw=raw_meta,
    )


def _resolve_key(env: Mapping[str, str], timeout: float, *, allow_secretspec: bool = True) -> str | None:
    """Return an explicit API key or a managed sudo-secretspec value without exposing either."""
    for name in (_KEY_ENV, _KEY_ENV_FALLBACK):
        explicit = str(env.get(name) or "").strip()
        if explicit:
            return explicit
    if not allow_secretspec:
        return None

    sudo_exec = shutil.which("sudo-secretspec")
    if sudo_exec is None:
        return None
    return _run_secret_cmd(
        [sudo_exec, "get", _KEY_SECRET, "--reason", _SECRETSPEC_REASON],
        timeout,
    )


def _run_secret_cmd(argv: list[str], timeout: float) -> str | None:
    try:
        result = subprocess.run(
            argv,
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
