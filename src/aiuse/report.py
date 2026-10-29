"""Human-readable terminal report (pretty by default)."""

from __future__ import annotations

import math
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TextIO

from aiuse.analysis.history import (
    compute_learned_burn_rates,
    history_section_lines,
    should_learn_from_history,
)
from aiuse.analysis.pace import (
    POOL_SCOPE_LABELS,
    format_pace,
    governing_partition,
    matrix_pool_scope,
    overall_pace,
    partition_independent_pools,
    pool_key_for_window,
    window_pace,
)
from aiuse.analysis.ranking import (
    _BAND_ATTENTION,
    _BAND_CONSERVE,
    _BAND_EMPTY,
    _BAND_ERROR,
    _BAND_MID,
    _BAND_NA,
    _BAND_USE,
    _alert_queue_priority,
    _known_reset_priority,
    _ladder_coverage_key,
    _ladder_sort_key,
    _pool_id_for_label,
    _Pricing,
    _Priority,
    alert_priority_band,
    alert_sort_key,
)
from aiuse.analysis.suggest import pick_suggestion
from aiuse.analysis.use_or_lose import (
    _DEFAULT_WAKING_HOURS,
    DAYS_PER_MONTH,
    _classify_flexibility,
    _compute_value_at_risk,
    _plan_meta,
    window_value_multiplier,
)
from aiuse.models import (
    OPERATOR_APPROVAL_NOTICE,
    AccountUsage,
    BillingKind,
    CrossCheck,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    canonical_provider,
    classify_window_minutes,
    claude_model_scope,
    effective_window_minutes,
    infer_window_clock,
    parse_dt,
    provider_config_key,
    provider_display_name,
    utcnow,
)
from aiuse.timefmt import format_as_of, format_clock, lead_phrase

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
    "3rd-party": "3rd",
    "on-demand": "dmnd",
    "grok-bot": "grok",
}


def _show_total_rows(config: dict[str, Any] | None) -> bool:
    analysis = (config or {}).get("analysis")
    if not isinstance(analysis, dict):
        return False
    return bool(analysis.get("show_total_rows", False))


def _hide_ceiling_pool_row(account: AccountUsage, pool_id: str, config: dict[str, Any] | None) -> bool:
    if _show_total_rows(config) or not pool_id:
        return False
    from aiuse.analysis.ceilings import CEILING_FAMILIES
    from aiuse.analysis.selfdescribe import pool_family_id

    families = CEILING_FAMILIES.get(account.provider, {})
    for ceiling_family, sub_families in families.items():
        ceiling_slug = f"cursor_{ceiling_family}" if account.provider == "cursor" else ceiling_family
        if pool_id != ceiling_slug and pool_id != ceiling_family:
            continue
        for window in account.windows:
            fam = pool_family_id(account.provider, window.label, window.window_id)
            if fam in sub_families:
                return True
    return False


def _hide_ceiling_window(account: AccountUsage, window: QuotaWindow, config: dict[str, Any] | None) -> bool:
    from aiuse.analysis.selfdescribe import pool_family_id

    family = pool_family_id(account.provider, window.label, window.window_id)
    return _hide_ceiling_pool_row(
        account, f"cursor_{family}" if account.provider == "cursor" and family else family or "", config
    )


def _find_account(snapshot: Snapshot | None, provider: str, account: str | None) -> AccountUsage | None:
    if snapshot is None:
        return None
    want = (account or "").casefold()
    for row in snapshot.accounts:
        if row.provider == provider and (row.account or "").casefold() == want:
            return row
    return None


def _skip_hidden_ceiling_alert(
    snapshot: Snapshot | None,
    alert: UseOrLoseAlert,
    config: dict[str, Any] | None,
) -> bool:
    """True when a ceiling-only alert should not add a matrix/ladder row."""
    account = _find_account(snapshot, alert.provider, alert.account)
    if account is None:
        return False
    pool_id = _pool_id_for_label(alert.window_label, alert.provider)
    return _hide_ceiling_pool_row(account, pool_id, config)


def _cursor_matrix_used_percent(
    account: AccountUsage,
    window: QuotaWindow,
    used: float,
) -> float:
    """Effective used % for Cursor breakdown rows when the shared total is tighter.

    The matrix shows that lower remaining figure and does not print the
    "own N% left, capped by total" sentence. ``--full`` and ``--for-chat``
    still explain the cap.
    """
    from aiuse.analysis.ceilings import effective_remaining, window_is_breakdown
    from aiuse.analysis.selfdescribe import pool_family_id

    family = pool_family_id(account.provider, window.label, window.window_id)
    if not window_is_breakdown(account.provider, family):
        return used
    effective_left, _ = effective_remaining(window, account.provider, account.windows)
    if effective_left is None:
        return used
    return max(0.0, 100.0 - effective_left)


@dataclass
class _MatrixRow:
    """One line of the usage table: a single account, or one pool within it.

    ``clocks`` maps a clock key ("5h" / "weekly" / "monthly") to the cell for
    that column. ``note`` replaces the whole numeric tail for rows that have no
    windows to bucket — failed fetches and non-expiring prepaid balances.
    ``tail_note`` annotates a row that *does* render clocks (e.g. a Grok row
    whose wallet rides along after the weekly cell) instead of hiding them.
    """

    sort_key: tuple
    band: int
    queue_score: int | None
    service: str
    account: str
    account_full: str | None = None
    scope: str = "—"
    clocks: dict[str, _ClockCell] = field(default_factory=dict)
    value_usd: float | None = None
    pace_percent: int | None = None
    pace_early: bool = False
    note: str | None = None
    tail_note: str | None = None
    stale: bool = False
    measured_at: datetime | None = None
    sublimit_notes: list[str] = field(default_factory=list)
    # A routing limit (Claude Fable), not a pool to burn. Blank tag and score.
    unranked: bool = False
    # The account requires operator approval before an agent spends it (omp):
    # empty 5H and WEEK cells carry the notice. A row with no clocks folds it
    # into the situation text instead.
    operator_gate: bool = False


@dataclass
class _ClockCell:
    """A percentage for one clock column, plus how much to trust it."""

    used_percent: float
    inferred: bool = False  # clock bucket was guessed, not declared
    folded: int = 0  # extra windows on this clock, collapsed into this one
    days_until_reset: float | None = None
    reset_estimated: bool = False
    window_minutes: int | None = None  # full window length, when the source or label states it
    pace_percent: int | None = None
    pace_early: bool = False


_UNSTARTED_MARK = "↻"  # a rolling window's clock starts on first use
_OPERATOR_GATE_TEXT = f"({OPERATOR_APPROVAL_NOTICE})"
# Shorter wording for terminals too narrow for the full text; keeps the instruction readable.
_OPERATOR_GATE_SHORT = "(AI: ask operator)"
# The two adjacent clock columns the operator-gate text spans, centered.
_GATE_CLOCKS = ("5h", "weekly")
_GATE_FIRST = [key for key, _label in CLOCK_COLUMNS].index(_GATE_CLOCKS[0])
_UNSTARTED_LEGEND = f"{_UNSTARTED_MARK} = starts on first use"
_FOLD_MARK = "+"  # more than one window on this clock, showing the most-used
_FOLD_LEGEND = f"{_FOLD_MARK} = >1 window, most-used shown"


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

    show_pace: bool = True
    show_value: bool = True
    compact_deadline: bool = False
    short_scope: bool = False
    show_scope: bool = True
    show_account: bool = True
    fold_identity: bool = False
    # Widen 5H + WEEK so an operator-gate row's text fits whole; the last
    # thing a narrow terminal gives up (the text is then clamped).
    reserve_gate: bool = True


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
    ai_note: bool = True,
) -> str:
    """
    Pretty report.

    **Default** (and ``brief=True``): the clock matrix only (stdout), via
    ``render_clock_matrix`` — one row per account/pool, one column per reset
    clock. Meta / errors / ``ai --full`` hint belong on stderr via
    ``render_stderr_meta``.

    **Full** (``full=True``): long report with per-provider detail.

    ``brief`` is kept for CLI compatibility and is ignored when ``full=True``.
    ``ai_note`` is passed to ``render_clock_matrix`` (default mode only).
    """
    del brief  # Alias of default; retained so callers need not change overnight.
    s = _Style(use_color(force=color))
    if not full:
        width = glance_width if glance_width is not None else terminal_width()
        return render_clock_matrix(alerts, snapshot=snapshot, config=config, s=s, width=width, ai_note=ai_note)

    lines: list[str] = []
    width = ACTION_PLAN_WIDTH
    plan_width = glance_width if glance_width is not None else width
    accounts = _sorted_accounts(snapshot.accounts)
    n_accounts = len(accounts)
    n_actionable = sum(1 for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE))

    lines.append(s.bold("=" * width))
    title = "AI USAGE — USE IT OR LOSE IT (full)"
    lines.append(s.bold(s.cyan(title)))
    meta = f"Collected at {format_clock(snapshot.collected_at, date=True)}"
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

    if getattr(snapshot, "disabled_services", None):
        lines.append("")
        lines.append(s.bold("## Disabled by operator"))
        lines.append(s.dim("-" * width))
        for provider, reason in sorted(snapshot.disabled_services.items()):
            lines.append(
                f"  {provider_display_name(provider)}: {reason} "
                "— off-limits entirely (no quota collected); remove the [disabled_services] entry to re-enable."
            )

    if getattr(snapshot, "hidden_pools", None):
        lines.append("")
        lines.append(s.bold("## Hidden by operator"))
        lines.append(s.dim("-" * width))
        for key, reason in sorted(snapshot.hidden_pools.items()):
            lines.append(f"  {key}: {reason} — display-only; remove the [hidden_pools] entry to show again.")

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


_WEEKLY_MINUTES = 7 * 24 * 60

# Ladder display tags, keyed by the bands of ``aiuse.analysis.ranking``.  The
# numeric band order is the visual order.
_BAND_TAG = {
    _BAND_ERROR: ("error", "red"),
    _BAND_EMPTY: ("empty", "red"),
    _BAND_NA: ("n/a  ", "dim"),
    _BAND_CONSERVE: ("slow ", "yellow"),
    _BAND_MID: ("mid  ", "cyan"),
    _BAND_USE: ("use  ", "green"),
}


# The ``##`` score of a slow, mid or use row is the dollar figure its priority leads
# with (``analysis.ranking``: capacity in slow, projected waste in mid and use),
# log-scaled between these clamps, 6 of a band's 24 points per decade: $0.01 or
# less is the band's floor, $100 or more its ceiling.
_AT_RISK_FLOOR_USD = 0.01
_AT_RISK_CEIL_USD = 100.0
# Score range of each active band.  A band's ceiling sits one below the next
# band's floor, so a category change adds evidence without leaving a gap.
_BAND_SCORE_RANGE = {_BAND_CONSERVE: (25, 49), _BAND_MID: (50, 74), _BAND_USE: (75, 99)}


def _queue_score(band: int, priority: _Priority) -> int | None:
    """Map queue evidence onto one semantic 0–99 ``use next`` scale.

    The category is evidence in its own right: ``slow`` means use is currently
    unsafe, ``mid`` has no active burn recommendation, and ``use`` does.  The
    thresholds are therefore shared action states, not three independently
    stretched mini-scales.  In particular, the strongest row on either side
    of a category boundary differs by one point rather than an artificial
    band-sized jump.  Inside a band the score is the row's dollars at risk
    (``priority[0]``) log-scaled onto the band's range.
    """
    if band in (_BAND_ERROR, _BAND_NA):
        return None
    if band == _BAND_EMPTY:
        return 0

    low, high = _BAND_SCORE_RANGE[band]
    at_risk = max(_AT_RISK_FLOOR_USD, min(_AT_RISK_CEIL_USD, float(priority[0])))
    fraction = math.log(at_risk / _AT_RISK_FLOOR_USD) / math.log(_AT_RISK_CEIL_USD / _AT_RISK_FLOOR_USD)
    return low + round(fraction * (high - low))


def _account_is_non_expiring_prepaid(account: AccountUsage) -> bool:
    """Purchased API credits that roll until spent (Deepseek, OpenRouter, …)."""
    return account.billing_kind in (BillingKind.PREPAID_BALANCE, BillingKind.PAYG_API)


def _positive_usd(amount: float | None) -> float | None:
    if amount is None or amount <= 0 or round(amount, 2) <= 0:
        return None
    return float(amount)


def _bedrock_meter_usd(account: AccountUsage) -> float | None:
    """Bedrock's month-to-date PAYG spend, including a measured ``$0.00``.

    ``None`` means the row names no total. A ``balance_usd`` on this provider
    is the old misread of ``Spend: $…`` and still counts up.
    """
    if canonical_provider(account.provider) != "bedrock":
        return None
    uc = account.usage_credits
    if uc is not None and uc.used is not None:
        return float(uc.used)
    if account.balance_usd is not None:
        return float(account.balance_usd)
    return None


def _bedrock_stored_unknown(account: AccountUsage) -> bool:
    """An older snapshot stored this Bedrock total as ``error="UNKNOWN"``.

    A measured ``$0.00`` used that string and omitted ``usage_credits``. Watch
    reuses the snapshot and does not recollect, so the board reads the sentinel
    as ``↑ $0.00 PAYG`` instead of the error band. Any other error string stays
    an error.
    """
    return canonical_provider(account.provider) == "bedrock" and (account.error or "").strip() == "UNKNOWN"


def _bedrock_display_spend(account: AccountUsage) -> float | None:
    """Positive Bedrock spend. A measured zero stays on :func:`_bedrock_meter_usd`."""
    return _positive_usd(_bedrock_meter_usd(account))


def _account_is_spend_up_payg(account: AccountUsage) -> bool:
    """PAYG meter that counts *up* from $0 (Muse, Bedrock), not prepaid remaining."""
    if _bedrock_display_spend(account) is not None:
        return True
    uc = account.usage_credits
    return (
        account.billing_kind == BillingKind.PAYG_API
        and uc is not None
        and uc.used is not None
        and account.balance_usd is None
    )


def _overage_remaining_usd(account: AccountUsage) -> float | None:
    """Prepaid overage dollars still left. Not a quota percent."""
    uc = account.usage_credits
    if uc is not None and uc.remaining is not None:
        return float(uc.remaining)
    for window in account.windows:
        if (window.window_id or "").casefold() != "on-demand":
            continue
        if window.refill_capacity is None or window.remaining() is None:
            continue
        return float(window.refill_capacity) * float(window.remaining()) / 100.0
    return None


def dollar_meter(
    direction: str,
    kind: str,
    amount: float,
    total: float | None = None,
    *,
    compact: bool = False,
) -> str:
    """One dollar figure with its direction and its kind.

    ``down`` is a balance being spent (plan grant or prepaid wallet). ``up``
    is a meter that climbs (pay-as-you-go). ``kind`` is ``plan`` (included in
    a monthly grant), ``prepaid`` (bought ahead), or ``PAYG``. ``compact`` is
    the OTHER column; the spaced form is the ladder, the detail view, and chat.
    """
    arrow = "↓" if direction == "down" else "↑"
    if compact:
        head = f"{arrow}${amount:.2f}"
        if total is not None:
            head += f"/${total:.2f}"
        return f"{head} {kind}"
    if direction == "down" and kind == "plan":
        if total is not None:
            return f"↓ ${amount:.2f} left of ${total:.2f} plan"
        return f"↓ ${amount:.2f} plan"
    if direction == "down":
        if total is not None:
            return f"↓ ${amount:.2f} left of ${total:.2f} {kind}"
        return f"↓ ${amount:.2f} {kind}"
    if total is not None:
        return f"↑ ${amount:.2f} of ${total:.2f} {kind}"
    return f"↑ ${amount:.2f} {kind}"


def _usd_refill_total(account: AccountUsage) -> float | None:
    for window in account.windows:
        if window.refill_capacity_unit == "usd" and window.refill_capacity:
            return float(window.refill_capacity)
    return None


def _bedrock_as_of(account: AccountUsage) -> str:
    """`` (as of 10/29 9:02pm)`` from the Cost Explorer measurement, else the row time."""
    if canonical_provider(account.provider) != "bedrock":
        return ""
    raw = account.raw if isinstance(account.raw, dict) else {}
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    stamp = usage.get("bedrockReadAt") if isinstance(usage, dict) else None
    when = parse_dt(stamp) if isinstance(stamp, str) else None
    if when is None:
        when = account.collected_at
    if when is None:
        return ""
    return " " + format_as_of(when)


def _extra_usage_wallet_fragment(account: AccountUsage) -> str:
    """Short suffix when a subscription row also has a prepaid overage wallet."""
    remaining = _overage_remaining_usd(account)
    if remaining is None:
        return ""
    return " · " + dollar_meter("down", "prepaid", remaining)


def _matrix_row_shows_wallet(account: AccountUsage, pool_id: str) -> bool:
    """One wallet suffix per account. Cursor shows it on auto, not on every pool."""
    if account.provider != "cursor":
        return pool_id != "cursor_on_demand"
    from aiuse.analysis.pace import cursor_pool_family

    families = {
        cursor_pool_family(window.label, window_id=window.window_id, provider="cursor") for window in account.windows
    }
    if "auto" in families:
        return pool_id == "cursor_auto"
    if "third_party" in families:
        return pool_id == "cursor_third_party"
    return pool_id not in {"cursor_on_demand", ""}


def _api_inventory_note(account: AccountUsage) -> str:
    """Ladder/matrix note for a prepaid countdown or a pay-as-you-go meter."""
    if canonical_provider(account.provider) == "bedrock":
        # Pay-as-you-go only. A measured $0 is that total, with the reading time.
        # The UNKNOWN error string is that same $0 on a snapshot watch reused.
        spent = _bedrock_meter_usd(account)
        if spent is None and _bedrock_stored_unknown(account):
            spent = 0.0
        if spent is None:
            return "UNKNOWN"
        uc = account.usage_credits
        limit = float(uc.limit) if uc is not None and uc.limit is not None and uc.limit > 0 else None
        return dollar_meter("up", "PAYG", spent, limit) + _bedrock_as_of(account)
    if _account_is_spend_up_payg(account):
        uc = account.usage_credits
        assert uc is not None and uc.used is not None
        limit = float(uc.limit) if uc.limit is not None else None
        return dollar_meter("up", "PAYG", float(uc.used), limit)
    if account.balance_usd is not None:
        if account.spent_usd is not None:
            left = dollar_meter("down", "plan", account.balance_usd, _usd_refill_total(account))
            return f"↑ ${account.spent_usd:.2f} spent · {left}"
        return dollar_meter("down", "prepaid", account.balance_usd)
    if account.credits_remaining is not None:
        return f"↓ {account.credits_remaining:g} credits"
    return "API"


def _account_prepaid_is_depleted(account: AccountUsage) -> bool:
    """Empty band only for prepaid wallets at/under $0 — not for spend-up PAYG."""
    if canonical_provider(account.provider) == "bedrock" or _account_is_spend_up_payg(account):
        return False
    return account.balance_usd is not None and account.balance_usd <= 0.0


def _window_use_urgency(window: QuotaWindow) -> float:
    """Soft 0–100 recommendation score for an unalerted live window.

    Capacity and deadline pressure multiply: plentiful capacity far from reset
    is less urgent than the same capacity near reset.  The previous additive
    formula routinely gave healthy monthly pools raw scores above active burn
    alerts, which then had to be hidden by category-local score offsets.
    Unknown reset timing is treated like a soft three-week horizon.
    """
    rem = max(0.0, min(100.0, float(window.remaining() or 0.0)))
    days = window.days_until_reset()
    if days is None:
        deadline_pressure = 0.25
    else:
        days_clamped = max(0.0, float(days))
        deadline_pressure = 1.0 / (1.0 + days_clamped / 7.0)
    return rem * deadline_pressure


def _unalerted_window_band(window: QuotaWindow | None) -> int:
    """Band for a live window that analysis did not turn into an alert.

    The full remaining-capacity range is deliberately simple here: only zero
    (or a malformed negative value) is empty; positive capacity remains an
    on-pace ``mid`` row unless pace analysis emitted a conserve/burn alert.
    """
    remaining = window.remaining() if window is not None else None
    if remaining is not None and remaining <= 0.0:
        return _BAND_EMPTY
    return _BAND_MID


def _account_use_urgency(account: AccountUsage) -> float:
    """On-pace / no-alert accounts: soft queue score from governing window."""
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


def _account_queue_priority(
    account: AccountUsage,
    band: int,
    window: QuotaWindow | None = None,
    pricing: _Pricing | None = None,
    now: datetime | None = None,
) -> _Priority:
    """Category-specific priority for rows without an analysis alert.

    A live window leads with the dollars its own pace projects to expire unspent
    (no alert carried that pace, so it is measured here at ``now``), or with what
    it holds when there is no pace to project; see :class:`_Pricing`.
    """
    if band == _BAND_ERROR:
        return (0.0, 0.0, 0.0)
    if band == _BAND_EMPTY:
        representative = window or _pick_representative_window(account.windows)
        days = representative.days_until_reset() if representative is not None else None
        known, sooner = _known_reset_priority(days)
        return (known, sooner, 0.0)
    if band == _BAND_NA:
        # Spend-up PAYG is a cost meter, not capacity.  Count-down inventory is
        # optional capacity: larger comparable balances sit lower in this
        # explicitly non-urgent lane.
        if _account_is_spend_up_payg(account):
            return (0.0, 0.0, 0.0)
        if account.balance_usd is not None:
            return (2.0, float(account.balance_usd), 0.0)
        if account.credits_remaining is not None:
            return (1.0, float(account.credits_remaining), 0.0)
        return (0.0, 0.0, 0.0)
    representative = window or _pick_representative_window(account.windows)
    if representative is None:
        # Nothing to size: no dollars at risk, the floor of its band.
        return (0.0, _account_use_urgency(account), 0.0, 0.0)
    at_risk = (pricing or _Pricing.from_config(None)).window_at_risk_usd(
        account.provider, account.account, representative, now
    )
    return (at_risk, _window_use_urgency(representative), 0.0, 0.0)


def _pool_queue_priority(
    account: AccountUsage,
    band: int,
    windows: list[QuotaWindow],
    pricing: _Pricing | None = None,
    now: datetime | None = None,
) -> _Priority:
    """Queue a no-alert pool using its strongest meaningful reset clock.

    A longest-window-only heuristic hides useful weekly opportunities behind a
    monthly parent.  Taking every window instead makes ubiquitous nested 5h
    rate limits dominate.  Pick one representative per clock, ignore 5h when
    the pool also has weekly/monthly clocks, then take the clock with the most
    dollars at risk (its projected waste), the soft recommendation breaking ties.
    Single-clock short-window providers still rank normally.
    """
    if band != _BAND_MID or not windows:
        return _account_queue_priority(account, band, _pick_representative_window(windows), pricing, now)

    by_clock: dict[str, list[QuotaWindow]] = {}
    for window in windows:
        if window.remaining() is None:
            continue
        clock, _inferred = infer_window_clock(window)
        by_clock.setdefault(clock or "unknown", []).append(window)
    if not by_clock:
        return _account_queue_priority(account, band, pricing=pricing, now=now)

    clocks = list(by_clock)
    if "5h" in by_clock and any(clock in by_clock for clock in ("weekly", "monthly")):
        clocks.remove("5h")
    pricing = pricing or _Pricing.from_config(None)
    candidates = [
        (
            pricing.window_at_risk_usd(account.provider, account.account, representative, now),
            _window_use_urgency(representative),
            0.0,
            0.0,
        )
        for clock in clocks
        if (representative := _pick_representative_window(by_clock[clock])) is not None
    ]
    return max(candidates) if candidates else _account_queue_priority(account, band, pricing=pricing, now=now)


def _pool_id_for_windows(windows: list[QuotaWindow], *, provider: str | None = None) -> str:
    for window in windows:
        key = pool_key_for_window(window, provider=provider)
        if key:
            return key
    return ""


def _context_fragment(account: AccountUsage) -> str:
    """One line for ACP context fill. It is not a plan-quota percent."""
    ctx = account.context_usage
    if ctx is None or ctx.used is None or not ctx.size:
        return "ACP context (no window size; not plan quota)"
    left = ctx.remaining_percent
    left_s = f"{left:.0f}% of the context window left" if left is not None else "context window"
    return f"ACP context {ctx.used}/{ctx.size} tokens ({left_s}; not plan quota)"


def _account_has_usage(account: AccountUsage) -> bool:
    return not account.error and (
        bool(account.windows)
        or account.balance_usd is not None
        or account.credits_remaining is not None
        or account.usage_credits is not None
        or account.context_usage is not None
        # Authenticated prepaid/PAYG with no meter yet (e.g. Muse key OK, billing API 404).
        or account.billing_kind in (BillingKind.PREPAID_BALANCE, BillingKind.PAYG_API)
    )


def _pick_representative_window(windows: list[QuotaWindow]) -> QuotaWindow | None:
    """Governing / included bar for a pool (or whole account)."""
    usable = [w for w in windows if w.remaining() is not None and claude_model_scope(w.label) is None]
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
    account: str | None,
    plans: dict[str, Any],
    analysis: dict[str, Any],
) -> float | None:
    """Dollar value of the unused part of a window, or None when unpriceable.

    Needs both a configured ``monthly_price`` for the plan (this account's own
    ``[plans.<provider>."<account>"]`` price, else the provider's) and a known
    window duration, so providers that report neither (grok, the unnamed
    antigravity quotas) legitimately have no dollar figure.  Unlike the board's
    capacity ordering there is no cheapest-plan fallback: an unpriced provider
    shows no dollars rather than an invented figure.
    """
    if not window.window_minutes:
        return None
    plan_meta = _plan_meta(provider, plans, account)
    monthly_price = plan_meta.get("monthly_price")
    if monthly_price is None:
        return None
    return round(
        _compute_value_at_risk(
            remaining=remaining,
            window_minutes=window.window_minutes,
            monthly_price=float(monthly_price),
            waking_hours_per_day=float(analysis.get("waking_hours_per_day", _DEFAULT_WAKING_HOURS)),
            value_multiplier=window_value_multiplier(plan_meta, window.window_minutes),
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
    if layout.show_pace:
        layout.show_pace = False
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
    if layout.reserve_gate and any(_row_shows_gate(row) for row in rows):
        layout.reserve_gate = False
        return True
    return False


def _row_shows_gate(row: _MatrixRow) -> bool:
    """An operator-gated row whose 5H and WEEK cells are both empty.

    Data in either cell wins: the notice only ever replaces empty cells.
    """
    return row.operator_gate and not any(key in row.clocks for key in _GATE_CLOCKS)


def _gate_span_width(w_clocks: list[int]) -> int:
    """Width of the 5H and WEEK cells plus the one-space gap between them."""
    return w_clocks[_GATE_FIRST] + 1 + w_clocks[_GATE_FIRST + 1]


def _gate_cell(s: _Style, width: int) -> str:
    """The operator-gate text centered in ``width`` columns, dim.

    The full notice when it fits, else the short wording, else the short wording clamped.
    """
    if len(_OPERATOR_GATE_TEXT) <= width:
        text = _OPERATOR_GATE_TEXT
    elif len(_OPERATOR_GATE_SHORT) <= width:
        text = _OPERATOR_GATE_SHORT
    else:
        text = _clamp_display_width(_OPERATOR_GATE_SHORT, width)
    left = (width - len(text)) // 2
    # Pad outside the dim codes, like the other empty cells.
    return " " * left + s.dim(text) + " " * (width - left - len(text))


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
    """`100u/0l` — percent used / percent left, labeled so the pair cannot be misread.

    The fold mark (``+``, more windows on this clock) is not part of it: it hangs
    after the reset span like ``↻`` (``_fold_mark``), so the right-aligned pair is
    the same width in every row and the ``l`` lines up down the column.
    """
    used_txt = _format_used_percent(cell.used_percent).rstrip("%")
    left = max(0.0, 100.0 - float(cell.used_percent))
    left_txt = _format_remaining_percent(left).rstrip("%")
    return f"{used_txt}u/{left_txt}l"


def _fold_mark(cell: _ClockCell) -> str:
    return _FOLD_MARK if cell.folded else ""


def _clock_span(cell: _ClockCell, *, compact: bool) -> tuple[_ResetSpan | None, bool]:
    """The span shown after a cell's percentages, and whether it is an unstarted window's full length.

    A cell with a reset timestamp shows the time left. A sub-day rolling window
    (the 5-hour kind) that has not been used yet (nothing consumed, no reset
    timestamp) cannot say when it resets -- its clock starts on first use -- so
    it shows its full length instead. Longer windows are usually anchored to a
    calendar or billing date, so an unused one gets no span. Otherwise there is
    no span.
    """
    span = _format_reset_span(cell.days_until_reset, estimated=cell.reset_estimated, compact=compact)
    if span is not None:
        return span, False
    if (
        cell.days_until_reset is None
        and cell.used_percent <= 0.0
        and cell.window_minutes
        and 0 < cell.window_minutes < 1440
    ):
        return _format_reset_span(cell.window_minutes / 1440.0, compact=compact), True
    return None, False


def _row_as_of(row: _MatrixRow) -> str:
    """``(as of 10/29 9:02pm)`` when this row's reading is past the freshness threshold."""
    if not row.stale or row.measured_at is None:
        return ""
    return format_as_of(row.measured_at)


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
    total = 5 + 1 + 2 + 1 + w_service  # band + ## + service
    if layout.show_account:
        w_account = max([len("ACCT")] + [len(account) for _svc, account, _scope in identities])
        total += 1 + w_account
    if layout.show_scope:
        w_scope = max([len("SCOPE")] + [len(scope) for _svc, _acct, scope in identities])
        total += 1 + w_scope
    if layout.show_value:
        total += 1 + _pace_column_width(rows)
    w_pct, w_spans = _clock_column_widths(rows, layout)
    for w_span in w_spans:
        total += 1 + (w_pct + ((1 + w_span) if w_span else 0))
    w_other = _other_column_width(rows)
    if w_other:
        total += 1 + w_other
    # Reserve the as-of stamp, or the start of a free-text note (never the
    # whole note), so those stay readable before numeric cells are squeezed.
    as_of = max((len(_row_as_of(row)) for row in rows), default=0)
    note_reserve = 5 if (not w_other and any(row.note for row in rows)) else 0
    extra = max(as_of, note_reserve)
    if extra:
        total += 1 + extra
    return total


def _pace_column_width(rows: list[_MatrixRow]) -> int:
    """Width of the PACE column: its header or the widest row pace."""
    return max(
        [len("PACE")]
        + [len(format_pace(row.pace_percent, row.pace_early)) for row in rows if row.pace_percent is not None]
    )


def _other_column_width(rows: list[_MatrixRow]) -> int:
    """Width of the OTHER column (wallet, grant, Fable-cap); 0 when no row has a tail note."""
    notes = [len(row.tail_note) for row in rows if row.tail_note and row.note is None]
    return max([len("OTHER")] + notes) if notes else 0


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
            span, unstarted = _clock_span(cell, compact=layout.compact_deadline)
            mark_width = len(_fold_mark(cell))
            if span is not None:
                w_spans[idx] = max(
                    w_spans[idx], len(span.plain()) + (len(_UNSTARTED_MARK) if unstarted else 0) + mark_width
                )
            elif mark_width:
                # No span, but the mark still needs its column after the pair.
                w_spans[idx] = max(w_spans[idx], mark_width)
    for idx, (_key, label) in enumerate(CLOCK_COLUMNS):
        w_pct = max(w_pct, len(label))
    if layout.show_pace:
        for idx, (key, _label) in enumerate(CLOCK_COLUMNS):
            suffix = max(
                [0]
                + [
                    len(f" {format_pace(row.clocks[key].pace_percent, row.clocks[key].pace_early)}")
                    for row in rows
                    if key in row.clocks and row.clocks[key].pace_percent is not None
                ]
            )
            w_spans[idx] += suffix
    if layout.reserve_gate and any(_row_shows_gate(row) for row in rows):
        clocks = [w_pct + ((1 + w_span) if w_span else 0) for w_span in w_spans]
        deficit = len(_OPERATOR_GATE_TEXT) - _gate_span_width(clocks)
        if deficit > 0:
            # Split the extra width between the two spans; a span that was 0
            # also gains its separating space, so it needs one column less.
            for idx, extra in ((_GATE_FIRST, deficit // 2), (_GATE_FIRST + 1, deficit - deficit // 2)):
                if extra <= 0:
                    continue
                w_spans[idx] = w_spans[idx] + extra if w_spans[idx] else max(1, extra - 1)
    return w_pct, w_spans


# Sourced from the analysis layer so the chat format names pools identically.
_POOL_SCOPE_LABELS = POOL_SCOPE_LABELS


def render_status_line(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    *,
    max_width: int = 120,
    config: dict[str, Any] | None = None,
) -> str:
    """Single-line status for shell prompts / status bars (``aiuse status``).

    No ANSI; compact summary of the most urgent burn/conserve alert (if any).  A
    burn leads as the one ``aiuse suggest`` names (the board's top row, priced from
    ``config``); a conserve alert leads only when its analyzer score tops every burn's.
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

    # Highest score first (same ordering as analyze_use_or_lose); among burns the
    # board's order decides which one leads.
    top = max(actionable, key=lambda a: (a.score, a.remaining_percent))
    if top.kind == "burn":
        top = pick_suggestion(burns, config) or top
    name = provider_display_name(top.provider)
    label = top.window_label
    rem = top.remaining_percent
    used = max(0.0, 100.0 - rem)
    pair = f"{used:.0f}% used / {rem:.0f}% left"
    if top.kind == "conserve":
        head = f"slow: {name} {label} {pair}"
    elif rem <= 0.0:
        head = f"empty: {name} {label} (100% used / 0% left)"
    else:
        head = f"use: {name} {label} {pair}"

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
    config: dict[str, Any] | None = None,
) -> str:
    """Stdout body: every provider, with each display band a useful queue.

    The fixed band order is error/empty/n/a/slow/mid/use.  Within a band,
    rows become more actionable toward the bottom according to that band's
    semantics (recovery, inventory, or dollars at risk in the window).
    """
    if s is None:
        s = _Style(use_color(force=color))
    pricing = _Pricing.from_config(config)
    now = snapshot.collected_at if snapshot is not None else None

    entries: list[tuple[tuple, str]] = []
    # Coverage includes independent pool id so Antigravity Gemini vs Claude/GPT
    # each keep a ladder row even when only one pool raised an alert.
    covered: set[tuple[str, str, str]] = set()

    for alert in alerts:
        if alert.urgency == Urgency.NONE:
            continue
        entries.append((alert_sort_key(alert, pricing), _priority_alert_line(alert, s, alert_priority_band(alert))))
        covered.add(
            _ladder_coverage_key(alert.provider, alert.account, _pool_id_for_label(alert.window_label, alert.provider))
        )

    accounts = _sorted_accounts(snapshot.accounts) if snapshot is not None else []
    for account in accounts:
        if not account.windows and account.context_usage is not None and not account.error:
            cov = _ladder_coverage_key(account.provider, account.account, "")
            if cov in covered:
                continue
            entries.append(
                (
                    _ladder_sort_key(_BAND_NA, (0.0, 0.0, 0.0), account.provider, account.account),
                    _priority_account_line(account, s, _BAND_NA),
                )
            )
            covered.add(cov)
            continue
        if (account.error or not _account_has_usage(account)) and not _bedrock_stored_unknown(account):
            cov = _ladder_coverage_key(account.provider, account.account, "")
            if cov in covered:
                continue
            entries.append(
                (
                    _ladder_sort_key(_BAND_ERROR, (0.0, 0.0, 0.0), account.provider, account.account),
                    _priority_error_line(account, s),
                )
            )
            covered.add(cov)
            continue
        if _account_is_non_expiring_prepaid(account) or _bedrock_stored_unknown(account):
            cov = _ladder_coverage_key(account.provider, account.account, "")
            if cov in covered:
                continue
            # A rolling prepaid balance is inventory only while there is money
            # to spend.  Zero or a negative balance is nevertheless exhausted
            # capacity and belongs with other empty services, not neutral n/a.
            # Spend-up PAYG (Muse) is never "empty" at $0 spent.
            depleted = _account_prepaid_is_depleted(account)
            band = _BAND_EMPTY if depleted else _BAND_NA
            entries.append(
                (
                    _ladder_sort_key(
                        band,
                        _account_queue_priority(account, band),
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
        pools = partition_independent_pools(account.windows, provider=account.provider) if account.windows else [[]]
        for pool in pools:
            pool_id = _pool_id_for_windows(pool, provider=account.provider) if pool else ""
            if _hide_ceiling_pool_row(account, pool_id, config):
                continue
            cov = _ladder_coverage_key(account.provider, account.account, pool_id)
            if cov in covered:
                continue
            window = _pick_representative_window(pool) if pool else None
            band = _unalerted_window_band(window)
            entries.append(
                (
                    _ladder_sort_key(
                        band,
                        _pool_queue_priority(account, band, pool, pricing, now),
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


# Which band wins when several alerts describe one pool lives in ranking
# (attention-grabbing states outrank quiet ones); see ``_BAND_ATTENTION``.


def _used_percent(window: QuotaWindow) -> float | None:
    """Percentage consumed, preferring the reported figure over the inverse."""
    if window.used_percent is not None:
        return float(window.used_percent)
    remaining = window.remaining()
    return None if remaining is None else max(0.0, 100.0 - float(remaining))


def _account_data_age(account: AccountUsage, now: datetime) -> float | None:
    """Seconds since this account's underlying reading (CodexBar ``updatedAt``, etc.).

    ``collected_at`` is the data freshness, not the snapshot time: a row reused
    across a throttle window still carries its original reading's timestamp, so
    this is the number the as-of stamp must key on.
    """
    if account.collected_at is None:
        return None
    return max(0.0, (now - account.collected_at).total_seconds())


def _build_matrix_rows(
    alerts: list[UseOrLoseAlert],
    snapshot: Snapshot | None,
    config: dict[str, Any] | None,
) -> list[_MatrixRow]:
    cfg = config or {}
    raw_plans = cfg.get("plans")
    raw_analysis = cfg.get("analysis")
    plans: dict[str, Any] = raw_plans if isinstance(raw_plans, dict) else {}
    # Ordering needs prices even when no config was passed; the dollar figure does not.
    pricing = _Pricing.from_config(cfg)
    analysis: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    try:
        fresh_threshold = float(analysis.get("fresh_threshold_seconds") or 1500.0)
    except (TypeError, ValueError):
        fresh_threshold = 1500.0

    # Collapse alerts onto the pool they describe, keeping the most
    # attention-grabbing band and the use-urgency that goes with it.
    pool_alert: dict[tuple[str, str, str], tuple[int, _Priority]] = {}
    for alert in alerts:
        if alert.urgency == Urgency.NONE:
            continue
        key = _ladder_coverage_key(
            alert.provider, alert.account, _pool_id_for_label(alert.window_label, alert.provider)
        )
        band = alert_priority_band(alert)
        candidate = (band, _alert_queue_priority(alert, band, pricing))
        current = pool_alert.get(key)
        stronger_same_band = False
        if current is not None and band == current[0]:
            # A pool with several conserve signals is governed by the most
            # restrictive one; every other queue keeps its most actionable
            # signal.  This affects which score positions the collapsed row.
            stronger_same_band = candidate[1] < current[1] if band == _BAND_CONSERVE else candidate[1] > current[1]
        if current is None or _BAND_ATTENTION[band] > _BAND_ATTENTION[current[0]] or stronger_same_band:
            pool_alert[key] = candidate

    rows: list[_MatrixRow] = []
    covered: set[tuple[str, str, str]] = set()
    # Accounts with no live windows are deferred: an alert may still describe
    # them (analysis can raise on data an account row cannot render), and the
    # alert is the more informative row. Same rule the ladder used.
    deferred: list[AccountUsage] = []
    now = snapshot.collected_at if snapshot is not None else utcnow()

    for account in _sorted_accounts(snapshot.accounts) if snapshot is not None else []:
        service = provider_display_name(account.provider)
        short = _short_account(account.account)

        context_only = not account.windows and account.context_usage is not None and not account.error
        if (
            account.error
            or not _account_has_usage(account)
            or _account_is_non_expiring_prepaid(account)
            or context_only
        ):
            deferred.append(account)
            continue

        for pool in (
            partition_independent_pools(account.windows, provider=account.provider) if account.windows else [[]]
        ):
            pool_id = _pool_id_for_windows(pool, provider=account.provider) if pool else ""
            if pool_id == "cursor_on_demand":
                continue
            if _hide_ceiling_pool_row(account, pool_id, cfg):
                continue
            clocks: dict[str, _ClockCell] = {}
            value: float | None = None
            sublimit_notes: list[str] = []
            fable_windows: list[QuotaWindow] = []

            for window in pool:
                used = _used_percent(window)
                model = claude_model_scope(window.label) if account.provider == "claude" else None
                if model is not None:
                    if model.casefold() == "fable":
                        fable_windows.append(window)
                        continue
                    meter = "unknown" if used is None else f"{used:.0f}u/{window.remaining():.0f}l"
                    span = _format_reset_span(window.days_until_reset(now))
                    reset = f"/{span.plain()}" if span else ""
                    pace_percent, pace_early = window_pace(window, now)
                    trend = f" ({format_pace(pace_percent, pace_early)} pace)" if pace_percent is not None else ""
                    sublimit_notes.append(f"{model} draws from the shared weekly: {meter}{reset}{trend}")
                    continue
                if used is None:
                    continue
                if account.provider == "cursor":
                    used = _cursor_matrix_used_percent(account, window, used)
                clock, inferred = infer_window_clock(window, now=now)
                days = window.days_until_reset(now)
                estimated = not window.reset_time_is_precise()
                window_minutes = effective_window_minutes(window.label, window.window_minutes)
                if clock is not None:
                    cell = clocks.get(clock)
                    pace_pct, pace_early = window_pace(window, now)
                    if cell is None:
                        clocks[clock] = _ClockCell(
                            used_percent=used,
                            inferred=inferred,
                            days_until_reset=days,
                            reset_estimated=estimated,
                            window_minutes=window_minutes,
                            pace_percent=pace_pct,
                            pace_early=pace_early,
                        )
                    else:
                        # Same clock, same pool (Cursor total ⊂ auto): show the
                        # most-consumed, which is the one that locks out first.
                        cell.folded += 1
                        if used > cell.used_percent:
                            cell.used_percent = used
                            cell.inferred = inferred
                            cell.days_until_reset = days
                            cell.reset_estimated = estimated
                            cell.window_minutes = window_minutes
                            cell.pace_percent = pace_pct
                            cell.pace_early = pace_early
                remaining = window.remaining()
                if remaining is not None:
                    priced = _window_value_usd(window, remaining, account.provider, account.account, plans, analysis)
                    # Max, not sum: a 5h window is carved out of the weekly
                    # budget above it, so adding them double-counts the money.
                    if priced is not None and (value is None or priced > value):
                        value = priced

            key = _ladder_coverage_key(account.provider, account.account, pool_id)
            # A fully depleted window blocks its whole pool (``governing_partition``
            # promotes it as governing for exactly that reason), but it raises no
            # alert of its own: zero projected waste classifies as ``on_pace``.  So
            # never let a sibling conserve/burn alert mask the pool's own empty
            # state -- take whichever band grabs more attention.
            representative = _pick_representative_window(pool) if pool else None
            own_band = _unalerted_window_band(representative)
            found = pool_alert.get(key)
            if found is not None and _BAND_ATTENTION[found[0]] >= _BAND_ATTENTION[own_band]:
                band, priority = found
            else:
                band = own_band
                priority = _pool_queue_priority(account, band, pool, pricing, now)

            note = None
            tail_note = None
            if not clocks:
                for window in pool:
                    if window.reset_description:
                        note = window.reset_description
                        break
            wallet = _overage_remaining_usd(account)
            if wallet is not None and _matrix_row_shows_wallet(account, pool_id):
                # A wallet is inventory, not a clock: it annotates the row
                # after the reset cells and never replaces them. Rows with
                # no clocks at all keep the note as the row.
                if clocks:
                    tail_note = dollar_meter("down", "prepaid", wallet, compact=True)
                else:
                    phrase = dollar_meter("down", "prepaid", wallet)
                    note = phrase if note is None else f"{note} · {phrase}"
            if clocks and account.spent_usd is not None and account.balance_usd is not None:
                # An expiring dollar grant (omp): what is left of the plan.
                # Spent stays in JSON spent_usd.
                tail_note = dollar_meter("down", "plan", account.balance_usd, _usd_refill_total(account), compact=True)
            data_age = _account_data_age(account, now)
            stale = data_age is not None and data_age > fresh_threshold
            pace_windows = [window for window in pool if claude_model_scope(window.label) is None]
            overall_pct, overall_early = overall_pace(pace_windows or pool, now)
            gate_info = account.rate_gated_by if isinstance(account.rate_gated_by, dict) else None
            if gate_info:
                # The grant's expiry is not a spendable clock. While the shared
                # Claude rate window blocks omp, a mid-band percent-left cell is
                # what gets tried and then 429s (2026-10-21, retry-after ~41h).
                raw_reset = gate_info.get("resets_at")
                reset = parse_dt(raw_reset if isinstance(raw_reset, str) else None)
                until = format_clock(reset, date=reset - now > timedelta(hours=24)) if reset is not None else "reset"
                dollars = (
                    " · " + dollar_meter("down", "plan", account.balance_usd, _usd_refill_total(account))
                    if account.balance_usd is not None
                    else ""
                )
                note = f"rate-gated until {until}{dollars}"
                clocks = {}
                tail_note = None
                band = _BAND_EMPTY
                priority = (0.0, 0.0, 0.0)
                overall_pct, overall_early = None, False
            parent_sort = _ladder_sort_key(band, priority, account.provider, account.account)
            rows.append(
                _MatrixRow(
                    sort_key=parent_sort,
                    band=band,
                    queue_score=_queue_score(band, priority),
                    service=service,
                    account=short,
                    account_full=account.account,
                    scope=matrix_pool_scope(pool_id, pool, provider=account.provider),
                    clocks=clocks,
                    value_usd=value,
                    pace_percent=overall_pct,
                    pace_early=overall_early,
                    note=note,
                    tail_note=tail_note,
                    stale=stale,
                    measured_at=account.collected_at,
                    sublimit_notes=sublimit_notes,
                    operator_gate=account.requires_operator_approval,
                )
            )
            if fable_windows:
                rows.append(
                    _fable_display_row(
                        service=service,
                        account=short,
                        account_full=account.account,
                        windows=fable_windows,
                        shared_windows=pool,
                        now=now,
                        pricing=pricing,
                        shared_score=rows[-1].queue_score,
                    )
                )
            covered.add(key)

    # Alerts about pools no account row rendered.
    for alert in alerts:
        if alert.urgency == Urgency.NONE:
            continue
        pool_id = _pool_id_for_label(alert.window_label, alert.provider)
        key = _ladder_coverage_key(alert.provider, alert.account, pool_id)
        if key in covered:
            continue
        if _skip_hidden_ceiling_alert(snapshot, alert, cfg):
            covered.add(key)
            continue
        covered.add(key)
        rows.append(_row_from_alert(alert, now=now, pricing=pricing))
        rows[-1].operator_gate = alert.requires_operator_approval

    for account in deferred:
        if _ladder_coverage_key(account.provider, account.account, "") in covered:
            continue
        service = provider_display_name(account.provider)
        short = _short_account(account.account)
        if not account.windows and account.context_usage is not None and not account.error:
            rows.append(
                _MatrixRow(
                    sort_key=_ladder_sort_key(_BAND_NA, (0.0, 0.0, 0.0), account.provider, account.account),
                    band=_BAND_NA,
                    queue_score=None,
                    service=service,
                    account=short,
                    account_full=account.account,
                    note=_context_fragment(account),
                )
            )
            continue
        if (account.error or not _account_has_usage(account)) and not _bedrock_stored_unknown(account):
            if account.provider == "sipb" and not account.error:
                band = _BAND_NA
                priority = _account_queue_priority(account, band)
                # Compact on purpose: the narrow default matrix clamps notes to
                # one line, so the slow/last-resort guidance must fit; the full
                # notes still render in --json and --full.
                note = "unlimited · slow — use sparingly / last resort"
                status_info = (account.raw or {}).get("status") or {}
                if status_info.get("up") and status_info.get("latency_s") is not None:
                    note += f" · UP {float(status_info['latency_s']):.1f}s"
                rows.append(
                    _MatrixRow(
                        sort_key=_ladder_sort_key(band, priority, account.provider, account.account),
                        band=band,
                        queue_score=_queue_score(band, priority),
                        service=service,
                        account=short,
                        account_full=account.account,
                        note=note,
                    )
                )
            else:
                rows.append(
                    _MatrixRow(
                        sort_key=_ladder_sort_key(_BAND_ERROR, (0.0, 0.0, 0.0), account.provider, account.account),
                        band=_BAND_ERROR,
                        queue_score=None,
                        service=service,
                        account=short,
                        account_full=account.account,
                        note=(account.error or "no usage data").strip(),
                    )
                )
            continue
        depleted = _account_prepaid_is_depleted(account)
        band = _BAND_EMPTY if depleted else _BAND_NA
        priority = _account_queue_priority(account, band)
        note = _api_inventory_note(account)
        rows.append(
            _MatrixRow(
                sort_key=_ladder_sort_key(
                    band,
                    priority,
                    account.provider,
                    account.account,
                ),
                band=band,
                queue_score=_queue_score(band, priority),
                service=service,
                account=short,
                account_full=account.account,
                note=note,
                operator_gate=account.requires_operator_approval,
            )
        )

    _disambiguate_accounts(rows)
    rows.sort(key=lambda r: r.sort_key)
    return rows


def _row_from_alert(
    alert: UseOrLoseAlert, *, now: datetime | None = None, pricing: _Pricing | None = None
) -> _MatrixRow:
    """A table row built from an alert alone, with no account windows behind it.

    Only ever one clock wide — an alert describes a single window — so these
    rows are deliberately sparser than an account-derived row.
    """
    band = alert_priority_band(alert)
    priority = _alert_queue_priority(alert, band, pricing)
    service = provider_display_name(alert.provider)
    row = _MatrixRow(
        sort_key=_ladder_sort_key(band, priority, alert.provider, alert.account),
        band=band,
        queue_score=_queue_score(band, priority),
        service=service,
        account=_short_account(alert.account),
        account_full=alert.account,
        scope=_POOL_SCOPE_LABELS.get(_pool_id_for_label(alert.window_label, alert.provider), "—"),
    )
    if alert.kind == "prepaid":
        row.note = f"{alert.window_label}"
        return row

    now = now or utcnow()
    synthetic = QuotaWindow(
        label=alert.window_label,
        remaining_percent=alert.remaining_percent,
        resets_at=now + timedelta(days=alert.days_until_reset) if alert.days_until_reset is not None else None,
        window_minutes=alert.window_minutes,
    )
    row.pace_percent, row.pace_early = window_pace(synthetic, now)
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
            pace_percent=row.pace_percent,
            pace_early=row.pace_early,
        )
    if alert.flexibility_profile is not None:
        row.value_usd = alert.flexibility_profile.value_at_risk_usd
    return row


def _missing_clock_glyph(row: _MatrixRow, idx: int) -> str:
    """Glyph for an empty clock cell.

    ``->`` when every window this row has is to the right. ``<-`` when every
    window is to the left, and also when the cell sits between two windows
    (that hole points left, at the earlier window). ``—`` when the row has no
    windows at all.
    """
    present = [i for i, (key, _label) in enumerate(CLOCK_COLUMNS) if row.clocks.get(key) is not None]
    if not present:
        return "—"
    if idx < present[0]:
        return "->"
    return "<-"


_LEGEND_SEP = " · "


def _wrap_on_separator(text: str, width: int) -> list[str]:
    """Greedily pack ``·``-separated clauses into lines no wider than ``width``.

    Breaks fall only between clauses, so a clause is never split; one longer
    than ``width`` gets a line of its own.
    """
    lines: list[str] = []
    current = ""
    for clause in text.split(_LEGEND_SEP):
        candidate = f"{current}{_LEGEND_SEP}{clause}" if current else clause
        if current and len(candidate) > width:
            lines.append(current)
            current = clause
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fable_weekly_bind_note(fable_windows: list[QuotaWindow], shared_windows: list[QuotaWindow]) -> str | None:
    """Say when the shared weekly is tighter than Fable's own half-share.

    Fable's percent-left is of its cap (at most half the weekly pool). The
    shared weekly's percent-left is of the whole pool. A raw minimum of those
    two numbers mixes denominators, so the note compares both as a share of
    the weekly pool.
    """
    owns = [window.remaining() for window in fable_windows if window.remaining() is not None]
    if not owns:
        return None
    own = min(owns)
    fable_of_weekly = own * 0.5
    weekly = next(
        (
            window
            for window in shared_windows
            if claude_model_scope(window.label) is None and infer_window_clock(window)[0] == "weekly"
        ),
        None,
    )
    if weekly is None or weekly.remaining() is None:
        return None
    shared_left = float(weekly.remaining())
    if shared_left >= fable_of_weekly:
        return None
    return (
        f"Fable cap is {own:.0f}% of its half-share ({fable_of_weekly:.0f}% of the weekly pool); "
        f"shared weekly has {shared_left:.0f}% left — weekly binds"
    )


# Fable may spend at most half the shared weekly pool. At or under this share
# of that pool the row is scarce: ``slow``, at the bottom of 25–49. 2% of the
# cap is 1% of the pool, which scores 27.
_FABLE_OF_WEEKLY = 0.5
_FABLE_SCARCE_WEEKLY_PERCENT = 5.0
_FABLE_SCARCE_SCORE_SPAN = 8


def _fable_weekly_share(fable_windows: list[QuotaWindow], shared_windows: list[QuotaWindow]) -> float | None:
    """Percent of the shared weekly pool Fable can still use.

    The cap's remaining percent is of its half-share, so it is halved before
    it is compared with a shared window. Any spent shared clock binds at 0.
    """
    owns = [window.remaining() for window in fable_windows if window.remaining() is not None]
    if not owns:
        return None
    share = min(owns) * _FABLE_OF_WEEKLY
    shared = [
        window.remaining()
        for window in shared_windows
        if claude_model_scope(window.label) is None and window.remaining() is not None
    ]
    if shared:
        share = min(share, min(shared))
    return share


def _fable_display_row(
    *,
    service: str,
    account: str,
    account_full: str | None,
    windows: list[QuotaWindow],
    shared_windows: list[QuotaWindow],
    now: datetime,
    pricing: _Pricing,
    shared_score: int | None,
) -> _MatrixRow:
    """A scored Fable row. Its score is not a second pile of Claude quota."""
    clocks: dict[str, _ClockCell] = {}
    for window in windows:
        used = _used_percent(window)
        if used is None:
            continue
        clock, inferred = infer_window_clock(window)
        if clock is None:
            continue
        pace_pct, pace_early = window_pace(window, now)
        cell = clocks.get(clock)
        fresh = _ClockCell(
            used_percent=used,
            inferred=inferred,
            days_until_reset=window.days_until_reset(now),
            reset_estimated=not window.reset_time_is_precise(),
            window_minutes=effective_window_minutes(window.label, window.window_minutes),
            pace_percent=pace_pct,
            pace_early=pace_early,
        )
        if cell is None:
            clocks[clock] = fresh
            continue
        cell.folded += 1
        if used > cell.used_percent:
            clocks[clock] = fresh
            clocks[clock].folded = cell.folded
    notes: list[str] = []
    bind = _fable_weekly_bind_note(windows, shared_windows)
    if bind:
        notes.append(bind)
    if any((window.remaining() or 0.0) <= 0.0 for window in windows):
        notes.append(
            "Fable's included allowance is used up. "
            "Switch to another Claude model if shared allowance remains, "
            "or continue Fable with usage credits."
        )
    pace_percent, pace_early = overall_pace(windows, now)
    share = _fable_weekly_share(windows, shared_windows)
    weekly_minutes = 7 * 24 * 60
    if share is None:
        band = _BAND_NA
        priority: _Priority = (0.0, 0.0, 0.0)
        queue_score = None
    elif share <= 0.0:
        band = _BAND_EMPTY
        days = windows[0].days_until_reset(now) if windows else None
        known, sooner = _known_reset_priority(days)
        priority = (known, sooner, 0.0)
        queue_score = 0
    elif share <= _FABLE_SCARCE_WEEKLY_PERCENT:
        band = _BAND_CONSERVE
        # Linear across the scarce slice so 1% of the weekly pool (2% of the
        # cap) is 27, the bottom of slow, instead of a mid score from leftover
        # dollars.
        queue_score = 25 + round((share / _FABLE_SCARCE_WEEKLY_PERCENT) * _FABLE_SCARCE_SCORE_SPAN)
        priority = (float(queue_score), 0.0, 0.0)
    else:
        band = _BAND_MID
        dollars = pricing.capacity_usd("claude", account_full, share, weekly_minutes, "Claude weekly")
        priority = (dollars, 0.0, 0.0, 0.0)
        queue_score = _queue_score(band, priority)
        # The log scale flattens a half-share into the same mid numbers as the
        # shared row. Fable burns that pool faster, so when its dollar score
        # would meet or beat the shared row it drops 12 points, staying in mid.
        if shared_score is not None and queue_score is not None and queue_score >= shared_score:
            queue_score = max(50, shared_score - 12)
        priority = (float(queue_score or 0), 0.0, 0.0, 0.0)
    return _MatrixRow(
        sort_key=_ladder_sort_key(band, priority, "claude", account_full),
        band=band,
        queue_score=queue_score,
        service=service,
        account=account,
        account_full=account_full,
        scope="fable",
        clocks=clocks,
        pace_percent=pace_percent,
        pace_early=pace_early,
        tail_note="Fable-cap",
        sublimit_notes=notes,
    )


def _matrix_shows_account(rows: list[_MatrixRow]) -> bool:
    """ACCT only when one service has two account labels that differ.

    Different services can each carry their own account without a column:
    the service name already tells those rows apart. A second, different
    account on the same service is the only case the column earns its width.
    """
    return bool(_services_needing_account(rows))


def _cell_color(s: _Style, used: float) -> Any:
    """Fuller bucket → hotter color. Neutral about good/bad — the band tag judges."""
    if used >= 99.0:
        return s.red
    if used >= 75.0:
        return s.yellow
    if used >= 25.0:
        return s.cyan
    return s.green


def _style_clock_percent(s: _Style, cell: _ClockCell, *, width: int) -> str:
    """Right-align ``NNu/NNl`` into ``width`` and color only the left token.

    The used side (``44u/``) stays the default color. The left side (``56l``)
    is one colored token, so the ``l`` does not drop out between the number
    and the reset span. An inferred clock is dim end to end.
    Visible width is always ``width``: a short pair is padded on the left,
    and the color codes are not part of that width, so the next column starts
    on the same character in every row.
    """
    padded = f"{_clock_percent_text(cell):>{width}}"
    if cell.inferred:
        return s.dim(padded)
    slash = padded.rfind("/")
    if slash < 0:
        return _cell_color(s, cell.used_percent)(padded)
    return padded[: slash + 1] + _cell_color(s, cell.used_percent)(padded[slash + 1 :])


AI_JSON_NOTE = "AI: Use `aiuse --json` for machine-readable output"


@dataclass
class ClockMatrixParts:
    """The clock matrix's pieces, for callers that stack them in another order.

    ``legend`` and ``ai_line`` are already centered over the table; ``center``
    centers any other line (watch's collection footer) the same way.
    ``ai_line`` is None when there is no table to explain.
    """

    table: list[str]
    legend: list[str] = field(default_factory=list)
    ai_line: str | None = None
    center_width: int = 0
    avail: int = ACTION_PLAN_WIDTH

    def center(self, text: str) -> str:
        visible = len(_strip_ansi(text))
        if visible >= self.avail:
            return _clamp_display_width(text, self.avail)
        return _clamp_display_width(" " * max(0, (self.center_width - visible) // 2) + text, self.avail)


def render_clock_matrix(
    alerts: list[UseOrLoseAlert],
    *,
    snapshot: Snapshot | None = None,
    config: dict[str, Any] | None = None,
    s: _Style | None = None,
    color: bool | None = None,
    width: int | None = None,
    ai_note: bool = True,
) -> str:
    """Table, then legend, then (with ``ai_note``) the AI ``--json`` line.

    Pass ``ai_note=False`` when the caller already printed the AI line, as
    ``render_stderr_meta`` does at the top of plain ``aiuse``.
    """
    parts = render_clock_matrix_parts(alerts, snapshot=snapshot, config=config, s=s, color=color, width=width)
    lines = parts.table + parts.legend
    if ai_note and parts.ai_line is not None:
        lines.append(parts.ai_line)
    return "\n".join(lines)


def render_clock_matrix_parts(
    alerts: list[UseOrLoseAlert],
    *,
    snapshot: Snapshot | None = None,
    config: dict[str, Any] | None = None,
    s: _Style | None = None,
    color: bool | None = None,
    width: int | None = None,
) -> ClockMatrixParts:
    """Stdout body: one row per account/pool, one column per reset clock.

    Every row is measured on the same three clocks, so a column can be read
    top to bottom. An em-dash means the service has no window on that clock —
    which is itself the answer to "why does this one only show a weekly?".

    ``##`` is the colored 0–99 action score: 0 is empty and 99 means use as
    soon as possible. Errors and non-expiring inventory render ``??`` / ``--``
    because neither has an honest position on that scale.

    Within slow, mid and use the score is the row's dollars at risk, log-scaled onto
    that band's range: in slow what is still unspent in its window (plan price per
    refill x percent left); in mid and use what its pace projects to expire unspent
    at the reset (a full window's value x the projected waste share).  So an imminent
    reset with real waste outranks a big window with days left, and the same waste
    share of a Max plan's weekly window outranks that of a Pro plan's 5-hour one.

    Percentages are **used**, not left: 0% is untouched, 100% is exhausted.
    When a clock has a reset timestamp the cell is ``75%/4h`` / ``89%/2d14h``
    — used percent, then that clock's own remaining time. A rolling window that
    has not been used yet carries no reset timestamp (its clock starts on first
    use); when its length is known the cell is ``0u/100l/5h↻`` — the full window
    length and a ``↻`` marker, explained by a legend line.
    """
    if s is None:
        s = _Style(use_color(force=color))
    rows = _build_matrix_rows(alerts, snapshot, config)
    avail = width or terminal_width()
    if not rows:
        return ClockMatrixParts(table=[s.green("use   nothing urgent under current thresholds")], avail=avail)

    layout = _MatrixLayout(
        show_scope=any(row.scope != "—" for row in rows),
        show_account=_matrix_shows_account(rows),
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
    w_value = _pace_column_width(rows)
    w_other = _other_column_width(rows)

    def _line(tag: str, score: str, service: str, account: str, scope: str, pace: str, tail: list[str]) -> str:
        cells = [tag, score, service]
        if layout.show_account:
            cells.append(account)
        if layout.show_scope:
            cells.append(scope)
        if layout.show_value:
            cells.append(pace)
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
                "##",
                f"{'SERVICE':<{w_service}}",
                f"{'ACCT':<{w_account}}",
                f"{'SCOPE':<{w_scope}}",
                f"{'PACE':>{w_value}}",
                header_clocks + ([f"{'OTHER':<{w_other}}"] if w_other else []),
            )
        )
    ]
    any_inferred = False
    any_folded = False
    any_deadline = False
    any_unstarted = False
    any_arrow = False
    zebra_width = _matrix_needed_width(rows, layout)
    for row_idx, row in enumerate(rows):
        if row.unranked:
            tag = "     "
            score = "  "
        else:
            tag_plain, tag_color = _BAND_TAG[row.band]
            tag = getattr(s, tag_color)(s.bold(f"{tag_plain:<5}"))
            score_plain = "??" if row.band == _BAND_ERROR else "--" if row.queue_score is None else str(row.queue_score)
            score = getattr(s, tag_color)(s.bold(f"{score_plain:>2}"))
        service_text, account_text, scope_text = identities[row_idx]
        service = s.bold(f"{service_text:<{w_service}}")
        account = s.dim(f"{account_text:<{w_account}}")
        scope = s.dim(f"{scope_text:<{w_scope}}")

        gate = _gate_cell(s, _gate_span_width(w_clocks)) if _row_shows_gate(row) else None
        if row.note is not None:
            # No clock cells: the explanation starts at the first data column
            # (left-justified ``* [text]``) instead of after a run of blanks.
            cells = [tag, score, service]
            if layout.show_account:
                cells.append(account)
            prefix = " ".join(cells)
            as_of = _row_as_of(row)
            stale_suffix = f" {as_of}" if as_of else ""
            room = avail - (len(_strip_ansi(prefix)) + 1 + len(stale_suffix))
            pieces = [prefix, s.dim(_situation_body(row.note, gated=_row_shows_gate(row), width=room))]
            if as_of:
                pieces.append(s.yellow(as_of))
            line = _clamp_display_width(" ".join(pieces).rstrip(), avail)
            lines.append(s.zebra_bg(line, zebra_width) if row_idx % 2 else line)
            for note in row.sublimit_notes:
                lines.append(_clamp_display_width(s.dim(f"        {note}"), avail))
            continue

        tail: list[str] = []
        for idx, (key, label) in enumerate(CLOCK_COLUMNS):
            cell = row.clocks.get(key)
            w_clock = w_clocks[idx]
            if gate is not None and key in _GATE_CLOCKS:
                # One cell across both columns; no arrow, so no arrow legend.
                if idx == _GATE_FIRST:
                    tail.append(gate)
                continue
            if cell is None:
                glyph = _missing_clock_glyph(row, idx)
                any_arrow = any_arrow or glyph != "—"
                under_label = f"{f'{glyph:^{len(label)}}':>{w_pct}}".rstrip()
                # Pad outside the dim codes: the last clock can end the line.
                tail.append(s.dim(under_label) + " " * (w_clock - len(under_label)))
                continue
            any_inferred = any_inferred or cell.inferred
            any_folded = any_folded or bool(cell.folded)
            span, unstarted = _clock_span(cell, compact=layout.compact_deadline)
            colored = _style_clock_percent(s, cell, width=w_pct)
            mark = _fold_mark(cell)
            if span is None:
                # The mark takes the separator's column: `0u/100l+` under `0u/100l/4h`.
                tail.append(colored + s.dim(mark) + (" " * (w_clock - w_pct - len(mark))))
                continue
            if unstarted:
                any_unstarted = True
                styled = colored + s.dim("/") + _style_reset_span(s, span) + s.dim(_UNSTARTED_MARK + mark)
                pad = w_clock - (w_pct + 1 + len(span.plain()) + len(_UNSTARTED_MARK) + len(mark))
                tail.append(styled + " " * pad if pad > 0 else styled)
                continue
            any_deadline = True
            styled = colored + s.dim("/") + _style_reset_span(s, span) + s.dim(mark)
            suffix = (
                f" {format_pace(cell.pace_percent, cell.pace_early)}"
                if layout.show_pace and cell.pace_percent is not None
                else ""
            )
            styled += s.dim(suffix)
            pad = w_clock - (w_pct + 1 + len(span.plain()) + len(mark) + len(suffix))
            if pad > 0:
                styled += " " * pad
            tail.append(styled)

        value_text = "—" if row.pace_percent is None else format_pace(row.pace_percent, row.pace_early)
        if w_other:
            other = row.tail_note or ""
            # Pad outside the dim codes so a trailing blank still rstrips.
            tail.append((s.dim(other) if other else "") + " " * (w_other - len(other)))
        as_of = _row_as_of(row)
        if as_of:
            tail.append(s.yellow(as_of))

        line = _line(tag, score, service, account, scope, f"{value_text:>{w_value}}", tail)
        lines.append(s.zebra_bg(line, zebra_width) if row_idx % 2 else line)
        for note in row.sublimit_notes:
            lines.append(_clamp_display_width(s.dim(f"        {note}"), avail))

    legend_bits = ["u/l = percent used / percent left"]
    if any_deadline:
        legend_bits.append("span = until this clock resets")
    if any_unstarted:
        legend_bits.append(_UNSTARTED_LEGEND)
    if any_inferred:
        legend_bits.append("dim = inferred")
    if any_folded:
        legend_bits.append(_FOLD_LEGEND)
    if any_arrow:
        legend_bits.append("-> / <- points at this row's clock")
    if layout.show_value or layout.show_pace:
        pace = "+ ahead / - behind"
        if layout.show_value:
            pace += " · PACE = highest"
        pace += " · ~ = early"
        legend_bits.append(pace)
    legend_bits.append("↑$ PAYG counts up · ↓$ plan or prepaid counts down")
    legend_items: list[tuple[str, str]] = [(" · ".join(legend_bits), s.dim(" · ".join(legend_bits)))]

    parts = ClockMatrixParts(table=lines, center_width=min(zebra_width, avail), avail=avail)
    parts.ai_line = parts.center(s.dim(AI_JSON_NOTE))
    for plain_text, fmt_text in legend_items:
        visible = len(_strip_ansi(fmt_text))
        if visible >= avail and fmt_text == s.dim(plain_text) and _LEGEND_SEP in plain_text:
            chunks = [(chunk, s.dim(chunk)) for chunk in _wrap_on_separator(plain_text, avail - 1)]
        else:
            chunks = [(plain_text, fmt_text)]
        parts.legend.extend(parts.center(chunk_fmt) for _chunk_plain, chunk_fmt in chunks)
    return parts


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
        parts.append(
            f"~lockout {format_clock(pace.projected_exhaust_at, weekday=True)}"
            f"{lead_phrase(pace.exhaustion_lead_seconds)}"
        )
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
        body = f"{name} · {who} · {alert.window_label}"
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
    remaining = _format_remaining_percent(alert.remaining_percent).rstrip("%")
    used = _format_used_percent(max(0.0, 100.0 - float(alert.remaining_percent))).rstrip("%")
    # Compact labeled pair (matrix-cell notation): ladder lines are
    # width-clamped at 80 cols and the guidance tail must survive the clamp.
    pair = f"{used}u/{remaining}l"
    # Depleted rows already use the empty tag — do not also say "pace" or
    # "~lockout", which imply capacity remains.
    if band == _BAND_EMPTY:
        body = f"{name} · {who} · {alert.window_label}: {pair} · resets {when}"
        return f"{_priority_tag(s, band)} {body}"
    verb = "pace" if alert.kind == "conserve" else "use"
    # Forecast before the deadline phrase so width-clamp keeps the useful bit.
    forecast = _forecast_fragment(alert, compact=True)
    body = f"{name} · {who} · {alert.window_label}: {pair}{forecast} · {verb} {when}"
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
    # Prepaid remaining vs spend-up PAYG: never fake window % urgency.
    if _account_is_non_expiring_prepaid(account):
        body = f"{name} · {who} · {_api_inventory_note(account)}"
        return f"{_priority_tag(s, band)} {body}"
    if window is None:
        window = _pick_representative_window(account.windows)
    if window is not None:
        if (
            band == _BAND_EMPTY
            and window.resets_at is None
            and window.reset_description
            and any(marker in window.reset_description.casefold() for marker in ("expired", "not renewed", "lapsed"))
        ):
            body = f"{name} · {who} · {window.reset_description}"
            return f"{_priority_tag(s, band)} {body}"
        rem = window.remaining() or 0.0
        when = _human_deadline(window.days_until_reset(), estimated=not window.reset_time_is_precise())
        # Empty capacity is not "ok" — only show reset timing.
        status = "resets" if band == _BAND_EMPTY else "ok"
        body = (
            f"{name} · {who} · {window.label}: {_format_remaining_percent(rem)} left · {status} {when}"
            f"{_extra_usage_wallet_fragment(account)}"
        )
    elif account.balance_usd is not None or account.credits_remaining is not None:
        body = f"{name} · {who} · {_api_inventory_note(account)}"
    elif account.context_usage is not None:
        body = f"{name} · {who} · {_context_fragment(account)}"
    else:
        body = f"{name} · {who} · on pace"
    return f"{_priority_tag(s, band)} {body}"


def render_stderr_meta(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    *,
    color: bool | None = None,
    ai_note: bool = True,
) -> str:
    """Collection meta, errors, and ``ai --full`` hint for stderr (default mode).

    With ``ai_note`` the AI ``--json`` line leads, so an AI reader meets it
    before the table; pair it with ``render_clock_matrix(ai_note=False)``.
    """
    s = _Style(use_color(force=color))
    accounts = _sorted_accounts(snapshot.accounts)
    n_accounts = len(accounts)
    n_actionable = sum(1 for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE))
    lines: list[str] = [s.dim(AI_JSON_NOTE)] if ai_note else []
    meta = f"Collected at {format_clock(snapshot.collected_at, date=True)}"
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
    if snapshot.uninstalled_tools:
        # Optional tools the operator never installed: expected, so dim, not red.
        lines.append(s.dim(f"Optional, not installed (expected): {', '.join(snapshot.uninstalled_tools)}"))
    lines.append(s.dim("Detail: ai --full"))
    return "\n".join(lines)


def _capacity_summary_line(alerts: list[UseOrLoseAlert], s: _Style) -> str | None:
    """One-line burn-capacity blurb shared with the detailed action plan."""
    action = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE) and a.kind != "conserve"]
    if not action:
        return None
    conserve = [a for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE) and a.kind == "conserve"]
    providers = len({a.provider for a in action} | {a.provider for a in conserve})
    window_count = len(action)
    window_word = "window" if window_count == 1 else "windows"
    provider_word = "provider" if providers == 1 else "providers"
    return s.dim(f"  {window_count} {window_word} with unused capacity across {providers} {provider_word}.")


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


def _situation_body(note: str, *, gated: bool, width: int) -> str:
    """``* [note]``, with the operator notice inside the brackets when ``gated``.

    A clock row still centers the notice across 5H and WEEK. This path is only
    for a row that has no clock cells. The short wording is used when the full
    notice would be cut off.
    """
    if not gated:
        return _situation_text(note, width)
    full = _situation_text(f"{note} {_OPERATOR_GATE_TEXT}", width)
    if width <= 0 or _OPERATOR_GATE_TEXT in full:
        return full
    short = _situation_text(f"{note} {_OPERATOR_GATE_SHORT}", width)
    if _OPERATOR_GATE_SHORT in short:
        return short
    return full


def _situation_text(note: str, width: int) -> str:
    """Left-justified explanation for a row with no clock cells: ``* [text]``.

    A row that does not fit keeps the closing bracket and puts the ellipsis
    inside it. ``width <= 0`` means the caller has no budget yet; return the
    full text and let the line clamp decide.
    """
    body = " ".join(note.split())
    text = f"* [{body}]"
    if width <= 0 or len(text) <= width:
        return text
    overhead = len("* [") + len("…]")
    if width <= overhead:
        return "* […]"
    return f"* [{body[: width - overhead]}…]"


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
        lockout = (
            f", locked out ~{format_clock(pace.projected_exhaust_at, weekday=True)}"
            f"{lead_phrase(pace.exhaustion_lead_seconds)}"
        )
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
    flex_note = ""
    if profile:
        if profile.consumption_flexibility >= 0.9:
            flex_note = "Burstable — one heavy session will cover it."
        elif profile.consumption_flexibility >= 0.4:
            flex_note = "Semi-throttled — steady usage will exhaust it."
        else:
            flex_note = "Throttled — single shot, use it or accept losing it."
        if profile.burn_estimate:
            flex_note = f"{flex_note} ({profile.burn_estimate})"

    pace = alert.pace
    pace_part = ""
    if pace is not None and pace.pace_ratio is not None:
        pace_pct = round((pace.pace_ratio - 1) * 100)
        pace_str = f"pace {pace_pct:+d}%"
        if pace.learned_sample_count > 0:
            pace_str += " (history-blended)"
        waste = pace.projected_waste_fraction
        if waste is not None:
            pace_part = f" · {pace_str} — projected {waste:.0%} unused@reset"
        else:
            pace_part = f" · {pace_str}"

    return (
        f"  {badge} {s.bold(provider_display_name(alert.provider))} · "
        f"{who} · {alert.window_label}: {alert.remaining_percent:.0f}% left · "
        f"use {when}{pace_part}\n"
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
    if days < 1:
        # Hours away, estimated or not: a carried 5-hour reset is not "~1 day".
        hours = max(1, int(round(days * 24)))
        return f"within ~{hours}h"
    if estimated:
        whole_days = max(1, int(days + 0.5))
        unit = "day" if whole_days == 1 else "days"
        return f"within ~{whole_days} {unit}"
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
    raw_analysis = cfg.get("analysis")
    analysis: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}

    head = s.bold(provider_display_name(acc.provider))
    if acc.account:
        head += f" · account={acc.account}"
    if acc.plan:
        head += s.dim(f" · plan={acc.plan}")
    head += s.dim(f" · {_source_description(acc.source)}")
    lines.append(head)

    if acc.error:
        lines.append(s.red(f"  ERROR: {acc.error}"))

    if _account_is_spend_up_payg(acc) or (
        acc.usage_credits is None and (acc.balance_usd is not None or acc.credits_remaining is not None)
    ):
        lines.append(f"  {_api_inventory_note(acc)}")
    elif acc.usage_credits is not None:
        lines.extend(_usage_credits_lines(acc.usage_credits, s))

    for w in acc.windows:
        if _hide_ceiling_window(acc, w, config):
            continue
        from aiuse.analysis.pace import is_cursor_on_demand_window

        if is_cursor_on_demand_window(w, provider=acc.provider):
            continue
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
        from aiuse.analysis.ceilings import ceiling_binding_note

        bind_note = ceiling_binding_note(w, acc.provider, acc.windows)
        from aiuse.analysis.pace import human_window_label

        lines.append(f"  quota: {human_window_label(w.label)}")
        lines.append(f"    {bar} {rem_colored} {used_s:10} {s.dim(reset_s)}")
        if bind_note:
            lines.append(s.dim(f"    ^ {bind_note}"))

        if rem is not None and w.window_minutes:
            detail = _consumption_line(w, acc.provider, analysis)
            if detail:
                lines.append(s.dim(f"    {detail}"))

    if acc.context_usage is not None and acc.windows:
        lines.append(f"  {_context_fragment(acc)}")

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
    return format_clock(dt, date=True)


def _usage_credits_lines(credits: Any, s: _Style) -> list[str]:
    """Pretty-print Claude (or similar) extra-usage wallet beside plan windows."""
    cur = getattr(credits, "currency", None) or "USD"
    lines = [s.bold("  extra $")]
    used = getattr(credits, "used", None)
    limit = getattr(credits, "limit", None)
    remaining = getattr(credits, "remaining", None)
    resets = getattr(credits, "resets_at", None)

    if used is not None and limit is not None:
        lines.append(f"    ↑ ${float(used):.2f} of ${float(limit):.2f} {cur}")
    elif used is not None:
        lines.append(f"    ↑ ${float(used):.2f} {cur}")
    if remaining is not None:
        lines.append(f"    ↓ ${float(remaining):.2f} {cur} left")
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


def _consumption_line(window: Any, provider: str, analysis: dict[str, Any]) -> str | None:
    if not window.window_minutes:
        return None

    provider_key = provider_config_key(provider)
    duration_kind = classify_window_minutes(window.window_minutes)

    flex_class, flex_score = _classify_flexibility(
        window_minutes=window.window_minutes, provider=provider, config=analysis
    )

    flex_bar = "░" if flex_score <= 0.1 else "▓" if flex_score >= 0.9 else "▒"
    class_label = flex_class.value

    parts: list[str] = [f"flex:{flex_bar} {class_label}"]

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

    pace_percent, pace_early = window_pace(window, utcnow())
    if pace_percent is not None:
        parts.append(f"pace {format_pace(pace_percent, pace_early)}")

    return " · ".join(parts) if parts else None


def render_chat_report(snapshot: Snapshot, alerts: list[UseOrLoseAlert]) -> str:
    """Deprecated shim — delegates to :mod:`aiuse.chat_format`."""
    from aiuse.chat_format import render_chat_report as _render

    return _render(snapshot, alerts)
