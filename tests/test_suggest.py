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
    # Same plan price, window and remaining% (equal dollars at risk): the analyzer score breaks the tie.
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


def _alert(
    provider: str,
    account: str | None,
    label: str,
    remaining: float,
    days: float,
    *,
    kind: str,
    score: float,
    urgency: Urgency = Urgency.HIGH,
    minutes: int | None = None,
) -> UseOrLoseAlert:
    return UseOrLoseAlert(
        urgency=urgency,
        provider=provider,
        account=account,
        window_label=label,
        remaining_percent=remaining,
        days_until_reset=days,
        plan=None,
        message=f"{kind} {label}",
        source="test",
        score=score,
        kind=kind,
        window_minutes=minutes,
    )


def test_a_burn_masked_by_a_conserve_sibling_is_not_suggested():
    # aiuse-57n: a Codex 5h burn next to its own pool's weekly conserve.  The board collapses the
    # pool onto the conserve (the stronger attention), so suggest must not offer the burn it hides.
    burn = _alert("codex", "g@x.com", "5h", 20.0, 0.2, kind="burn", score=80.0, minutes=300)
    conserve = _alert("codex", "g@x.com", "weekly", 30.0, 5.0, kind="conserve", score=90.0, minutes=10080)
    assert pick_suggestion([burn, conserve]) is None
    # The sibling is the cause: alone, the burn is the answer.
    assert pick_suggestion([burn]) is burn


def test_a_burn_survives_a_weaker_sibling_and_a_conserve_in_another_pool():
    burn = _alert("codex", "g@x.com", "5h", 20.0, 0.2, kind="burn", score=80.0, minutes=300)
    # A mid-band sibling carries less attention than the use-band burn: nothing is masked.
    mid = _alert("codex", "g@x.com", "weekly", 70.0, 5.0, kind="burn", score=30.0, urgency=Urgency.NONE, minutes=10080)
    assert pick_suggestion([burn, mid]) is burn
    # A conserve in a different account is a different pool: it does not hide this burn.
    other = _alert("codex", "h@x.com", "weekly", 30.0, 5.0, kind="conserve", score=99.0, minutes=10080)
    assert pick_suggestion([burn, other]) is burn


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
