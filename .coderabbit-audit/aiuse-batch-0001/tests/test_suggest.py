"""aiuse suggest — single winner (issue #2)."""

from __future__ import annotations

from aiuse.analysis.suggest import format_suggestion_line, pick_suggestion, suggestion_to_dict
from aiuse.models import Urgency, UseOrLoseAlert


def _burn(score: float, rem: float = 80.0, provider: str = "claude") -> UseOrLoseAlert:
    return UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider=provider,
        account="a@x.com",
        window_label=f"{provider} weekly",
        remaining_percent=rem,
        days_until_reset=2.0,
        plan=None,
        message=f"burn {provider}",
        source="test",
        score=score,
        kind="burn",
    )


def test_pick_suggestion_prefers_highest_burn_score():
    alerts = [
        _burn(40, provider="codex"),
        _burn(90, provider="claude"),
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="cursor",
            account=None,
            window_label="included",
            remaining_percent=5.0,
            days_until_reset=1.0,
            plan=None,
            message="slow down",
            source="test",
            score=99.0,
            kind="conserve",
        ),
    ]
    winner = pick_suggestion(alerts)
    assert winner is not None
    assert winner.provider == "claude"
    assert winner.score == 90


def test_pick_suggestion_none_when_only_conserve():
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.MEDIUM,
            provider="claude",
            account=None,
            window_label="5h",
            remaining_percent=2.0,
            days_until_reset=0.1,
            plan=None,
            message="conserve",
            source="test",
            score=80.0,
            kind="conserve",
        )
    ]
    assert pick_suggestion(alerts) is None
    assert suggestion_to_dict(None) is None
    assert "nothing urgent" in format_suggestion_line(None)


def test_format_and_dict_shape():
    alert = _burn(75, rem=91)
    d = suggestion_to_dict(alert)
    assert d is not None
    assert d["kind"] == "burn"
    assert d["score"] == 75
    assert d["remaining_percent"] == 91
    assert "reason" in d
    line = format_suggestion_line(alert)
    assert line.startswith("suggest:")
    assert "91%" in line
