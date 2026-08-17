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
    api_key = _resolve_api_key(env, timeout)
    if not api_key:
        return []

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
        limit_type = item.get("type", "unknown")
        label = f"ClinePass {limit_type.replace('_', ' ').title()}"

        # Cline returns 'percentUsed'
        percent_used = item.get("percentUsed")
        if percent_used is None:
            continue

        resets_at = parse_dt(item.get("resetsAt"))

        windows.append(
            QuotaWindow(
                label=label,
                used_percent=float(percent_used),
                resets_at=resets_at,
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


def _resolve_api_key(env: Mapping[str, str], timeout: float) -> str | None:
    """Return an explicit API key or fetch it from sudo-secretspec."""
    explicit = str(env.get(_ENV_VAR) or "").strip()
    if explicit:
        return explicit

    executable = shutil.which("sudo-secretspec")
    if executable is None:
        return None

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
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    api_key = result.stdout.strip()
    return api_key or None
