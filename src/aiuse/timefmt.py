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
