"""Collect quota windows from the distinct OpenUsage.sh terminal dashboard."""

from __future__ import annotations

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, parse_dt

from .base import CollectorError, run_json

_PROVIDER_ALIASES = {"claude_code": "claude", "opencode": "opencode-go"}
_QUOTA_METRIC_PREFIXES = ("rate_limit_", "plan_percent_used", "plan_auto_percent_used", "plan_api_percent_used")


def collect_openusage_sh(*, timeout: float = 45.0) -> list[AccountUsage]:
    """Read OpenUsage.sh's documented versioned JSON export.

    Only explicit subscription/rate-limit percentage metrics become quota
    windows; local token/cost estimates never drive aiuse's use-or-lose ladder.
    """
    payload = run_json(["openusage-sh", "export", "--output", "-", "--format", "json"], timeout=timeout)
    if not isinstance(payload, dict) or not isinstance(payload.get("snapshots"), list):
        raise CollectorError("OpenUsage.sh export missing snapshots[]")
    accounts: list[AccountUsage] = []
    for row in payload["snapshots"]:
        if not isinstance(row, dict) or str(row.get("status", "")).upper() != "OK":
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        resets_raw = row.get("resets")
        resets: dict[object, object] = resets_raw if isinstance(resets_raw, dict) else {}
        windows: list[QuotaWindow] = []
        for key, metric in metrics.items():
            if not isinstance(metric, dict) or not str(key).startswith(_QUOTA_METRIC_PREFIXES):
                continue
            if str(metric.get("unit", "")).lower() != "%":
                continue
            remaining = metric.get("remaining")
            used = metric.get("used")
            if not isinstance(remaining, (int, float)) and not isinstance(used, (int, float)):
                continue
            reset_raw = resets.get(key) or resets.get("quota_reset") or resets.get("billing_cycle_end")
            reset_at = parse_dt(reset_raw) if isinstance(reset_raw, str) else None
            windows.append(
                QuotaWindow(
                    label=str(metric.get("window") or key).replace("_", " "),
                    remaining_percent=float(remaining) if isinstance(remaining, (int, float)) else None,
                    used_percent=float(used) if isinstance(used, (int, float)) else None,
                    resets_at=reset_at,
                )
            )
        if not windows:
            continue
        provider_id = str(row.get("provider_id") or "unknown")
        accounts.append(
            AccountUsage(
                source="openusage_sh",
                provider=_PROVIDER_ALIASES.get(provider_id, provider_id),
                account=str(row.get("account_id") or "") or None,
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=windows,
                notes=["Live quota data exported by OpenUsage.sh."],
            )
        )
    return accounts
