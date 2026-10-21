"""Collect account quotas from the Paseo agent daemon.

``paseo usage ls --json`` (Paseo 0.12+) lists per-account quota windows and
balances for every provider the daemon has logins for, served from the
daemon's five-minute host cache — one cheap local call, no vendor API
traffic. A reading is as of the daemon's last successful refresh
(``successfulAt``), which drives source arbitration like any other
collector. A missing ``paseo`` binary returns no rows (the collector is optional).
Daemon-down exits 1 on stderr and surfaces as a collector error;
per-account login problems (``status: unavailable``) are paseo's to fix and
emit no row here.

Only providers aiuse already tracks through a direct collector are mapped
(``_PROVIDER_BY_SOURCE_ID``); everything else the daemon reports is skipped:

- ``cursor``: aiuse's Cursor rows are CodexBar pool families; an
  account-level balance from paseo would fork the pool world
  (docs/cursor-quota.md).
- ``omp``: omp rows need the operator-gate annotations and override
  calibration wired into the CodexBar/admin-api paths
  (docs/omp-cloud-credits.md).
- ``opencode``: OpenCode Go has a native collector with lapsed-plan
  detection (docs/opencode-go-quota.md).
- source ids with no aiuse provider (``pi``, ``muse``, …) cannot be reported;
  ``muse`` specifically stays native-only so its own collector keeps priority.
"""

from __future__ import annotations

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

from .base import run_json, which

_PROVIDER_BY_SOURCE_ID: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "copilot": "copilot",
    "grok": "grok",
    "antigravity": "antigravity",
}

_WINDOW_KIND_BY_ID = {"five_hour": "5h", "weekly": "weekly", "monthly": "monthly"}


def collect_paseo(*, timeout: float = 30.0) -> list[AccountUsage]:
    if not which("paseo"):
        # Optional, like qwencloud: machines without the daemon stay quiet.
        return []

    payload = run_json(["paseo", "usage", "ls", "--json"], timeout=timeout)
    entries = payload if isinstance(payload, list) else [payload]
    accounts: list[AccountUsage] = []
    for entry in entries:
        if isinstance(entry, dict):
            account = _from_entry(entry)
            if account is not None:
                accounts.append(account)
    return accounts


def _from_entry(entry: dict[str, Any]) -> AccountUsage | None:
    provider = _PROVIDER_BY_SOURCE_ID.get(str(entry.get("sourceId") or ""))
    report = entry.get("report")
    if provider is None or not isinstance(report, dict) or report.get("status") != "available":
        return None

    windows = [
        window
        for raw in report.get("windows") or []
        if isinstance(raw, dict) and (window := _window(provider, raw)) is not None
    ]

    notes = ["Paseo daemon usage report (five-minute host cache; `paseo usage ls`)."]
    for balance in report.get("balances") or []:
        if isinstance(balance, dict):
            note = _balance_note(balance)
            if note:
                notes.append(note)

    account_field = entry.get("account")
    return AccountUsage(
        source="paseo",
        provider=provider,
        account=account_field.get("label") if isinstance(account_field, dict) else None,
        plan=report.get("planLabel"),
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW if windows else BillingKind.UNKNOWN,
        windows=windows,
        notes=notes,
        collected_at=parse_dt(entry.get("successfulAt")) or parse_dt(entry.get("fetchedAt")),
        raw={key: value for key, value in entry.items() if key != "icon"},  # icon is a large SVG
    )


def _window(provider: str, raw: dict[str, Any]) -> QuotaWindow | None:
    used = coerce_float(raw.get("usedPct"))
    remaining = coerce_float(raw.get("remainingPct"))
    if remaining is None and used is not None:
        remaining = max(0.0, 100.0 - used)
    if used is None and remaining is None:
        return None

    window_id = str(raw.get("id") or "")
    label = _label(provider, window_id, str(raw.get("label") or window_id or "quota"))
    if provider == "copilot" and not keep_copilot_report_window(label):
        return None
    return QuotaWindow(
        label=label,
        used_percent=used,
        remaining_percent=remaining,
        window_minutes=WINDOW_NOMINAL_MINUTES.get(_window_kind(window_id)),
        resets_at=parse_dt(raw.get("resetsAt")),
        raw=raw,
    )


def _window_kind(window_id: str) -> str | None:
    if window_id in _WINDOW_KIND_BY_ID:
        return _WINDOW_KIND_BY_ID[window_id]
    # weekly_model_fable & co. are model sublimits of the weekly window.
    if window_id.startswith("weekly_model_"):
        return "weekly"
    return None


def _label(provider: str, window_id: str, label: str) -> str:
    if provider == "claude":
        model = window_id.removeprefix("weekly_model_")
        if model != window_id:
            return f"Claude Code weekly — {model.replace('_', ' ').title()}"
        kind = _window_kind(window_id)
        if kind == "5h":
            return "Claude Code 5-hour"
        if kind == "weekly":
            return "Claude Code weekly"
        if kind == "monthly":
            return "Claude Code monthly"
        return f"Claude Code {label}"
    if provider == "copilot":
        return f"GitHub Copilot {label}"
    return f"{provider.replace('-', ' ').title()} {label or window_id}"


def _balance_note(balance: dict[str, Any]) -> str | None:
    unit = str(balance.get("unit") or "").strip()
    remaining = coerce_float(balance.get("remaining"))
    if remaining is not None:
        return f"paseo balance {balance.get('id')}: {remaining:g} {unit} left"
    used = coerce_float(balance.get("used"))
    if used is not None:
        return f"paseo balance {balance.get('id')}: {used:g} {unit} used"
    return None
