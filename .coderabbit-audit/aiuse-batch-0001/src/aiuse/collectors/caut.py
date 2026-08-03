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
    keep_copilot_report_window,
)
from aiuse.models import (
    coerce_float as _f,
)

from .base import CollectorError, run_json, which
from .codexbar import PREPAID_HINTS, _billing_kind, _slot_label, _window

# Providers caut can actually fetch rate-limit windows for on a typical Mac
# (as of caut 0.1.0 / 2026-07). Others return "unsupported source … Auto".
# See docs/collectors-caut-openusage.md.
_DEFAULT_CAUT_PROVIDERS = "both"  # claude + codex only


def collect_caut(
    *,
    providers: str = _DEFAULT_CAUT_PROVIDERS,
    timeout: float = 90.0,
) -> list[AccountUsage]:
    """Shell out to ``caut usage --json``.

    Default ``providers=both`` (claude + codex). Passing ``all`` still works but
    most providers error with unsupported Auto strategies on caut 0.1.0.

    Claude windows are flaky upstream (oauth succeeds intermittently while
    doctor still says "Auth missing"). We retry once when no live windows.
    """
    if not which("caut"):
        raise CollectorError(
            "caut not found on PATH (install: cargo install --locked "
            "--git https://github.com/Dicklesworthstone/coding_agent_usage_tracker "
            "and ensure ~/.cargo/bin is on PATH, or symlink into ~/.local/bin)"
        )

    accounts = _fetch_caut_accounts(providers=providers, timeout=timeout)
    # Upstream flake: claude-oauth sometimes returns identity-only; one retry
    # often recovers rate-limit windows without forcing claude auth login.
    if not any(_row_has_live_quota(a) for a in accounts):
        accounts = _fetch_caut_accounts(providers=providers, timeout=timeout)
        if accounts and not any(_row_has_live_quota(a) for a in accounts):
            for acc in accounts:
                acc.notes = list(acc.notes) + [
                    "caut returned no quota windows after retry — see "
                    "docs/collectors-caut-openusage.md (claude auth / codex "
                    "identity-only / unsupported providers)."
                ]
                break

    return accounts


def _fetch_caut_accounts(*, providers: str, timeout: float) -> list[AccountUsage]:
    argv = ["caut", "usage", "--json"]
    # caut default is already "both"; only pass --provider when non-default.
    if providers and providers not in ("", "both"):
        argv.extend(["--provider", providers])

    payload = run_json(argv, timeout=timeout)
    if not isinstance(payload, dict):
        raise CollectorError("caut returned non-object JSON")

    errors_raw = payload.get("errors") or []
    global_errors = [_format_caut_error(e) for e in errors_raw if e]
    # Drop expected "unsupported source … Auto" noise when probing many providers.
    useful_errors = [e for e in global_errors if "unsupported source" not in e.lower()]

    data = payload.get("data")
    if not isinstance(data, list):
        raise CollectorError("caut JSON missing data[] array")

    accounts: list[AccountUsage] = []
    for row in data:
        if isinstance(row, dict):
            accounts.append(_from_row(row))

    if not accounts and useful_errors:
        raise CollectorError("caut: " + "; ".join(useful_errors[:5]))
    if not accounts and global_errors:
        raise CollectorError("caut: " + "; ".join(global_errors[:5]))

    if useful_errors and accounts:
        note = "caut provider errors: " + "; ".join(useful_errors[:8])
        accounts[0].notes = list(accounts[0].notes) + [note]

    return accounts


def _format_caut_error(err: Any) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)


def _row_has_live_quota(account: AccountUsage) -> bool:
    if account.error:
        return False
    if account.balance_usd is not None or account.credits_remaining is not None:
        return True
    return any(w.remaining() is not None or w.used_percent is not None for w in account.windows)


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
        # caut 0.1.0 often prints this even when oauth filled rate-limit windows.
        if "auth missing" in str(warning).lower() and windows:
            notes.append(
                "caut authWarning present but quota windows were returned "
                "(upstream credential-path quirk; ignore if remaining % looks sane)."
            )
        elif "auth missing" in str(warning).lower() and not windows:
            notes.append(
                "caut has no Claude credential file it understands — try "
                "`claude auth login`, or rely on cswap/OpenUsage for Claude."
            )

    plan = None
    identity_value = usage.get("identity")
    identity = identity_value if isinstance(identity_value, dict) else {}
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
