"""Pace math for use-or-lose scoring — the default `mode == "pace"` path in
analyze_use_or_lose (see use_or_lose.py)."""

from __future__ import annotations

from datetime import datetime, timedelta

from aiuse.models import PaceProfile, QuotaWindow, classify_window_minutes, nominal_window_minutes


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
    exhaust_at = now + timedelta(days=(1.0 - used_fraction) / r_hat) if r_hat > 1e-9 else None

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
    )


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

    When durations tie (e.g. Cursor Included/Auto/API all monthly), prefer a
    window whose label looks like the overall included bar, then list order.
    """
    scored = [
        (
            w.window_minutes or nominal_window_minutes(classify_window_minutes(w.window_minutes)) or 0,
            0 if "included" in (w.label or "").casefold() else 1,
            w,
        )
        for w in windows
        if w.remaining() is not None
    ]
    if not scored:
        return None, list(windows)
    # Longest minutes first; among ties, included (rank 0) before others.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    governing = scored[0][2]
    children = [w for w in windows if w is not governing]
    return governing, children


def independent_pool_key(label: str | None) -> str | None:
    """Hard-separated allotment family from a window label, if any.

    Some providers (notably Google AI / Antigravity) expose **independent**
    budgets that must not be collapsed by shared-allotment logic — e.g. Gemini
    usage vs Claude/GPT usage. Returns a stable slug for grouping, or None for
    the residual “same pool” group (Claude 5h⊂weekly, Cursor Included⊃Auto/API).
    """
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


def partition_independent_pools(windows: list[QuotaWindow]) -> list[list[QuotaWindow]]:
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
        key = independent_pool_key(window.label)
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
