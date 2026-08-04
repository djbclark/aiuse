"""Deterministic rich chat-format renderer (``--for-chat`` / ``--format chat``).

Produces a structured, emoji-classified report suitable for Telegram, Discord,
Slack, and other messaging clients.  No ANSI escape sequences, no hard wraps,
no LLM tokens — pure code rendering from the same ``Snapshot`` + ``alerts``
data the pretty terminal renderer uses.

Spec: https://github.com/djbclark/aiuse/issues/29
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiuse.analysis.pace import independent_pool_key
from aiuse.models import (
    AccountUsage,
    BillingKind,
    PaceProfile,
    QuotaWindow,
    Snapshot,
    UseOrLoseAlert,
    classify_window_minutes,
    provider_display_name,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMOJI_GREEN = "🟢"
EMOJI_YELLOW = "🟡"
EMOJI_ORANGE = "🟠"
EMOJI_RED = "🔴"
EMOJI_PREPAID = "💳"
EMOJI_INFO = "ℹ️"

# Severity ordering (higher = worse).
_SEVERITY = {EMOJI_GREEN: 0, EMOJI_YELLOW: 1, EMOJI_ORANGE: 2, EMOJI_RED: 3}

# Maximum action items in the ACTION section.
MAX_ACTION_ITEMS = 5

# Window-duration sort order (shorter first).
_DURATION_ORDER = {"5h": 0, "daily": 1, "weekly": 2, "monthly": 3}


# ---------------------------------------------------------------------------
# Status emoji helpers
# ---------------------------------------------------------------------------


def _remaining_emoji(remaining: float) -> str:
    """Emoji from remaining-% bands."""
    if remaining < 10:
        return EMOJI_RED
    if remaining < 25:
        return EMOJI_ORANGE
    if remaining < 70:
        return EMOJI_YELLOW
    return EMOJI_GREEN


def _pace_emoji(pace_ratio: float | None) -> str | None:
    """Emoji override from pace ratio (may be None = no override)."""
    if pace_ratio is None:
        return None
    if pace_ratio > 1.30:
        return EMOJI_ORANGE
    if pace_ratio > 1.10:
        return EMOJI_YELLOW
    if pace_ratio < 0.80:
        return EMOJI_GREEN
    return None


def _worst_emoji(a: str, b: str | None) -> str:
    """Return the more-severe of two emoji statuses."""
    if b is None:
        return a
    return a if _SEVERITY.get(a, 0) >= _SEVERITY.get(b, 0) else b


def status_emoji(
    remaining: float,
    pace_ratio: float | None = None,
) -> str:
    """Composite status emoji using severity aggregation."""
    rem_emoji = _remaining_emoji(remaining)
    pce_emoji = _pace_emoji(pace_ratio)
    return _worst_emoji(rem_emoji, pce_emoji)


# ---------------------------------------------------------------------------
# Pace interpretation language
# ---------------------------------------------------------------------------


def _projected_exhaustion_before_reset(
    pace: PaceProfile | None,
    resets_at: datetime | None,
) -> bool:
    """Whether the pace data projects exhaustion before the window resets."""
    if pace is None:
        return False
    if pace.projected_used_fraction is not None and pace.projected_used_fraction >= 1.0:
        return True
    if pace.projected_exhaust_at is not None and resets_at is not None and pace.projected_exhaust_at < resets_at:
        return True
    return False


def pace_interpretation(
    pace: PaceProfile | None,
    resets_at: datetime | None,
) -> str | None:
    """Human-readable one-line pace interpretation, or None if no pace data."""
    if pace is None or pace.pace_ratio is None:
        return None

    ratio_s = f"`{pace.pace_ratio:.2f}×` normal"

    exhausts = _projected_exhaustion_before_reset(pace, resets_at)

    if exhausts:
        phrase = "projected to exhaust before reset"
        if pace.has_overage:
            phrase += " and overage may create real spending"
        return f"Pace: {ratio_s} — {phrase}"

    if pace.projected_waste_fraction is not None and pace.projected_waste_fraction >= 0.20:
        return f"Pace: {ratio_s} — likely unused capacity at reset"

    if pace.pace_ratio < 0.80:
        return f"Pace: {ratio_s} — likely unused capacity at reset"

    if pace.pace_ratio > 1.30:
        return f"Pace: {ratio_s} — significantly ahead of sustainable pace"

    if pace.pace_ratio > 1.10:
        return f"Pace: {ratio_s} — slightly ahead of sustainable pace"

    return f"Pace: {ratio_s} — roughly on sustainable pace"


# ---------------------------------------------------------------------------
# Human-friendly deadline (chat-specific: no ~ prefix for non-estimated)
# ---------------------------------------------------------------------------


def _chat_deadline(days: float | None, *, estimated: bool = False) -> str:
    """Compact reset-time string for chat output."""
    if days is None:
        return "reset time unavailable"
    if days <= 0:
        return "reset imminent"
    if estimated:
        whole = max(1, int(days + 0.5))
        return f"~{whole}d" if whole < 3 else f"~{whole} days"
    # Sub-day → hours+minutes.
    total_hours = days * 24
    if total_hours < 24:
        h = int(total_hours)
        m = int((total_hours - h) * 60)
        if m > 0:
            return f"{h}h {m}m"
        return f"{h}h"
    # 1+ days.
    d = int(days)
    remainder_hours = int((days - d) * 24)
    if remainder_hours > 0:
        return f"{d}d {remainder_hours}h"
    return f"{d}d"


def _format_remaining(remaining: float) -> str:
    """Format remaining percentage for chat display."""
    if remaining <= 0:
        return "0%"
    if remaining < 1.0:
        return "<1%"
    return f"{remaining:.0f}%"


# ---------------------------------------------------------------------------
# Window classification
# ---------------------------------------------------------------------------


def _is_monthly_window(window: QuotaWindow) -> bool:
    cadence = classify_window_minutes(window.window_minutes)
    if cadence == "monthly":
        return True
    if window.window_minutes is not None and window.window_minutes >= 28 * 24 * 60:
        return True
    return False


def _cadence_label(window: QuotaWindow) -> str:
    """Short cadence string for the heading line."""
    cadence = classify_window_minutes(window.window_minutes)
    return cadence or "window"


def _duration_sort_key(window: QuotaWindow) -> int:
    cadence = classify_window_minutes(window.window_minutes)
    return _DURATION_ORDER.get(cadence or "", 99)


# ---------------------------------------------------------------------------
# Alert lookup
# ---------------------------------------------------------------------------


def _build_alert_index(
    alerts: list[UseOrLoseAlert],
) -> dict[tuple[str, str | None, str], UseOrLoseAlert]:
    """Index alerts by (provider, account, window_label) for fast lookup."""
    idx: dict[tuple[str, str | None, str], UseOrLoseAlert] = {}
    for a in alerts:
        key = (a.provider, a.account, a.window_label)
        # Keep highest-score alert if duplicates exist.
        if key not in idx or a.score > idx[key].score:
            idx[key] = a
    return idx


def _find_alert(
    idx: dict[tuple[str, str | None, str], UseOrLoseAlert],
    provider: str,
    account: str | None,
    window_label: str,
) -> UseOrLoseAlert | None:
    return idx.get((provider, account, window_label))


# ---------------------------------------------------------------------------
# Row model: one entry in the rendered report
# ---------------------------------------------------------------------------


class _WindowRow:
    """Intermediate presentation model for one account+window."""

    __slots__ = (
        "provider",
        "account",
        "window",
        "alert",
        "remaining",
        "emoji",
        "cadence",
        "is_monthly",
        "governing_warning",
        "pace_line",
    )

    def __init__(
        self,
        provider: str,
        account: str | None,
        window: QuotaWindow,
        alert: UseOrLoseAlert | None,
    ) -> None:
        self.provider = provider
        self.account = account
        self.window = window
        self.alert = alert
        self.remaining = window.remaining() if window.remaining() is not None else 0.0
        self.cadence = _cadence_label(window)
        self.is_monthly = _is_monthly_window(window)
        self.governing_warning: str | None = None

        # Pace data.
        pace = alert.pace if alert else None
        pace_ratio = pace.pace_ratio if pace else None

        self.emoji = status_emoji(self.remaining, pace_ratio)
        self.pace_line = pace_interpretation(pace, window.resets_at)

    @property
    def heading(self) -> str:
        """Bold heading line: **Provider · account · cadence**."""
        parts = [provider_display_name(self.provider)]
        if self.account:
            parts.append(self.account)
        parts.append(self.window.label)
        return f"**{' · '.join(parts)}**"

    @property
    def status_line(self) -> str:
        """First continuation: remaining + reset."""
        rem = _format_remaining(self.remaining)
        estimated = not self.window.reset_time_is_precise()
        deadline = _chat_deadline(
            self.window.days_until_reset(),
            estimated=estimated,
        )
        return f"`{rem} left · resets in {deadline}`"

    def severity_key(self) -> int:
        """Numeric severity for sorting (higher = worse)."""
        return _SEVERITY.get(self.emoji, 0)

    def sort_key(self) -> tuple[Any, ...]:
        """Full sort tuple for ordering within a section."""
        urgency_rank = 0
        if self.alert:
            urgency_rank = {
                "CRITICAL": 5,
                "HIGH": 4,
                "MEDIUM": 3,
                "LOW": 2,
                "INFO": 1,
                "NONE": 0,
            }.get(self.alert.urgency.name, 0)

        resets_at = self.window.resets_at
        # Nulls sort last.
        resets_at_key = resets_at.timestamp() if resets_at else float("inf")

        return (
            -self.severity_key(),  # worst first
            -urgency_rank,
            resets_at_key,  # soonest first
            provider_display_name(self.provider).casefold(),
            (self.account or "").casefold(),
            _duration_sort_key(self.window),
            self.window.label.casefold(),
        )


# ---------------------------------------------------------------------------
# Governing-window detection
# ---------------------------------------------------------------------------


def _apply_governing_warnings(rows: list[_WindowRow]) -> None:
    """Annotate exhausted governing windows whose siblings still show capacity.

    Groups rows by (provider, account), finds the longest-duration window in
    each group, and adds the governing-budget warning if it is exhausted while
    shorter siblings have remaining capacity.
    """
    # Group rows by (provider, account).
    groups: dict[tuple[str, str | None], list[_WindowRow]] = {}
    for row in rows:
        key = (row.provider, row.account)
        groups.setdefault(key, []).append(row)

    for group_rows in groups.values():
        if len(group_rows) < 2:
            continue

        # Partition into independent pools if applicable (e.g. Antigravity
        # Gemini vs Claude/GPT, Cursor Included vs Other Models).
        pools: dict[str | None, list[_WindowRow]] = {}
        for row in group_rows:
            pool_key = independent_pool_key(row.window.label)
            pools.setdefault(pool_key, []).append(row)

        for pool_rows in pools.values():
            if len(pool_rows) < 2:
                continue
            # Find the longest-duration window (governing candidate).
            governing = max(pool_rows, key=lambda r: r.window.window_minutes or 0)
            if governing.remaining > 0:
                continue  # Governing is not exhausted; no warning needed.
            siblings_with_capacity = [r for r in pool_rows if r is not governing and r.remaining > 0]
            if siblings_with_capacity:
                governing.governing_warning = (
                    "The shorter windows may still show open capacity, but they draw this same exhausted budget."
                )


# ---------------------------------------------------------------------------
# Long-cycle / monthly highlight selection
# ---------------------------------------------------------------------------


def _select_long_cycle_highlights(
    rows: list[_WindowRow],
    alerts: list[UseOrLoseAlert],
) -> list[_WindowRow]:
    """Select rows for the MONTHLY / LONG-CYCLE OUTLOOK section.

    This section *repeats* selected windows from the main list (it does not
    remove them).  Criteria:
    1. True monthly windows.
    2. Governing parent windows (longest duration in their pool).
    3. Weekly windows with important pacing alerts (projected exhaust / conserve).
    4. Weekly windows likely to waste significant capacity.
    """
    selected: list[_WindowRow] = []
    seen: set[int] = set()

    for row in rows:
        if id(row) in seen:
            continue
        # 1. Monthly windows.
        if row.is_monthly:
            selected.append(row)
            seen.add(id(row))
            continue
        # 2. Has a governing warning → it's a governing parent.
        if row.governing_warning:
            selected.append(row)
            seen.add(id(row))
            continue

    # 3 & 4: Weekly windows with notable alerts.
    for row in rows:
        if id(row) in seen:
            continue
        cadence = classify_window_minutes(row.window.window_minutes)
        if cadence != "weekly":
            continue
        if row.alert is None:
            continue
        # Projected to exhaust → conserve alert.
        if row.alert.kind == "conserve":
            selected.append(row)
            seen.add(id(row))
            continue
        # Significant waste projected.
        pace = row.alert.pace
        if pace and pace.projected_waste_fraction is not None and pace.projected_waste_fraction >= 0.20:
            selected.append(row)
            seen.add(id(row))
            continue

    return selected


# ---------------------------------------------------------------------------
# Monthly pacing note
# ---------------------------------------------------------------------------


def _monthly_pacing_note(
    sub_rows: list[_WindowRow],
    long_cycle_rows: list[_WindowRow],
) -> str | None:
    """Emit note when important providers lack true monthly windows.

    Only emitted when:
    1. The report has a long-cycle section.
    2. At least one important subscription provider has no true monthly window.
    3. The provider appears in the report's action-relevant rows.
    """
    if not long_cycle_rows:
        return None

    # Providers that DO have a monthly window.
    monthly_providers: set[str] = set()
    all_providers: set[str] = set()

    for row in sub_rows:
        all_providers.add(row.provider)
        if row.is_monthly:
            monthly_providers.add(row.provider)

    # Providers that appear in the long-cycle section (i.e. are "important").
    important_providers = {row.provider for row in long_cycle_rows}

    # Important providers without monthly data.
    missing = important_providers - monthly_providers
    if not missing:
        return None

    has_monthly = monthly_providers & important_providers
    missing_names = sorted(provider_display_name(p) for p in missing)
    has_names = sorted(provider_display_name(p) for p in has_monthly)

    if has_names:
        has_part = f"aiuse reports a true monthly window for {', '.join(has_names)}, but "
        miss_part = f"{' and '.join(missing_names)} {'are' if len(missing_names) > 1 else 'is'} reported as weekly windows, so weekly pacing is shown instead of inventing monthly data"
        return f"{has_part}{miss_part}"

    miss_part = f"{' and '.join(missing_names)} {'are' if len(missing_names) > 1 else 'is'} reported as weekly windows; weekly pacing is shown instead of inventing monthly data"
    return miss_part


# ---------------------------------------------------------------------------
# Action section
# ---------------------------------------------------------------------------


def _build_action_items(alerts: list[UseOrLoseAlert]) -> list[str]:
    """Build deterministic action items from alerts.

    Priority:
    1. Exhausted governing windows.
    2. High-urgency conserve alerts.
    3. High-urgency burn alerts.
    4. Medium-urgency conserve alerts.
    5. Medium-urgency burn candidates.
    """
    items: list[str] = []
    seen_pools: set[tuple[str, str | None]] = set()

    # Sort alerts into priority buckets.
    buckets: list[list[UseOrLoseAlert]] = [[] for _ in range(5)]

    for a in alerts:
        if a.kind == "prepaid":
            continue
        pool_key = (a.provider, a.account)
        urgency_high = a.urgency.name in ("CRITICAL", "HIGH")
        urgency_med = a.urgency.name == "MEDIUM"

        # Bucket 0: exhausted governing (remaining ~0 and it's a long window).
        cadence = classify_window_minutes(a.window_minutes)
        if a.remaining_percent < 1 and cadence == "monthly":
            buckets[0].append(a)
        elif urgency_high and a.kind == "conserve":
            buckets[1].append(a)
        elif urgency_high and a.kind == "burn":
            buckets[2].append(a)
        elif urgency_med and a.kind == "conserve":
            buckets[3].append(a)
        elif urgency_med and a.kind == "burn":
            buckets[4].append(a)

    for bucket in buckets:
        for a in bucket:
            if len(items) >= MAX_ACTION_ITEMS:
                break
            pool_key = (a.provider, a.account)
            # Deduplicate by provider+account (keep most urgent per pool).
            if pool_key in seen_pools:
                continue
            seen_pools.add(pool_key)

            name = provider_display_name(a.provider)
            acct = f" · {a.account}" if a.account else ""
            rem = _format_remaining(a.remaining_percent)

            if a.kind == "conserve":
                pace_s = ""
                if a.pace and a.pace.pace_ratio is not None:
                    pace_s = f" and pace is `{a.pace.pace_ratio:.2f}×` normal"
                items.append(f"Conserve {name}{acct}: `{rem}` remains{pace_s}")
            elif a.kind == "burn":
                pace_s = ""
                if a.pace and a.pace.pace_ratio is not None:
                    pace_s = f" pacing at `{a.pace.pace_ratio:.2f}×`"
                if a.remaining_percent < 1:
                    items.append(f"{name}{acct} is exhausted")
                else:
                    items.append(f"{name}{acct} has `{rem}` remaining{pace_s}; capacity may be wasted")

    return items


# ---------------------------------------------------------------------------
# Prepaid row rendering
# ---------------------------------------------------------------------------


def _render_prepaid_row(account: AccountUsage) -> list[str]:
    """Render a single prepaid/API balance row."""
    lines: list[str] = []
    name = provider_display_name(account.provider)
    acct_part = f" · {account.account}" if account.account else ""

    lines.append(f"{EMOJI_PREPAID} **{name}{acct_part}**")

    if account.balance_usd is not None:
        bal = account.balance_usd
        if bal < 0:
            lines.append(f"   ↳ `-${abs(bal):.2f}` balance · no expiry")
            lines.append("   ↳ Negative balance reported; do not treat this as available capacity")
        else:
            lines.append(f"   ↳ `Balance: ${bal:.2f}` · no expiry")
    elif account.credits_remaining is not None:
        lines.append(f"   ↳ `{account.credits_remaining:g} credits remaining` · no expiry")
    else:
        lines.append("   ↳ API balance · no expiry")

    return lines


def _prepaid_sort_key(account: AccountUsage) -> tuple[Any, ...]:
    """Sort prepaid rows: negative/zero first, then lowest positive, then highest."""
    bal = account.balance_usd
    if bal is None:
        bal = float("inf")  # Unknown balances sort last.
    if bal <= 0:
        return (0, bal)  # Negative/zero first.
    return (1, bal)  # Positive, ascending.


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------


def render_chat_report(snapshot: Snapshot, alerts: list[UseOrLoseAlert]) -> str:
    """Deterministic rich chat-format renderer.

    Produces structured output with emoji status markers, pacing language,
    governing-window warnings, and action recommendations.  No ANSI codes,
    no hard wraps, no LLM tokens.
    """
    sections: list[str] = []

    # --- Header ---
    ts = snapshot.collected_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections.append(f"🤖 **AI USAGE · {ts}**\n━━━━━━━━━━━━━━━━━━━━")

    # --- Build alert index ---
    alert_idx = _build_alert_index(alerts)

    # --- Partition accounts ---
    sub_accounts: list[AccountUsage] = []
    prepaid_accounts: list[AccountUsage] = []
    error_accounts: list[AccountUsage] = []

    for account in snapshot.accounts:
        if account.error:
            error_accounts.append(account)
        elif account.billing_kind in (BillingKind.PREPAID_BALANCE, BillingKind.PAYG_API):
            prepaid_accounts.append(account)
        else:
            sub_accounts.append(account)

    # --- Build subscription window rows ---
    sub_rows: list[_WindowRow] = []
    for account in sub_accounts:
        for window in account.windows:
            alert = _find_alert(alert_idx, account.provider, account.account, window.label)
            row = _WindowRow(account.provider, account.account, window, alert)
            sub_rows.append(row)

    # Apply governing-window warnings.
    _apply_governing_warnings(sub_rows)

    # Sort subscription rows.
    sub_rows.sort(key=lambda r: r.sort_key())

    # --- Render subscription windows ---
    if sub_rows:
        lines = ["📊 **SUBSCRIPTION WINDOWS**"]
        for row in sub_rows:
            lines.append("")
            lines.append(f"{row.emoji} {row.heading}")
            lines.append(f"   ↳ {row.status_line}")
            if row.pace_line:
                lines.append(f"   ↳ {row.pace_line}")
            if row.remaining <= 0 and not row.pace_line and not row.governing_warning:
                lines.append("   ↳ Exhausted")
            if row.governing_warning:
                lines.append(f"   ↳ {row.governing_warning}")
        sections.append("\n".join(lines))

    # --- Render prepaid/API balances ---
    if prepaid_accounts:
        prepaid_accounts.sort(key=_prepaid_sort_key)
        lines = [f"{EMOJI_PREPAID} **PREPAID / API BALANCES**"]
        for account in prepaid_accounts:
            lines.append("")
            lines.extend(_render_prepaid_row(account))
        sections.append("\n".join(lines))

    # --- Long-cycle / monthly outlook ---
    long_cycle_rows = _select_long_cycle_highlights(sub_rows, alerts)
    if long_cycle_rows:
        # Sort: exhausted governing first, then high-risk, then waste.
        long_cycle_rows.sort(key=lambda r: r.sort_key())
        lines = ["📅 **MONTHLY / LONG-CYCLE OUTLOOK**"]
        for row in long_cycle_rows:
            lines.append("")
            lines.append(f"{row.emoji} {row.heading}")
            lines.append(f"   ↳ {row.status_line}")
            if row.pace_line:
                lines.append(f"   ↳ {row.pace_line}")
            if row.governing_warning:
                lines.append(f"   ↳ {row.governing_warning}")
        sections.append("\n".join(lines))

    # --- Monthly pacing note ---
    note = _monthly_pacing_note(sub_rows, long_cycle_rows)
    if note:
        sections.append(f"{EMOJI_INFO} **MONTHLY PACING NOTE**\n   ↳ {note}")

    # --- Action section ---
    action_items = _build_action_items(alerts)
    if action_items:
        lines = ["📌 **ACTION**"]
        for item in action_items:
            lines.append(f"   ↳ {item}")
        sections.append("\n".join(lines))

    # --- Errors ---
    if error_accounts or snapshot.collector_errors:
        lines = ["⚠️ **ERRORS**"]
        for err in snapshot.collector_errors:
            lines.append(f"   ↳ {err}")
        for acc in error_accounts:
            name = provider_display_name(acc.provider)
            who = acc.account or "default"
            lines.append(f"   ↳ {name} ({who}): {acc.error}")
        sections.append("\n".join(lines))

    if not sections[1:]:  # Only header, no data.
        sections.append("No usage data collected.")

    return "\n\n".join(sections)
