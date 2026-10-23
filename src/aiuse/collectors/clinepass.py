"""Collect usage quota directly from ClinePass API.

With no ``[[collectors.clinepass.accounts]]`` configured this reads one
unlabelled key (``AIUSE_CLINE_API_KEY``, else ``sudo-secretspec get
CLINE_API_KEY``). Each configured account is fetched separately and reported
as its own row labelled with the account's ``label``; one account's failure
becomes an error row for that label instead of aborting the others.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import requests

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, parse_dt
from aiuse.secretspec import resolve_manifest_path

from .base import CollectorError

_API_URL = "https://api.cline.bot/api/v1/users/me/plan/usage-limits"
_ENV_VAR = "AIUSE_CLINE_API_KEY"
_SECRET_NAME = "CLINE_API_KEY"
_TIMEOUT = 10.0
_REASON = "aiuse live quota collection"


@dataclass(frozen=True)
class ClinePassAccount:
    """One ``[[collectors.clinepass.accounts]]`` entry."""

    label: str
    env: tuple[str, ...] = ()
    secret: str | None = None


def parse_accounts(raw: Any) -> list[ClinePassAccount]:
    """Labelled accounts from config; entries without a label are skipped."""
    if not isinstance(raw, (list, tuple)):
        return []
    accounts: list[ClinePassAccount] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        env_raw = entry.get("env")
        env_items = [env_raw] if isinstance(env_raw, str) else env_raw if isinstance(env_raw, (list, tuple)) else []
        env = tuple(str(name).strip() for name in env_items if str(name).strip())
        secret = str(entry.get("secret") or "").strip() or None
        accounts.append(ClinePassAccount(label=label, env=env, secret=secret))
    return accounts


def collect_clinepass(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
    accounts: Any = None,
) -> list[AccountUsage]:
    """Fetch usage limits from the ClinePass API, one row per configured account."""
    env = os.environ if environ is None else environ
    configured = parse_accounts(accounts)
    if configured:
        return [_collect_account(spec, env, timeout) for spec in configured]

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

    windows = _fetch_windows(api_key, timeout)
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


def _collect_account(spec: ClinePassAccount, env: Mapping[str, str], timeout: float) -> AccountUsage:
    """One labelled account; every failure is an error row for that label."""

    def error_row(message: str) -> AccountUsage:
        return AccountUsage(
            source="clinepass",
            provider="clinepass",
            account=spec.label,
            error=message,
            billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        )

    if not spec.env and not spec.secret:
        return error_row(f"ClinePass account {spec.label} has no env or secret configured")
    api_key, key_error = _resolve_api_key(env, timeout, env_vars=spec.env, secret=spec.secret, plain_secretspec=True)
    if not api_key:
        return error_row(key_error or "ClinePass API key unavailable")
    try:
        windows = _fetch_windows(api_key, timeout)
    except CollectorError as exc:
        return error_row(str(exc))
    except (ValueError, TypeError, AttributeError) as exc:
        # A malformed limit (e.g. percentUsed "N/A") must not abort the other accounts.
        return error_row(f"ClinePass API returned unparsable limits ({exc.__class__.__name__})")
    if not windows:
        return error_row("ClinePass API returned no valid limits")
    return AccountUsage(
        source="clinepass",
        provider="clinepass",
        account=spec.label,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=windows,
        notes=["Live data fetched directly from ClinePass API."],
    )


def _fetch_windows(api_key: str, timeout: float) -> list[QuotaWindow]:
    """Quota windows for one key. Errors name the failure, never the key."""
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
    return windows


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


def _resolve_api_key(
    env: Mapping[str, str],
    timeout: float,
    *,
    env_vars: Sequence[str] = (_ENV_VAR,),
    secret: str | None = _SECRET_NAME,
    plain_secretspec: bool = False,
) -> tuple[str | None, str | None]:
    """Return (api_key, error). Exactly one is non-None.

    Every failure carries a distinct reason. They used to collapse into a bare
    None, so a broker timeout, a missing binary and an empty secret were
    indistinguishable downstream — and all three silently dropped the provider
    from the snapshot.

    Order: ``env_vars``, then (labelled accounts only) plain ``secretspec get``
    against the aiuse manifest, then ``sudo-secretspec get``.
    """
    for name in env_vars:
        explicit = str(env.get(name) or "").strip()
        if explicit:
            return explicit, None

    env_label = "/".join(env_vars) if env_vars else "no env var"
    if secret is None:
        return None, f"{env_label} unset and no SecretSpec name configured"

    plain_error: str | None = None
    if plain_secretspec:
        api_key, plain_error = _secretspec_get(env, secret, timeout)
        if api_key:
            return api_key, None

    executable = shutil.which("sudo-secretspec")
    if executable is None:
        reason = f"{env_label} unset and sudo-secretspec not on PATH, so {secret} could not be read"
        return None, f"{reason} ({plain_error})" if plain_error else reason

    try:
        result = subprocess.run(
            [
                executable,
                "get",
                secret,
                "--reason",
                _REASON,
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
        return None, (f"sudo-secretspec timed out reading {secret} (broker busy?)")
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"sudo-secretspec failed: {exc.__class__.__name__}"

    if result.returncode != 0:
        return None, (f"sudo-secretspec exited {result.returncode} reading {secret}")

    api_key = result.stdout.strip()
    if not api_key:
        return None, f"sudo-secretspec returned an empty {secret}"
    return api_key, None


def _secretspec_get(env: Mapping[str, str], secret: str, timeout: float) -> tuple[str | None, str | None]:
    """Plain ``secretspec get`` against the aiuse manifest (same call as deepseek)."""
    executable = shutil.which("secretspec")
    if executable is None:
        return None, "secretspec not on PATH"
    manifest = str(resolve_manifest_path(env))
    try:
        result = subprocess.run(
            [executable, "get", "--file", manifest, "--reason", _REASON, secret],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=min(max(timeout, 0.1), _TIMEOUT),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"secretspec timed out reading {secret}"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"secretspec failed: {exc.__class__.__name__}"
    if result.returncode != 0:
        return None, f"secretspec exited {result.returncode} reading {secret}"
    api_key = result.stdout.strip()
    if not api_key:
        return None, f"secretspec returned an empty {secret}"
    return api_key, None
