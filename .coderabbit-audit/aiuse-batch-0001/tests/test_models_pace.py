"""Serialization tests for Phase 2 pace model additions (Step 9)."""

from datetime import datetime, timezone

from aiuse.models import PaceProfile, Urgency, UseOrLoseAlert


def test_pace_profile_and_alert_kind_round_trip_in_to_dict():
    exhaust = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
    pace = PaceProfile(
        elapsed_fraction=0.5,
        used_fraction=0.3,
        pace_ratio=0.6,
        projected_used_fraction=0.7,
        projected_waste_fraction=0.3,
        projected_waste_usd=2.5,
        projected_exhaust_at=exhaust,
        governing=True,
        gated_by=None,
        confidence="measured",
    )
    alert = UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="claude",
        account="a@example.com",
        window_label="Weekly",
        remaining_percent=36.0,
        days_until_reset=2.0,
        plan="Pro",
        message="use it",
        source="cswap",
        score=50.0,
        kind="conserve",
        pace=pace,
        window_minutes=10080,
    )
    d = alert.to_dict()
    # New keys
    assert d["kind"] == "conserve"
    assert d["pace"]["used_fraction"] == 0.3
    assert d["pace"]["projected_exhaust_at"] == exhaust.isoformat()
    assert d["pace"]["confidence"] == "measured"
    # Pre-existing keys still present
    for key in (
        "urgency",
        "provider",
        "account",
        "window_label",
        "remaining_percent",
        "days_until_reset",
        "plan",
        "message",
        "source",
        "score",
        "window_minutes",
        "deadline_is_estimated",
    ):
        assert key in d
    assert d["urgency"] == "medium"
    assert d["remaining_percent"] == 36.0
    assert d["window_minutes"] == 10080


def test_same_measurement_compares_remaining_percent():
    from aiuse.models import QuotaWindow

    a = QuotaWindow(label="w", remaining_percent=50.0, window_minutes=300)
    b = QuotaWindow(label="w", remaining_percent=80.0, window_minutes=300)
    assert not a.same_measurement(b)
