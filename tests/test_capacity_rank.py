"""Every active band orders by capacity: dollars at risk in the row's window (aiuse-b31).

Capacity is ``price / max(refills per waking month, 1) x remaining% x multiplier``
-- the analyzer's value-at-risk -- so a 5-hour window is worth far less than a
weekly one, and a percent of a $200 plan far more than a percent of a $20 one.
Slow, mid and use each log-scale it onto their own score range; the band's old
signal (readiness, analyzer score, soft urgency) only breaks ties.
"""

from datetime import timedelta

import pytest

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.report import (
    _alert_queue_priority,
    _build_matrix_rows,
    _Pricing,
    _queue_score,
    render_priority_ladder,
)

# The 16-waking-hours-a-day month a plan's price is spread over, in minutes.
ACTIVE_MONTH_MINUTES = 16 * 30.44 * 60
WEEKLY, FIVE_HOUR, MONTHLY = 10080, 300, 43200
_BAND_SLOW, _BAND_MID, _BAND_USE = 3, 4, 5


def _share(minutes):
    """One refill's share of the plan price (a refill is never worth more than the plan)."""
    return min(minutes / ACTIVE_MONTH_MINUTES, 1.0)


def _alert(
    provider,
    account,
    label,
    minutes,
    remaining,
    *,
    kind="burn",
    urgency=Urgency.HIGH,
    score=80.0,
    days=2.0,
):
    return UseOrLoseAlert(
        urgency=urgency,
        provider=provider,
        account=account,
        window_label=label,
        remaining_percent=remaining,
        days_until_reset=days,
        plan=None,
        message=kind,
        source="test",
        score=score,
        kind=kind,
        window_minutes=minutes,
    )


def _rows(alerts, plans=None, snapshot=None, **analysis):
    config = {"plans": plans} if plans is not None else {}
    if analysis:
        config["analysis"] = analysis
    return _build_matrix_rows(alerts, snapshot, config)


CLAUDE_PLANS = {
    "claude": {
        "monthly_price": 20,
        "value_multiplier": {"5h": 1.4},
        "max@x.com": {"monthly_price": 200},
    },
    "copilot": {"monthly_price": 10},
}


def test_capacity_is_the_value_at_risk_of_the_alerts_window():
    pricing = _Pricing.from_config({"plans": CLAUDE_PLANS})
    # Weekly 50% of a $20 plan: half of a refill worth 34.5% of the price.
    assert pricing.capacity_usd("claude", "pro@x.com", 50.0, WEEKLY) == pytest.approx(20 * _share(WEEKLY) * 0.5)
    # The account's own $200 price, not the provider's $20.
    assert pricing.capacity_usd("claude", "max@x.com", 50.0, WEEKLY) == pytest.approx(200 * _share(WEEKLY) * 0.5)
    # A 5h window is 1/97 of the waking month, scaled by the plan's 5h value_multiplier.
    assert pricing.capacity_usd("claude", "pro@x.com", 100.0, FIVE_HOUR) == pytest.approx(20 * _share(FIVE_HOUR) * 1.4)
    # Monthly or longer: one refill is the whole price, never more.
    assert pricing.capacity_usd("copilot", None, 50.0, MONTHLY) == pytest.approx(5.0)
    assert pricing.capacity_usd("copilot", None, 50.0, 3 * MONTHLY) == pytest.approx(5.0)
    # remaining% outside 0..100 is clamped, not extrapolated.
    assert pricing.capacity_usd("copilot", None, 250.0, MONTHLY) == pytest.approx(10.0)
    assert pricing.capacity_usd("copilot", None, -5.0, MONTHLY) == 0.0


def test_window_length_comes_from_the_label_when_the_source_sent_none():
    pricing = _Pricing.from_config({"plans": CLAUDE_PLANS})
    assert pricing.capacity_usd("claude", None, 40.0, None, "Claude Code 5-hour") == pytest.approx(
        pricing.capacity_usd("claude", None, 40.0, FIVE_HOUR)
    )
    assert pricing.capacity_usd("claude", None, 40.0, None, "weekly quota") == pytest.approx(
        pricing.capacity_usd("claude", None, 40.0, WEEKLY)
    )
    # A declared length wins over the label.
    assert pricing.capacity_usd("claude", None, 40.0, WEEKLY, "5-hour") == pytest.approx(
        pricing.capacity_usd("claude", None, 40.0, WEEKLY)
    )


def test_waking_hours_scale_windows_shorter_than_the_waking_month_only():
    plans = {"copilot": {"monthly_price": 10}}
    base = _Pricing.from_config({"plans": plans})
    short_days = _Pricing.from_config({"plans": plans, "analysis": {"waking_hours_per_day": 8}})
    assert short_days.capacity_usd("copilot", None, 100.0, WEEKLY) == pytest.approx(
        2 * base.capacity_usd("copilot", None, 100.0, WEEKLY)
    )
    assert short_days.capacity_usd("copilot", None, 100.0, MONTHLY) == pytest.approx(10.0)


def test_unknown_window_length_has_no_capacity_and_sits_at_the_band_floor():
    pricing = _Pricing.from_config({"plans": CLAUDE_PLANS})
    assert pricing.capacity_usd("claude", None, 80.0, None, "quota") == 0.0
    assert pricing.capacity_usd("claude", None, 80.0, 0, None) == 0.0

    # Known length outranks unknown; two unknowns keep the old analyzer-score order.
    known = _alert("claude", "known@x.com", "weekly", WEEKLY, 10.0, score=60.0)
    blind_low = _alert("claude", "blind-low@x.com", "quota", None, 100.0, score=70.0)
    blind_high = _alert("claude", "blind-high@x.com", "quota", None, 100.0, score=140.0)
    rows = _rows([known, blind_high, blind_low], CLAUDE_PLANS)
    assert [r.account_full for r in rows] == ["blind-low@x.com", "blind-high@x.com", "known@x.com"]
    assert [r.queue_score for r in rows][:2] == [75, 75]
    assert rows[2].queue_score > 75


def test_use_band_orders_by_dollars_not_analyzer_score():
    # The 5h burn has the louder analyzer score (time bonus), but its whole window
    # holds $0.29; the weekly one holds $2.76.  Rows sort ascending: best sits last.
    short = _alert("claude", "short@x.com", "Claude Code 5-hour", FIVE_HOUR, 100.0, score=150.0, days=0.1)
    weekly = _alert("claude", "weekly@x.com", "Claude Code weekly", WEEKLY, 40.0, score=60.0, days=2.0)
    rows = _rows([weekly, short], CLAUDE_PLANS)
    assert [r.account_full for r in rows] == ["short@x.com", "weekly@x.com"]
    assert [r.band for r in rows] == [_BAND_USE, _BAND_USE]
    assert [r.queue_score for r in rows] == [84, 90]


def test_a_percent_of_a_bigger_plan_is_worth_more_in_use_and_mid():
    # 30% of a $200 account ($20.70) outranks 90% of a $10 plan ($3.10), whatever the analyzer says.
    for urgency, band in ((Urgency.HIGH, _BAND_USE), (Urgency.LOW, _BAND_MID)):
        big = _alert("claude", "max@x.com", "weekly", WEEKLY, 30.0, urgency=urgency, score=50.0)
        small = _alert("copilot", None, "weekly", WEEKLY, 90.0, urgency=urgency, score=95.0)
        rows = _rows([big, small], CLAUDE_PLANS)
        assert [r.band for r in rows] == [band, band]
        assert [r.service for r in rows] == ["copilot", "claude"]
        assert rows[1].queue_score > rows[0].queue_score


@pytest.mark.parametrize(
    ("kind", "urgency", "band"),
    [
        ("conserve", Urgency.HIGH, _BAND_SLOW),
        ("burn", Urgency.LOW, _BAND_MID),
        ("burn", Urgency.HIGH, _BAND_USE),
    ],
)
def test_window_length_normalises_capacity_in_every_band(kind, urgency, band):
    # Same account price, remaining% and reset: only the window length differs.  The 5h
    # alert carries the stronger analyzer score, so ordering by it would put it last.
    short = _alert("claude", "short@x.com", "5-hour", FIVE_HOUR, 60.0, kind=kind, urgency=urgency, score=95.0)
    weekly = _alert("claude", "weekly@x.com", "weekly", WEEKLY, 60.0, kind=kind, urgency=urgency, score=55.0)
    rows = _rows([weekly, short], CLAUDE_PLANS)
    assert [r.band for r in rows] == [band, band]
    assert [r.account_full for r in rows] == ["short@x.com", "weekly@x.com"]
    assert rows[1].queue_score > rows[0].queue_score


@pytest.mark.parametrize(
    ("kind", "urgency", "low", "high"),
    [
        ("conserve", Urgency.HIGH, 25, 49),
        ("burn", Urgency.LOW, 50, 74),
        ("burn", Urgency.HIGH, 75, 99),
    ],
)
def test_alert_scores_stay_inside_their_band_at_both_capacity_extremes(kind, urgency, low, high):
    plans = {"claude": {"monthly_price": 20, "huge@x.com": {"monthly_price": 5000}}}
    tiny = _alert("claude", "tiny@x.com", "5-hour", FIVE_HOUR, 2.0, kind=kind, urgency=urgency)
    huge = _alert("claude", "huge@x.com", "monthly", MONTHLY, 100.0, kind=kind, urgency=urgency)
    rows = _rows([huge, tiny], plans)
    assert [r.queue_score for r in rows] == [low, high]


@pytest.mark.parametrize(
    ("kind", "urgency"),
    [("burn", Urgency.LOW), ("burn", Urgency.HIGH)],
)
def test_equal_capacity_falls_back_to_the_analyzer_score(kind, urgency):
    plans = {"claude": {"monthly_price": 20}}
    loud = _alert("claude", "loud@x.com", "weekly", WEEKLY, 50.0, kind=kind, urgency=urgency, score=90.0)
    quiet = _alert("claude", "quiet@x.com", "weekly", WEEKLY, 50.0, kind=kind, urgency=urgency, score=40.0)
    pricing = _Pricing.from_config({"plans": plans})
    assert _alert_queue_priority(loud, pricing=pricing)[0] == _alert_queue_priority(quiet, pricing=pricing)[0]
    rows = _rows([loud, quiet], plans)
    assert [r.account_full for r in rows] == ["quiet@x.com", "loud@x.com"]
    assert rows[0].queue_score == rows[1].queue_score


def _account(provider, name, *windows):
    """An account whose windows are ``(label, remaining%, days to reset, minutes)`` tuples."""
    now = utcnow()
    return AccountUsage(
        provider=provider,
        source="test",
        account=name,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label=label,
                remaining_percent=remaining,
                resets_at=now + timedelta(days=days),
                window_minutes=minutes,
            )
            for label, remaining, days, minutes in windows
        ],
    )


def test_a_pool_collapses_onto_its_most_valuable_burn_and_most_restrictive_conserve():
    # One claude account, two alerts in the same band: the row takes the burn window with
    # the most dollars at risk (weekly), but the conserve window with the fewest (5h).
    snapshot = Snapshot(
        collected_at=utcnow(),
        accounts=[
            _account("claude", "a@x.com", ("5-hour", 100.0, 0.1, FIVE_HOUR), ("weekly", 40.0, 2.0, WEEKLY)),
            _account("claude", "b@x.com", ("5-hour", 40.0, 0.1, FIVE_HOUR), ("weekly", 5.0, 2.0, WEEKLY)),
        ],
    )
    plans = {"claude": {"monthly_price": 200, "value_multiplier": {"5h": 1.4}}}
    alerts = [
        _alert("claude", "a@x.com", "5-hour", FIVE_HOUR, 100.0, score=150.0),  # $2.87
        _alert("claude", "a@x.com", "weekly", WEEKLY, 40.0, score=60.0),  # $27.60
        _alert("claude", "b@x.com", "5-hour", FIVE_HOUR, 40.0, kind="conserve"),  # $1.15
        _alert("claude", "b@x.com", "weekly", WEEKLY, 5.0, kind="conserve"),  # $3.45
    ]
    burn_row, slow_row = sorted(_rows(alerts, plans, snapshot), key=lambda r: r.account_full)
    assert burn_row.band == _BAND_USE and burn_row.queue_score == 75 + 21  # the 5h's $2.87 would score 75 + 15
    assert slow_row.band == _BAND_SLOW and slow_row.queue_score == 25 + 12  # $3.45 would score 25 + 15


def test_unalerted_mid_rows_order_by_capacity_then_soft_urgency():
    snapshot = Snapshot(
        collected_at=utcnow(),
        accounts=[
            _account("claude", "max@x.com", ("weekly", 50.0, 3.0, WEEKLY)),  # $34.49
            _account("claude", "pro@x.com", ("weekly", 50.0, 3.0, WEEKLY)),  # $3.45
            _account("copilot", None, ("weekly", 50.0, 3.0, WEEKLY)),  # $1.72
            # $1.72, but further from its reset: the soft urgency tie-break sorts it first.
            _account("copilot", "late@x.com", ("weekly", 50.0, 6.0, WEEKLY)),
        ],
    )
    rows = _rows([], CLAUDE_PLANS, snapshot)
    assert [r.band for r in rows] == [_BAND_MID] * 4
    assert [(r.service, r.account_full) for r in rows] == [
        ("copilot", "late@x.com"),
        ("copilot", None),
        ("claude", "pro@x.com"),
        ("claude", "max@x.com"),
    ]
    assert [r.queue_score for r in rows] == [63, 63, 65, 71]


def test_unalerted_mid_row_without_a_window_sits_at_the_mid_floor():
    snapshot = Snapshot(
        collected_at=utcnow(),
        accounts=[AccountUsage(provider="credits_only", source="test", balance_usd=12.0)],
    )
    (row,) = _rows([], None, snapshot)
    assert row.band == _BAND_MID and row.queue_score == 50


def test_ladder_orders_by_the_configured_account_price():
    # render_priority_ladder gets prices from the config it is handed.
    max_acct = _alert("claude", "max@x.com", "weekly", WEEKLY, 30.0, score=50.0)
    pro_acct = _alert("claude", "pro@x.com", "weekly", WEEKLY, 30.0, score=95.0)
    with_prices = render_priority_ladder([pro_acct, max_acct], color=False, width=200, config={"plans": CLAUDE_PLANS})
    assert with_prices.index("pro@x.com") < with_prices.index("max@x.com")
    # Without a config both fall back to the built-in provider price and tie on capacity:
    # the analyzer score decides.
    without = render_priority_ladder([pro_acct, max_acct], color=False, width=200)
    assert without.index("max@x.com") < without.index("pro@x.com")


def test_queue_score_is_a_pure_log_scale_of_capacity_inside_each_band():
    for band, low in ((_BAND_SLOW, 25), (_BAND_MID, 50), (_BAND_USE, 75)):
        # Six points per decade of dollars, $0.01 to $100.
        assert [_queue_score(band, (usd, 0.0)) for usd in (0.01, 0.1, 1.0, 10.0, 100.0)] == [
            low,
            low + 6,
            low + 12,
            low + 18,
            low + 24,
        ]
        assert _queue_score(band, (0.0, 0.0)) == low
        assert _queue_score(band, (1e9, 0.0)) == low + 24
