"""Exhaustion forecasts say how far ahead of the window's reset they land."""

from datetime import timedelta

import pytest

from aiuse.analysis.pace import compute_pace
from aiuse.chat_format import pace_interpretation
from aiuse.models import PaceProfile, QuotaWindow, Snapshot, Urgency, UseOrLoseAlert, utcnow
from aiuse.report import _conserve_line, _forecast_fragment, _Style, render_report
from aiuse.timefmt import exhaustion_lead_seconds, format_lead, lead_phrase


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (30, "under a minute"),
        (60, "1 minute"),
        (40 * 60, "40 minutes"),
        (3600, "1 hour"),
        (5 * 3600 + 600, "5 hours"),
        (86400, "1 day"),
        (3 * 86400 + 3600, "3 days"),
        (13 * 86400, "13 days"),
        (14 * 86400, "2 weeks"),
        (22 * 86400, "3 weeks"),
    ],
)
def test_format_lead_scales_units(seconds, expected):
    assert format_lead(seconds) == expected


def test_exhaustion_lead_seconds_only_before_reset():
    now = utcnow()
    assert exhaustion_lead_seconds(now, now + timedelta(days=3)) == pytest.approx(3 * 86400)
    assert exhaustion_lead_seconds(now + timedelta(days=4), now + timedelta(days=3)) is None
    assert exhaustion_lead_seconds(None, now) is None
    assert exhaustion_lead_seconds(now, None) is None
    assert lead_phrase(None) == ""
    assert lead_phrase(3 * 86400) == ", 3 days before reset"


def _fast_window(now):
    # Weekly window 3 days in, 80% used -> exhausts well before reset.
    return QuotaWindow(
        label="Weekly",
        used_percent=80.0,
        window_minutes=10080,
        resets_at=now + timedelta(days=4),
    )


def test_compute_pace_sets_lead_and_json():
    now = utcnow()
    pace = compute_pace(_fast_window(now), now=now)
    assert pace is not None and pace.projected_exhaust_at is not None
    expected = (now + timedelta(days=4) - pace.projected_exhaust_at).total_seconds()
    assert pace.exhaustion_lead_seconds == pytest.approx(expected)
    assert pace.exhaustion_lead_seconds > 0
    assert pace.to_dict()["exhaustion_lead_seconds"] == pytest.approx(expected)


def test_compute_pace_no_lead_when_slow():
    now = utcnow()
    w = QuotaWindow(label="Weekly", used_percent=10.0, window_minutes=10080, resets_at=now + timedelta(days=1))
    pace = compute_pace(w, now=now)
    assert pace is not None
    assert pace.exhaustion_lead_seconds is None
    assert pace.to_dict()["exhaustion_lead_seconds"] is None


def _conserve_alert(now, lead_days=3.0):
    exhaust = now + timedelta(days=1)
    return UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="claude",
        account="a@example.com",
        window_label="Weekly",
        remaining_percent=30.0,
        days_until_reset=1 + lead_days,
        plan=None,
        message="pace yourself",
        source="cswap",
        score=70.0,
        kind="conserve",
        pace=PaceProfile(
            elapsed_fraction=0.4,
            used_fraction=0.7,
            pace_ratio=1.75,
            projected_used_fraction=1.0,
            projected_waste_fraction=0.0,
            projected_waste_usd=0.0,
            projected_exhaust_at=exhaust,
            exhaustion_lead_seconds=lead_days * 86400,
        ),
    )


def test_forecast_fragment_and_conserve_line_include_lead():
    now = utcnow()
    alert = _conserve_alert(now)
    assert "3 days before reset" in _forecast_fragment(alert, compact=True)
    line = _conserve_line(alert, _Style(False))
    assert "locked out ~" in line
    assert ", 3 days before reset" in line


def test_full_report_includes_lead():
    now = utcnow()
    text = render_report(
        Snapshot(collected_at=now, accounts=[]), [_conserve_alert(now)], config={}, color=False, full=True
    )
    assert "3 days before reset" in text


def test_hours_lead_for_short_window():
    now = utcnow()
    alert = _conserve_alert(now, lead_days=5 / 24)
    assert "5 hours before reset" in _forecast_fragment(alert, compact=True)


def test_chat_pace_line_includes_time_and_lead():
    now = utcnow()
    resets = now + timedelta(weeks=3)
    pace = PaceProfile(
        elapsed_fraction=0.2,
        used_fraction=0.6,
        pace_ratio=3.0,
        projected_used_fraction=1.0,
        projected_waste_fraction=0.0,
        projected_waste_usd=0.0,
        projected_exhaust_at=now + timedelta(days=1),
    )
    line = pace_interpretation(pace, resets)
    assert line is not None
    assert line.startswith("Projected to exhaust ~")
    assert line.endswith(", 3 weeks before reset")
