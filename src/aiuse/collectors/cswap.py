"""Collect multi-account Claude Code quota status from cswap.

Primary path: ``cswap list --json`` (schema v1).

Reliability path: when JSON marks a slot ``usageStatus: unavailable`` with no
``usage`` payload, hydrate from cswap's on-disk usage cache
(``cache/usage.json`` under the claude-swap data dir). That cache still holds
``lastGood`` measurements the human ``cswap list`` view shows with an age note,
but which the JSON contract deliberately omits once the measurement ages past
decision-grade trust (``STALE_OK_S`` / ``TRUST_MAX_AGE_S``). Exhausted accounts
are especially affected: cswap postpones the next poll until reset, so the JSON
view can report ``unavailable`` for hours while the last-known 100% figure is
still correct for reporting.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    UsageCredits,
    classify_window_minutes,
    parse_dt,
)
from aiuse.models import coerce_float as _number
from aiuse.models import coerce_int as _int_or_none

from .base import CollectorError, run_json, which


def collect_cswap(*, timeout: float = 45.0) -> list[AccountUsage]:
    if not which("cswap"):
        raise CollectorError("cswap not found on PATH")

    data = run_json(["cswap", "list", "--json"], timeout=timeout)
    if not isinstance(data, dict):
        raise CollectorError("unexpected cswap list JSON shape")
    if isinstance(data.get("error"), dict):
        raise CollectorError(str(data["error"].get("message") or data["error"]))
    if data.get("schemaVersion") not in (None, 1):
        raise CollectorError(f"unsupported cswap JSON schema version: {data.get('schemaVersion')}")

    cache = _load_usage_cache()
    accounts: list[AccountUsage] = []
    for item in data.get("accounts") or []:
        if isinstance(item, dict):
            accounts.append(_account_from_item(item, data.get("activeAccountNumber"), cache=cache))
    if not accounts:
        accounts.append(
            AccountUsage(
                source="cswap",
                provider="claude",
                error="cswap has no registered Claude Code accounts",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
            )
        )
    return accounts


def _account_from_item(
    item: dict[str, Any],
    active_number: Any,
    *,
    cache: dict[str, Any] | None = None,
) -> AccountUsage:
    email = item.get("email")
    number = item.get("number")
    active = bool(item.get("active")) or (number is not None and active_number is not None and number == active_number)
    usage_status = str(item.get("usageStatus") or "unavailable")
    usage = item.get("usage") if isinstance(item.get("usage"), dict) else None

    windows: list[QuotaWindow] = []
    notes: list[str] = [f"cswap slot {number}" + ("; active" if active else "")]
    error: str | None = None
    billing_kind = BillingKind.SUBSCRIPTION_WINDOW
    usage_credits: UsageCredits | None = None

    if item.get("alias"):
        notes.append(f"cswap alias: {item['alias']}")
    if item.get("disabled"):
        notes.append("This Claude Code account is disabled in cswap rotation.")
    if item.get("organizationName"):
        notes.append(f"Claude organization: {item['organizationName']}")
    if item.get("usageFetchedAt"):
        freshness = f"Quota fetched at {item['usageFetchedAt']}"
        if item.get("usageAgeSeconds") is not None:
            freshness += f" ({float(item['usageAgeSeconds']):.0f}s old)"
        notes.append(freshness + ".")

    if usage_status != "ok":
        if usage_status == "api_key":
            billing_kind = BillingKind.PAYG_API
            notes.append("API-key account: cswap reports no Claude subscription quota.")
        else:
            detail = {
                "keychain_unavailable": "the macOS Keychain could not be read",
                "no_credentials": "this cswap slot has no readable credentials",
                "token_expired": "the active Claude token is expired",
                "relogin_required": "Claude login is required again",
                "unavailable": "live Claude quota could not be fetched",
            }.get(usage_status, f"cswap returned status {usage_status}")
            error = f"Canonical Claude usage unavailable: {detail}."

    if usage:
        windows.extend(_windows_from_usage(usage))
        usage_credits = _usage_credits_from_spend(usage)
        _append_spend_note(notes, usage_credits)

    # Display-grade recovery: when decision-grade JSON omitted usage, reuse the
    # same lastGood row the human `cswap list` view would show with an age note.
    if not windows and usage_status not in ("ok", "api_key"):
        hydrated = _hydrate_from_cache(cache, number=number, email=email)
        if hydrated is not None:
            cache_usage, age_s, fetched_at = hydrated
            windows.extend(_windows_from_usage(cache_usage))
            if usage_credits is None:
                usage_credits = _usage_credits_from_spend(cache_usage)
                _append_spend_note(notes, usage_credits)
            if windows or usage_credits is not None:
                error = None  # usable for reporting; age is called out in notes
                if age_s is not None:
                    notes.append(
                        f"Using cswap's last-known quota from local cache "
                        f"(≈{age_s:.0f}s old"
                        + (f", fetched {fetched_at}" if fetched_at else "")
                        + "); `cswap list --json` omitted it as decision-stale."
                    )
                else:
                    notes.append(
                        "Using cswap's last-known quota from local cache; "
                        "`cswap list --json` omitted it as decision-stale."
                    )

    account_name = str(email) if email else f"cswap-slot-{number}"
    # Mirror remaining credit headroom onto balance_usd for generic prepaid UI.
    balance_usd = usage_credits.remaining if usage_credits is not None else None
    return AccountUsage(
        source="cswap",
        provider="claude",
        account=account_name,
        billing_kind=billing_kind,
        windows=windows,
        balance_usd=balance_usd,
        usage_credits=usage_credits,
        error=error,
        notes=notes,
        raw=item,
    )


def _windows_from_usage(usage: dict[str, Any]) -> list[QuotaWindow]:
    windows: list[QuotaWindow] = []
    windows.extend(_named_window(usage, ("fiveHour", "five_hour"), "Claude Code 5-hour"))
    windows.extend(_named_window(usage, ("sevenDay", "seven_day", "weekly"), "Claude Code weekly"))
    windows.extend(_named_window(usage, ("monthly",), "Claude Code monthly"))

    for index, key in enumerate(("primary", "secondary", "tertiary"), start=1):
        block = usage.get(key)
        if not isinstance(block, dict):
            continue
        label = _generic_label(block, index)
        window = _window_from_block(label, block)
        if window and not _same_window_present(windows, window):
            windows.append(window)

    scoped = usage.get("scoped")
    if isinstance(scoped, list):
        for block in scoped:
            if not isinstance(block, dict):
                continue
            model_name = str(block.get("name") or "unnamed model")
            window = _window_from_block(f"Claude Code weekly — {model_name}", block)
            if window and not _same_window_present(windows, window):
                windows.append(window)

    return windows


def _usage_credits_from_spend(usage: dict[str, Any]) -> UsageCredits | None:
    """Parse cswap ``usage.spend`` (extra-usage wallet) into structured credits.

    cswap normalizes Anthropic ``extra_usage`` to currency units (cents/100):
    ``used``, ``limit``, ``pct``, optional ``resetsAt`` / ``currency``.
    """
    spend = usage.get("spend")
    if not isinstance(spend, dict):
        return None
    used = _number(spend.get("used"))
    limit = _number(spend.get("limit"))
    pct = _number(spend.get("pct") if spend.get("pct") is not None else spend.get("usedPercent"))
    currency = str(spend.get("currency") or "USD")
    resets = parse_dt(spend.get("resetsAt") or spend.get("resets_at") or spend.get("resetAt") or spend.get("reset_at"))
    if used is None and limit is None and pct is None and resets is None:
        return None
    remaining: float | None = None
    if used is not None and limit is not None:
        remaining = max(0.0, limit - used)
    elif pct is not None and limit is not None:
        remaining = max(0.0, limit * (1.0 - pct / 100.0))
    return UsageCredits(
        used=used,
        limit=limit,
        remaining=remaining,
        currency=currency,
        used_percent=pct,
        resets_at=resets,
    )


def _append_spend_note(notes: list[str], credits: UsageCredits | None) -> None:
    if credits is None:
        return
    cur = credits.currency
    if credits.used is not None and credits.limit is not None:
        notes.append(
            f"Usage credits: {credits.used:g} of {credits.limit:g} {cur} spent"
            + (f" ({credits.used_percent:g}% of limit)" if credits.used_percent is not None else "")
            + (f"; {credits.remaining:g} {cur} headroom" if credits.remaining is not None else "")
            + "."
        )
    elif credits.used is not None:
        notes.append(f"Usage credits spent: {credits.used:g} {cur}.")
    if credits.resets_at is not None:
        notes.append(f"Usage credits reset at {credits.resets_at.isoformat()}.")


_NOMINAL_MINUTES = {
    "Claude Code 5-hour": 300,
    "Claude Code weekly": 10080,
    "Claude Code monthly": 43800,
}


def _named_window(usage: dict[str, Any], keys: tuple[str, ...], label: str) -> list[QuotaWindow]:
    for key in keys:
        block = usage.get(key)
        if isinstance(block, dict):
            window = _window_from_block(label, block)
            if window and window.window_minutes is None:
                window.window_minutes = _NOMINAL_MINUTES.get(label)
            return [window] if window else []
        if isinstance(block, (int, float)):
            return [
                QuotaWindow(
                    label=label,
                    used_percent=float(block),
                    window_minutes=_NOMINAL_MINUTES.get(label),
                )
            ]
    return []


def _window_from_block(
    label: str,
    block: dict[str, Any],
    *,
    now: datetime | None = None,
) -> QuotaWindow | None:
    # cswap schema v1 uses `pct`; the other spellings keep compatibility with
    # older/future adapters without weakening cswap's authority. Cache lastGood
    # uses the internal snake_case shape (`pct` + `resets_at`).
    used = _number(
        block.get("pct")
        if block.get("pct") is not None
        else block.get("usedPercent")
        if block.get("usedPercent") is not None
        else block.get("utilization")
    )
    if used is None and "used" in block and "limit" in block:
        raw_used = _number(block.get("used"))
        limit = _number(block.get("limit"))
        if raw_used is not None and limit is not None and limit > 0:
            used = (raw_used / limit) * 100

    remaining = _number(block.get("remainingPercent"))
    if remaining is None and used is not None:
        remaining = max(0.0, 100.0 - used)

    resets = parse_dt(block.get("resetsAt") or block.get("resets_at") or block.get("resetAt") or block.get("reset_at"))
    # Prefer a live countdown from resets_at. Cached lastGood freezes countdown
    # at fetch time (e.g. "17h 8m" still present two hours later); human cswap
    # list recomputes at render — match that for reporting.
    description: str | None
    if resets is not None:
        description = _countdown_from_reset(resets, now=now)
    else:
        raw_desc = block.get("countdown") or block.get("resetDescription") or block.get("reset_description")
        description = str(raw_desc) if raw_desc else None
    if used is None and remaining is None and resets is None and not description:
        return None
    return QuotaWindow(
        label=label,
        used_percent=used,
        remaining_percent=remaining,
        resets_at=resets,
        window_minutes=_int_or_none(block.get("windowMinutes") or block.get("window_minutes")),
        reset_description=description,
        raw=block,
    )


def _countdown_from_reset(resets_at: datetime, *, now: datetime | None = None) -> str:
    """Human-style remaining time, same shape as cswap's format_reset countdown."""
    now = now or datetime.now(timezone.utc)
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)
    total_seconds = max(0, int((resets_at - now).total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _generic_label(block: dict[str, Any], index: int) -> str:
    minutes = _int_or_none(block.get("windowMinutes") or block.get("window_minutes"))
    kind = classify_window_minutes(minutes)
    if kind == "5h":
        return "Claude Code 5-hour"
    if kind == "weekly":
        return "Claude Code weekly"
    if kind == "monthly":
        return "Claude Code monthly"
    return f"Claude Code quota {index} (unnamed by cswap)"


def _same_window_present(windows: list[QuotaWindow], candidate: QuotaWindow) -> bool:
    return any(window.same_measurement(candidate) for window in windows)


# ---------------------------------------------------------------------------
# Local usage-cache recovery (display-grade, same data human `cswap list` shows)
# ---------------------------------------------------------------------------


def _cswap_data_dirs() -> list[Path]:
    """Candidate claude-swap data roots (same layout as cswap's paths.py)."""
    dirs: list[Path] = []
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        dirs.append(Path(xdg) / "claude-swap")
    # Linux/WSL default under XDG; harmless to probe on macOS.
    dirs.append(Path.home() / ".local" / "share" / "claude-swap")
    # macOS / Windows legacy root (and current default on this machine).
    dirs.append(Path.home() / ".claude-swap-backup")
    # De-dupe while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for path in dirs:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _load_usage_cache() -> dict[str, Any] | None:
    """Load ``cache/usage.json`` if present; never raises into the collector."""
    for root in _cswap_data_dirs():
        path = root / "cache" / "usage.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _hydrate_from_cache(
    cache: dict[str, Any] | None,
    *,
    number: Any,
    email: Any,
) -> tuple[dict[str, Any], float | None, str | None] | None:
    """Return ``(usage_dict, age_seconds, fetched_at_iso)`` from lastGood, if any."""
    if not cache:
        return None
    accounts = cache.get("accounts")
    if not isinstance(accounts, dict):
        return None

    row: dict[str, Any] | None = None
    if number is not None and str(number) in accounts and isinstance(accounts[str(number)], dict):
        row = accounts[str(number)]
    elif email:
        email_l = str(email).lower()
        for candidate in accounts.values():
            if isinstance(candidate, dict) and str(candidate.get("email") or "").lower() == email_l:
                row = candidate
                break
    if row is None:
        return None

    last_good = row.get("lastGood") or row.get("last_good")
    if not isinstance(last_good, dict) or not last_good:
        return None

    fetched_at = row.get("fetchedAt") if row.get("fetchedAt") is not None else row.get("fetched_at")
    age_s: float | None = None
    fetched_iso: str | None = None
    if isinstance(fetched_at, (int, float)):
        age_s = max(0.0, time.time() - float(fetched_at))
        try:
            fetched_iso = datetime.fromtimestamp(float(fetched_at), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            fetched_iso = None

    return last_good, age_s, fetched_iso
