"""Pace math for use-or-lose scoring — the default `mode == "pace"` path in
analyze_use_or_lose (see use_or_lose.py)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from aiuse.models import (
    PaceProfile,
    QuotaWindow,
    canonical_provider,
    classify_window_minutes,
    claude_model_scope,
    nominal_window_minutes,
)
from aiuse.timefmt import exhaustion_lead_seconds


def compute_pace(
    window: QuotaWindow,
    *,
    now: datetime,
    learned_rate_per_day: float | None = None,  # fraction/day, e.g. 0.30 == 30%/day
    learned_sample_count: int = 0,
    e_min: float = 0.05,
) -> PaceProfile | None:
    remaining = window.remaining()
    if remaining is None:
        return None
    used_fraction = (100.0 - remaining) / 100.0
    if used_fraction <= 0:
        return PaceProfile(
            elapsed_fraction=None,
            used_fraction=used_fraction,
            pace_ratio=None,
            projected_used_fraction=None,
            projected_waste_fraction=None,
            projected_waste_usd=None,
            projected_exhaust_at=None,
            confidence="low",
        )

    kind = classify_window_minutes(window.window_minutes)
    duration_minutes = window.window_minutes or nominal_window_minutes(kind)
    confidence = "measured" if window.window_minutes else ("inferred" if duration_minutes else "low")

    if not window.resets_at or not duration_minutes:
        return PaceProfile(
            elapsed_fraction=None,
            used_fraction=used_fraction,
            pace_ratio=None,
            projected_used_fraction=None,
            projected_waste_fraction=None,
            projected_waste_usd=None,
            projected_exhaust_at=None,
            confidence="low",
        )

    t_left_days = max(0.0, (window.resets_at - now).total_seconds() / 86400.0)
    d_days = duration_minutes / 1440.0
    elapsed = min(1.0, max(0.0, 1.0 - t_left_days / d_days))

    r_now = used_fraction / (max(elapsed, e_min) * d_days)  # fraction/day
    if learned_rate_per_day is not None and learned_sample_count > 0:
        lam = learned_sample_count / (learned_sample_count + 2.0)
        r_hat = (1 - lam) * r_now + lam * learned_rate_per_day
        blended_n = learned_sample_count
    else:
        r_hat = r_now
        blended_n = 0

    projected_used = min(1.0, used_fraction + r_hat * t_left_days)
    waste = 1.0 - projected_used
    exhaust_at = None
    if r_hat > 1e-9:
        if window.refill_capacity_unit != "usd" or used_fraction > 0:
            exhaust_at = now + timedelta(days=(1.0 - used_fraction) / r_hat)

    return PaceProfile(
        elapsed_fraction=elapsed,
        used_fraction=used_fraction,
        pace_ratio=used_fraction / max(elapsed, e_min),
        projected_used_fraction=projected_used,
        projected_waste_fraction=waste,
        projected_waste_usd=None,  # filled in by the caller once it knows the plan price
        projected_exhaust_at=exhaust_at,
        confidence=confidence,
        learned_sample_count=blended_n,
        exhaustion_lead_seconds=exhaustion_lead_seconds(exhaust_at, window.resets_at),
    )


def format_pace(percent: int, early: bool = False) -> str:
    """Format a pace percentage with an explicit sign and optional '~' marker when early."""
    marker = "~" if early else ""
    return f"{percent:+d}%{marker}"


def window_pace_ratio(window: QuotaWindow, now: datetime) -> float | None:
    """Unblended pace, absent for exhausted or unclocked windows."""
    remaining = window.remaining()
    if remaining is None or remaining <= 0 or not window.resets_at:
        return None
    if not window.window_minutes and claude_model_scope(window.label) is not None:
        window = replace(window, window_minutes=7 * 24 * 60)
    pace = compute_pace(window, now=now)
    return pace.pace_ratio if pace is not None else None


def window_is_early(window: QuotaWindow, now: datetime) -> bool:
    """Whether a clocked, non-exhausted window is under 10% elapsed."""
    remaining = window.remaining()
    if remaining is None or remaining <= 0 or not window.resets_at:
        return False
    if not window.window_minutes and claude_model_scope(window.label) is not None:
        window = replace(window, window_minutes=7 * 24 * 60)
    pace = compute_pace(window, now=now)
    if pace is None or pace.elapsed_fraction is None:
        return False
    return pace.elapsed_fraction < 0.10


def window_pace(window: QuotaWindow, now: datetime) -> tuple[int | None, bool]:
    """Unblended pace percent and early flag (False when pace is None)."""
    remaining = window.remaining()
    if remaining is None or remaining <= 0 or not window.resets_at:
        return None, False
    if not window.window_minutes and claude_model_scope(window.label) is not None:
        window = replace(window, window_minutes=7 * 24 * 60)
    pace = compute_pace(window, now=now)
    if pace is None or pace.pace_ratio is None:
        return None, False
    percent = round((pace.pace_ratio - 1) * 100)
    early = pace.elapsed_fraction is not None and pace.elapsed_fraction < 0.10
    return percent, early


def window_pace_percent(window: QuotaWindow, now: datetime) -> int | None:
    percent, _ = window_pace(window, now)
    return percent


def overall_pace(windows: list[QuotaWindow], now: datetime) -> tuple[int | None, bool]:
    """Highest window pace and whether that governing window is early.

    (None, False) when no window has pace or any window is exhausted.
    """
    if any((remaining := window.remaining()) is not None and remaining <= 0 for window in windows):
        return None, False
    candidates: list[tuple[int, bool]] = []
    for window in windows:
        pct, early = window_pace(window, now)
        if pct is not None:
            candidates.append((pct, early))
    if not candidates:
        return None, False
    return max(candidates, key=lambda pair: pair[0])


def overall_pace_percent(windows: list[QuotaWindow], now: datetime) -> int | None:
    """Highest window pace: the window furthest ahead of an even spend runs out first.

    None when any window is exhausted: the row is locked out until that window
    resets, so a pace from its other windows would read as spare capacity.
    """
    percent, _ = overall_pace(windows, now)
    return percent


def overall_pace_early(windows: list[QuotaWindow], now: datetime) -> bool:
    _, early = overall_pace(windows, now)
    return early


def classify_pace(
    pace: PaceProfile,
    *,
    resets_at: datetime | None,
    waste_alert_fraction: float,
    min_elapsed_fraction: float,
    conserve_min_lead_hours: float,
    has_learned_rate: bool,
) -> str:
    """Returns 'conserve' | 'burn' | 'on_pace' | 'unknown'."""
    if pace.projected_waste_fraction is None and pace.projected_exhaust_at is None:
        return "unknown"
    # Too early in the window (no learned rate) → do not trust burn/conserve yet.
    if pace.elapsed_fraction is not None and pace.elapsed_fraction < min_elapsed_fraction and not has_learned_rate:
        return "on_pace"
    if pace.projected_exhaust_at and resets_at:
        if pace.projected_exhaust_at < resets_at - timedelta(hours=conserve_min_lead_hours):
            return "conserve"
    if pace.projected_waste_fraction is None:
        return "unknown"
    if pace.projected_waste_fraction >= waste_alert_fraction:
        return "burn"
    return "on_pace"


def governing_partition(windows: list[QuotaWindow]) -> tuple[QuotaWindow | None, list[QuotaWindow]]:
    """Longest-duration window with usable remaining() governs; the rest are children.

    If any window is completely exhausted (0% remaining), it becomes the governing
    window because it temporarily blocks the entire pool, overriding longer durations.
    When durations tie, prefer a window whose label is ``total``, then list order.
    CodexBar's ``primary`` slot id is not a total ceiling: every vendor uses it.
    Claude model caps are children, never whole-pool governing windows.
    """
    usable = [w for w in windows if w.remaining() is not None and claude_model_scope(w.label) is None]
    if not usable:
        return None, list(windows)

    exhausted = [w for w in usable if (w.remaining() or 0.0) <= 0.0]
    if exhausted:
        # Shortest duration first (the immediate block)
        exhausted.sort(
            key=lambda w: (
                w.window_minutes or nominal_window_minutes(classify_window_minutes(w.window_minutes)) or 0,
                0 if _is_total_ceiling_window(w) else 1,
            )
        )
        governing = exhausted[0]
        children = [w for w in windows if w is not governing]
        return governing, children

    scored = [
        (
            w.window_minutes or nominal_window_minutes(classify_window_minutes(w.window_minutes)) or 0,
            0 if _is_total_ceiling_window(w) else 1,
            w,
        )
        for w in usable
    ]
    # Longest minutes first; among ties, total ceiling (rank 0) before others.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    governing = scored[0][2]
    children = [w for w in windows if w is not governing]
    return governing, children


# Stable CodexBar extra-window id for Cursor's Grok Bot weekly allowance.
# Match on this id only — never on the display title (Cursor may rename it).
CURSOR_GROK_BOT_WINDOW_ID = "cursor-grok-bot"


def _is_total_ceiling_window(window: QuotaWindow) -> bool:
    # Label only. CodexBar assigns window id ``primary`` to every vendor's first
    # rate slot; treating that as a Cursor ceiling splits those windows off the
    # row they share with weekly/monthly.
    return (window.label or "").strip().casefold() == "total"


def _provider_is_cursor(provider: str | None) -> bool:
    return provider is not None and canonical_provider(provider) == "cursor"


def cursor_pool_family(
    label: str | None,
    *,
    window_id: str | None = None,
    provider: str | None = None,
) -> str | None:
    """Canonical Cursor pool_family slug (snake_case), or None.

    ``primary`` / ``secondary`` / ``tertiary`` are CodexBar's generic rate-slot
    ids. They name Cursor pools only when ``provider`` is cursor. The Grok Bot
    extra-window id is unique and matches on its own. Passing a non-cursor
    provider skips Cursor label heuristics too, so a foreign window is never
    filed as ``total`` or ``auto``.
    """
    if provider is not None and not _provider_is_cursor(provider):
        return None
    wid = (window_id or "").strip().casefold()
    if wid == CURSOR_GROK_BOT_WINDOW_ID:
        return "grok_bot"
    # Slot ids are ambiguous unless we know the window belongs to Cursor.
    if _provider_is_cursor(provider):
        if wid == "primary":
            return "total"
        if wid == "secondary":
            return "auto"
        if wid == "tertiary":
            return "third_party"
        if wid == "on-demand":
            return "on_demand"
    text = (label or "").casefold()
    if not text:
        return None
    if "grok bot" in text or text == "grok-bot":
        return "grok_bot"
    if text == "on-demand" or "on demand" in text or "on-demand" in text:
        return "on_demand"
    if text == "total":
        return "total"
    if text == "auto" or text == "cursor auto":
        return "auto"
    if "third party" in text or "third-party" in text:
        return "third_party"
    if "cursor other models" in text or "cursor api" in text:
        return "third_party"
    if "included" in text or "cursor included" in text:
        return "total"
    return None


def independent_pool_key(
    label: str | None,
    *,
    window_id: str | None = None,
    provider: str | None = None,
) -> str | None:
    """Hard-separated allotment family from a window label, if any.

    Some providers (notably Google AI / Antigravity) expose **independent**
    budgets that must not be collapsed by shared-allotment logic — e.g. Gemini
    usage vs Claude/GPT usage. Cursor exposes total (monthly umbrella), auto and
    third-party breakdown rows, on-demand spend cap, and grok-bot (weekly) as
    separate families. Returns a stable slug for grouping, or None for the
    residual “same pool” group (Claude 5h⊂weekly, and every non-Cursor vendor's
    nested clocks).

    Cursor slot ids and the Grok Bot extra-window id select a Cursor family
    only for that provider (see :func:`cursor_pool_family`). Other vendors keep
    one residual pool so their 5h/weekly/monthly windows stay columns of one row.
    """
    cursor_family = cursor_pool_family(label, window_id=window_id, provider=provider)
    if cursor_family is not None:
        return f"cursor_{cursor_family}"
    text = (label or "").casefold()
    if not text:
        return None
    # More specific markers first so "Claude/GPT" does not also hit a bare gemini rule.
    if (
        "claude/gpt" in text
        or "claude / gpt" in text
        or "non-gemini" in text
        or "nongemini" in text
        or "non gemini" in text
    ):
        return "claude_gpt"
    if "gemini" in text:
        return "gemini"
    return None


def pool_key_for_window(
    window: QuotaWindow | None,
    *,
    label: str | None = None,
    provider: str | None = None,
) -> str:
    """Stable pool slug for grouping; empty string = residual pool."""
    if window is not None:
        key = independent_pool_key(window.label, window_id=window.window_id, provider=provider)
        if key is not None:
            return key
        label = window.label
    return independent_pool_key(label, provider=provider) or ""


def cursor_breakdown_window(window: QuotaWindow, *, provider: str | None = None) -> bool:
    """Cursor auto / third-party breakdown rows — visible but not scored.

    A non-cursor provider never has breakdown rows. CodexBar's ``secondary`` /
    ``tertiary`` slot ids are that vendor's weekly/monthly clocks, not Cursor
    auto / third-party.
    """
    if provider is not None and not _provider_is_cursor(provider):
        return False
    if _provider_is_cursor(provider):
        wid = (window.window_id or "").strip().casefold()
        if wid in ("secondary", "tertiary"):
            return True
    family = cursor_pool_family(window.label, window_id=window.window_id, provider=provider)
    return family in ("auto", "third_party")


# Short human names for the pool slugs above. Lives here rather than in a
# renderer so the table and the chat format name a pool the same way.
POOL_SCOPE_LABELS = {
    "gemini": "gemini",
    "claude_gpt": "claude/gpt",
    "cursor_total": "total",
    "cursor_third_party": "3rd-party",
    "cursor_auto": "auto",
    "cursor_grok_bot": "grok-bot",
    "cursor_on_demand": "on-demand",
}


def human_window_label(label: str) -> str:
    """Display label. The stored label and JSON pool_family stay unchanged."""
    if label.casefold() in {"third-party", "third party"}:
        return "3rd-party"
    return label


def is_cursor_on_demand_window(window: QuotaWindow, *, provider: str | None = None) -> bool:
    """Cursor's dollar overage cap. It is a wallet, not a percent-left pool."""
    return cursor_pool_family(window.label, window_id=window.window_id, provider=provider) == "on_demand"


def pool_scope_label(pool_id: str | None) -> str | None:
    """Display name for an independent pool, or None for the residual pool."""
    if not pool_id:
        return None
    return POOL_SCOPE_LABELS.get(pool_id, pool_id)


def matrix_pool_scope(
    pool_id: str,
    pool: list[QuotaWindow] | None = None,
    *,
    provider: str | None = None,
) -> str:
    """SCOPE column label for a matrix/ladder pool row.

    A residual pool is ``—``. Cursor's ``primary`` slot (or a ``total`` label)
    is the ceiling scope, and only for Cursor — other vendors' first rate slot
    is not a total row.
    """
    if pool_id:
        return POOL_SCOPE_LABELS.get(pool_id, pool_id)
    if _provider_is_cursor(provider) and pool:
        for window in pool:
            wid = (window.window_id or "").strip().casefold()
            if wid == "primary":
                return "total"
            label = (window.label or "").strip().casefold()
            if label in ("total", "cursor included"):
                return "total"
    return "—"


def partition_independent_pools(
    windows: list[QuotaWindow],
    *,
    provider: str | None = None,
) -> list[list[QuotaWindow]]:
    """Group windows into hard-separated allotment pools.

    Windows that share a non-None :func:`independent_pool_key` form one pool.
    Unlabeled / residual windows form a single additional pool. Within each
    pool, :func:`governing_partition` still applies (5h ⊂ weekly, etc.).

    Order is stable: first named pool in window-list order, then residual.
    """
    if not windows:
        return []
    named: dict[str, list[QuotaWindow]] = {}
    named_order: list[str] = []
    residual: list[QuotaWindow] = []
    for window in windows:
        key = independent_pool_key(window.label, window_id=window.window_id, provider=provider)
        if key is None:
            residual.append(window)
            continue
        if key not in named:
            named[key] = []
            named_order.append(key)
        named[key].append(window)
    pools = [named[key] for key in named_order]
    if residual:
        pools.append(residual)
    return pools if pools else [list(windows)]
