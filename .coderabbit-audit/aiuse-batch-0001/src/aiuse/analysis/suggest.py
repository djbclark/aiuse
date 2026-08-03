"""Single-winner recommendation: which pool to burn next."""

from __future__ import annotations

from typing import Any

from aiuse.models import Urgency, UseOrLoseAlert, provider_display_name


def pick_suggestion(alerts: list[UseOrLoseAlert]) -> UseOrLoseAlert | None:
    """Return the best **burn** alert, or None if nothing is urgent to use.

    Conserve alerts mean slow down — they never win as “use next.”
    INFO / prepaid inventory never wins.
    """
    burns = [a for a in alerts if a.kind == "burn" and a.urgency not in (Urgency.INFO, Urgency.NONE)]
    if not burns:
        return None
    # Higher score first; more remaining and sooner reset as light tie-breakers.
    return max(
        burns,
        key=lambda a: (
            float(a.score),
            float(a.remaining_percent),
            -(float(a.days_until_reset) if a.days_until_reset is not None else 99.0),
        ),
    )


def suggestion_to_dict(alert: UseOrLoseAlert | None) -> dict[str, Any] | None:
    """Stable JSON shape for ``suggestion`` (null when nothing to burn)."""
    if alert is None:
        return None
    return {
        "provider": alert.provider,
        "account": alert.account,
        "window_label": alert.window_label,
        "kind": alert.kind,
        "urgency": alert.urgency.value,
        "remaining_percent": alert.remaining_percent,
        "days_until_reset": alert.days_until_reset,
        "score": alert.score,
        "reason": alert.message,
        "source": alert.source,
        "plan": alert.plan,
    }


def format_suggestion_line(alert: UseOrLoseAlert | None) -> str:
    """One human line for ``aiuse suggest`` stdout."""
    if alert is None:
        return "suggest: nothing urgent (no burn alerts under current thresholds)"
    name = provider_display_name(alert.provider)
    who = alert.account or "default"
    rem = alert.remaining_percent
    when = alert.days_until_reset
    if when is None:
        when_s = "reset time unknown"
    elif when < 1:
        when_s = f"within ~{max(1, int(round(when * 24)))}h"
    else:
        when_s = f"within {when:.1f} days"
    return f"suggest: {name} · {who} · {alert.window_label}: {rem:.0f}% left · use {when_s} · score {alert.score:.0f}"
