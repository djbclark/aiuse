"""Collect subscription usage from tokscale (complementary to codexbar)."""

from __future__ import annotations

import re
from typing import Any

from aiuse.models import (
    WINDOW_NOMINAL_MINUTES,
    AccountUsage,
    BillingKind,
    QuotaWindow,
    coerce_float,
    keep_copilot_report_window,
    parse_dt,
)

from .base import CollectorError, run_json, which


def collect_tokscale(*, timeout: float = 45.0) -> list[AccountUsage]:
    if not which("tokscale"):
        raise CollectorError("tokscale not found on PATH")

    payload = run_json(["tokscale", "usage", "--json"], timeout=timeout)
    rows = payload if isinstance(payload, list) else [payload]
    accounts: list[AccountUsage] = []
    for row in rows:
        if isinstance(row, dict):
            accounts.append(_from_row(row))
    return accounts


def _from_row(row: dict[str, Any]) -> AccountUsage:
    provider = str(row.get("provider") or "unknown")
    # Normalize for merge/dedupe with codexbar
    provider_key = provider.lower().replace(" ", "-")

    windows: list[QuotaWindow] = []
    for m in row.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        used = coerce_float(m.get("used_percent"))
        remaining = coerce_float(m.get("remaining_percent"))
        if remaining is None and used is not None:
            remaining = max(0.0, 100.0 - used)
        label = _metric_label(provider_key, str(m.get("label") or "unnamed quota"))
        if provider_key == "copilot" and not keep_copilot_report_window(label):
            continue
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used,
                remaining_percent=remaining,
                window_minutes=_infer_window_minutes(provider_key, m.get("label", ""), label),
                resets_at=parse_dt(m.get("resets_at")),
                reset_description=m.get("remaining_label"),
                raw=m,
            )
        )

    notes: list[str] = ["Live data fetched by tokscale for selection and cross-checking."]
    credit_status = row.get("credit_status")
    if isinstance(credit_status, dict):
        if credit_status.get("balance") is not None:
            notes.append(f"credit_balance={credit_status.get('balance')}")
        if credit_status.get("has_credits"):
            notes.append("has_credits=true")
        if credit_status.get("overage_limit_reached"):
            notes.append("overage_limit_reached")

    reset_credits = row.get("reset_credits")
    if isinstance(reset_credits, dict) and reset_credits.get("available_count") is not None:
        notes.append(f"reset_credits={reset_credits['available_count']}")

    return AccountUsage(
        source="tokscale",
        provider=provider_key,
        account=row.get("email"),
        plan=row.get("plan"),
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW if windows else BillingKind.UNKNOWN,
        windows=windows,
        notes=notes,
        raw=row,
    )


def _metric_label(provider: str, label: str) -> str:
    key = label.lower()
    if provider == "codex" and key == "weekly":
        return "Codex weekly quota"
    if provider == "copilot":
        return {
            "chat": "GitHub Copilot chat messages",
            "completions": "GitHub Copilot completions",
            "premium": "GitHub Copilot premium requests",
        }.get(key, f"GitHub Copilot {label}")
    if provider == "grok-build" and key == "weekly":
        return "Grok weekly quota"
    return f"{provider.replace('-', ' ').title()} {label}"


def _infer_window_minutes(provider: str, raw_label: str, display_label: str) -> int | None:
    key = (raw_label or "").lower().strip()
    provider_key = (provider or "").lower().replace(" ", "-")
    # Copilot label vocabulary only — do not apply to other providers.
    if provider_key == "copilot" and key in ("chat", "completions", "premium"):
        return WINDOW_NOMINAL_MINUTES["monthly"]
    if key in ("session", "5h", "5-hour", "5 hour") or "5-hour" in key or "5 hour" in key:
        return WINDOW_NOMINAL_MINUTES["5h"]
    # Avoid matching "15h" / "45h" via bare "5h" substring.
    if re.search(r"(?<![0-9])5h(?![0-9a-z])", key):
        return WINDOW_NOMINAL_MINUTES["5h"]
    if key in ("weekly",) or "week" in key:
        return WINDOW_NOMINAL_MINUTES["weekly"]
    if provider_key == "copilot":
        return WINDOW_NOMINAL_MINUTES["monthly"]
    return None
