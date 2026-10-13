"""Mid and use rows rank by the dollars their pace projects to expire unspent.

A row's priority leads with ``projected_waste_fraction x cycle value``: the share of one full refill of its
window that will still be unspent at the reset, priced exactly like capacity (the account's price, the plan's
value_multiplier, the cheapest-plan fallback for an unpriced provider).  An imminent reset with real waste
therefore outranks a big window with days left.  A row with no pace to project keeps its capacity, and slow
rows lead with capacity as before.  ``suggestion`` (and ``aiuse suggest``, and the status line's burn) is the
board's top actionable burn: one ordering, shared through ``aiuse.analysis.ranking``.
"""

from datetime import timedelta

import pytest

from aiuse.analysis.ranking import _alert_queue_priority, _Pricing, alert_sort_key
from aiuse.analysis.suggest import pick_suggestion
from aiuse.analysis.use_or_lose import analyze_use_or_lose
from aiuse.models import (
    AccountUsage,
    BillingKind,
    PaceProfile,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    provider_display_name,
    utcnow,
)
from aiuse.report import _build_matrix_rows, render_clock_matrix, render_status_line

# The 16-waking-hours-a-day month a plan's price is spread over, in minutes.
ACTIVE_MONTH_MINUTES = 16 * 30.44 * 60
WEEKLY, FIVE_HOUR, MONTHLY = 10080, 300, 43200
_BAND_ERROR, _BAND_EMPTY, _BAND_NA, _BAND_SLOW, _BAND_MID, _BAND_USE = 0, 1, 2, 3, 4, 5

PLANS = {
    "claude": {"monthly_price": 20, "value_multiplier": {"5h": 1.4}, "max@x.com": {"monthly_price": 200}},
    "codex": {"monthly_price": 20},
    "copilot": {"monthly_price": 10},
    "cursor": {"monthly_price": 20},
    "opencode": {"monthly_price": 10},
}
CONFIG = {"plans": PLANS}


def _share(minutes):
    """One refill's share of the plan price (a refill is never worth more than the plan)."""
    return min(minutes / ACTIVE_MONTH_MINUTES, 1.0)


def _pace(waste):
    """A pace projecting ``waste`` of the window unspent at its reset (None: nothing to project)."""
    return PaceProfile(
        elapsed_fraction=0.5,
        used_fraction=0.5,
        pace_ratio=1.0,
        projected_used_fraction=None if waste is None else 1.0 - waste,
        projected_waste_fraction=waste,
        projected_waste_usd=None,
        projected_exhaust_at=None,
    )


def _alert(
    provider,
    account,
    label,
    minutes,
    remaining,
    *,
    pace=None,
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
        pace=pace,
    )


def _account(now, provider, name, *windows):
    """An account whose windows are ``(label, remaining%, days to reset, minutes)`` tuples."""
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


# ── the key: projected waste, priced like capacity ─────────────────────────────────────────────


def test_projected_waste_is_the_waste_share_of_a_full_refill_priced_like_capacity():
    pricing = _Pricing.from_config(CONFIG)
    # The account's own price and the plan's 5h multiplier, then the provider's price.
    assert pricing.at_risk_usd("claude", "max@x.com", 70.0, FIVE_HOUR, "5-hour", 0.5) == pytest.approx(
        0.5 * 200 * _share(FIVE_HOUR) * 1.4
    )
    assert pricing.at_risk_usd("claude", "pro@x.com", 70.0, FIVE_HOUR, "5-hour", 0.5) == pytest.approx(
        0.5 * 20 * _share(FIVE_HOUR) * 1.4
    )
    # An unpriced provider is valued at the cheapest configured plan ($10), as capacity is.
    assert pricing.at_risk_usd("devin", None, 70.0, WEEKLY, "weekly", 0.5) == pytest.approx(0.5 * 10 * _share(WEEKLY))
    # Monthly or longer: a refill is the whole price, never more.
    assert pricing.at_risk_usd("copilot", None, 20.0, MONTHLY, "monthly", 0.4) == pytest.approx(4.0)
    # The window length may come from the label when the source sent none; none at all has no refill to price.
    assert pricing.at_risk_usd("copilot", None, 20.0, None, "Copilot weekly", 0.4) == pytest.approx(
        0.4 * 10 * _share(WEEKLY)
    )
    assert pricing.at_risk_usd("copilot", None, 20.0, None, "quota", 0.4) == 0.0
    # A waste share outside 0..1 is clamped, not extrapolated.
    assert pricing.at_risk_usd("copilot", None, 20.0, MONTHLY, None, 1.7) == pytest.approx(10.0)
    assert pricing.at_risk_usd("copilot", None, 20.0, MONTHLY, None, -0.2) == 0.0
    # The waste is a share of a full refill: what is left now (remaining%) does not scale it.
    assert pricing.at_risk_usd("copilot", None, 5.0, MONTHLY, None, 0.4) == pytest.approx(4.0)


def test_an_alert_leads_with_the_dollars_its_pace_projects_to_expire():
    pricing = _Pricing.from_config(CONFIG)
    alert = _alert("claude", "max@x.com", "weekly", WEEKLY, 40.0, score=77.0, days=2.0, pace=_pace(0.31))
    priority = _alert_queue_priority(alert, pricing=pricing)
    assert priority[0] == pytest.approx(0.31 * 200 * _share(WEEKLY))
    # The tie-breakers are unchanged: analyzer score, remaining capacity, sooner reset.
    assert priority[1:] == (77.0, 40.0, -2.0)


def test_imminent_5h_waste_outranks_an_on_pace_weekly_with_more_raw_dollars():
    short = _alert(
        "claude", "short@x.com", "Claude Code 5-hour", FIVE_HOUR, 70.0, score=140.0, days=0.02, pace=_pace(0.9)
    )
    weekly = _alert("claude", "weekly@x.com", "Claude Code weekly", WEEKLY, 60.0, score=60.0, days=3.0, pace=_pace(0.0))
    pricing = _Pricing.from_config(CONFIG)
    # By capacity the weekly window is ten times the bigger: that was the whole ordering.
    assert pricing.capacity_usd("claude", "weekly@x.com", 60.0, WEEKLY) > 10 * pricing.capacity_usd(
        "claude", "short@x.com", 70.0, FIVE_HOUR
    )
    # By waste the 5h window has $0.26 about to expire and the on-pace weekly one has nothing to lose.
    assert _alert_queue_priority(short, pricing=pricing)[0] == pytest.approx(0.9 * 20 * _share(FIVE_HOUR) * 1.4)
    assert _alert_queue_priority(weekly, pricing=pricing)[0] == 0.0
    rows = _build_matrix_rows([short, weekly], None, CONFIG)
    assert [r.band for r in rows] == [_BAND_USE, _BAND_USE]
    assert [r.account_full for r in rows] == ["weekly@x.com", "short@x.com"]  # rows sort ascending: best last
    assert [r.queue_score for r in rows] == [75, 83]


def test_the_same_holds_for_windows_without_an_alert_which_the_board_paces_itself():
    now = utcnow()
    snapshot = Snapshot(
        collected_at=now,
        accounts=[
            # Half used with half the week gone: exactly on pace, on course to spend all of it.
            _account(now, "copilot", None, ("weekly", 50.0, 3.5, WEEKLY)),
            # 30% used with 93% of the window gone and 20 minutes left: it will leave ~68% unspent.
            _account(now, "claude", "five@x.com", ("5-hour", 70.0, 20 / 1440, FIVE_HOUR)),
        ],
    )
    rows = _build_matrix_rows([], snapshot, CONFIG)
    assert [r.band for r in rows] == [_BAND_MID, _BAND_MID]
    assert [r.service for r in rows] == ["copilot", "claude"]
    assert [r.queue_score for r in rows] == [50, 58]
    # Capacity ($1.72 against $0.20) would have put the weekly window last.
    pricing = _Pricing.from_config(CONFIG)
    assert pricing.capacity_usd("copilot", None, 50.0, WEEKLY) > 8 * pricing.capacity_usd(
        "claude", "five@x.com", 70.0, FIVE_HOUR
    )


def test_an_early_window_ranks_by_its_projection_like_any_other():
    # The analyzer will not call a window under 15% elapsed burn or conserve, but a paced window
    # projects a waste share all the same, and that share (not its capacity) is what the row is worth.
    now = utcnow()
    snapshot = Snapshot(
        collected_at=now,
        accounts=[
            # 30% used 5% into the week: at that rate it is gone long before the reset.
            _account(now, "copilot", "fast@x.com", ("weekly", 70.0, 7 * 0.95, WEEKLY)),
            # Untouched 5% into the week: all of it is still to be lost.
            _account(now, "copilot", "idle@x.com", ("weekly", 100.0, 7 * 0.95, WEEKLY)),
        ],
    )
    rows = _build_matrix_rows([], snapshot, CONFIG)
    assert [r.account_full for r in rows] == ["fast@x.com", "idle@x.com"]
    assert rows[0].queue_score == 50
    assert rows[1].queue_score == 65  # $3.45: the whole weekly refill of a $10 plan


# ── no pace: capacity, as before ─────────────────────────────────────────────────────────────


def test_a_row_with_no_pace_to_project_ranks_by_its_capacity():
    pricing = _Pricing.from_config(CONFIG)
    capacity = pricing.capacity_usd("claude", "a@x.com", 60.0, WEEKLY)
    assert capacity == pytest.approx(20 * _share(WEEKLY) * 0.6)
    # No pace at all, and a pace that projects nothing (an unclocked window): the same dollars.
    no_pace = _alert("claude", "a@x.com", "weekly", WEEKLY, 60.0, pace=None)
    unprojected = _alert("claude", "a@x.com", "weekly", WEEKLY, 60.0, pace=_pace(None))
    assert _alert_queue_priority(no_pace, pricing=pricing)[0] == pytest.approx(capacity)
    assert _alert_queue_priority(unprojected, pricing=pricing)[0] == pytest.approx(capacity)

    # An unalerted window with no reset time has no pace either: it scores its capacity.
    snapshot = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                provider="copilot",
                source="test",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[QuotaWindow(label="weekly", remaining_percent=60.0, window_minutes=WEEKLY)],
            )
        ],
    )
    (row,) = _build_matrix_rows([], snapshot, CONFIG)
    assert row.band == _BAND_MID
    assert row.queue_score == 64  # 60% of a $3.45 weekly refill = $2.07


def test_no_pace_ranks_at_capacity_the_most_it_could_lose_above_a_window_projected_to_be_used():
    # Same window, same 60% left: one is projected to leave 20% of it, the other has no pace.
    projected = _alert("copilot", "projected@x.com", "weekly", WEEKLY, 60.0, score=90.0, pace=_pace(0.2))
    unknown = _alert("copilot", "unknown@x.com", "weekly", WEEKLY, 60.0, score=10.0)
    rows = _build_matrix_rows([unknown, projected], None, CONFIG)
    assert [r.account_full for r in rows] == ["projected@x.com", "unknown@x.com"]


# ── slow is unchanged ────────────────────────────────────────────────────────────────────────


def test_slow_rows_still_rank_by_capacity_whatever_their_projected_waste():
    pricing = _Pricing.from_config(CONFIG)
    # 5% of a $200 plan's week against 5% of a $10 plan's: the second claims 95% waste, the first none.
    rich = _alert("claude", "max@x.com", "weekly", WEEKLY, 5.0, kind="conserve", pace=_pace(0.0))
    poor = _alert("copilot", None, "weekly", WEEKLY, 5.0, kind="conserve", pace=_pace(0.95))
    for alert in (rich, poor):
        assert _alert_queue_priority(alert, pricing=pricing)[0] == pytest.approx(
            pricing.capacity_usd(alert.provider, alert.account, 5.0, WEEKLY)
        )
    rows = _build_matrix_rows([rich, poor], None, CONFIG)
    assert [r.band for r in rows] == [_BAND_SLOW, _BAND_SLOW]
    assert [r.account_full for r in rows] == [None, "max@x.com"]  # most capacity last


# ── the suggestion is the board's top actionable burn ───────────────────────────────────────────


def test_suggestion_follows_the_board_not_the_analyzer_score():
    loud = _alert("codex", None, "Codex 5-hour", FIVE_HOUR, 80.0, score=145.0, days=0.04, pace=_pace(0.75))
    rich = _alert("claude", "max@x.com", "Claude weekly", WEEKLY, 40.0, score=60.0, days=2.0, pace=_pace(0.31))
    assert max([loud, rich], key=lambda a: a.score) is loud  # what the suggestion used to pick
    assert pick_suggestion([loud, rich], CONFIG) is rich
    assert pick_suggestion([rich, loud], CONFIG) is rich
    # ... which is the row the board puts last.
    rows = _build_matrix_rows([loud, rich], None, CONFIG)
    assert rows[-1].account_full == "max@x.com"
    assert alert_sort_key(rich, _Pricing.from_config(CONFIG)) > alert_sort_key(loud, _Pricing.from_config(CONFIG))


def test_suggestion_prices_the_board_from_the_config_it_is_given():
    claude = _alert("claude", "a@x.com", "weekly", WEEKLY, 50.0, pace=_pace(0.5))
    copilot = _alert("copilot", None, "weekly", WEEKLY, 50.0, pace=_pace(0.5))
    assert pick_suggestion([claude, copilot]) is claude  # built-in prices: $20 against $10
    assert pick_suggestion([claude, copilot], {"plans": {"copilot": {"monthly_price": 500}}}) is copilot
    assert pick_suggestion([claude, copilot], {"plans": {"claude": {"a@x.com": {"monthly_price": 1}}}}) is copilot


def test_suggestion_takes_the_highest_band_before_the_dollars():
    use = _alert("copilot", None, "weekly", WEEKLY, 90.0, pace=_pace(0.31), days=2.0)
    mid_but_bigger = _alert(
        "claude", "max@x.com", "monthly", MONTHLY, 90.0, urgency=Urgency.LOW, pace=_pace(0.9), days=12.0
    )
    pricing = _Pricing.from_config(CONFIG)
    assert _alert_queue_priority(mid_but_bigger, pricing=pricing)[0] > _alert_queue_priority(use, pricing=pricing)[0]
    assert pick_suggestion([use, mid_but_bigger], CONFIG) is use


def test_suggestion_eligibility_is_unchanged():
    conserve = _alert("codex", None, "weekly", WEEKLY, 30.0, kind="conserve", score=200.0, pace=_pace(0.99))
    info = _alert("claude", "max@x.com", "weekly", WEEKLY, 90.0, urgency=Urgency.INFO, score=300.0, pace=_pace(0.99))
    quiet = _alert("claude", "max@x.com", "weekly", WEEKLY, 90.0, urgency=Urgency.NONE, pace=_pace(0.99))
    prepaid = _alert("deepseek", None, "$12.00", None, 0.0, kind="prepaid", urgency=Urgency.INFO)
    assert pick_suggestion([conserve, info, quiet, prepaid], CONFIG) is None
    assert pick_suggestion([], CONFIG) is None
    burn = _alert("copilot", None, "weekly", WEEKLY, 50.0, urgency=Urgency.LOW, score=5.0, pace=_pace(0.31))
    assert pick_suggestion([conserve, info, quiet, prepaid, burn], CONFIG) is burn


def _mixed_fixture(now):
    """Every band on one board, with alerts from the real analyzer (so each carries a real pace)."""
    snapshot = Snapshot(
        collected_at=now,
        accounts=[
            # use: a Max weekly window at 90% left with 2 days to go -- $59 of a $200 plan about to lapse.
            _account(now, "claude", "max@x.com", ("Claude Code weekly", 90.0, 2.0, WEEKLY)),
            # use: a Codex 5h window with an hour left -- the loudest analyzer score, a few cents.
            _account(now, "codex", None, ("Codex 5-hour", 80.0, 0.04, FIVE_HOUR)),
            # use: Copilot's monthly window, 60% left with 5 days to go.
            _account(now, "copilot", None, ("Copilot premium requests", 60.0, 5.0, MONTHLY)),
            # mid: 20 days from a reset, past the analyzer's horizon, so no alert.
            _account(now, "cursor", None, ("Cursor included", 85.0, 20.0, MONTHLY)),
            # slow: 8% left of the week and burning fast.
            _account(now, "opencode-go", None, ("OpenCode Go weekly", 8.0, 5.0, WEEKLY)),
            # empty
            _account(now, "zai", None, ("zai weekly", 0.0, 3.0, WEEKLY)),
            # error, n/a
            AccountUsage(provider="grok", source="test", error="failed"),
            AccountUsage(
                provider="deepseek",
                source="test",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=12.0,
            ),
        ],
    )
    config = {
        "analysis": {
            "scoring_mode": "pace",
            "pace": {"waste_alert_fraction": 0.30, "min_elapsed_fraction": 0.15, "conserve_min_lead_hours": 4.0},
            "max_days_until_reset": 14,
            "min_value_at_risk_usd": 0.0,
            "min_value_fraction": 0.0,
            "waking_hours_per_day": 16,
            "learn_from_history": False,
        },
        "plans": PLANS,
    }
    return snapshot, analyze_use_or_lose(snapshot, config), config


def test_suggestion_is_the_boards_top_row_on_a_mixed_board():
    now = utcnow()
    snapshot, alerts, config = _mixed_fixture(now)
    rows = _build_matrix_rows(alerts, snapshot, config)
    assert {r.band for r in rows} == {_BAND_ERROR, _BAND_EMPTY, _BAND_NA, _BAND_SLOW, _BAND_MID, _BAND_USE}

    winner = pick_suggestion(alerts, config)
    assert winner is not None
    top = rows[-1]
    assert top.band == _BAND_USE
    assert (top.service, top.account_full) == (provider_display_name(winner.provider), winner.account)
    assert (winner.provider, winner.account) == ("claude", "max@x.com")

    # The analyzer's loudest burn is the Codex 5h window; the suggestion no longer follows it.
    burns = [a for a in alerts if a.kind == "burn"]
    assert max(burns, key=lambda a: a.score).provider == "codex"
    assert [r.service for r in rows if r.band == _BAND_USE][-1] == provider_display_name("claude")

    # What the user reads: the last table row, the suggestion and the status line's head agree.
    plain = render_clock_matrix(alerts, snapshot=snapshot, config=config, color=False, width=200)
    last_row = [line for line in plain.splitlines() if line.startswith(("use ", "mid ", "slow", "empty"))][-1]
    assert last_row.startswith("use") and "claude" in last_row
    assert render_status_line(snapshot, alerts, config=config).startswith(f"use: claude {winner.window_label} ")


def test_status_line_leads_with_the_suggested_burn_and_keeps_a_louder_conserve_first():
    now = utcnow()
    snapshot, alerts, config = _mixed_fixture(now)
    head = render_status_line(snapshot, alerts, config=config).split(" · ")[0]
    assert head.startswith("use: ") and "Claude Code weekly" in head
    # A conserve alert still leads when its analyzer score tops every burn's (unchanged).
    loud_slow = _alert("opencode-go", None, "OpenCode Go weekly", WEEKLY, 8.0, kind="conserve", score=500.0)
    assert render_status_line(snapshot, [*alerts, loud_slow], config=config).startswith("slow: ")


# ── the JSON dollars and the ranking dollars ────────────────────────────────────────────────────


def test_ranking_waste_matches_the_json_projected_waste_usd_of_a_priced_account():
    # ``pace.projected_waste_usd`` (``--json``) prices the account the way the ranking does: per account,
    # with the plan's multiplier.  They differ only where the analyzer has no price to use.
    now = utcnow()
    _snapshot, alerts, config = _mixed_fixture(now)
    pricing = _Pricing.from_config(config)
    priced = [a for a in alerts if a.kind == "burn" and a.pace is not None]
    assert {(a.provider, a.account) for a in priced} >= {("claude", "max@x.com"), ("codex", None), ("copilot", None)}
    for alert in priced:
        waste = pricing.at_risk_usd(
            alert.provider,
            alert.account,
            alert.remaining_percent,
            alert.window_minutes,
            alert.window_label,
            alert.pace.projected_waste_fraction,
        )
        assert alert.pace.projected_waste_usd == pytest.approx(waste, abs=0.006)
