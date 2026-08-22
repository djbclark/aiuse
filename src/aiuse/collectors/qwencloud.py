"""Collect QwenCloud quotas directly from the qwencloud CLI.

The qwencloud CLI (qwencloud.com, v1.3+) authenticates via `qwencloud auth
login` (OAuth PKCE / device flow — there is no API-key mode) and exposes one
call that carries every aiuse-relevant signal:

    qwencloud usage summary --format json
      -> {coding_plan: {subscribed, plan, windows: {per_5h, weekly, monthly}},
          token_plan: {subscribed, planName, totalCredits, remainingCredits},
          pay_as_you_go: {total: {cost, currency}}, free_tier: [...]}

The coding plan contributes the use-or-lose windows (5h / weekly / monthly
credit windows, each {remaining, total, used_pct, next_reset_at}); the token
plan is a rolling credit pool; PAYG is spend against a configurable billing
limit (`qwencloud billing limit`). Per-model free-tier quotas are ignored —
they are not subscription use-or-lose allotments and would add hundreds of
noise rows.

CodexBar's own qwen-cloud provider reports canonical id "qwencloud" too, so
the two sources cross-check on one identity when both are enabled.
"""

from __future__ import annotations

from typing import Any

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    UsageCredits,
    coerce_float,
    parse_dt,
)

from .base import CollectorError, run_json, which

# window key -> (label, nominal minutes). Monthly follows models.py
# WINDOW_NOMINAL_MINUTES (43800), not the 30-day 43200 spelling.
_CODING_PLAN_WINDOWS: dict[str, tuple[str, int]] = {
    "per_5h": ("qwen 5-hour", 300),
    "weekly": ("qwen weekly", 10080),
    "monthly": ("qwen monthly", 43800),
}


def collect_qwencloud(*, timeout: float = 45.0) -> list[AccountUsage]:
    if not which("qwencloud"):
        # Optional collector (like muse/clinepass): stay quiet when the CLI is
        # absent so machines without QwenCloud don't accumulate error noise.
        return []

    try:
        payload = run_json(["qwencloud", "usage", "summary", "--format", "json"], timeout=timeout)
    except CollectorError as exc:
        message = str(exc)
        if _looks_unauthenticated(message):
            return [
                AccountUsage(
                    source="qwencloud",
                    provider="qwencloud",
                    error=f"qwencloud CLI not authenticated: run `qwencloud auth login` ({message})",
                    billing_kind=BillingKind.UNKNOWN,
                )
            ]
        raise
    if not isinstance(payload, dict):
        raise CollectorError("qwencloud usage summary returned unexpected JSON shape")

    accounts: list[AccountUsage] = []
    coding = _coding_plan_account(payload.get("coding_plan"))
    if coding is not None:
        accounts.append(coding)
    token = _token_plan_account(payload.get("token_plan"))
    if token is not None:
        accounts.append(token)
    payg = _payg_account(payload.get("pay_as_you_go"), timeout=timeout)
    if payg is not None:
        accounts.append(payg)

    if accounts:
        return accounts

    # Authenticated but nothing subscribed: report the absence instead of an
    # empty list so the operator can distinguish "no plan" from "no collector".
    return [
        AccountUsage(
            source="qwencloud",
            provider="qwencloud",
            error="No active QwenCloud coding/token plan on this account",
            billing_kind=BillingKind.UNKNOWN,
            notes=["Run `qwencloud usage summary` for per-model free-tier quotas."],
        )
    ]


def _looks_unauthenticated(message: str) -> bool:
    lowered = message.lower()
    return "auth" in lowered or "login" in lowered or "credential" in lowered


def _coding_plan_account(raw: Any) -> AccountUsage | None:
    if not isinstance(raw, dict) or not raw.get("subscribed"):
        return None
    windows_raw = raw.get("windows")
    windows: list[QuotaWindow] = []
    if isinstance(windows_raw, dict):
        for key, (label, minutes) in _CODING_PLAN_WINDOWS.items():
            window = _window(windows_raw.get(key), label, minutes)
            if window is not None:
                windows.append(window)
    plan = raw.get("plan")
    account = AccountUsage(
        source="qwencloud",
        provider="qwencloud",
        plan=str(plan) if plan else "Coding Plan",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        notes=["Live data fetched directly from the qwencloud CLI."],
    )
    if windows:
        account.windows = windows
    else:
        account.error = "Coding plan subscribed but no quota windows returned"
    return account


def _token_plan_account(raw: Any) -> AccountUsage | None:
    if not isinstance(raw, dict) or not raw.get("subscribed"):
        return None
    total = coerce_float(raw.get("totalCredits"))
    remaining = coerce_float(raw.get("remainingCredits"))
    used_pct = coerce_float(raw.get("usedPct"))
    plan = raw.get("planName")
    account = AccountUsage(
        source="qwencloud",
        provider="qwencloud",
        plan=str(plan) if plan else "Token Plan",
        billing_kind=BillingKind.PREPAID_BALANCE,
        credits_remaining=remaining,
        notes=["Seat credit pool rolls until spent.", "Live data fetched directly from the qwencloud CLI."],
    )
    if total is not None and total > 0:
        if used_pct is None and remaining is not None:
            used_pct = max(0.0, 100.0 * (total - remaining) / total)
        account.windows = [
            QuotaWindow(
                label="qwen token plan credits",
                used_percent=used_pct,
                remaining_percent=None if used_pct is None else max(0.0, 100.0 - used_pct),
                refill_capacity=total,
                refill_capacity_unit="credits",
            )
        ]
    return account


def _payg_account(raw: Any, *, timeout: float) -> AccountUsage | None:
    if not isinstance(raw, dict):
        return None
    limit = _billing_limit(timeout)
    if limit is None:
        return None
    limit_usd, currency = limit
    total_raw = raw.get("total")
    total = total_raw if isinstance(total_raw, dict) else {}
    used = coerce_float(total.get("cost"))
    if used is None:
        return None
    used_pct = 100.0 * used / limit_usd if limit_usd > 0 else None
    return AccountUsage(
        source="qwencloud",
        provider="qwencloud",
        plan="PAYG",
        billing_kind=BillingKind.PAYG_API,
        usage_credits=UsageCredits(
            used=used,
            limit=limit_usd,
            remaining=max(0.0, limit_usd - used) if limit_usd > 0 else None,
            currency=currency or "USD",
            used_percent=used_pct,
        ),
        notes=["Month-to-date spend against the qwencloud billing limit."],
    )


def _billing_limit(timeout: float) -> tuple[float, str] | None:
    """Best-effort `qwencloud billing limit`; never fatal to the main parse."""
    try:
        payload = run_json(["qwencloud", "billing", "limit", "--format", "json"], timeout=timeout)
    except CollectorError:
        return None
    if not isinstance(payload, dict) or str(payload.get("status") or "") != "active":
        return None
    amount = coerce_float(payload.get("limitAmount"))
    if amount is None or amount <= 0:
        return None
    currency = str(payload.get("currency") or "USD")
    return amount, currency


def _window(raw: Any, label: str, minutes: int) -> QuotaWindow | None:
    if not isinstance(raw, dict):
        return None
    used_pct = coerce_float(raw.get("used_pct"))
    if used_pct is None:
        remaining = coerce_float(raw.get("remaining"))
        total = coerce_float(raw.get("total"))
        if remaining is not None and total is not None and total > 0:
            used_pct = max(0.0, 100.0 * (total - remaining) / total)
        else:
            return None
    return QuotaWindow(
        label=label,
        used_percent=used_pct,
        remaining_percent=max(0.0, 100.0 - used_pct),
        resets_at=parse_dt(raw.get("next_reset_at")),
        window_minutes=minutes,
        refill_capacity=coerce_float(raw.get("total")),
        refill_capacity_unit="credits",
        raw=dict(raw),
    )
