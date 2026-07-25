"""Collect live quotas via ``caut`` (Coding Agent Usage Tracker).

``caut`` is a cross-platform CLI port of CodexBar-style usage probes. JSON
schema is ``caut.v1`` (envelope with ``data`` rows per provider).
"""

from __future__ import annotations

from typing import Any

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    coerce_float as _f,
    keep_copilot_report_window,
    parse_dt,
)

from .base import CollectorError, run_json, which
from .codexbar import PREPAID_HINTS, _billing_kind, _slot_label, _window


def collect_caut(
    *,
    providers: str = "all",
    timeout: float = 90.0,
) -> list[AccountUsage]:
    """Shell out to ``caut usage --json``.

    Default ``providers=all`` queries every caut-supported provider for
    maximum cross-check coverage (correctness over speed).
    """
    if not which("caut"):
        raise CollectorError(
            "caut not found on PATH (install: cargo install --locked "
            "--git https://github.com/Dicklesworthstone/coding_agent_usage_tracker "
            "and ensure ~/.cargo/bin is on PATH, or symlink into ~/.local/bin)"
        )

    argv = ["caut", "usage", "--json"]
    if providers and providers != "both":
        argv.extend(["--provider", providers])

    payload = run_json(argv, timeout=timeout)
    if not isinstance(payload, dict):
        raise CollectorError("caut returned non-object JSON")

    errors_raw = payload.get("errors") or []
    global_errors = [str(e) for e in errors_raw if e]

    data = payload.get("data")
    if not isinstance(data, list):
        # Some older shapes may be a bare list
        if isinstance(payload, list):
            data = payload
        else:
            raise CollectorError("caut JSON missing data[] array")

    accounts: list[AccountUsage] = []
    for row in data:
        if isinstance(row, dict):
            accounts.append(_from_row(row))

    if not accounts and global_errors:
        raise CollectorError("caut: " + "; ".join(global_errors[:5]))

    if global_errors and accounts:
        note = "caut provider errors: " + "; ".join(global_errors[:8])
        for acc in accounts:
            acc.notes = list(acc.notes) + [note]
            break  # once is enough on the snapshot path via first account notes

    return accounts


def _from_row(row: dict[str, Any]) -> AccountUsage:
    """Map one caut.v1 provider row into AccountUsage (CodexBar-shaped usage)."""
    provider = str(row.get("provider") or "unknown").lower()
    source_tag = str(row.get("source") or "unknown")
    err = row.get("error")
    if isinstance(err, dict):
        err = err.get("message") or str(err)
    if isinstance(err, str) and err:
        return AccountUsage(
            source="caut",
            provider=provider,
            account=_account_from_row(row),
            error=err,
            billing_kind=_billing_kind(provider, None),
            raw=row,
        )

    usage_value = row.get("usage")
    usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}

    windows: list[QuotaWindow] = []
    for index, key in enumerate(("primary", "secondary", "tertiary"), start=1):
        block = usage.get(key)
        if not isinstance(block, dict):
            continue
        label = _slot_label(provider, index, block)
        window = _window(label, block)
        if window:
            windows.append(window)

    if provider == "copilot":
        windows = [window for window in windows if keep_copilot_report_window(window.label)]

    balance_usd = None
    credits_remaining = None
    credits = row.get("credits")
    if isinstance(credits, dict):
        credits_remaining = _f(credits.get("remaining"))
        if credits_remaining is not None and provider.lower() in PREPAID_HINTS:
            balance_usd = credits_remaining

    notes: list[str] = [f"Live data fetched by caut via {source_tag}."]
    warning = row.get("authWarning")
    if warning:
        notes.append(str(warning))

    plan = None
    identity = usage.get("identity") if isinstance(usage.get("identity"), dict) else {}
    plan = usage.get("loginMethod") or identity.get("loginMethod") or row.get("plan")

    billing = _billing_kind(provider, usage, windows)
    if billing == BillingKind.PREPAID_BALANCE and balance_usd is None and credits_remaining is not None:
        balance_usd = credits_remaining

    # No usable windows/balance — still emit the row so identity is visible.
    if not windows and balance_usd is None and credits_remaining is None:
        if not any(usage.get(k) for k in ("primary", "secondary", "tertiary")):
            notes.append("caut returned identity/auth only (no quota windows this run).")

    return AccountUsage(
        source="caut",
        provider=provider,
        account=_account_from_row(row),
        plan=str(plan) if plan else None,
        billing_kind=billing,
        windows=windows,
        balance_usd=balance_usd,
        credits_remaining=credits_remaining,
        notes=notes,
        raw=row,
    )


def _account_from_row(row: dict[str, Any]) -> str | None:
    account = row.get("account")
    if account:
        return str(account)
    usage = row.get("usage")
    if isinstance(usage, dict):
        identity = usage.get("identity")
        if isinstance(identity, dict) and identity.get("accountEmail"):
            return str(identity["accountEmail"])
        if usage.get("accountEmail"):
            return str(usage["accountEmail"])
    return None
