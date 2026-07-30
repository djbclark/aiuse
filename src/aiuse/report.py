"""Human-readable terminal report (pretty by default)."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, TextIO

from aiuse.analysis.history import (
    compute_learned_burn_rates,
    history_section_lines,
    should_learn_from_history,
)
from aiuse.analysis.pace import (
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
    classify_window_minutes,
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
# Compact "at a glance" trailer: at most this many alert lines per provider.
BRIEF_MAX_LINES_PER_PROVIDER = 3


class _Style:
    """ANSI colors when stdout is a TTY and color is not disabled."""

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

    **Default** (and ``brief=True``): ranked priority ladder only (stdout).
    Meta / errors / ``ai --full`` hint belong on stderr via
    ``render_stderr_meta``.

    **Full** (``full=True``): long report with per-provider detail.

    ``brief`` is kept for CLI compatibility and is ignored when ``full=True``.
    """
    del brief  # Alias of default; retained so callers need not change overnight.
    s = _Style(use_color(force=color))
    if not full:
        width = glance_width if glance_width is not None else ACTION_PLAN_WIDTH
        return render_priority_ladder(alerts, snapshot=snapshot, s=s, width=width)

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
            band = _BAND_NA
            entries.append(
                (
                    _ladder_sort_key(
                        _LANE_NA,
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
    return "\n".join(_clamp_display_width(line, width) for _key, line in entries)


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
    if alert.kind == "conserve" and pace.projected_exhaust_at is not None:
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
        else _human_deadline(alert.days_until_reset)
    )
    verb = "pace" if alert.kind == "conserve" else "use"
    # Forecast before the deadline phrase so width-clamp keeps the useful bit.
    forecast = _forecast_fragment(alert, compact=True)
    remaining = _format_remaining_percent(alert.remaining_percent)
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
        rem = window.remaining() or 0.0
        when = _human_deadline(window.days_until_reset())
        body = f"{name} · {who} · {window.label}: {_format_remaining_percent(rem)} left · ok {when}"
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
    brief_body = _render_brief_action_plan(alerts, s, width=width, max_lines=ACTION_PLAN_MAX_LINES - 2)
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
                when = _human_deadline(alert.days_until_reset)
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
    width: int,
    max_lines: int,
    max_lines_per_provider: int = BRIEF_MAX_LINES_PER_PROVIDER,
) -> list[str]:
    """
    One-line-per-alert compact plan for the final viewport.

    Fits in ``max_lines`` physical rows (callers reserve title + rule outside).
    At most ``max_lines_per_provider`` alert lines are kept per provider (highest
    score first within each section), so one busy service cannot dominate.
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
        lines.append(_clamp_display_width(row, width))
        used += row_h
    else:
        if omitted:
            lines.append(s.dim(f"  … +{omitted} more (see detailed plan above)"))

    return lines


def _brief_alert_line(alert: UseOrLoseAlert, s: _Style, *, kind: str) -> str:
    icon = URGENCY_ICON.get(alert.urgency, "   ")
    who = alert.account or "default"
    when = _human_deadline(alert.days_until_reset)
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
    when = _human_deadline(alert.days_until_reset)
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
    when = _human_deadline(alert.days_until_reset)

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
    when = _human_deadline(alert.days_until_reset)
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


def _human_deadline(days: float | None) -> str:
    if days is None:
        return "before the next reset (time unknown)"
    if days <= 0:
        return "immediately (reset imminent or past)"
    if days < 1:
        hours = max(1, int(round(days * 24)))
        return f"within ~{hours}h"
    if days < 2:
        return "within 1 day"
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
            reset_s = f"resets in {days:.1f}d ({_fmt_dt(w.resets_at)})"
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
        rate_key = f"{provider_key}:{duration_kind}"
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
