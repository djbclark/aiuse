"""Human-readable terminal report (pretty by default)."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TextIO

from aiuse.analysis.history import (
    compute_learned_burn_rates,
    history_section_lines,
    should_learn_from_history,
)
from aiuse.analysis.pace import (
    POOL_SCOPE_LABELS,
    compute_pace,
    governing_partition,
    independent_pool_key,
    partition_independent_pools,
)
from aiuse.analysis.use_or_lose import DAYS_PER_MONTH, _classify_flexibility, _compute_value_at_risk
from aiuse.models import (
    AccountUsage,
    BillingKind,
    CrossCheck,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    canonical_provider,
    classify_window_minutes,
    infer_window_clock,
    provider_config_key,
    provider_display_name,
    utcnow,
)

URGENCY_ICON = {
    Urgency.CRITICAL: "!!!",
    Urgency.HIGH: "!! ",
    Urgency.MEDIUM: "!  ",
    Urgency.LOW: ".  ",
    Urgency.INFO: "i  ",
    Urgency.NONE: "   ",
}

# Action plan is always last so the terminal lands on it after `aiuse` returns.
# Target: entire last plan block fits on a typical 24-row viewport without
# scrolling back (header of the block + ~22 body lines ≈ 23 lines total).
ACTION_PLAN_MAX_LINES = 23
ACTION_PLAN_WIDTH = 80

# Clock columns, in the order they appear in the usage table. There is
# deliberately no "daily" column: the data model buckets durations into these
# three only (see models.classify_window_minutes), and a provider's short
# rate-limit window is the 5h one even when its docs call it a daily cap.
CLOCK_COLUMNS: tuple[tuple[str, str], ...] = (("5h", "5H"), ("weekly", "WEEK"), ("monthly", "MONTH"))
# Widest the usage table is allowed to grow, however wide the terminal is.
# Past this the eye loses the row when scanning left to right.
TABLE_MAX_WIDTH = 110

# Designed SCOPE abbreviations for the first identity-squish stage. Blind
# truncation ("gemin", "gmai") is worse than the full word; only these tokens
# are used, and only when width requires it.
_SCOPE_SHORT = {
    "gemini": "gem",
    "claude/gpt": "c/gpt",
    "other models": "oth",
}


@dataclass
class _MatrixRow:
    """One line of the usage table: a single account, or one pool within it.

    ``clocks`` maps a clock key ("5h" / "weekly" / "monthly") to the cell for
    that column. ``note`` replaces the whole numeric tail for rows that have no
    windows to bucket — failed fetches and non-expiring prepaid balances.
    """

    sort_key: tuple
    band: int
    service: str
    account: str
    account_full: str | None = None
    scope: str = "—"
    clocks: dict[str, _ClockCell] = field(default_factory=dict)
    value_usd: float | None = None
    note: str | None = None


@dataclass
class _ClockCell:
    """A percentage for one clock column, plus how much to trust it."""

    used_percent: float
    inferred: bool = False  # clock bucket was guessed, not declared
    folded: int = 0  # extra windows on this clock, collapsed into this one
    days_until_reset: float | None = None
    reset_estimated: bool = False


@dataclass(frozen=True)
class _ResetSpan:
    """A reset distance split so the largest unit can be highlighted.

    ``plain()`` is ``prefix + head + tail`` with no separator: ``2d14h``,
    ``2h27m``, ``6m``, ``~12d``, ``now``.
    """

    prefix: str = ""
    head: str = ""
    tail: str = ""

    def plain(self) -> str:
        return f"{self.prefix}{self.head}{self.tail}"


@dataclass
class _MatrixLayout:
    """Which optional pieces of the clock matrix the current width can afford."""

    show_value: bool = True
    compact_deadline: bool = False
    short_scope: bool = False
    show_scope: bool = True
    show_account: bool = True
    fold_identity: bool = False


def terminal_width(default: int = ACTION_PLAN_WIDTH) -> int:
    """Usable terminal columns, honoring COLUMNS and falling back when piped.

    ``shutil.get_terminal_size`` already prefers ``$COLUMNS`` and falls back to
    its ``fallback`` argument when stdout is not a tty, which is what we want
    for pipes and CI capture.
    """
    try:
        cols = shutil.get_terminal_size(fallback=(default, 24)).columns
    except (OSError, ValueError):
        return default
    # A hostile or unset COLUMNS can report 0; keep a floor the table can use.
    return max(40, cols)


# Compact "at a glance" trailer: at most this many alert lines per provider.
BRIEF_MAX_LINES_PER_PROVIDER = 3


class _Style:
    """ANSI colors when stdout is a TTY and color is not disabled."""

    # Zebra-stripe background: 256-color dark gray (visible on dark terminals,
    # subtle enough on light ones).  Reset-bg is \033[49m so we only undo the
    # background without clobbering other attributes.
    _ZEBRA_BG = "48;5;236"

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, t: str) -> str:
        return self._wrap("1", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)

    def green(self, t: str) -> str:
        return self._wrap("32", t)

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)

    def magenta(self, t: str) -> str:
        return self._wrap("35", t)

    def zebra_bg(self, text: str, width: int = 0) -> str:
        """Wrap *text* in a subtle dark-gray background for zebra striping.

        Embedded ``\\033[0m`` resets (from nested ``_wrap`` calls) are replaced
        with ``\\033[0;48;5;236m`` so the background persists through styled
        spans.  A final ``\\033[49m`` (BG-only reset) closes the stripe.

        When *width* > 0 the visible line is right-padded with spaces so the
        background color extends edge-to-edge across the table.

        Falls back to plain text when color is disabled — rows remain perfectly
        readable without any visual cue since the data itself is unmodified.
        """
        if not self.enabled:
            return text
        bg_on = f"\033[{self._ZEBRA_BG}m"
        # Replace full resets inside the line with reset + re-apply BG so
        # styled cells (bold, red, dim …) do not punch holes in the stripe.
        inner = text.replace("\033[0m", f"\033[0;{self._ZEBRA_BG}m")
        if width > 0:
            plain_len = len(_strip_ansi(inner))
            if plain_len < width:
                inner += " " * (width - plain_len)
        return f"{bg_on}{inner}\033[49m"

    def urgency(self, level: Urgency, text: str) -> str:
        if level == Urgency.CRITICAL:
            return self.bold(self.red(text))
        if level == Urgency.HIGH:
            return self.red(text)
        if level == Urgency.MEDIUM:
            return self.yellow(text)
        if level == Urgency.LOW:
            return self.cyan(text)
        if level == Urgency.INFO:
            return self.dim(text)
        return text


def use_color(*, stream: TextIO | None = None, force: bool | None = None) -> bool:
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def render_report(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    *,
    config: dict[str, Any] | None = None,
    color: bool | None = None,
    traditional_summary: bool = False,
    full: bool = False,
    brief: bool = False,
    glance_width: int | None = None,
) -> str:
    """
    Pretty report.

    **Default** (and ``brief=True``): the clock matrix only (stdout), via
    ``render_clock_matrix`` — one row per account/pool, one column per reset
    clock. Meta / errors / ``ai --full`` hint belong on stderr via
    ``render_stderr_meta``.

    **Full** (``full=True``): long report with per-provider detail.

    ``brief`` is kept for CLI compatibility and is ignored when ``full=True``.
    """
    del brief  # Alias of default; retained so callers need not change overnight.
    s = _Style(use_color(force=color))
    if not full:
        width = glance_width if glance_width is not None else terminal_width()
        return render_clock_matrix(alerts, snapshot=snapshot, config=config, s=s, width=width)

    lines: list[str] = []
    width = ACTION_PLAN_WIDTH
    plan_width = glance_width if glance_width is not None else width
    accounts = _sorted_accounts(snapshot.accounts)
    n_accounts = len(accounts)
    n_actionable = sum(1 for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE))

    lines.append(s.bold("=" * width))
    title = "AI USAGE — USE IT OR LOSE IT (full)"
    lines.append(s.bold(s.cyan(title)))
    meta = f"Collected at {snapshot.collected_at.isoformat()}"
    meta += f" · {n_accounts} account{'s' if n_accounts != 1 else ''}"
    if n_actionable:
        meta += f" · {n_actionable} alert{'s' if n_actionable != 1 else ''}"
    else:
        meta += " · no burn/conserve alerts"
    lines.append(s.dim(meta))
    analysis_cfg = (config or {}).get("analysis") or {}
    if not isinstance(analysis_cfg, dict):
        analysis_cfg = {}
    lines.append(s.bold("=" * width))

    waking_hours = float(analysis_cfg.get("waking_hours_per_day", 16))
    learned_burn_rates: dict[str, tuple[float, int]] = {}
    if should_learn_from_history(analysis_cfg):
        try:
            retention = int(analysis_cfg.get("snapshot_retention_days") or 90)
        except (TypeError, ValueError):
            retention = 90
        learned_burn_rates = compute_learned_burn_rates(current=snapshot, retention_days=retention)

    lines.append("")
    lines.append(s.bold("## History"))
    lines.append(s.dim("-" * width))
    for hist_line in history_section_lines(snapshot, analysis_cfg=analysis_cfg):
        lines.append(s.dim(hist_line))

    lines.append("")
    lines.append(s.bold("## Per-provider usage"))
    lines.append(s.dim("-" * width))
    if accounts:
        for acc in accounts:
            lines.extend(_render_account(acc, s, config=config, learned_burn_rates=learned_burn_rates))
    else:
        lines.append(s.dim("  (no provider data collected)"))

    lines.append("")
    lines.append(s.bold("## Cross-checks (informational)"))
    lines.append(s.dim("-" * width))
    lines.append(
        s.dim(
            "  Tools poll at different times; multi-account Claude is cswap-only. "
            "Gaps rarely mean both tools are wrong."
        )
    )
    if snapshot.cross_checks:
        lines.extend(_render_cross_checks(snapshot.cross_checks, s))
    else:
        lines.append(s.dim("  (no overlapping live measurements were available)"))
    lines.append("")

    if snapshot.collector_errors:
        lines.append(s.bold(s.red("## Collector errors")))
        lines.append(s.dim("-" * width))
        for err in snapshot.collector_errors:
            lines.append(s.red(f"  - {err}"))
        lines.append("")

    lines.append(s.bold("## Tips"))
    lines.append(s.dim("-" * width))
    lines.extend(_tips_lines(s))
    lines.append("")
    lines.extend(
        _render_action_plan_section(
            alerts,
            s,
            width=plan_width,
            traditional_summary=traditional_summary,
            waking_hours_per_day=waking_hours,
        )
    )
    return "\n".join(lines)


# Ladder display tags. error/empty/n/a are fixed lanes; slow/mid/use share a
# continuous use-urgency continuum within the active lane.
_BAND_ERROR = 0  # could not fetch usage
_BAND_EMPTY = 1  # totally depleted
_BAND_NA = 2  # non-expiring prepaid / payg — no use-or-lose urgency
_BAND_CONSERVE = 3  # pace yourself
_BAND_MID = 4  # on pace / advisory / low urgency
_BAND_USE = 5  # important to use soon

# Sort lanes (primary key): error → empty → n/a → active continuum
_LANE_ERROR = 0
_LANE_EMPTY = 1
_LANE_NA = 2
_LANE_ACTIVE = 3

_BAND_TAG = {
    _BAND_ERROR: ("error", "red"),
    _BAND_EMPTY: ("empty", "red"),
    _BAND_NA: ("n/a  ", "dim"),
    _BAND_CONSERVE: ("slow ", "yellow"),
    _BAND_MID: ("mid  ", "cyan"),
    _BAND_USE: ("use  ", "green"),
}


def alert_priority_band(alert: UseOrLoseAlert) -> int:
    """Display tag; sort uses band lane + ``alert_use_urgency`` within the lane."""
    rem = alert.remaining_percent
    # Prepaid API balances never expire — inventory only, never empty/use tags.
    if alert.kind == "prepaid":
        return _BAND_NA
    # No capacity is always empty, regardless of an analysis urgency that may
    # be absent or stale. Prepaid was handled above because it is inventory.
    if rem <= 0.0:
        return _BAND_EMPTY
    if alert.urgency == Urgency.NONE:
        return _BAND_MID
    if alert.kind == "conserve" and rem <= 1.0:
        return _BAND_EMPTY
    if alert.kind == "conserve":
        return _BAND_CONSERVE
    if alert.urgency == Urgency.INFO:
        return _BAND_MID
    if alert.kind == "burn":
        if alert.urgency == Urgency.LOW:
            return _BAND_MID
        days = alert.days_until_reset
        if days is not None and days > 7.0:
            return _BAND_MID
        return _BAND_USE
    return _BAND_MID


def alert_use_urgency(alert: UseOrLoseAlert) -> float:
    """Higher = more urgent to use *now* (appears lower on the ladder).

    Continuum from “most empty for the longest” (low) to “burn this soon” (high).
    Display tags stay empty/n/a/slow/mid/use; error/empty/n/a sort by lane first.
    """
    rem = max(0.0, float(alert.remaining_percent))
    days = float(alert.days_until_reset) if alert.days_until_reset is not None else 30.0
    score = float(alert.score)
    days_clamped = min(max(days, 0.0), 60.0)

    # Non-expiring prepaid tokens — lane handles placement; value is secondary.
    if alert.kind == "prepaid":
        return 0.0

    if alert.kind == "conserve" or (rem <= 1.0 and alert.kind != "burn"):
        # Emptier + longer until reset → lower (top of list).
        return 8.0 + rem * 0.25 - days_clamped * 0.55 + score * 0.04

    if alert.urgency == Urgency.INFO:
        return 38.0 + rem * 0.12 - days_clamped * 0.25

    if alert.kind == "burn":
        # Higher analysis score + sooner reset + remaining to burn → bottom.
        soon = max(0.0, 1.0 - days_clamped / 14.0)
        return 55.0 + score * 0.35 + soon * 25.0 + rem * 0.12

    return 42.0 + rem * 0.18 - days_clamped * 0.3


def _account_is_non_expiring_prepaid(account: AccountUsage) -> bool:
    """Purchased API credits that roll until spent (Deepseek, OpenRouter, …)."""
    return account.billing_kind in (BillingKind.PREPAID_BALANCE, BillingKind.PAYG_API)


def _window_use_urgency(window: QuotaWindow) -> float:
    """Mild mid urgency for an on-pace window (remaining + reset)."""
    rem = float(window.remaining() or 0.0)
    days = window.days_until_reset()
    days_clamped = min(max(float(days) if days is not None else 30.0, 0.0), 60.0)
    soon = max(0.0, 1.0 - days_clamped / 14.0)
    return 42.0 + rem * 0.15 + soon * 12.0


def _unalerted_window_band(window: QuotaWindow | None) -> tuple[int, int]:
    """Band/lane for a live window that analysis did not turn into an alert.

    The full remaining-capacity range is deliberately simple here: only zero
    (or a malformed negative value) is empty; positive capacity remains an
    on-pace ``mid`` row unless pace analysis emitted a conserve/burn alert.
    """
    remaining = window.remaining() if window is not None else None
    if remaining is not None and remaining <= 0.0:
        return _BAND_EMPTY, _LANE_EMPTY
    return _BAND_MID, _LANE_ACTIVE


def _account_use_urgency(account: AccountUsage) -> float:
    """On-pace / no-alert accounts: mild mid urgency from remaining + reset."""
    # CodexBar often encodes prepaid balances as a fake 100%-remaining window
    # with no reset — never treat that as use-or-lose urgency.
    if _account_is_non_expiring_prepaid(account):
        return 0.0
    window = _pick_representative_window(account.windows)
    if window is not None:
        return _window_use_urgency(window)
    if account.balance_usd is not None or account.credits_remaining is not None:
        return 36.0
    return 40.0


def _band_sort_lane(band: int) -> int:
    """Primary ladder order: error → empty → n/a → active (slow/mid/use)."""
    if band == _BAND_ERROR:
        return _LANE_ERROR
    if band == _BAND_EMPTY:
        return _LANE_EMPTY
    if band == _BAND_NA:
        return _LANE_NA
    return _LANE_ACTIVE


def _ladder_sort_key(lane: int, urgency: float, provider: str, account: str | None) -> tuple:
    """Lane first, then urgency within the lane; stable by name."""
    return (lane, urgency, provider.casefold(), (account or "").casefold())


def _ladder_coverage_key(provider: str, account: str | None, pool_id: str = "") -> tuple[str, str, str]:
    """Provider + account + independent pool (Gemini vs Claude/GPT, etc.)."""
    return (provider.casefold(), (account or "").casefold(), pool_id)


def _pool_id_for_label(label: str | None) -> str:
    return independent_pool_key(label) or ""


def _pool_id_for_windows(windows: list[QuotaWindow]) -> str:
    for window in windows:
        key = independent_pool_key(window.label)
        if key:
            return key
    return ""


def _account_has_usage(account: AccountUsage) -> bool:
    return not account.error and (
        bool(account.windows)
        or account.balance_usd is not None
        or account.credits_remaining is not None
        or account.usage_credits is not None
    )


def _pick_representative_window(windows: list[QuotaWindow]) -> QuotaWindow | None:
    """Governing / included bar for a pool (or whole account)."""
    usable = [w for w in windows if w.remaining() is not None]
    if not usable:
        return None
    gov, _ = governing_partition(usable)
    if gov is not None:
        return gov
    usable.sort(
        key=lambda w: (
            0 if "included" in (w.label or "").casefold() else 1,
            -(w.window_minutes or 0),
            -(w.remaining() or 0),
        )
    )
    return usable[0]


def _short_account(account: str | None) -> str:
    """Compact an account for the table's ACCT column.

    Emails collapse to the first label of their domain (``djbclark@gmail.com``
    → ``gmail``), because the local part is almost always identical across a
    user's accounts and the domain is the part that distinguishes them.
    Callers must run :func:`_disambiguate_accounts` over a finished row set —
    two accounts of one provider can share a domain, and a column that prints
    the same short name twice is worse than a long one.
    """
    text = (account or "").strip()
    if not text or text.casefold() == "default":
        return "—"
    if "@" in text:
        domain = text.split("@", 1)[1]
        first = domain.split(".")[0].strip()
        if first:
            return first
    return text


def _disambiguate_accounts(rows: list[_MatrixRow]) -> None:
    """Restore full account names wherever a short one is ambiguous.

    Ambiguity is scoped per service: ``gmail`` under two different services is
    unambiguous to a reader, but two ``gmail`` rows under one service is not.
    """
    seen: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        seen.setdefault((row.service, row.account), set()).add(row.account_full or "")
    for row in rows:
        if len(seen[(row.service, row.account)]) > 1 and row.account_full:
            row.account = row.account_full


def _window_value_usd(
    window: QuotaWindow,
    remaining: float,
    provider: str,
    plans: dict[str, Any],
    analysis: dict[str, Any],
) -> float | None:
    """Dollar value of the unused part of a window, or None when unpriceable.

    Needs both a configured ``monthly_price`` for the plan and a known window
    duration, so providers that report neither (grok, the unnamed antigravity
    quotas) legitimately have no dollar figure.
    """
    if not window.window_minutes:
        return None
    plan_meta = plans.get(provider_config_key(provider))
    if not isinstance(plan_meta, dict):
        return None
    monthly_price = plan_meta.get("monthly_price")
    if monthly_price is None:
        return None
    value_multipliers = plan_meta.get("value_multiplier")
    duration_kind = classify_window_minutes(window.window_minutes)
    window_mult = 1.0
    if isinstance(value_multipliers, dict) and duration_kind:
        window_mult = float(value_multipliers.get(duration_kind, 1.0))
    return round(
        _compute_value_at_risk(
            remaining=remaining,
            window_minutes=window.window_minutes,
            monthly_price=float(monthly_price),
            waking_hours_per_day=float(analysis.get("waking_hours_per_day", 16)),
            value_multiplier=window_mult,
        ),
        2,
    )


def _format_reset_span(
    days: float | None,
    *,
    estimated: bool = False,
    compact: bool = False,
) -> _ResetSpan | None:
    """Two-unit reset distance, or None when the clock has no timestamp.

    Integers only, no calendar months. At most two components; every zero is
    dropped. Minutes appear only when the remainder is under one day; seconds
    only when it is under one minute. ``compact`` keeps the largest unit alone.
    Date-only stamps (``estimated``) render as ``~And`` / ``~Nh``.
    """
    if days is None:
        return None
    if days <= 0:
        return _ResetSpan(head="now")

    prefix = "~" if estimated else ""
    if estimated:
        if days < 1:
            hours = max(1, int(round(days * 24.0)))
            return _ResetSpan(prefix=prefix, head=f"{hours}h")
        return _ResetSpan(prefix=prefix, head=f"{max(1, int(round(days)))}d")

    total_sec = days * 86400.0
    if total_sec < 60:
        return _ResetSpan(head=f"{max(1, int(round(total_sec)))}s")

    if total_sec < 86400:
        total_min = int(round(total_sec / 60.0))
        if total_min >= 24 * 60:
            return _ResetSpan(head="1d")
        hours, minutes = divmod(total_min, 60)
        if compact:
            return _ResetSpan(head=f"{hours}h" if hours else f"{minutes}m")
        if hours and minutes:
            return _ResetSpan(head=f"{hours}h", tail=f"{minutes}m")
        if hours:
            return _ResetSpan(head=f"{hours}h")
        return _ResetSpan(head=f"{minutes}m")

    total_hr = int(round(total_sec / 3600.0))
    day_count, hours = divmod(total_hr, 24)
    if day_count == 0:
        if compact:
            return _ResetSpan(head=f"{hours}h" if hours else "1d")
        if hours:
            return _ResetSpan(head=f"{hours}h")
        return _ResetSpan(head="1d")
    if compact:
        return _ResetSpan(head=f"{day_count}d")
    if hours:
        return _ResetSpan(head=f"{day_count}d", tail=f"{hours}h")
    return _ResetSpan(head=f"{day_count}d")


def _style_reset_span(s: _Style, span: _ResetSpan) -> str:
    """Dim the decoration; bold the largest unit so a column still scans."""
    return s.dim(span.prefix) + s.bold(span.head) + s.dim(span.tail)


def _scope_display(scope: str, *, short: bool) -> str:
    if not short or scope == "—":
        return scope
    return _SCOPE_SHORT.get(scope, scope)


def _services_needing_scope(rows: list[_MatrixRow]) -> set[str]:
    by_service: dict[str, set[str]] = {}
    for row in rows:
        by_service.setdefault(row.service, set()).add(row.scope)
    return {service for service, scopes in by_service.items() if len(scopes) > 1}


def _services_needing_account(rows: list[_MatrixRow]) -> set[str]:
    by_service: dict[str, set[str]] = {}
    for row in rows:
        by_service.setdefault(row.service, set()).add(row.account)
    return {service for service, accounts in by_service.items() if len(accounts) > 1}


def _advance_matrix_layout(layout: _MatrixLayout, rows: list[_MatrixRow]) -> bool:
    """Apply the next width-squish stage. False when nothing remains to drop."""
    if layout.show_value:
        layout.show_value = False
        return True
    if not layout.compact_deadline:
        layout.compact_deadline = True
        return True
    if layout.show_scope and not layout.short_scope:
        layout.short_scope = True
        return True
    if layout.show_scope and not _services_needing_scope(rows):
        layout.show_scope = False
        return True
    if layout.show_account and not _services_needing_account(rows):
        layout.show_account = False
        return True
    if not layout.fold_identity and (
        (layout.show_scope and _services_needing_scope(rows))
        or (layout.show_account and _services_needing_account(rows))
    ):
        layout.fold_identity = True
        layout.show_scope = False
        layout.show_account = False
        return True
    return False


def _row_identity(
    row: _MatrixRow,
    layout: _MatrixLayout,
    *,
    scope_needed: set[str],
    account_needed: set[str],
) -> tuple[str, str, str]:
    """Service / account / scope labels for one row under ``layout``."""
    scope = _scope_display(row.scope, short=layout.short_scope or layout.fold_identity)
    if not layout.fold_identity:
        return row.service, row.account, scope
    parts = [row.service]
    if row.service in account_needed and row.account != "—":
        parts.append(row.account)
    if row.service in scope_needed and row.scope != "—":
        parts.append(scope)
    return "/".join(parts), "—", "—"


def _clock_percent_text(cell: _ClockCell) -> str:
    mark = "+" if cell.folded else ""
    return f"{_format_used_percent(cell.used_percent)}{mark}"


def _clock_plain(cell: _ClockCell, *, compact: bool) -> str:
    percent = _clock_percent_text(cell)
    span = _format_reset_span(cell.days_until_reset, estimated=cell.reset_estimated, compact=compact)
    return f"{percent}/{span.plain()}" if span is not None else percent


def _matrix_needed_width(rows: list[_MatrixRow], layout: _MatrixLayout) -> int:
    identities = [
        _row_identity(
            row,
            layout,
            scope_needed=_services_needing_scope(rows),
            account_needed=_services_needing_account(rows),
        )
        for row in rows
    ]
    w_service = max([len("SERVICE")] + [len(service) for service, _acct, _scope in identities])
    total = 5 + 1 + w_service
    if layout.show_account:
        w_account = max([len("ACCT")] + [len(account) for _svc, account, _scope in identities])
        total += 1 + w_account
    if layout.show_scope:
        w_scope = max([len("SCOPE")] + [len(scope) for _svc, _acct, scope in identities])
        total += 1 + w_scope
    w_pct, w_spans = _clock_column_widths(rows, layout)
    for w_span in w_spans:
        total += 1 + (w_pct + ((1 + w_span) if w_span else 0))
    if layout.show_value:
        total += 1 + max(7, len("$ UNUSED"))
    return total


def _clock_column_widths(rows: list[_MatrixRow], layout: _MatrixLayout) -> tuple[int, list[int]]:
    w_pct = 5
    w_spans = [0] * len(CLOCK_COLUMNS)
    for row in rows:
        if row.note is not None:
            continue
        for idx, (key, _label) in enumerate(CLOCK_COLUMNS):
            cell = row.clocks.get(key)
            if cell is None:
                continue
            w_pct = max(w_pct, len(_clock_percent_text(cell)))
            span = _format_reset_span(
                cell.days_until_reset,
                estimated=cell.reset_estimated,
                compact=layout.compact_deadline,
            )
            if span is not None:
                w_spans[idx] = max(w_spans[idx], len(span.plain()))
    for idx, (_key, label) in enumerate(CLOCK_COLUMNS):
        w_pct = max(w_pct, len(label))
    return w_pct, w_spans


# Sourced from the analysis layer so the chat format names pools identically.
_POOL_SCOPE_LABELS = POOL_SCOPE_LABELS


def render_status_line(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    *,
    max_width: int = 120,
) -> str:
    """Single-line status for shell prompts / status bars (``aiuse status``).

    No ANSI; compact summary of the most urgent burn/conserve alert (if any).
    """
    if snapshot.collector_errors and not snapshot.accounts:
        detail = snapshot.collector_errors[0]
        if len(snapshot.collector_errors) > 1:
            detail = f"{detail} (+{len(snapshot.collector_errors) - 1} more)"
        return _clamp_display_width(f"error: {detail}", max_width)

    actionable = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE) and a.kind != "prepaid"]
    burns = [a for a in actionable if a.kind == "burn"]
    conserves = [a for a in actionable if a.kind == "conserve"]

    if not actionable:
        n_acc = len(snapshot.accounts)
        if n_acc == 0:
            return _clamp_display_width("ok: no accounts (nothing to rank)", max_width)
        return _clamp_display_width("ok: nothing urgent under current thresholds", max_width)

    # Highest score first (same ordering as analyze_use_or_lose).
    top = max(actionable, key=lambda a: (a.score, a.remaining_percent))
    name = provider_display_name(top.provider)
    label = top.window_label
    rem = top.remaining_percent
    if top.kind == "conserve":
        head = f"slow: {name} {label} {rem:.0f}%"
    elif rem <= 0.0:
        head = f"empty: {name} {label}"
    else:
        head = f"use: {name} {label} {rem:.0f}%"

    bits: list[str] = [head]
    forecast = _forecast_fragment(top, compact=True).lstrip(" ·")
    if forecast:
        bits.append(forecast)
    if burns:
        bits.append(f"{len(burns)} burn" + ("s" if len(burns) != 1 else ""))
    if conserves:
        bits.append(f"{len(conserves)} slow")
    line = " · ".join(bits)
    return _clamp_display_width(line, max_width)


def render_priority_ladder(
    alerts: list[UseOrLoseAlert],
    *,
    snapshot: Snapshot | None = None,
    s: _Style | None = None,
    color: bool | None = None,
    width: int = ACTION_PLAN_WIDTH,
) -> str:
    """Stdout body: every provider, sorted by use-urgency (no blank lines).

    Tags (error/empty/n/a/slow/mid/use) label each row. error/empty/n/a are
    fixed lanes near the top; slow/mid/use share a continuum from conserve to
    use-soon (bottom). Failed fetches stay at the top as ``error``.
    """
    if s is None:
        s = _Style(use_color(force=color))

    entries: list[tuple[tuple, str]] = []
    # Coverage includes independent pool id so Antigravity Gemini vs Claude/GPT
    # each keep a ladder row even when only one pool raised an alert.
    covered: set[tuple[str, str, str]] = set()

    for alert in alerts:
        if alert.urgency == Urgency.NONE:
            continue
        band = alert_priority_band(alert)
        key = _ladder_sort_key(
            _band_sort_lane(band),
            alert_use_urgency(alert),
            alert.provider,
            alert.account,
        )
        entries.append((key, _priority_alert_line(alert, s, band)))
        covered.add(_ladder_coverage_key(alert.provider, alert.account, _pool_id_for_label(alert.window_label)))

    accounts = _sorted_accounts(snapshot.accounts) if snapshot is not None else []
    for account in accounts:
        if account.error or not _account_has_usage(account):
            cov = _ladder_coverage_key(account.provider, account.account, "")
            if cov in covered:
                continue
            entries.append(
                (
                    _ladder_sort_key(_LANE_ERROR, -1000.0, account.provider, account.account),
                    _priority_error_line(account, s),
                )
            )
            covered.add(cov)
            continue
        if _account_is_non_expiring_prepaid(account):
            cov = _ladder_coverage_key(account.provider, account.account, "")
            if cov in covered:
                continue
            # A rolling prepaid balance is inventory only while there is money
            # to spend.  Zero or a negative balance is nevertheless exhausted
            # capacity and belongs with other empty services, not neutral n/a.
            depleted = account.balance_usd is not None and account.balance_usd <= 0.0
            band = _BAND_EMPTY if depleted else _BAND_NA
            entries.append(
                (
                    _ladder_sort_key(
                        _LANE_EMPTY if depleted else _LANE_NA,
                        _account_use_urgency(account),
                        account.provider,
                        account.account,
                    ),
                    _priority_account_line(account, s, band),
                )
            )
            covered.add(cov)
            continue

        # One ladder row per hard-separated pool (Gemini vs Claude/GPT, …);
        # single-pool providers still emit exactly one row.
        pools = partition_independent_pools(account.windows) if account.windows else [[]]
        for pool in pools:
            pool_id = _pool_id_for_windows(pool) if pool else ""
            cov = _ladder_coverage_key(account.provider, account.account, pool_id)
            if cov in covered:
                continue
            window = _pick_representative_window(pool) if pool else None
            band, lane = _unalerted_window_band(window)
            urgency = _window_use_urgency(window) if window is not None else _account_use_urgency(account)
            entries.append(
                (
                    _ladder_sort_key(
                        lane,
                        urgency,
                        account.provider,
                        account.account,
                    ),
                    _priority_account_line(account, s, band, window=window),
                )
            )
            covered.add(cov)

    if not entries:
        return s.green("use   nothing urgent under current thresholds")

    entries.sort(key=lambda item: item[0])
    out: list[str] = []
    for idx, (_key, line) in enumerate(entries):
        clamped = _clamp_display_width(line, width)
        out.append(s.zebra_bg(clamped, width) if idx % 2 else clamped)
    return "\n".join(out)


# Which band wins when several alerts describe one pool. Attention-grabbing
# states outrank quiet ones, so a pool with an exhausted weekly and a healthy
# 5h still reads as exhausted.
_BAND_ATTENTION = {
    _BAND_ERROR: 5,
    _BAND_EMPTY: 4,
    _BAND_CONSERVE: 3,
    _BAND_USE: 2,
    _BAND_MID: 1,
    _BAND_NA: 0,
}


def _used_percent(window: QuotaWindow) -> float | None:
    """Percentage consumed, preferring the reported figure over the inverse."""
    if window.used_percent is not None:
        return float(window.used_percent)
    remaining = window.remaining()
    return None if remaining is None else max(0.0, 100.0 - float(remaining))


def _build_matrix_rows(
    alerts: list[UseOrLoseAlert],
    snapshot: Snapshot | None,
    config: dict[str, Any] | None,
) -> list[_MatrixRow]:
    cfg = config or {}
    raw_plans = cfg.get("plans")
    raw_analysis = cfg.get("analysis")
    plans: dict[str, Any] = raw_plans if isinstance(raw_plans, dict) else {}
    analysis: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}

    # Collapse alerts onto the pool they describe, keeping the most
    # attention-grabbing band and the use-urgency that goes with it.
    pool_alert: dict[tuple[str, str, str], tuple[int, float]] = {}
    for alert in alerts:
        if alert.urgency == Urgency.NONE:
            continue
        key = _ladder_coverage_key(alert.provider, alert.account, _pool_id_for_label(alert.window_label))
        band = alert_priority_band(alert)
        candidate = (band, alert_use_urgency(alert))
        current = pool_alert.get(key)
        if current is None or _BAND_ATTENTION[band] > _BAND_ATTENTION[current[0]]:
            pool_alert[key] = candidate

    rows: list[_MatrixRow] = []
    covered: set[tuple[str, str, str]] = set()
    # Accounts with no live windows are deferred: an alert may still describe
    # them (analysis can raise on data an account row cannot render), and the
    # alert is the more informative row. Same rule the ladder used.
    deferred: list[AccountUsage] = []
    now = utcnow()

    for account in _sorted_accounts(snapshot.accounts) if snapshot is not None else []:
        service = provider_display_name(account.provider)
        short = _short_account(account.account)

        if account.error or not _account_has_usage(account) or _account_is_non_expiring_prepaid(account):
            deferred.append(account)
            continue

        for pool in partition_independent_pools(account.windows) if account.windows else [[]]:
            pool_id = _pool_id_for_windows(pool) if pool else ""
            clocks: dict[str, _ClockCell] = {}
            value: float | None = None

            for window in pool:
                used = _used_percent(window)
                if used is None:
                    continue
                clock, inferred = infer_window_clock(window)
                days = window.days_until_reset(now)
                estimated = not window.reset_time_is_precise()
                if clock is not None:
                    cell = clocks.get(clock)
                    if cell is None:
                        clocks[clock] = _ClockCell(
                            used_percent=used,
                            inferred=inferred,
                            days_until_reset=days,
                            reset_estimated=estimated,
                        )
                    else:
                        # Same clock, same pool (Cursor Included ⊂ Auto): show the
                        # most-consumed, which is the one that locks out first.
                        cell.folded += 1
                        if used > cell.used_percent:
                            cell.used_percent = used
                            cell.inferred = inferred
                            cell.days_until_reset = days
                            cell.reset_estimated = estimated
                remaining = window.remaining()
                if remaining is not None:
                    priced = _window_value_usd(window, remaining, account.provider, plans, analysis)
                    # Max, not sum: a 5h window is carved out of the weekly
                    # budget above it, so adding them double-counts the money.
                    if priced is not None and (value is None or priced > value):
                        value = priced

            key = _ladder_coverage_key(account.provider, account.account, pool_id)
            found = pool_alert.get(key)
            if found is not None:
                band, urgency = found
            else:
                representative = _pick_representative_window(pool) if pool else None
                band, _lane = _unalerted_window_band(representative)
                urgency = (
                    _window_use_urgency(representative) if representative is not None else _account_use_urgency(account)
                )

            note = None
            if not clocks:
                for window in pool:
                    if window.reset_description:
                        note = window.reset_description
                        break
            rows.append(
                _MatrixRow(
                    sort_key=_ladder_sort_key(_band_sort_lane(band), urgency, account.provider, account.account),
                    band=band,
                    service=service,
                    account=short,
                    account_full=account.account,
                    scope=_POOL_SCOPE_LABELS.get(pool_id, "—"),
                    clocks=clocks,
                    value_usd=value,
                    note=note,
                )
            )
            covered.add(key)

    # Alerts about pools no account row rendered.
    for alert in alerts:
        if alert.urgency == Urgency.NONE:
            continue
        key = _ladder_coverage_key(alert.provider, alert.account, _pool_id_for_label(alert.window_label))
        if key in covered:
            continue
        covered.add(key)
        rows.append(_row_from_alert(alert))

    for account in deferred:
        if _ladder_coverage_key(account.provider, account.account, "") in covered:
            continue
        service = provider_display_name(account.provider)
        short = _short_account(account.account)
        if account.error or not _account_has_usage(account):
            rows.append(
                _MatrixRow(
                    sort_key=_ladder_sort_key(_LANE_ERROR, -1000.0, account.provider, account.account),
                    band=_BAND_ERROR,
                    service=service,
                    account=short,
                    account_full=account.account,
                    note=(account.error or "no usage data").strip(),
                )
            )
            continue
        depleted = account.balance_usd is not None and account.balance_usd <= 0.0
        if account.balance_usd is not None:
            note = f"balance ${account.balance_usd:.2f} (no expiry)"
        elif account.credits_remaining is not None:
            note = f"credits {account.credits_remaining:g} (no expiry)"
        else:
            note = "prepaid API (no expiry)"
        rows.append(
            _MatrixRow(
                sort_key=_ladder_sort_key(
                    _LANE_EMPTY if depleted else _LANE_NA,
                    _account_use_urgency(account),
                    account.provider,
                    account.account,
                ),
                band=_BAND_EMPTY if depleted else _BAND_NA,
                service=service,
                account=short,
                account_full=account.account,
                note=note,
            )
        )

    _disambiguate_accounts(rows)
    rows.sort(key=lambda r: r.sort_key)
    return rows


def _row_from_alert(alert: UseOrLoseAlert) -> _MatrixRow:
    """A table row built from an alert alone, with no account windows behind it.

    Only ever one clock wide — an alert describes a single window — so these
    rows are deliberately sparser than an account-derived row.
    """
    band = alert_priority_band(alert)
    service = provider_display_name(alert.provider)
    row = _MatrixRow(
        sort_key=_ladder_sort_key(_band_sort_lane(band), alert_use_urgency(alert), alert.provider, alert.account),
        band=band,
        service=service,
        account=_short_account(alert.account),
        account_full=alert.account,
        scope=_POOL_SCOPE_LABELS.get(_pool_id_for_label(alert.window_label), "—"),
    )
    if alert.kind == "prepaid":
        row.note = f"{alert.window_label} (no expiry)"
        return row

    synthetic = QuotaWindow(
        label=alert.window_label,
        remaining_percent=alert.remaining_percent,
        resets_at=None,
        window_minutes=None,
    )
    clock, inferred = infer_window_clock(synthetic)
    if clock is None and alert.days_until_reset is not None:
        # No duration and an unhelpful label: fall back to reset distance, the
        # same last resort infer_window_clock uses when it has a timestamp.
        days = float(alert.days_until_reset)
        clock, inferred = ("5h" if days <= 0.5 else "weekly" if days <= 8.0 else "monthly", True)
    if clock is not None:
        row.clocks[clock] = _ClockCell(
            used_percent=max(0.0, 100.0 - float(alert.remaining_percent)),
            inferred=inferred,
            days_until_reset=alert.days_until_reset,
            reset_estimated=alert.deadline_is_estimated,
        )
    if alert.flexibility_profile is not None:
        row.value_usd = alert.flexibility_profile.value_at_risk_usd
    return row


def _cell_color(s: _Style, used: float) -> Any:
    """Fuller bucket → hotter color. Neutral about good/bad — the band tag judges."""
    if used >= 99.0:
        return s.red
    if used >= 75.0:
        return s.yellow
    if used >= 25.0:
        return s.cyan
    return s.green


def render_clock_matrix(
    alerts: list[UseOrLoseAlert],
    *,
    snapshot: Snapshot | None = None,
    config: dict[str, Any] | None = None,
    s: _Style | None = None,
    color: bool | None = None,
    width: int | None = None,
) -> str:
    """Stdout body: one row per account/pool, one column per reset clock.

    Every row is measured on the same three clocks, so a column can be read
    top to bottom. An em-dash means the service has no window on that clock —
    which is itself the answer to "why does this one only show a weekly?".

    Percentages are **used**, not left: 0% is untouched, 100% is exhausted.
    When a clock has a reset timestamp the cell is ``75%/4h`` / ``89%/2d14h``
    — used percent, then that clock's own remaining time.
    """
    if s is None:
        s = _Style(use_color(force=color))
    rows = _build_matrix_rows(alerts, snapshot, config)
    if not rows:
        return s.green("use   nothing urgent under current thresholds")

    avail = min(width or terminal_width(), TABLE_MAX_WIDTH)
    layout = _MatrixLayout(
        show_scope=any(row.scope != "—" for row in rows),
        show_account=any(row.account != "—" for row in rows),
    )
    while _matrix_needed_width(rows, layout) > avail:
        if not _advance_matrix_layout(layout, rows):
            break

    scope_needed = _services_needing_scope(rows)
    account_needed = _services_needing_account(rows)
    identities = [_row_identity(row, layout, scope_needed=scope_needed, account_needed=account_needed) for row in rows]
    w_service = max([len("SERVICE")] + [len(service) for service, _acct, _scope in identities])
    w_account = max([len("ACCT")] + [len(account) for _svc, account, _scope in identities])
    w_scope = max([len("SCOPE")] + [len(scope) for _svc, _acct, scope in identities])
    w_pct, w_spans = _clock_column_widths(rows, layout)
    w_clocks = [w_pct + ((1 + w_span) if w_span else 0) for w_span in w_spans]
    w_value = max(7, len("$ UNUSED"))

    def _line(tag: str, service: str, account: str, scope: str, tail: list[str]) -> str:
        cells = [tag, service]
        if layout.show_account:
            cells.append(account)
        if layout.show_scope:
            cells.append(scope)
        cells.extend(tail)
        return _clamp_display_width(" ".join(cells).rstrip(), avail)

    header_clocks = []
    for (_key, label), w_clock, w_span in zip(CLOCK_COLUMNS, w_clocks, w_spans, strict=True):
        header_clocks.append(f"{label:>{w_pct}}" + (" " * (1 + w_span) if w_span else ""))
        if len(header_clocks[-1]) < w_clock:
            header_clocks[-1] = f"{header_clocks[-1]:<{w_clock}}"

    lines = [
        s.dim(
            _line(
                f"{'':<5}",
                f"{'SERVICE':<{w_service}}",
                f"{'ACCT':<{w_account}}",
                f"{'SCOPE':<{w_scope}}",
                header_clocks + ([f"{'$ UNUSED':>{w_value}}"] if layout.show_value else []),
            )
        )
    ]
    any_inferred = False
    any_folded = False
    any_deadline = False
    zebra_width = _matrix_needed_width(rows, layout)
    for row_idx, row in enumerate(rows):
        tag_plain, tag_color = _BAND_TAG[row.band]
        tag = getattr(s, tag_color)(s.bold(f"{tag_plain:<5}"))
        service_text, account_text, scope_text = identities[row_idx]
        service = s.bold(f"{service_text:<{w_service}}")
        account = s.dim(f"{account_text:<{w_account}}")
        scope = s.dim(f"{scope_text:<{w_scope}}")

        if row.note is not None:
            line = _line(tag, service, account, scope, [s.dim(row.note)])
            lines.append(s.zebra_bg(line, zebra_width) if row_idx % 2 else line)
            continue

        tail: list[str] = []
        present_indices = [idx for idx, (k, _) in enumerate(CLOCK_COLUMNS) if row.clocks.get(k) is not None]
        for idx, (key, _label) in enumerate(CLOCK_COLUMNS):
            cell = row.clocks.get(key)
            w_clock = w_clocks[idx]
            if cell is None:
                if not present_indices or min(present_indices) < idx < max(present_indices):
                    missing_char = "—"
                elif idx < min(present_indices):
                    missing_char = "->"
                else:
                    missing_char = "<-"
                tail.append(s.dim(f"{missing_char:>{w_clock}}"))
                continue
            any_inferred = any_inferred or cell.inferred
            any_folded = any_folded or bool(cell.folded)
            percent = _clock_percent_text(cell)
            span = _format_reset_span(
                cell.days_until_reset,
                estimated=cell.reset_estimated,
                compact=layout.compact_deadline,
            )
            colored = (
                s.dim(f"{percent:>{w_pct}}")
                if cell.inferred
                else _cell_color(s, cell.used_percent)(f"{percent:>{w_pct}}")
            )
            if span is None:
                tail.append(colored + (" " * (w_clock - w_pct)))
                continue
            any_deadline = True
            styled = colored + s.dim("/") + _style_reset_span(s, span)
            pad = w_clock - (w_pct + 1 + len(span.plain()))
            if pad > 0:
                styled += " " * pad
            tail.append(styled)

        if layout.show_value:
            value_text = "—" if row.value_usd is None else f"${row.value_usd:,.2f}"
            tail.append(f"{value_text:>{w_value}}")

        line = _line(tag, service, account, scope, tail)
        lines.append(s.zebra_bg(line, zebra_width) if row_idx % 2 else line)

    legend_items: list[tuple[str, str]] = []
    if any_deadline:
        legend_items.append(
            (
                "2d14h = until this clock resets · bold = largest unit",
                s.dim("2d14h = until this clock resets · bold = largest unit"),
            )
        )
    if any_inferred:
        legend_items.append(("dim % = clock inferred, not reported", s.dim("dim % = clock inferred, not reported")))
    if any_folded:
        legend_items.append(
            ("+ = >1 window on that clock, showing most-used", s.dim("+ = >1 window on that clock, showing most-used"))
        )

    plain_note = "Note: 100% means 100% Used"
    fmt_note = s.dim("Note: ") + s.red("100% means 100% Used")
    legend_items.append((plain_note, fmt_note))

    plain_ai_note = "AI: Use `aiuse --json` for machine-readable output"
    fmt_ai_note = s.dim(plain_ai_note)
    legend_items.append((plain_ai_note, fmt_ai_note))

    table_width = zebra_width
    for plain_text, fmt_text in legend_items:
        visible = len(_strip_ansi(fmt_text))
        if visible >= avail:
            lines.append(_clamp_display_width(fmt_text, avail))
            continue
        margin_size = max(0, (min(table_width, avail) - visible) // 2)
        lines.append(_clamp_display_width(" " * margin_size + fmt_text, avail))

    return "\n".join(lines)


def _format_used_percent(used: float) -> str:
    """Keep a barely-touched window distinct from a genuinely untouched one."""
    if 0.0 < used < 1.0:
        return ">0%"
    return f"{used:.0f}%"


def _priority_tag(s: _Style, band: int) -> str:
    tag, color_name = _BAND_TAG[band]
    return getattr(s, color_name)(s.bold(tag))


def _format_remaining_percent(remaining: float) -> str:
    """Keep a positive fractional remainder distinct from an empty 0%."""
    return "<1%" if 0.0 < remaining < 1.0 else f"{remaining:.0f}%"


def _forecast_fragment(alert: UseOrLoseAlert, *, compact: bool = True) -> str:
    """Compact lockout / waste forecast from PaceProfile (ladder + status).

    Keep short: ladder lines are width-clamped (~80 cols).
    """
    pace = alert.pace
    if pace is None:
        return ""
    parts: list[str] = []
    # Already-empty capacity is not "about to lock out" — skip the forecast.
    rem = float(alert.remaining_percent)
    if alert.kind == "conserve" and pace.projected_exhaust_at is not None and rem > 1.0:
        parts.append(f"~lockout {pace.projected_exhaust_at.strftime('%a %H:%M')}")
    if alert.kind == "burn" and pace.projected_waste_fraction is not None:
        waste_pct = pace.projected_waste_fraction * 100.0
        if waste_pct >= 5.0:
            parts.append(f"~{waste_pct:.0f}%waste")
    if not compact and pace.learned_sample_count > 0:
        n = pace.learned_sample_count
        parts.append(f"hist n={n}")
    if not parts:
        return ""
    return " · " + " · ".join(parts)


def _priority_alert_line(alert: UseOrLoseAlert, s: _Style, band: int) -> str:
    who = alert.account or "default"
    name = s.bold(provider_display_name(alert.provider))
    if alert.kind == "prepaid":
        # Inventory only — no "use before reset" language for non-expiring tokens.
        body = f"{name} · {who} · {alert.window_label} (no expiry)"
        return f"{_priority_tag(s, band)} {body}"
    # Chronic-underuse alerts summarize earlier cycles rather than one live
    # account/window.  A fresh Claude 5-hour window can legitimately have no
    # reset timestamp until it is first used, so do not describe that normal
    # state as an unknown deadline.
    when = (
        "more each cycle"
        if alert.source == "history" and alert.days_until_reset is None
        else _human_deadline(alert.days_until_reset, estimated=alert.deadline_is_estimated)
    )
    remaining = _format_remaining_percent(alert.remaining_percent)
    # Depleted rows already use the empty tag — do not also say "pace" or
    # "~lockout", which imply capacity remains.
    if band == _BAND_EMPTY:
        body = f"{name} · {who} · {alert.window_label}: {remaining} left · resets {when}"
        return f"{_priority_tag(s, band)} {body}"
    verb = "pace" if alert.kind == "conserve" else "use"
    # Forecast before the deadline phrase so width-clamp keeps the useful bit.
    forecast = _forecast_fragment(alert, compact=True)
    body = f"{name} · {who} · {alert.window_label}: {remaining} left{forecast} · {verb} {when}"
    return f"{_priority_tag(s, band)} {body}"


def _priority_error_line(account: AccountUsage, s: _Style) -> str:
    who = account.account or "default"
    detail = (account.error or "no usage data").strip()
    body = f"{s.bold(provider_display_name(account.provider))} · {who} · {detail}"
    return f"{_priority_tag(s, _BAND_ERROR)} {body}"


def _priority_account_line(
    account: AccountUsage,
    s: _Style,
    band: int,
    *,
    window: QuotaWindow | None = None,
) -> str:
    """One mid/ok line for a live account/pool that did not raise a burn/conserve alert."""
    who = account.account or "default"
    name = s.bold(provider_display_name(account.provider))
    # Prepaid / pay-as-you-go: show balance inventory, never fake window % urgency.
    if _account_is_non_expiring_prepaid(account):
        if account.balance_usd is not None:
            body = f"{name} · {who} · balance ${account.balance_usd:.2f} (no expiry)"
        elif account.credits_remaining is not None:
            body = f"{name} · {who} · credits {account.credits_remaining:g} (no expiry)"
        else:
            body = f"{name} · {who} · prepaid API (no expiry)"
        return f"{_priority_tag(s, band)} {body}"
    if window is None:
        window = _pick_representative_window(account.windows)
    if window is not None:
        if (
            band == _BAND_EMPTY
            and window.resets_at is None
            and window.reset_description
            and "expired" in window.reset_description.casefold()
        ):
            body = f"{name} · {who} · {window.reset_description}"
            return f"{_priority_tag(s, band)} {body}"
        rem = window.remaining() or 0.0
        when = _human_deadline(window.days_until_reset(), estimated=not window.reset_time_is_precise())
        # Empty capacity is not "ok" — only show reset timing.
        status = "resets" if band == _BAND_EMPTY else "ok"
        body = f"{name} · {who} · {window.label}: {_format_remaining_percent(rem)} left · {status} {when}"
    elif account.balance_usd is not None:
        body = f"{name} · {who} · balance ${account.balance_usd:.2f}"
    elif account.credits_remaining is not None:
        body = f"{name} · {who} · credits {account.credits_remaining:g}"
    else:
        body = f"{name} · {who} · on pace"
    return f"{_priority_tag(s, band)} {body}"


def render_stderr_meta(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    *,
    color: bool | None = None,
) -> str:
    """Collection meta, errors, and ``ai --full`` hint for stderr (default mode)."""
    s = _Style(use_color(force=color))
    accounts = _sorted_accounts(snapshot.accounts)
    n_accounts = len(accounts)
    n_actionable = sum(1 for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE))
    lines: list[str] = []
    meta = f"Collected at {snapshot.collected_at.isoformat()}"
    meta += f" · {n_accounts} account{'s' if n_accounts != 1 else ''}"
    if n_actionable:
        meta += f" · {n_actionable} alert{'s' if n_actionable != 1 else ''}"
    else:
        meta += " · no burn/conserve alerts"
    lines.append(s.dim(meta))
    if snapshot.collector_errors:
        lines.append(s.red("Collector errors:"))
        for err in snapshot.collector_errors:
            lines.append(s.red(f"  - {err}"))
    capacity = _capacity_summary_line(alerts, s)
    if capacity:
        lines.append(capacity.strip())
    lines.append(s.dim("Detail: ai --full"))
    return "\n".join(lines)


def _capacity_summary_line(alerts: list[UseOrLoseAlert], s: _Style) -> str | None:
    """One-line burn-capacity blurb shared with the detailed action plan."""
    action = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE) and a.kind != "conserve"]
    if not action:
        return None
    conserve = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE) and a.kind == "conserve"]
    total_value_usd = sum(
        a.flexibility_profile.value_at_risk_usd
        for a in action
        if a.flexibility_profile and a.flexibility_profile.value_at_risk_usd is not None
    )
    providers = len({a.provider for a in action} | {a.provider for a in conserve})
    if total_value_usd > 0:
        return s.dim(
            f"  Available capacity this cycle: {s.bold(f'${total_value_usd:.2f}')} "
            f"across {len(action)} windows ({providers} providers)."
        )
    return s.dim(f"  {len(action)} windows with unused capacity across {providers} providers.")


def _physical_line_count(lines: list[str]) -> int:
    """Count terminal rows, including embedded newlines inside a list entry."""
    if not lines:
        return 0
    return sum(part.count("\n") + 1 for part in lines)


def _render_action_plan_section(
    alerts: list[UseOrLoseAlert],
    s: _Style,
    *,
    width: int,
    traditional_summary: bool,
    waking_hours_per_day: float,
) -> list[str]:
    """
    Build the trailing action-plan block(s).

    Prefer a single detailed plan when it fits in ``ACTION_PLAN_MAX_LINES``.
    Otherwise emit detailed + compact brief, with brief always last.
    """
    if traditional_summary:
        detailed_body = _render_traditional_summary(alerts, s, width=width)
    else:
        detailed_body = _render_action_plan(alerts, s, width=width, waking_hours_per_day=waking_hours_per_day)

    header_title = "## Action plan — use these before they reset"
    # Section = title + rule + body (+ optional trailing blank already in body)
    detailed_block = [
        s.bold(header_title),
        s.dim("-" * width),
        *detailed_body,
    ]
    detailed_rows = _physical_line_count(detailed_block)

    if detailed_rows <= ACTION_PLAN_MAX_LINES:
        return detailed_block

    # Too tall for one screen: full detail, then a compact plan the viewport
    # can hold without scrolling back.
    brief_body = _render_brief_action_plan(
        alerts,
        s,
        # The real viewport, not the 80-column rule width above — bounded the
        # same way the usage table bounds itself.
        clamp_width=min(terminal_width(), TABLE_MAX_WIDTH),
        max_lines=ACTION_PLAN_MAX_LINES - 2,
    )
    out: list[str] = [
        s.bold("## Action plan (detailed)"),
        s.dim("-" * width),
        *detailed_body,
    ]
    if out and out[-1] != "":
        out.append("")
    out.append(s.bold("## Action plan — at a glance"))
    out.append(s.dim("-" * width))
    out.extend(brief_body)
    return out


def _tips_lines(s: _Style) -> list[str]:
    return [
        s.dim("  • Unused subscription windows expire at reset — burn on real work."),
        s.dim("  • Prepaid API balances usually roll; no rush unless a promo expires."),
        s.dim("  • Claude multi-account: cswap is canonical (CodexBar/caut/OpenUsage/tokscale ≈ active session)."),
        s.dim("  • Re-run: ai · JSON: ai --json · quiet: ai -q · setup: ai doctor · ai --help"),
    ]


def _render_traditional_summary(
    alerts: list[UseOrLoseAlert],
    s: _Style,
    *,
    width: int,
) -> list[str]:
    action = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE)]
    conserve = [a for a in action if a.kind == "conserve"]
    action = [a for a in action if a.kind != "conserve"]
    info = [a for a in alerts if a.urgency == Urgency.INFO]
    lines: list[str] = []

    if not action and not conserve:
        lines.append(s.green("  Nothing urgent: no large unused subscription windows"))
        lines.append(s.green("  are about to reset under your current thresholds."))
        lines.append(s.dim("  (Quotas may be well-used, resets far out, or live quota data missing"))
        lines.append(s.dim("   — check per-provider detail above.)"))
    else:
        lines.append(s.dim("  Paid plan capacity that goes unused when the window resets is gone forever."))
        lines.append(s.dim("  Prefer these providers/accounts soon so you do not leave tokens on the table."))
        lines.append("")

        if conserve:
            lines.append(s.bold("  Conserve — pace until reset"))
            lines.append(s.dim("  " + "-" * (width - 4)))
            for alert in sorted(conserve, key=lambda a: (-a.score,)):
                lines.append(_summary_alert_line(alert, s))
            lines.append("")

        # Group by time bucket for a clear "within X" narrative
        buckets: dict[str, list[UseOrLoseAlert]] = {
            "within 24 hours": [],
            "within 3 days": [],
            "within 7 days": [],
            "within 14 days": [],
            "later / unknown reset": [],
        }
        for alert in action:
            buckets[_time_bucket(alert.days_until_reset)].append(alert)

        for bucket_name, items in buckets.items():
            if not items:
                continue
            lines.append(s.bold(f"  → {bucket_name}"))
            for alert in sorted(
                items,
                key=lambda a: (
                    a.days_until_reset if a.days_until_reset is not None else 999,
                    -a.remaining_percent,
                ),
            ):
                lines.append(_summary_alert_line(alert, s))
            lines.append("")

        # One-line action plan (burn only)
        if action:
            lines.append(s.bold("  Action plan"))
            lines.append(s.dim("  " + "-" * (width - 4)))
            for i, alert in enumerate(
                sorted(
                    action,
                    key=lambda a: (
                        a.days_until_reset if a.days_until_reset is not None else 999,
                        -a.score,
                    ),
                ),
                start=1,
            ):
                when = _human_deadline(alert.days_until_reset, estimated=alert.deadline_is_estimated)
                who = alert.account or "default account"
                lines.append(
                    f"  {i}. {s.bold(provider_display_name(alert.provider))} ({who}): burn "
                    f"{s.yellow(f'{alert.remaining_percent:.0f}%')} of "
                    f"{alert.window_label} {when}"
                )
            lines.append("")

    if info:
        lines.append(s.bold("  Advisory / low urgency (no hard deadline)"))
        lines.append(s.dim("  " + "-" * (width - 4)))
        for alert in info:
            lines.append(s.dim(f"  · {alert.message}"))
        lines.append("")

    return lines


def _render_brief_action_plan(
    alerts: list[UseOrLoseAlert],
    s: _Style,
    *,
    clamp_width: int,
    max_lines: int,
    max_lines_per_provider: int = BRIEF_MAX_LINES_PER_PROVIDER,
) -> list[str]:
    """
    One-line-per-alert compact plan for the final viewport.

    Fits in ``max_lines`` physical rows (callers reserve title + rule outside).
    At most ``max_lines_per_provider`` alert lines are kept per provider (highest
    score first within each section), so one busy service cannot dominate.

    ``clamp_width`` is the width alert rows are *truncated* to — how much room
    the caller actually has, which is not the same number as the width it draws
    its section rules at. The plain renderer draws 80-column rules regardless of
    terminal size; passing that same 80 in here silently cut alert text on a
    wide terminal, so the two are separate parameters now.
    """
    action = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE)]
    conserve = sorted(
        [a for a in action if a.kind == "conserve"],
        key=lambda a: (-a.score,),
    )
    burns = [a for a in action if a.kind != "conserve"]
    lines: list[str] = []

    if not action:
        lines.append(s.green("  Nothing urgent under current thresholds."))
        return lines

    provider_lines: dict[str, int] = {}
    omitted = 0

    def _take_alert(alert: UseOrLoseAlert) -> bool:
        nonlocal omitted
        key = alert.provider.casefold()
        used = provider_lines.get(key, 0)
        if used >= max_lines_per_provider:
            omitted += 1
            return False
        provider_lines[key] = used + 1
        return True

    # Flatten to ordered display rows (headers + alert lines), then take what fits.
    rows: list[str] = []
    if conserve:
        kept = [a for a in conserve if _take_alert(a)]
        if kept:
            rows.append(s.bold("  CONSERVE"))
            for alert in kept:
                rows.append(_brief_alert_line(alert, s, kind="conserve"))
    if burns:
        buckets = _action_buckets(burns)
        for bucket_label in ("THIS WEEK", "THIS WEEKEND", "LATER THIS MONTH", "THROTTLED"):
            items = sorted(buckets.get(bucket_label, []), key=lambda a: (-a.score,))
            kept = [a for a in items if _take_alert(a)]
            if not kept:
                continue
            rows.append(s.bold(f"  {bucket_label}"))
            for alert in kept:
                rows.append(_brief_alert_line(alert, s, kind="burn"))

    # Reserve one row for a possible "+N more" footer when we truncate.
    body_budget = max(1, max_lines - 1)
    used = 0
    for row in rows:
        row_h = _physical_line_count([row])
        if used + row_h > body_budget:
            remaining = len(rows) - len(lines)
            omitted += remaining
            lines.append(s.dim(f"  … +{omitted} more (see detailed plan above)"))
            break
        lines.append(_clamp_display_width(row, clamp_width))
        used += row_h
    else:
        if omitted:
            lines.append(s.dim(f"  … +{omitted} more (see detailed plan above)"))

    return lines


def _brief_alert_line(alert: UseOrLoseAlert, s: _Style, *, kind: str) -> str:
    icon = URGENCY_ICON.get(alert.urgency, "   ")
    who = alert.account or "default"
    when = _human_deadline(alert.days_until_reset, estimated=alert.deadline_is_estimated)
    verb = "pace" if kind == "conserve" else "use"
    return (
        f"  {s.urgency(alert.urgency, icon)} "
        f"{s.bold(provider_display_name(alert.provider))} · {who} · "
        f"{alert.window_label}: {alert.remaining_percent:.0f}% left · {verb} {when}"
    )


def _clamp_display_width(text: str, width: int) -> str:
    """Truncate plain or lightly-styled text to roughly ``width`` display cols."""
    # Strip ANSI for length; if over, cut the raw string and re-append reset.
    plain = _strip_ansi(text)
    if len(plain) <= width:
        return text
    # Prefer truncating the unstyled form when styles make counting hard.
    cut = max(0, width - 1)
    return plain[:cut] + "…"


def _strip_ansi(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\033" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and text[j] != "m":
                j += 1
            i = j + 1 if j < len(text) else len(text)
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _render_action_plan(
    alerts: list[UseOrLoseAlert],
    s: _Style,
    *,
    width: int,
    waking_hours_per_day: float = 16.0,
) -> list[str]:
    action = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE)]
    conserve = [a for a in action if a.kind == "conserve"]
    action = [a for a in action if a.kind != "conserve"]  # burn-only buckets below
    info = [a for a in alerts if a.urgency == Urgency.INFO]
    lines: list[str] = []

    rule = "─" * max(8, min(width - 4, 76))

    if not action and not conserve and not info:
        lines.append(s.green("  Nothing urgent: no large unused subscription windows"))
        lines.append(s.green("  are about to reset under your current thresholds."))
        lines.append(s.dim("  (Quotas may be well-used, resets far out, or live quota data missing"))
        lines.append(s.dim("   — check per-provider detail above.)"))
        return lines

    if conserve:
        lines.append(f"  {s.bold('CONSERVE — pace yourself, avoid lockout before reset')}")
        lines.append(s.dim(f"  {rule}"))
        for alert in sorted(conserve, key=lambda a: (-a.score,)):
            lines.append(_conserve_line(alert, s))
        lines.append("")

    if action:
        capacity = _capacity_summary_line(
            [*action, *conserve],
            s,
        )
        if capacity:
            lines.append(capacity)
        lines.append("")

        buckets = _action_buckets(action)
        for bucket_label, bucket_name in [
            ("THIS WEEK", "start now — capacity will reset or needs lead time"),
            ("THIS WEEKEND", "plan ahead"),
            ("LATER THIS MONTH", "before next billing cycle"),
        ]:
            items = buckets.get(bucket_label, [])
            if not items:
                continue
            lines.append(f"  {s.bold(bucket_label)} ({s.dim(bucket_name)})")
            lines.append(s.dim(f"  {rule}"))
            for alert in sorted(items, key=lambda a: (-a.score,)):
                lines.append(_action_plan_line(alert, s))
            lines.append("")

        throttled = buckets.get("THROTTLED", [])
        if throttled:
            lines.append(f"  {s.bold('THROTTLED — ACCUMULATING WASTE')}")
            lines.append(s.dim(f"  {rule}"))
            lines.append(s.dim("  These windows refill so fast you can't use them all. Estimated"))
            lines.append(s.dim("  plan value silently wasted each month:"))
            lines.append("")
            for alert in sorted(throttled, key=lambda a: (-a.score,)):
                lines.append(_throttled_waste_line(alert, s, waking_hours_per_day=waking_hours_per_day))
            lines.append("")

    if info:
        lines.append(s.bold("  ADVISORY / LOW URGENCY (no hard deadline)"))
        lines.append(s.dim(f"  {rule}"))
        for alert in info:
            lines.append(s.dim(f"  · {alert.message}"))
        lines.append("")

    return lines


def _action_buckets(alerts: list[UseOrLoseAlert]) -> dict[str, list[UseOrLoseAlert]]:
    buckets: dict[str, list[UseOrLoseAlert]] = {
        "THIS WEEK": [],
        "THIS WEEKEND": [],
        "LATER THIS MONTH": [],
        "THROTTLED": [],
    }
    for alert in alerts:
        profile = alert.flexibility_profile
        is_throttled = profile is not None and profile.consumption_flexibility < 0.2
        days = alert.days_until_reset

        if is_throttled and days is not None and days <= 3:
            buckets["THIS WEEK"].append(alert)
        elif is_throttled:
            buckets["THROTTLED"].append(alert)
        elif days is not None and days <= 7:
            buckets["THIS WEEK"].append(alert)
        elif days is not None and days <= 10:
            buckets["THIS WEEKEND"].append(alert)
        else:
            buckets["LATER THIS MONTH"].append(alert)

    return buckets


def _conserve_line(alert: UseOrLoseAlert, s: _Style) -> str:
    icon = URGENCY_ICON.get(alert.urgency, "   ")
    who = alert.account or "default"
    when = _human_deadline(alert.days_until_reset, estimated=alert.deadline_is_estimated)
    pace = alert.pace
    lockout = ""
    if pace and pace.projected_exhaust_at:
        lockout = f", locked out ~{pace.projected_exhaust_at.strftime('%a %H:%M UTC')}"
    waste = ""
    if pace and pace.projected_waste_fraction is not None and pace.projected_waste_fraction >= 0.05:
        waste = f", ~{pace.projected_waste_fraction:.0%} unused if pace holds"
    learned = ""
    if pace and pace.learned_sample_count > 0:
        learned = f" (history n={pace.learned_sample_count})"
    return (
        f"  {s.urgency(alert.urgency, icon)} {s.bold(provider_display_name(alert.provider))} · "
        f"{who} · {alert.window_label}: {alert.remaining_percent:.0f}% left · resets {when}"
        f"{lockout}{waste}{learned}\n"
        f"      {s.dim(alert.message)}"
    )


def _action_plan_line(alert: UseOrLoseAlert, s: _Style) -> str:
    icon = URGENCY_ICON.get(alert.urgency, "   ")
    badge = s.urgency(alert.urgency, f"{icon}")
    who = alert.account or "default"
    when = _human_deadline(alert.days_until_reset, estimated=alert.deadline_is_estimated)

    profile = alert.flexibility_profile
    value_part = ""
    flex_note = ""
    if profile:
        if profile.value_at_risk_usd is not None:
            value_part = f" · ${profile.value_at_risk_usd:.2f} at risk"
        if profile.consumption_flexibility >= 0.9:
            flex_note = "Burstable — one heavy session will cover it."
        elif profile.consumption_flexibility >= 0.4:
            flex_note = "Semi-throttled — steady usage will exhaust it."
        else:
            flex_note = "Throttled — single shot, use it or accept losing it."
        if profile.burn_estimate:
            flex_note = f"{flex_note} ({profile.burn_estimate})"

    pace = alert.pace
    if pace is not None and pace.pace_ratio is not None:
        waste = pace.projected_waste_fraction
        if waste is not None:
            value_part += f" · pace {pace.pace_ratio:.1f}x — projected {waste:.0%} unused@reset"
        else:
            value_part += f" · pace {pace.pace_ratio:.1f}x"
        if pace.projected_exhaust_at is not None and alert.kind == "burn":
            # Burn rows: exhaust after reset is less interesting; only if before reset.
            pass
        if pace.learned_sample_count > 0:
            n = pace.learned_sample_count
            value_part += f" · blended with history ({n} sample{'s' if n != 1 else ''})"

    return (
        f"  {badge} {s.bold(provider_display_name(alert.provider))} · "
        f"{who} · {alert.window_label}: {alert.remaining_percent:.0f}% left · "
        f"use {when}{value_part}\n"
        f"      {s.dim(flex_note)}"
    )


def _throttled_waste_line(
    alert: UseOrLoseAlert,
    s: _Style,
    *,
    waking_hours_per_day: float = 16.0,
) -> str:
    who = alert.account or "default"
    profile = alert.flexibility_profile
    value_usd = profile.value_at_risk_usd if profile else None
    remaining = alert.remaining_percent

    if value_usd is not None and value_usd > 0.01 and alert.window_minutes:
        active_cycles = (waking_hours_per_day * DAYS_PER_MONTH * 60) / alert.window_minutes
        monthly_waste = value_usd * active_cycles
        return s.dim(
            f"  · {provider_display_name(alert.provider)} · {who} · "
            f"{alert.window_label}: {remaining:.0f}% left per cycle "
            f"(~${value_usd:.2f}/cycle ≈ ~${monthly_waste:.2f}/month wasted at this pace)"
        )
    return s.dim(
        f"  · {provider_display_name(alert.provider)} · {who} · {alert.window_label}: {remaining:.0f}% left per cycle"
    )


def _summary_alert_line(alert: UseOrLoseAlert, s: _Style) -> str:
    icon = URGENCY_ICON.get(alert.urgency, "   ")
    badge = s.urgency(alert.urgency, f"[{icon} {alert.urgency.value.upper():8}]")
    when = _human_deadline(alert.days_until_reset, estimated=alert.deadline_is_estimated)
    who = alert.account or "default"
    return (
        f"    {badge} {s.bold(provider_display_name(alert.provider))} · {who} · "
        f"{alert.window_label}: {alert.remaining_percent:.0f}% left · use {when}"
    )


def _time_bucket(days: float | None) -> str:
    if days is None:
        return "later / unknown reset"
    if days <= 1:
        return "within 24 hours"
    if days <= 3:
        return "within 3 days"
    if days <= 7:
        return "within 7 days"
    if days <= 14:
        return "within 14 days"
    return "later / unknown reset"


def _human_deadline(days: float | None, *, estimated: bool = False) -> str:
    if days is None:
        return "before the next reset (time unknown)"
    if days <= 0:
        return "immediately (reset imminent or past)"
    if estimated:
        whole_days = max(1, int(days + 0.5))
        unit = "day" if whole_days == 1 else "days"
        return f"within ~{whole_days} {unit}"
    if days < 1:
        hours = max(1, int(round(days * 24)))
        return f"within ~{hours}h"
    return f"within {days:.1f} days"


def _sorted_accounts(accounts: list[AccountUsage]) -> list[AccountUsage]:
    return sorted(
        accounts,
        key=lambda a: (
            provider_display_name(a.provider).casefold(),
            (a.account or "").casefold(),
            a.source.casefold(),
        ),
    )


def _render_account(
    acc: AccountUsage,
    s: _Style,
    *,
    config: dict[str, Any] | None = None,
    learned_burn_rates: dict[str, tuple[float, int]] | None = None,
) -> list[str]:
    lines: list[str] = []
    cfg = config or {}
    raw_plans = cfg.get("plans")
    raw_analysis = cfg.get("analysis")
    plans: dict[str, Any] = raw_plans if isinstance(raw_plans, dict) else {}
    analysis: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    rates = learned_burn_rates or {}

    head = s.bold(provider_display_name(acc.provider))
    if acc.account:
        head += f" · account={acc.account}"
    if acc.plan:
        head += s.dim(f" · plan={acc.plan}")
    head += s.dim(f" · {_source_description(acc.source)}")
    lines.append(head)

    if acc.error:
        lines.append(s.red(f"  ERROR: {acc.error}"))

    if acc.usage_credits is not None:
        lines.extend(_usage_credits_lines(acc.usage_credits, s))
    elif acc.balance_usd is not None:
        lines.append(f"  balance: {s.green(f'${acc.balance_usd:.2f}')}")
    if acc.credits_remaining is not None and acc.usage_credits is None and acc.balance_usd is None:
        lines.append(f"  credits remaining: {acc.credits_remaining}")

    for w in acc.windows:
        rem = w.remaining()
        rem_s = f"{rem:.0f}% left" if rem is not None else "n/a"
        used_s = f"{w.used_percent:.0f}% used" if w.used_percent is not None else ""
        days = w.days_until_reset()
        if days is not None:
            reset_s = (
                f"resets {_human_deadline(days, estimated=not w.reset_time_is_precise())} ({_fmt_dt(w.resets_at)})"
            )
        elif w.reset_description:
            reset_s = w.reset_description
        else:
            reset_s = "reset unknown"
        bar = _colored_bar(rem if rem is not None else 0, s)
        rem_padded = f"{rem_s:10}"
        rem_colored = rem_padded
        if rem is not None:
            if rem >= 70:
                rem_colored = s.yellow(rem_padded)
            elif rem >= 40:
                rem_colored = s.cyan(rem_padded)
            else:
                rem_colored = s.green(rem_padded)
        lines.append(f"  quota: {w.label}")
        lines.append(f"    {bar} {rem_colored} {used_s:10} {s.dim(reset_s)}")

        if rem is not None and w.window_minutes:
            detail = _consumption_line(w, rem, acc.provider, plans, analysis, s, learned_burn_rates=rates)
            if detail:
                lines.append(s.dim(f"    {detail}"))

    for note in acc.notes:
        lines.append(s.dim(f"  · {note}"))
    lines.append("")
    return lines


def _render_cross_checks(checks: list[CrossCheck], s: _Style) -> list[str]:
    lines: list[str] = []
    order = {"warning": 0, "unavailable": 1, "consistent": 2}
    for check in sorted(
        checks,
        key=lambda item: (
            order.get(item.status, 9),
            item.provider.casefold(),
            (item.account or "").casefold(),
        ),
    ):
        # Soft labels: disagreements are expected with poll lag / hydrate / multi-account
        if check.status == "warning":
            status = s.yellow("NOTE")
            body = s.dim(check.message) if _looks_soft_cross_check(check.message) else check.message
        elif check.status == "unavailable":
            status = s.dim("SKIP")
            body = s.dim(check.message)
        elif check.status == "consistent":
            status = s.dim("OK")
            body = s.dim(check.message)
        else:
            status = check.status.upper()
            body = check.message
        subject = provider_display_name(check.provider)
        if check.account:
            subject += f" · account={check.account}"
        sources = " vs ".join(check.sources)
        lines.append(f"  [{status}] {s.bold(subject)} · {sources}")
        lines.append(f"    {body}")
    return lines


def _looks_soft_cross_check(message: str) -> bool:
    """True when copy already frames the gap as expected / non-fatal."""
    lower = message.casefold()
    soft_markers = (
        "normal when",
        "often expected",
        "does not mean",
        "poll",
        "last-good",
        "hydrate",
        "stale",
        "single-session",
        "did not match",
        "no independent",
        "two-tool cross-check is unavailable",
    )
    return any(m in lower for m in soft_markers)


def _colored_bar(remaining_percent: float, s: _Style, width: int = 12) -> str:
    remaining_percent = max(0.0, min(100.0, remaining_percent))
    filled = int(round((remaining_percent / 100.0) * width))
    body = "=" * filled + "-" * (width - filled)
    bar = f"[{body}]"
    if remaining_percent >= 70:
        return s.yellow(bar)
    if remaining_percent >= 40:
        return s.cyan(bar)
    return s.green(bar)


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "?"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _usage_credits_lines(credits: Any, s: _Style) -> list[str]:
    """Pretty-print Claude (or similar) extra-usage wallet beside plan windows."""
    cur = getattr(credits, "currency", None) or "USD"
    lines = [s.bold("  usage credits (extra / pay-as-you-go / on-demand)")]
    used = getattr(credits, "used", None)
    limit = getattr(credits, "limit", None)
    remaining = getattr(credits, "remaining", None)
    pct = getattr(credits, "used_percent", None)
    resets = getattr(credits, "resets_at", None)

    if used is not None and limit is not None:
        pct_s = f" · {pct:.0f}% of limit" if pct is not None else ""
        lines.append(f"    spent: {used:g} of {limit:g} {cur}{pct_s}")
    elif used is not None:
        lines.append(f"    spent: {used:g} {cur}")
    if remaining is not None:
        lines.append(f"    remaining headroom: {s.green(f'{remaining:g} {cur}')}")
    if resets is not None:
        lines.append(f"    resets: {_fmt_dt(resets)}")
    return lines


def _source_description(source: str) -> str:
    return {
        "cswap": "canonical source: cswap",
        "codexbar": "selected live source: CodexBar",
        "caut": "selected live source: caut",
        "openusage_ai": "selected live source: OpenUsage.ai",
        "openusage_sh": "selected live source: OpenUsage.sh",
        "tokscale": "selected live source: tokscale",
    }.get(source, f"source: {source}")


def _consumption_line(
    window: Any,
    remaining: float,
    provider: str,
    plans: dict[str, Any],
    analysis: dict[str, Any],
    s: _Style,
    *,
    learned_burn_rates: dict[str, tuple[float, int]] | None = None,
) -> str | None:
    if not window.window_minutes:
        return None

    provider_key = provider_config_key(provider)
    plan_meta: dict[str, Any] = {}
    meta = plans.get(provider_key)
    if isinstance(meta, dict):
        plan_meta = meta

    monthly_price = plan_meta.get("monthly_price")
    value_multipliers = plan_meta.get("value_multiplier")
    waking = float(analysis.get("waking_hours_per_day", 16))
    duration_kind = classify_window_minutes(window.window_minutes)

    flex_class, flex_score = _classify_flexibility(
        window_minutes=window.window_minutes, provider=provider, config=analysis
    )

    value_usd: float | None = None
    if monthly_price is not None and window.window_minutes:
        window_mult = 1.0
        if isinstance(value_multipliers, dict) and duration_kind:
            window_mult = float(value_multipliers.get(duration_kind, 1.0))
        value_usd = round(
            _compute_value_at_risk(
                remaining=remaining,
                window_minutes=window.window_minutes,
                monthly_price=float(monthly_price),
                waking_hours_per_day=waking,
                value_multiplier=window_mult,
            ),
            2,
        )

    flex_bar = "░" if flex_score <= 0.1 else "▓" if flex_score >= 0.9 else "▒"
    class_label = flex_class.value

    parts: list[str] = []
    if value_usd is not None:
        parts.append(f"${value_usd:.2f}")
    parts.append(f"flex:{flex_bar} {class_label}")

    capacity = window.refill_capacity
    capacity_unit = window.refill_capacity_unit
    if capacity is None and duration_kind:
        overrides_cfg = analysis.get("provider_overrides") or {}
        overrides = overrides_cfg if isinstance(overrides_cfg, dict) else {}
        prov_overrides = overrides.get(provider_key)
        if isinstance(prov_overrides, dict):
            window_overrides = prov_overrides.get(duration_kind)
            if isinstance(window_overrides, dict):
                capacity = window_overrides.get("refill_capacity")
                if capacity_unit is None:
                    capacity_unit = window_overrides.get("refill_capacity_unit")

    if capacity and window.window_minutes:
        unit = capacity_unit or ""
        parts.append(f"{capacity:.0f}{unit}/cycle")

    # Pace fragment for detail view (on-pace windows still show here).
    learned_rate: float | None = None
    learned_n = 0
    rates = learned_burn_rates or {}
    if rates and duration_kind:
        # Canonical provider id, not the config key — that is how
        # history.compute_learned_burn_rates stores them.
        rate_key = f"{canonical_provider(provider)}:{duration_kind}"
        if rate_key in rates:
            learned_rate, learned_n = rates[rate_key]
    try:
        pace_profile = compute_pace(
            window,
            now=utcnow(),
            learned_rate_per_day=learned_rate,
            learned_sample_count=learned_n,
        )
    except Exception:  # noqa: BLE001
        pace_profile = None
    if pace_profile is not None and pace_profile.pace_ratio is not None:
        pace_s = f"pace {pace_profile.pace_ratio:.1f}x"
        if pace_profile.learned_sample_count > 0:
            n = pace_profile.learned_sample_count
            pace_s += f" (blended w/ history, {n} sample{'s' if n != 1 else ''})"
        parts.append(pace_s)

    return " · ".join(parts) if parts else None


def render_chat_report(snapshot: Snapshot, alerts: list[UseOrLoseAlert]) -> str:
    """Deprecated shim — delegates to :mod:`aiuse.chat_format`."""
    from aiuse.chat_format import render_chat_report as _render

    return _render(snapshot, alerts)
