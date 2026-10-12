from aiuse.models import FlexibilityClass, FlexibilityProfile, PaceProfile, Urgency, UseOrLoseAlert
from aiuse.report import _capacity_summary_line, _Style


def _alert(
    provider: str = "codex",
    *,
    pace_ratio: float | None = 0.3,
    projected_waste_fraction: float | None = None,
    kind: str = "burn",
    window_minutes: int | None = 300,
) -> UseOrLoseAlert:
    pace = (
        PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.5,
            pace_ratio=pace_ratio,
            projected_used_fraction=None,
            projected_waste_fraction=projected_waste_fraction,
            projected_waste_usd=None,
            projected_exhaust_at=None,
            governing=True,
            gated_by=None,
            confidence="measured",
        )
        if pace_ratio is not None
        else None
    )
    return UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider=provider,
        account=None,
        window_label="Codex quota",
        remaining_percent=50.0,
        days_until_reset=2.0,
        plan=None,
        message="test",
        source="test",
        score=1.0,
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.BURSTABLE,
            consumption_flexibility=1.0,
            value_at_risk_usd=35.65,
        ),
        kind=kind,
        pace=pace,
        window_minutes=window_minutes,
    )


def test_under_pace_item_includes_waste_and_uses_short_window() -> None:
    line = _capacity_summary_line([_alert(projected_waste_fraction=0.93)], _Style(False))

    assert line == "  Pace: codex 5h -70% pace (93% unused at reset) · 1 window across 1 provider."
    assert "$" not in line


def test_over_pace_item_has_positive_delta() -> None:
    line = _capacity_summary_line([_alert(pace_ratio=2.04)], _Style(False))

    assert line is not None
    assert "codex 5h +104% pace" in line


def test_more_than_three_paced_alerts_are_summarized() -> None:
    alerts = [_alert(provider=f"provider-{index}") for index in range(5)]

    line = _capacity_summary_line(alerts, _Style(False))

    assert line is not None
    assert line.count(" pace") == 3
    assert "+2 more" in line
    assert "5 windows across 5 providers" in line


def test_no_pace_uses_pluralized_fallback() -> None:
    line = _capacity_summary_line([_alert(pace_ratio=None)], _Style(False))

    assert line == "  1 window with unused capacity across 1 provider."
    assert "$" not in line


def test_empty_alerts_return_none() -> None:
    assert _capacity_summary_line([], _Style(False)) is None


def test_conserve_only_alerts_return_none() -> None:
    assert _capacity_summary_line([_alert(kind="conserve")], _Style(False)) is None
