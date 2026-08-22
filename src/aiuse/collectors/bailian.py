"""Collect Alibaba Cloud (Bailian / Model Studio) plan quotas via the bl CLI.

The bailian-cli (binary ``bl``, also installed as ``bailian``) is Alibaba
Cloud's Model Studio console CLI. After ``bl auth login --console`` (one-time
browser OAuth, credentials in ``~/.bailian/config.json``) it exposes the plan
quotas that CodexBar's alibaba-*-plan providers read through Chrome cookies:

    bl usage token-plan --output json
      -> {"per5HourPercentage": 0.42, "per5HourResetTime": 1788000000000,
          "per1WeekPercentage": 0.0178, "per1WeekResetTime": 1788016260000}

Percentages are fractions **consumed** (documented in steipete/CodexBar#2328,
same gateway the menu-bar apps QwenBar/QwenUsage use); reset times are epoch
milliseconds. ``bl usage coding-plan --output json`` returns the same shape
for the Team coding plan and ``{}`` when absent.

This is the Alibaba Cloud account, distinct from the qwencloud.com account
the ``qwencloud`` collector reads (canonical providers ``alibaba`` vs
``qwencloud``), so an operator holding both gets two independent rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, coerce_float

from .base import CollectorError, run_json, which

# CLI field -> (label, nominal minutes). Percentages are consumed fractions.
_WINDOWS: tuple[tuple[str, str, str, int], ...] = (
    ("per5HourPercentage", "per5HourResetTime", "alibaba 5-hour", 300),
    ("per1WeekPercentage", "per1WeekResetTime", "alibaba weekly", 10080),
)


def collect_bailian(*, timeout: float = 45.0) -> list[AccountUsage]:
    binary = which("bl") or which("bailian")
    if binary is None:
        # Optional collector: quiet when the CLI is not installed.
        return []
    # Normalize to the short binary name for stable source ids in tests.
    argv_binary = "bl" if binary.endswith("/bl") or binary.endswith("\\bl") else "bailian"

    accounts: list[AccountUsage] = []
    errors: list[str] = []
    for plan, subcommand in (("Token Plan", "token-plan"), ("Coding Plan", "coding-plan")):
        try:
            payload = run_json([argv_binary, "usage", subcommand, "--output", "json"], timeout=timeout)
        except CollectorError as exc:
            message = str(exc)
            if _looks_unauthenticated(message):
                # No console login on this machine: quiet, like muse/clinepass.
                return []
            errors.append(f"{subcommand}: {message}")
            continue
        account = _account_from_payload(payload, plan=plan)
        if account is not None:
            accounts.append(account)

    if accounts:
        return accounts
    if errors:
        raise CollectorError("; ".join(errors))
    # CLI present and authenticated, but no plan data at all: quiet.
    return []


def _account_from_payload(payload: Any, *, plan: str) -> AccountUsage | None:
    if not isinstance(payload, dict) or not payload:
        return None
    windows: list[QuotaWindow] = []
    for pct_key, reset_key, label, minutes in _WINDOWS:
        used_fraction = coerce_float(payload.get(pct_key))
        if used_fraction is None:
            # The 5-hour field is omitted when the plan has no 5h limit.
            continue
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used_fraction * 100.0,
                remaining_percent=max(0.0, 100.0 - used_fraction * 100.0),
                resets_at=_epoch_ms(payload.get(reset_key)),
                window_minutes=minutes,
                raw={key: payload[key] for key in (pct_key, reset_key) if key in payload},
            )
        )
    if not windows:
        return None
    return AccountUsage(
        source="bailian",
        provider="alibaba",
        plan=plan,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=windows,
        notes=["Live data fetched directly from the bailian CLI (bl usage)."],
    )


def _epoch_ms(value: Any) -> datetime | None:
    milliseconds = coerce_float(value)
    if milliseconds is None or milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)


def _looks_unauthenticated(message: str) -> bool:
    lowered = message.lower()
    return (
        "console access token" in lowered
        or "not authenticated" in lowered
        or "auth login" in lowered
        or "login required" in lowered
    )
