"""Collect usage quota directly from ClinePass API."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping

import requests

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, parse_dt

from .base import CollectorError

_API_URL = "https://api.cline.bot/api/v1/users/me/plan/usage-limits"
_ENV_VAR = "AIUSE_CLINE_API_KEY"
_SECRET_NAME = "CLINE_API_KEY"
_TIMEOUT = 10.0


def collect_clinepass(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
) -> list[AccountUsage]:
    """Fetch usage limits from the ClinePass API."""
    env = os.environ if environ is None else environ
    api_key, key_error = _resolve_api_key(env, timeout)
    if not api_key:
        # Report the account with an error rather than returning [].
        # Returning an empty list makes an unreachable provider indistinguishable
        # from one that is not configured: the account simply vanishes from the
        # snapshot, and anything reading that snapshot sees "no data" where it
        # should see "could not check". That is actively dangerous for a
        # burn-rate or quota alert, which would read silence as healthy.
        return [
            AccountUsage(
                source="clinepass",
                provider="clinepass",
                error=key_error or "ClinePass API key unavailable",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
            )
        ]

    try:
        response = requests.get(
            _API_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"ClinePass API returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise CollectorError(f"ClinePass API request failed: {exc.__class__.__name__}") from exc
    except ValueError as exc:
        raise CollectorError("ClinePass API returned invalid JSON") from exc

    if not data.get("success"):
        raise CollectorError("ClinePass API returned success=false")

    limits_data = data.get("data", {}).get("limits", [])
    if not isinstance(limits_data, list):
        raise CollectorError("ClinePass API limits missing or invalid type")

    windows: list[QuotaWindow] = []
    for item in limits_data:
        if not isinstance(item, dict):
            continue
        # Cline returns 'percentUsed'
        percent_used = item.get("percentUsed")
        if percent_used is None:
            continue
        used = float(percent_used)
        label, minutes = _clinepass_window(str(item.get("type") or "unknown"))
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used,
                remaining_percent=max(0.0, 100.0 - used),
                resets_at=parse_dt(item.get("resetsAt")),
                window_minutes=minutes,
            )
        )

    if not windows:
        return [
            AccountUsage(
                source="clinepass",
                provider="clinepass",
                error="ClinePass API returned no valid limits",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
            )
        ]

    return [
        AccountUsage(
            source="clinepass",
            provider="clinepass",
            billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
            windows=windows,
            notes=["Live data fetched directly from ClinePass API."],
        )
    ]


_CLINEPASS_WINDOWS: dict[str, tuple[str, int]] = {
    "five_hour": ("ClinePass 5-hour", 300),
    "weekly": ("ClinePass weekly", 10080),
    "monthly": ("ClinePass monthly", 43200),
}


def _clinepass_window(limit_type: str) -> tuple[str, int | None]:
    mapped = _CLINEPASS_WINDOWS.get(limit_type)
    if mapped:
        return mapped
    return f"ClinePass {limit_type.replace('_', ' ')}", None


def _resolve_api_key(env: Mapping[str, str], timeout: float) -> tuple[str | None, str | None]:
    """Return (api_key, error). Exactly one is non-None.

    Every failure carries a distinct reason. They used to collapse into a bare
    None, so a broker timeout, a missing binary and an empty secret were
    indistinguishable downstream — and all three silently dropped the provider
    from the snapshot.
    """
    explicit = str(env.get(_ENV_VAR) or "").strip()
    if explicit:
        return explicit, None

    executable = shutil.which("sudo-secretspec")
    if executable is None:
        return None, (f"{_ENV_VAR} unset and sudo-secretspec not on PATH, so {_SECRET_NAME} could not be read")

    try:
        result = subprocess.run(
            [
                executable,
                "get",
                _SECRET_NAME,
                "--reason",
                "aiuse live quota collection",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=min(max(timeout, 0.1), _TIMEOUT),
            check=False,
        )
    except subprocess.TimeoutExpired:
        # The likeliest intermittent cause: the privilege-separated broker is
        # busy, so the lookup exceeds its window even though the secret exists.
        return None, (f"sudo-secretspec timed out reading {_SECRET_NAME} (broker busy?)")
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"sudo-secretspec failed: {exc.__class__.__name__}"

    if result.returncode != 0:
        return None, (f"sudo-secretspec exited {result.returncode} reading {_SECRET_NAME}")

    api_key = result.stdout.strip()
    if not api_key:
        return None, f"sudo-secretspec returned an empty {_SECRET_NAME}"
    return api_key, None
