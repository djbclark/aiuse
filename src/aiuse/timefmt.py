"""Human-facing time-of-day formatting shared by every renderer."""

from __future__ import annotations

from datetime import datetime


def format_clock(
    when: datetime,
    *,
    seconds: bool = False,
    date: bool = False,
    weekday: bool = False,
) -> str:
    """Local 12-hour time as people say it.

    ``9:02pm``, ``9:02:07pm``, ``Mon 9:02pm`` (``weekday``), ``Sat Oct 3 9:02pm`` (``date``,
    which already includes the weekday).
    """
    local = when.astimezone()
    clock = f"{local.hour % 12 or 12}:{local:%M}" + (f":{local:%S}" if seconds else "")
    clock += "am" if local.hour < 12 else "pm"
    if date:
        return f"{local:%a %b} {local.day} {clock}"
    return f"{local:%a} {clock}" if weekday else clock


def format_lead(seconds: float) -> str:
    """How far ahead of a reset something happens, scaled to hours, days or weeks.

    ``"40 minutes"``, ``"5 hours"``, ``"3 days"``, ``"2 weeks"``. Rounds to the
    nearest whole unit; under a minute reads ``"under a minute"``.
    """
    secs = max(0.0, float(seconds))

    def plural(n: int, unit: str) -> str:
        return f"{n} {unit}" + ("" if n == 1 else "s")

    if secs < 60:
        return "under a minute"
    if secs < 3600:
        return plural(max(1, round(secs / 60)), "minute")
    if secs < 86400:
        return plural(max(1, round(secs / 3600)), "hour")
    if secs < 14 * 86400:
        return plural(max(1, round(secs / 86400)), "day")
    return plural(max(2, round(secs / (7 * 86400))), "week")


def exhaustion_lead_seconds(exhaust_at: datetime | None, resets_at: datetime | None) -> float | None:
    """Seconds between a projected exhaustion and the window's reset (None unless it is before the reset)."""
    if exhaust_at is None or resets_at is None:
        return None
    lead = (resets_at - exhaust_at).total_seconds()
    return lead if lead > 0 else None


def lead_phrase(seconds: float | None) -> str:
    """``", 3 days before reset"`` or ``""`` when there is no lead to report."""
    if seconds is None or seconds <= 0:
        return ""
    return f", {format_lead(seconds)} before reset"
