"""Deterministic rich chat-format renderer (``--for-chat`` / ``--format chat``).

Produces a structured, emoji-classified report suitable for Telegram, Discord,
Slack, and other messaging clients.  No ANSI escape sequences, no hard wraps,
no LLM tokens — pure code rendering from the same ``Snapshot`` + ``alerts``
data the pretty terminal renderer uses.

Spec: https://github.com/djbclark/aiuse/issues/29
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from aiuse.analysis.pace import (
    cursor_breakdown_window,
    format_pace,
    human_window_label,
    independent_pool_key,
    is_cursor_on_demand_window,
    overall_pace,
    pool_key_for_window,
    pool_scope_label,
    window_pace,
)
from aiuse.models import (
    OPERATOR_APPROVAL_NOTICE,
    AccountUsage,
    BillingKind,
    PaceProfile,
    QuotaWindow,
    RoutingContext,
    Snapshot,
    UseOrLoseAlert,
    canonical_provider,
    classify_window_minutes,
    claude_model_scope,
    parse_dt,
    provider_display_name,
)
from aiuse.timefmt import exhaustion_lead_seconds, format_clock, lead_phrase

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


def _format_english_list(items: list[str]) -> str:
    """Format a list of strings into proper English (e.g., A, B, and C)."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


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
    """Whether the pace data projects exhaustion before the window resets.

    ``projected_exhaust_at`` alone is not sufficient evidence. It is set even
    for a window already at 0% (where it lands in the past) and for one being
    consumed slower than sustainably, so trusting it unguarded produced claims
    that contradicted their own numbers — "Pace: `0.00×` normal — projected to
    exhaust before reset". Burning below 1.0× cannot exhaust a window by its
    own reset, so require the ratio to support the claim. The plain report
    guards the same forecast differently (see ``_forecast_fragment``), which is
    why the two outputs used to disagree about the same alert.
    """
    if pace is None:
        return False
    if pace.pace_ratio is None or pace.pace_ratio < 1.0:
        return False
    if pace.projected_used_fraction is not None and pace.projected_used_fraction >= 1.0:
        return True
    return pace.projected_exhaust_at is not None and resets_at is not None and pace.projected_exhaust_at < resets_at


def _projection_disagrees_with_ratio(pace: PaceProfile | None) -> bool:
    """Projection says exhaustion, the ratio beside it says otherwise.

    ``projected_used_fraction`` is blended with learned history burn rates
    while ``pace_ratio`` reports the current window, so the two legitimately
    diverge — a window idle right now whose history says it always burns out.
    Reporting the projection *as if the printed ratio implied it* is what
    produced "Pace: `0.00×` normal — projected to exhaust before reset".
    """
    if pace is None or pace.pace_ratio is None:
        return False
    if pace.pace_ratio >= 1.0:
        return False
    return pace.projected_used_fraction is not None and pace.projected_used_fraction >= 1.0


def pace_interpretation(
    pace: PaceProfile | None,
    resets_at: datetime | None,
) -> str | None:
    """Human-readable one-line pace projection, or None if no projection to report."""
    if pace is None or pace.pace_ratio is None:
        return None

    if _projected_exhaustion_before_reset(pace, resets_at):
        phrase = "Projected to exhaust before reset"
        lead = pace.exhaustion_lead_seconds
        if lead is None:
            lead = exhaustion_lead_seconds(pace.projected_exhaust_at, resets_at)
        if pace.projected_exhaust_at is not None and lead is not None:
            when = format_clock(pace.projected_exhaust_at, weekday=True)
            phrase = f"Projected to exhaust ~{when}{lead_phrase(lead)}"
        if pace.has_overage:
            phrase += " and overage may create real spending"
        return phrase

    if _projection_disagrees_with_ratio(pace):
        phrase = "History projects exhaustion before reset"
        if pace.has_overage:
            phrase += " and overage may create real spending"
        return phrase

    return None


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
        if days < 1:
            # A carried 5-hour reset is an estimate, but it is hours away, not "~1d".
            return f"~{max(1, round(days * 24))}h"
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
        "now",
        "pace_percent",
        "pace_early",
        "grant",
    )

    def __init__(
        self,
        provider: str,
        account: str | None,
        window: QuotaWindow,
        alert: UseOrLoseAlert | None,
        now: datetime | None = None,
        grant: str | None = None,
    ) -> None:
        self.provider = provider
        self.account = account
        self.window = window
        self.alert = alert
        self.remaining = window.remaining() if window.remaining() is not None else 0.0
        self.cadence = _cadence_label(window)
        self.is_monthly = _is_monthly_window(window)
        self.governing_warning: str | None = None
        self.now = now
        # Dollar fragment of an expiring grant (omp); its reset is an expiry.
        self.grant = grant

        # Pace data.
        pace = alert.pace if alert else None
        pace_ratio = pace.pace_ratio if pace else None

        self.emoji = status_emoji(self.remaining, pace_ratio)
        self.pace_line = pace_interpretation(pace, window.resets_at)
        paced: tuple[int | None, bool] = window_pace(window, now) if now is not None else (None, False)
        self.pace_percent, self.pace_early = paced

    @property
    def heading(self) -> str:
        """Bold heading line: **Provider · account · cadence**."""
        return _chat_heading(self.provider, self.account, human_window_label(self.window.label))

    @property
    def status_line(self) -> str:
        """First continuation: remaining + reset."""
        rem = _format_remaining(self.remaining)
        estimated = not self.window.reset_time_is_precise()
        deadline = _chat_deadline(
            self.window.days_until_reset(self.now),
            estimated=estimated,
        )
        verb = "expires" if self.grant else "resets"
        reset_part = deadline if deadline.startswith("reset") else f"{verb} in {deadline}"
        parts = [f"{rem} left", *([self.grant] if self.grant else []), reset_part]
        if self.pace_percent is not None:
            parts.append(f"pace {format_pace(self.pace_percent, self.pace_early)}")
        return f"`{' · '.join(parts)}`"

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
# Pool grouping: one entry per account, or per independent pool within it
# ---------------------------------------------------------------------------


class _PoolEntry:
    """The windows of one account that share a single allotment pool.

    This is the unit the report renders, and it is deliberately the same unit
    the usage table renders as one row: an account, or one hard-separated pool
    within it (Antigravity Gemini vs Claude/GPT, Cursor total with auto /
    third-party breakdown). Rendering one entry per *window* instead made chat disagree with
    the table about how many things there even are — four agy entries against
    two table rows.
    """

    __slots__ = ("provider", "account", "pool_id", "rows")

    def __init__(
        self,
        provider: str,
        account: str | None,
        pool_id: str,
        rows: list[_WindowRow],
    ) -> None:
        self.provider = provider
        self.account = account
        self.pool_id = pool_id
        self.rows = rows

    @property
    def scope(self) -> str | None:
        """Pool name, or None for the residual (single-pool) group."""
        return pool_scope_label(self.pool_id)

    @property
    def emoji(self) -> str:
        """Worst status among the pool's windows.

        A pool whose weekly budget is exhausted reads as exhausted even while
        its 5-hour window shows headroom — the shorter window is carved out of
        the longer one, so the bad news governs.
        """
        shared = [r for r in self.rows if claude_model_scope(r.window.label) is None]
        if not shared:
            return EMOJI_INFO
        worst = shared[0].emoji
        for row in shared[1:]:
            worst = _worst_emoji(worst, row.emoji)
        return worst

    @property
    def heading(self) -> str:
        """Bold heading line: **provider · account · pool**."""
        return _chat_heading(self.provider, self.account, self.scope)

    def sort_key(self) -> tuple[Any, ...]:
        """Order by the entry's most severe window, so the worst float up."""
        shared = [r for r in self.rows if claude_model_scope(r.window.label) is None]
        return min(row.sort_key() for row in shared or self.rows)


def display_pool_key(window: QuotaWindow, *, provider: str | None = None) -> str:
    """Group breakdown rows under their ceiling pool for chat rendering."""
    slug = pool_key_for_window(window, provider=provider)
    if slug in ("cursor_auto", "cursor_third_party"):
        return "cursor_total"
    return slug


def _group_rows_into_pools(rows: list[_WindowRow]) -> list[_PoolEntry]:
    """Collapse per-window rows into one entry per account/pool."""
    entries: dict[tuple[str, str | None, str], _PoolEntry] = {}
    order: list[tuple[str, str | None, str]] = []
    for row in rows:
        key = (row.provider, row.account, display_pool_key(row.window, provider=row.provider))
        entry = entries.get(key)
        if entry is None:
            entry = _PoolEntry(row.provider, row.account, key[2], [])
            entries[key] = entry
            order.append(key)
        entry.rows.append(row)

    result = [entries[key] for key in order]
    for entry in result:
        # Shortest clock first, matching the table's 5H → WEEK → MONTH columns.
        entry.rows.sort(key=lambda r: (_duration_sort_key(r.window), r.window.label.casefold()))
    return result


# ---------------------------------------------------------------------------
# Governing-window detection
# ---------------------------------------------------------------------------


def _apply_governing_warnings(rows: list[_WindowRow]) -> None:
    """Annotate exhausted governing windows whose siblings still show capacity.

    Within each account/pool, finds the longest-duration window and adds the
    governing-budget warning if it is exhausted while shorter siblings still
    report capacity.
    """
    for entry in _group_rows_into_pools(rows):
        pool_rows = [r for r in entry.rows if claude_model_scope(r.window.label) is None]
        if len(pool_rows) < 2:
            continue
        # Find the longest-duration window (governing candidate).
        governing = max(pool_rows, key=lambda r: r.window.window_minutes or 0)
        if governing.remaining > 0:
            continue  # Governing is not exhausted; no warning needed.
        siblings_with_capacity = [r for r in pool_rows if r is not governing and r.remaining > 0]
        if siblings_with_capacity:
            sibs_str = _format_english_list([f"'{r.window.label}'" for r in siblings_with_capacity])
            governing.governing_warning = f"The shorter {sibs_str} windows may still show open capacity, but they draw from this same exhausted '{governing.window.label}' budget."


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

    # 3: All weekly windows.
    for row in rows:
        if id(row) in seen:
            continue
        cadence = classify_window_minutes(row.window.window_minutes)
        if cadence == "weekly":
            selected.append(row)
            seen.add(id(row))

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

    missing_str = _format_english_list(missing_names)
    verb = "are" if len(missing_names) > 1 else "is"

    if has_names:
        has_str = _format_english_list(has_names)
        return f"aiuse reports a true monthly window for {has_str}. Note: {missing_str} {verb} reported on a weekly cadence. Pacing is shown against these weekly windows instead of inventing monthly data."

    return f"Note: {missing_str} {verb} reported on a weekly cadence. Pacing is shown against these weekly windows instead of inventing monthly data."


# ---------------------------------------------------------------------------
# Action section
# ---------------------------------------------------------------------------


def _alert_pace(
    alert: UseOrLoseAlert,
    sub_rows: list[_WindowRow] | None,
    now: datetime | None,
) -> tuple[int | None, bool]:
    """Signed pace percent and early flag for an alert, resolving the QuotaWindow when possible."""
    if sub_rows and now:
        for r in sub_rows:
            if (r.provider, r.account, r.window.label) == (alert.provider, alert.account, alert.window_label):
                return r.pace_percent, r.pace_early
    if alert.pace and alert.pace.pace_ratio is not None:
        pct = round((alert.pace.pace_ratio - 1) * 100)
        early = alert.pace.elapsed_fraction is not None and alert.pace.elapsed_fraction < 0.10
        return pct, early
    return None, False


def _alert_pace_percent(
    alert: UseOrLoseAlert,
    sub_rows: list[_WindowRow] | None,
    now: datetime | None,
) -> int | None:
    """Signed pace percent for an alert, resolving the QuotaWindow when possible."""
    pct, _ = _alert_pace(alert, sub_rows, now)
    return pct


def _build_action_items(
    alerts: list[UseOrLoseAlert],
    routing_context: RoutingContext | None = None,
    sub_rows: list[_WindowRow] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Build deterministic action items from alerts.

    Priority:
    1. Exhausted governing windows.
    2. High-urgency conserve alerts.
    3. High-urgency burn alerts.
    4. Medium-urgency conserve alerts.
    5. Medium-urgency burn candidates.
    """
    items: list[str] = []
    # Keyed by independent pool, not merely by account: agy's Gemini and
    # Claude/GPT budgets are separate things to act on, and the usage table
    # already treats them as separate rows.
    seen_pools: set[tuple[str, str | None, str]] = set()

    if routing_context is not None and sub_rows is not None:
        primary_prov = routing_context.primary_provider
        primary_rows = [
            r
            for r in sub_rows
            if r.provider == primary_prov
            and (
                (scope := claude_model_scope(r.window.label)) is None
                or scope.casefold() in routing_context.primary_model.casefold()
            )
        ]
        if primary_rows:
            min_remaining = min(r.remaining for r in primary_rows)
            name = provider_display_name(primary_prov)
            rem = _format_remaining(min_remaining)
            if min_remaining >= 25:
                items.append(f"Keep {name} as primary: it has `{rem}` remaining, above the `25%` stability threshold")
            else:
                if routing_context.fallback_provider:
                    fallback_name = provider_display_name(routing_context.fallback_provider)
                    items.append(
                        f"Switch primary to {fallback_name}: {name} is below the `25%` stability threshold (`{rem}` remaining)"
                    )
                else:
                    items.append(
                        f"Primary {name} is below the `25%` stability threshold (`{rem}` remaining), but no fallback is configured"
                    )

        if routing_context.fallback_provider:
            name = provider_display_name(routing_context.fallback_provider)
            items.append(f"Use {name} as the free fallback")

    # Sort alerts into priority buckets.
    buckets: list[list[UseOrLoseAlert]] = [[] for _ in range(5)]

    for a in alerts:
        if a.kind == "prepaid":
            continue
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
            pool_id = independent_pool_key(a.window_label, provider=a.provider) or ""
            pool_key = (a.provider, a.account, pool_id)
            # Deduplicate per pool (keep the most urgent alert in each).
            if pool_key in seen_pools:
                continue
            seen_pools.add(pool_key)

            name = provider_display_name(a.provider)
            scope = pool_scope_label(pool_id)
            acct = f" · {a.account}" if a.account else ""
            if scope:
                acct += f" · {scope}"
            rem = _format_remaining(a.remaining_percent)
            pace_pct, pace_early = _alert_pace(a, sub_rows, now)
            gate = f" — {OPERATOR_APPROVAL_NOTICE}" if a.requires_operator_approval else ""

            if a.kind == "conserve":
                # Nothing left to conserve — the plain report tags these `empty`
                # rather than advising pace, and this must say the same thing.
                if a.remaining_percent < 1:
                    items.append(f"{name}{acct} is exhausted{gate}")
                    continue
                pace_s = f" and pace `{format_pace(pace_pct, pace_early)}`" if pace_pct is not None else ""
                items.append(f"Conserve {name}{acct}: `{rem}` remains{pace_s}{gate}")
            elif a.kind == "burn":
                pace_s = f" pacing at `{format_pace(pace_pct, pace_early)}`" if pace_pct is not None else ""
                if a.remaining_percent < 1:
                    items.append(f"{name}{acct} is exhausted{gate}")
                else:
                    items.append(f"{name}{acct} has `{rem}` remaining{pace_s}; capacity may be wasted{gate}")

    return items


# ---------------------------------------------------------------------------
# Prepaid row rendering
# ---------------------------------------------------------------------------


def _render_prepaid_row(account: AccountUsage, *, include_account: bool = True) -> list[str]:
    """Render a single prepaid/API balance row."""
    lines: list[str] = []
    name = provider_display_name(account.provider)
    acct_part = f" · {account.account}" if include_account and account.account else ""

    from aiuse.report import _api_inventory_note, dollar_meter

    if canonical_provider(account.provider) == "bedrock":
        # Pay-as-you-go spend, or UNKNOWN when Cost Explorer reported $0.
        lines.append(f"{EMOJI_INFO} **{name}{acct_part}**: `{_api_inventory_note(account)}`")
        return lines

    uc = account.usage_credits
    spend_up = (
        account.billing_kind == BillingKind.PAYG_API
        and uc is not None
        and uc.used is not None
        and account.balance_usd is None
    )
    if spend_up or account.balance_usd is not None or account.credits_remaining is not None:
        if account.balance_usd is not None and account.balance_usd < 0:
            lines.append(
                f"{EMOJI_PREPAID} **{name}{acct_part}**: `{dollar_meter('down', 'prepaid', account.balance_usd)}` (negative reported)"
            )
        elif not spend_up and (
            account.balance_usd == 0 or (account.balance_usd is None and account.credits_remaining == 0)
        ):
            lines.append(f"{EMOJI_PREPAID} **{name}{acct_part}**: `empty`")
        elif account.credits_remaining is not None and account.credits_remaining < 0 and account.balance_usd is None:
            lines.append(
                f"{EMOJI_PREPAID} **{name}{acct_part}**: `↓ {account.credits_remaining:g} credits` (negative reported)"
            )
        else:
            lines.append(f"{EMOJI_PREPAID} **{name}{acct_part}**: `{_api_inventory_note(account)}`")
    else:
        lines.append(f"{EMOJI_PREPAID} **{name}{acct_part}**: API")

    if account.requires_operator_approval:
        lines.append(f"   ↳ {OPERATOR_APPROVAL_NOTICE}")
    return lines


def _row_notes(row: _WindowRow, *, provider: str, windows: list[QuotaWindow]) -> list[str]:
    """Continuation lines attached to a single window."""
    notes: list[str] = []
    from aiuse.analysis.ceilings import ceiling_binding_note

    bind = ceiling_binding_note(row.window, provider, windows)
    if bind:
        notes.append(bind)
    model = claude_model_scope(row.window.label)
    if model is not None and model.casefold() != "fable":
        notes.append(f"{model} draws from the shared weekly; not a separate quota")
    if row.pace_line:
        notes.append(row.pace_line)
    if row.remaining <= 0 and not row.pace_line and not row.governing_warning:
        notes.append("Exhausted")
    if row.governing_warning:
        notes.append(row.governing_warning)
    return notes


def _window_line_label(row: _WindowRow, scope: str | None) -> str:
    """Window label with a redundant pool prefix removed.

    Inside a ``gemini`` entry the label "Gemini 5-hour" only needs to say
    "5-hour" — the heading already carries the pool.
    """
    label = row.window.label
    shown = label
    if scope and label.casefold().startswith(scope.casefold()):
        trimmed = label[len(scope) :].strip(" ·-—")
        if trimmed:
            shown = trimmed
    return human_window_label(shown)


def _chat_heading(provider: str, account: str | None, *extra: str | None, include_account: bool = True) -> str:
    parts = [provider_display_name(provider)]
    if include_account and account:
        parts.append(account)
    parts.extend(part for part in extra if part)
    return f"**{' · '.join(parts)}**"


def _is_fable_row(row: _WindowRow) -> bool:
    model = claude_model_scope(row.window.label)
    return model is not None and model.casefold() == "fable"


def _chat_includes_account(accounts: list[AccountUsage]) -> bool:
    """A repeated account name does not help a person choose a pool."""
    names = {(account.account or "").strip() for account in accounts}
    names.discard("")
    if "default" in {name.casefold() for name in names}:
        names = {name for name in names if name.casefold() != "default"}
    return len(names) > 1


def _render_fable_block(row: _WindowRow) -> list[str]:
    """Fable as its own human block: allowance, reset, Fable-cap pace, no score."""
    rem = _format_remaining(row.remaining)
    estimated = not row.window.reset_time_is_precise()
    deadline = _chat_deadline(row.window.days_until_reset(row.now), estimated=estimated)
    reset_part = deadline if deadline.startswith("reset") else f"resets in {deadline}"
    parts = [f"{rem} of Fable cap left", reset_part]
    if row.pace_percent is not None:
        parts.append(f"Fable-cap pace {format_pace(row.pace_percent, row.pace_early)}")
    lines = [
        f"{EMOJI_INFO} **Claude · Fable allowance · weekly**",
        f"   ↳ `{' · '.join(parts)}`",
        (
            "   ↳ Fable can use up to half of the plan's shared weekly allowance. "
            "It consumes that allowance faster than other Claude models. "
            "This is not extra quota, and other Claude usage can leave less available."
        ),
        "   ↳ Also limited by Claude's shared weekly allowance.",
    ]
    if row.remaining <= 0:
        lines.append(
            "   ↳ Fable's included allowance is used up. "
            "Switch to another Claude model if shared allowance remains, "
            "or continue Fable with usage credits."
        )
    return lines


def _render_pool_entry(
    entry: _PoolEntry,
    now: datetime | None = None,
    *,
    account_windows: list[QuotaWindow] | None = None,
    include_account: bool = True,
    operator_gate: bool = False,
    rate_gate: Mapping[str, Any] | None = None,
) -> list[str]:
    """Render one account/pool. Single-window pools stay in the compact form.

    ``operator_gate`` adds the operator-approval notice right under the status
    line (under the heading for a multi-window entry); ``rate_gate`` adds the
    shared-Claude-window omp gate the same way.
    """
    gate_lines = [f"   ↳ {OPERATOR_APPROVAL_NOTICE}"] if operator_gate else []
    if rate_gate is not None:
        reset = parse_dt(rate_gate.get("resets_at"))
        until = format_clock(reset, date=True) if reset is not None else str(rate_gate.get("resets_at") or "?")
        gate_lines.append(
            f"   ↳ 🚫 Rate-gated: shared Claude {rate_gate.get('window_label') or 'window'} exhausted until {until} — "
            "credits cannot be spent before it resets"
        )
    headline_windows = [r.window for r in entry.rows if not cursor_breakdown_window(r.window, provider=entry.provider)]
    overall, overall_early = (
        overall_pace(headline_windows, now) if now is not None and headline_windows else (None, False)
    )
    pace_suffix = f" · pace {format_pace(overall, overall_early)}" if overall is not None else ""

    if len(entry.rows) == 1:
        row = entry.rows[0]
        lines = [
            f"{row.emoji} {_chat_heading(row.provider, row.account, human_window_label(row.window.label), include_account=include_account)}{pace_suffix}",
            f"   ↳ {row.status_line}",
            *gate_lines,
        ]
        scope = account_windows or [r.window for r in entry.rows]
        lines.extend(f"   ↳ {note}" for note in _row_notes(row, provider=row.provider, windows=scope))
        return lines

    lines = [
        f"{entry.emoji} {_chat_heading(entry.provider, entry.account, entry.scope, include_account=include_account)}{pace_suffix}",
        *gate_lines,
    ]
    scope = account_windows or [r.window for r in entry.rows]
    for row in entry.rows:
        lines.append(f"   ↳ {_window_line_label(row, entry.scope)} — {row.status_line}")
        # Deeper indent so a pace line is unambiguously about the window above
        # it rather than the entry as a whole.
        lines.extend(f"     · {note}" for note in _row_notes(row, provider=row.provider, windows=scope))
    return lines


def _grant_fragment(account: AccountUsage, window: QuotaWindow) -> str | None:
    """``↑ $0.73 spent · ↓ $249.27 left of $250.00 plan`` for an expiring grant.

    Only accounts that report ``spent_usd`` (omp's Claude API credits) carry
    one; every other window keeps its plain status line. The spent figure
    counts up through the grant. The left figure counts down and is part of
    the monthly plan, not pay-as-you-go.
    """
    if account.spent_usd is None:
        return None
    from aiuse.report import dollar_meter

    parts = [f"↑ ${account.spent_usd:.2f} spent"]
    if account.balance_usd is not None:
        total = (
            float(window.refill_capacity) if window.refill_capacity_unit == "usd" and window.refill_capacity else None
        )
        parts.append(dollar_meter("down", "plan", account.balance_usd, total))
    return " · ".join(parts)


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


def _has_reportable_usage(account: AccountUsage) -> bool:
    """Whether an account carries anything this report can render.

    Mirrors ``report._account_has_usage`` so the two formats agree on which
    accounts count as having data at all.
    """
    return (
        bool(account.windows)
        or account.balance_usd is not None
        or account.credits_remaining is not None
        or account.usage_credits is not None
    )


def _render_routing(routing: RoutingContext) -> str:
    lines = ["🎯 **ROUTING**", ""]
    primary_prov = provider_display_name(routing.primary_provider)
    primary = f"`{routing.primary_model}` via `{primary_prov}`"
    lines.append(f"Primary: {primary}")
    if routing.fallback_model and routing.fallback_provider:
        fallback_prov = provider_display_name(routing.fallback_provider)
        fallback = f"`{routing.fallback_model}` via `{fallback_prov}`"
        lines.append(f"Fallback: {fallback}")
    return "\n".join(lines)


def render_chat_report(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    routing_context: RoutingContext | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> str:
    """Deterministic rich chat-format renderer.

    Produces structured output with emoji status markers, pacing language,
    governing-window warnings, and action recommendations.  No ANSI codes,
    no hard wraps, no LLM tokens.
    """
    sections: list[str] = []

    # --- Header ---
    ts = format_clock(snapshot.collected_at, date=True)
    sections.append(f"🤖 **AI USAGE · {ts}**\n━━━━━━━━━━━━━━━━━━━━")

    for provider, reason in sorted((getattr(snapshot, "disabled_services", None) or {}).items()):
        sections.append(f"🚫 **Disabled by operator:** {provider_display_name(provider)} — {reason}")

    for key, reason in sorted((getattr(snapshot, "hidden_pools", None) or {}).items()):
        sections.append(f"🙈 **Hidden by operator:** {key} — {reason}")

    raw_analysis = (config or {}).get("analysis")
    analysis_cfg = raw_analysis if isinstance(raw_analysis, dict) else {}
    show_total_rows = bool(analysis_cfg.get("show_total_rows", False))

    if routing_context is not None:
        sections.append(_render_routing(routing_context))

    # --- Build alert index ---
    alert_idx = _build_alert_index(alerts)

    # --- Partition accounts ---
    sub_accounts: list[AccountUsage] = []
    prepaid_accounts: list[AccountUsage] = []
    error_accounts: list[AccountUsage] = []
    no_data_accounts: list[AccountUsage] = []

    for account in snapshot.accounts:
        if account.error:
            error_accounts.append(account)
        elif account.billing_kind in (BillingKind.PREPAID_BALANCE, BillingKind.PAYG_API):
            prepaid_accounts.append(account)
        elif _has_reportable_usage(account):
            sub_accounts.append(account)
        else:
            # Collected cleanly but produced nothing — no windows, no balance.
            # These used to fall into ``sub_accounts``, contribute no rows, and
            # disappear from the report entirely, so chat silently disagreed
            # with the usage table about which services exist.
            no_data_accounts.append(account)

    # --- Build subscription window rows ---
    sub_rows: list[_WindowRow] = []
    now = snapshot.collected_at
    from aiuse.report import _hide_ceiling_window

    for account in sub_accounts:
        for window in account.windows:
            if not show_total_rows and _hide_ceiling_window(account, window, config):
                continue
            if is_cursor_on_demand_window(window, provider=account.provider):
                continue
            alert = _find_alert(alert_idx, account.provider, account.account, window.label)
            row = _WindowRow(
                account.provider, account.account, window, alert, now=now, grant=_grant_fragment(account, window)
            )
            sub_rows.append(row)

    # Apply governing-window warnings.
    _apply_governing_warnings(sub_rows)

    # Sort subscription rows.
    sub_rows.sort(key=lambda r: r.sort_key())

    # --- Render subscription windows, one entry per account/pool ---
    pool_entries = _group_rows_into_pools(sub_rows)
    pool_entries.sort(key=lambda e: e.sort_key())
    windows_by_account = {(a.provider, a.account): a.windows for a in sub_accounts}
    gated_accounts = {(a.provider, a.account) for a in sub_accounts if a.requires_operator_approval}
    rate_gates = {(a.provider, a.account): a.rate_gated_by for a in sub_accounts if a.rate_gated_by is not None}
    include_account = _chat_includes_account(snapshot.accounts)
    if pool_entries:
        lines = ["📊 **SUBSCRIPTION WINDOWS**"]
        for entry in pool_entries:
            fable_rows = [row for row in entry.rows if _is_fable_row(row)]
            entry.rows = [row for row in entry.rows if not _is_fable_row(row)]
            acct_windows = windows_by_account.get((entry.provider, entry.account), [])
            if entry.rows:
                lines.extend(
                    _render_pool_entry(
                        entry,
                        now=now,
                        account_windows=acct_windows,
                        include_account=include_account,
                        operator_gate=(entry.provider, entry.account) in gated_accounts,
                        rate_gate=rate_gates.get((entry.provider, entry.account)),
                    )
                )
                if entry.provider == "cursor" and entry.scope == "auto":
                    host = next(
                        (
                            account
                            for account in sub_accounts
                            if account.provider == "cursor" and account.account == entry.account
                        ),
                        None,
                    )
                    if host is not None:
                        from aiuse.report import _extra_usage_wallet_fragment

                        wallet = _extra_usage_wallet_fragment(host).strip(" ·")
                        if wallet:
                            lines.append(f"   ↳ {wallet}")
            for row in fable_rows:
                lines.extend(_render_fable_block(row))
        sections.append("\n".join(lines))

    # --- Render prepaid/API balances ---
    if prepaid_accounts:
        prepaid_accounts.sort(key=_prepaid_sort_key)
        lines = [f"{EMOJI_PREPAID} **PREPAID / API BALANCES**"]
        for account in prepaid_accounts:
            lines.extend(_render_prepaid_row(account, include_account=include_account))
        sections.append("\n".join(lines))

    # --- Action section ---
    action_items = _build_action_items(
        alerts,
        routing_context=routing_context,
        sub_rows=sub_rows,
        now=now,
    )
    if action_items:
        lines = ["📌 **ACTION**"]
        for item in action_items:
            lines.append(f"   ↳ {item}")
        sections.append("\n".join(lines))

    # --- Errors ---
    if error_accounts or no_data_accounts or snapshot.collector_errors:
        lines = ["⚠️ **ERRORS**"]
        for err in snapshot.collector_errors:
            lines.append(f"   ↳ {err}")
        for acc in error_accounts:
            name = provider_display_name(acc.provider)
            who = acc.account or "default"
            lines.append(f"   ↳ {name} ({who}): {acc.error}")
        for acc in no_data_accounts:
            name = provider_display_name(acc.provider)
            who = acc.account or "default"
            lines.append(f"   ↳ {name} ({who}): no usage data")
        sections.append("\n".join(lines))

    if pool_entries:
        sections.append("+ ahead / - behind · ~ = early · ↑$ PAYG counts up · ↓$ plan or prepaid counts down")

    if not sections[1:]:  # Only header, no data.
        sections.append("No usage data collected.")

    return "\n\n".join(sections)
