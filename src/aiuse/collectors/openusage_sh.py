"""Collect quota windows from the distinct OpenUsage.sh terminal dashboard."""

from __future__ import annotations

from aiuse.models import AccountUsage, BillingKind, QuotaWindow, parse_dt

from .base import CollectorError, run_json

_PROVIDER_ALIASES = {"claude_code": "claude", "opencode": "opencode-go"}
_QUOTA_METRIC_PREFIXES = ("rate_limit_", "plan_percent_used", "plan_auto_percent_used", "plan_api_percent_used")
_METRIC_PRIORITY = {
    "rate_limit_primary": 0,
    "rate_limit_secondary": 1,
    "plan_percent_used": 2,
    "plan_auto_percent_used": 3,
    "plan_api_percent_used": 4,
}


def _window_label(provider_id: str, key: str, metric: dict[object, object]) -> str:
    """Give known provider metric keys stable cross-source labels."""
    if provider_id == "cursor":
        return {
            "plan_percent_used": "Cursor Included",
            "plan_auto_percent_used": "Cursor Auto",
            "plan_api_percent_used": "Cursor other models",
        }.get(key, str(metric.get("window") or key).replace("_", " "))
    label = str(metric.get("window") or key).replace("_", " ")
    return "weekly" if label.casefold() == "7d" else label


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
        provider_id = str(row.get("provider_id") or "unknown")
        # OpenUsage.sh can expose the same underlying quota via both a rate
        # limit and a plan metric.  Deduplicate only exact values for the same
        # raw window, keeping the more explicit rate-limit metric.
        candidates: list[tuple[tuple[object, ...], int, QuotaWindow]] = []
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
            remaining_value = float(remaining) if isinstance(remaining, (int, float)) else None
            used_value = float(used) if isinstance(used, (int, float)) else None
            candidates.append(
                (
                    (str(metric.get("window") or "").casefold(), remaining_value, used_value),
                    _METRIC_PRIORITY.get(str(key), 99),
                    QuotaWindow(
                        label=_window_label(provider_id, str(key), metric),
                        remaining_percent=remaining_value,
                        used_percent=used_value,
                        resets_at=reset_at,
                    ),
                )
            )
        selected: dict[tuple[object, ...], tuple[int, QuotaWindow]] = {}
        for signature, priority, window in candidates:
            previous = selected.get(signature)
            if previous is None or priority < previous[0]:
                selected[signature] = (priority, window)
        windows = [window for _priority, window in selected.values()]
        if not windows:
            continue
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
