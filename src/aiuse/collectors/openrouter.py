"""Collect OpenRouter account credit directly via its Management API."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping

import requests

from aiuse.models import AccountUsage, BillingKind
from aiuse.secretspec import resolve_manifest_path

from .base import CollectorError

_API_URL = "https://openrouter.ai/api/v1/credits"
_KEY_ENV = "AIUSE_OPENROUTER_MANAGEMENT_KEY"
_KEY_SECRET = "OPENROUTER_MANAGEMENT_KEY"
_SECRETSPEC_TIMEOUT = 5.0
_USER_AGENT = "aiuse OpenRouter collector"


def collect_openrouter(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
) -> list[AccountUsage]:
    """Return the native OpenRouter source, or nothing until a management key is supplied."""
    env = os.environ if environ is None else environ
    key = _resolve_key(env, timeout, allow_secretspec=environ is None)
    if not key:
        return []

    try:
        response = requests.get(
            _API_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": _USER_AGENT,
            },
        )
        if response.status_code in (401, 403):
            # Key exists but is invalid/unauthorized. Let user know.
            return [
                AccountUsage(
                    source="openrouter_api",
                    provider="openrouter",
                    error="OpenRouter API rejected the key (HTTP 401/403). Ensure it is a Management Key, not a routing key.",
                    billing_kind=BillingKind.PREPAID_BALANCE,
                )
            ]
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"OpenRouter API returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise CollectorError(f"OpenRouter API request failed: {exc.__class__.__name__}") from exc
    except ValueError as exc:
        raise CollectorError("OpenRouter API returned invalid JSON") from exc

    if "data" not in data or not isinstance(data["data"], dict):
        raise CollectorError("OpenRouter API response missing 'data' object")

    payload = data["data"]
    total_credits = payload.get("total_credits")
    total_usage = payload.get("total_usage")

    if not isinstance(total_credits, (int, float)) or not isinstance(total_usage, (int, float)):
        raise CollectorError("OpenRouter API response missing numeric total_credits or total_usage")

    remaining_credits = float(total_credits) - float(total_usage)
    if remaining_credits < 0:
        remaining_credits = 0.0

    return [
        AccountUsage(
            source="openrouter_api",
            provider="openrouter",
            billing_kind=BillingKind.PREPAID_BALANCE,
            balance_usd=remaining_credits,
            notes=[
                "Live data fetched directly from OpenRouter Management API.",
                f"OpenRouter prepaid credits: ${float(total_credits):.2f} funded, ${float(total_usage):.2f} spent.",
            ],
            raw=payload,
        )
    ]


def _resolve_key(env: Mapping[str, str], timeout: float, *, allow_secretspec: bool = True) -> str | None:
    """Return an explicit management key or a SecretSpec value without exposing either."""
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
            [
                executable,
                "get",
                "--file",
                manifest,
                "--reason",
                "aiuse OpenRouter balance collection",
                _KEY_SECRET,
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
    key = result.stdout.strip()
    return key or None
