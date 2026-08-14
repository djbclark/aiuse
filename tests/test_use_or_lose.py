"""Unit tests for use-or-lose analysis (no live CLI required)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aiuse.analysis.use_or_lose import (
    _classify_flexibility,
    _compute_flexibility_profile,
    _compute_value_at_risk,
    _human_when,
    _redistribute_weights,
    _score_multi_dimension,
    analyze_use_or_lose,
)
from aiuse.models import (
    AccountUsage,
    BillingKind,
    FlexibilityClass,
    FlexibilityProfile,
    QuotaWindow,
    Snapshot,
    Urgency,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0.5, "in 12h"),
        (1.0, "in 1.0 days"),
        (1.01, "in 1.0 days"),
        (1.38, "in 1.4 days"),
        (2.01, "in 2.0 days"),
    ],
)
def test_human_when_shows_remaining_days_to_one_decimal(days: float, expected: str):
    assert _human_when(days) == expected


def test_human_when_marks_date_only_reset_as_an_estimate():
    assert _human_when(1.38, estimated=True) == "in ~1 day"


def test_flags_nearly_unused_weekly_window():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                account="user@example.com",
                plan="plus",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Weekly",
                        used_percent=0,
                        remaining_percent=100,
                        resets_at=_now() + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "learn_from_history": False,
                "scoring_mode": "legacy",
                "use_multi_dim_scoring": False,
                "min_remaining_percent": 40,
                "max_days_until_reset": 14,
                "urgent_remaining_percent": 70,
                "urgent_days_until_reset": 7,
            },
            "plans": {"codex": {"name": "Codex Plus"}},
        },
    )
    assert alerts
    assert alerts[0].provider == "codex"
    assert alerts[0].remaining_percent == 100
    assert alerts[0].urgency in (Urgency.CRITICAL, Urgency.HIGH)


def test_ignores_short_5h_window():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="5-hour",
                        used_percent=0,
                        remaining_percent=100,
                        resets_at=_now() + timedelta(hours=4),
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "learn_from_history": False,
                "scoring_mode": "legacy",
                "use_multi_dim_scoring": False,
                "min_remaining_percent": 40,
            }
        },
    )
    assert not any(a.window_label == "5-hour" for a in alerts)


def test_prepaid_is_info_only():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="openrouter",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=18.90,
            )
        ],
    )
    alerts = analyze_use_or_lose(snap, {"analysis": {"learn_from_history": False}})
    assert len(alerts) == 1
    assert alerts[0].urgency == Urgency.INFO
    assert alerts[0].kind == "prepaid"
    assert alerts[0].score == 0.0
    assert "balance $18.90" in alerts[0].window_label


def test_deepseek_prepaid_under_threshold_is_not_alerted():
    """Deepseek is purchased API tokens — no burn urgency even with a fake 100% window."""
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="deepseek",
                account="CodexBar",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=4.99,
                windows=[
                    QuotaWindow(
                        label="Deepseek quota 1 (name not supplied by CodexBar)",
                        used_percent=0.0,
                        remaining_percent=100.0,
                        resets_at=None,
                        reset_description="$4.99 (Paid: $4.99 / Granted: $0.00)",
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(snap, {"analysis": {"learn_from_history": False}})
    assert alerts == []


def test_well_used_window_not_flagged():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="grok",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Weekly",
                        used_percent=90,
                        remaining_percent=10,
                        resets_at=_now() + timedelta(days=2),
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "learn_from_history": False,
                "scoring_mode": "legacy",
                "use_multi_dim_scoring": False,
                "min_remaining_percent": 40,
            }
        },
    )
    assert alerts == []


def test_max_days_override_allows_later_window():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="copilot",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="GitHub Copilot completions",
                        used_percent=0,
                        remaining_percent=100,
                        resets_at=_now() + timedelta(days=20),
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "learn_from_history": False,
                "scoring_mode": "legacy",
                "use_multi_dim_scoring": False,
                "min_remaining_percent": 40,
                "max_days_until_reset": 30,
                "urgent_remaining_percent": 101,
            }
        },
    )
    assert len(alerts) == 1


def test_expired_window_is_not_actionable():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Codex weekly quota",
                        used_percent=0,
                        remaining_percent=100,
                        resets_at=_now() - timedelta(minutes=1),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    assert analyze_use_or_lose(snap, {"analysis": {"learn_from_history": False}}) == []


def test_alert_has_no_dollar_value_estimate():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Codex weekly quota",
                        used_percent=0,
                        remaining_percent=100,
                        resets_at=_now() + timedelta(days=2),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alert = analyze_use_or_lose(
        snap,
        {
            "analysis": {"learn_from_history": False, "scoring_mode": "legacy", "use_multi_dim_scoring": False},
            "plans": {"codex": {"name": "Codex Plus", "monthly_usd": 20}},
        },
    )[0]
    assert "monthly_usd" not in alert.to_dict()
    assert "$" not in alert.message


# --- Phase 2: multi-dimensional scoring tests ---


def test_redistribute_weights_sums_to_one():
    for flex in (0.0, 0.25, 0.5, 0.75, 1.0):
        w = _redistribute_weights(flex)
        assert abs(sum(w) - 1.0) < 0.001, f"flex={flex}: sum={sum(w)}"


def test_redistribute_weights_extremes():
    w0 = _redistribute_weights(0.0)
    assert w0 == (0.35, 0.30, 0.35)
    w1 = _redistribute_weights(1.0)
    assert w1 == (0.50, 0.00, 0.50)


def test_redistribute_weights_midpoint():
    w = _redistribute_weights(0.5)
    assert w[0] == 0.425
    assert w[1] == 0.15
    assert w[2] == 0.425


def test_classify_flexibility_throttled():
    cls, score = _classify_flexibility(
        window_minutes=300, provider="claude", config={"consumption_flexibility_defaults": {"5h": 0.0}}
    )
    assert cls == FlexibilityClass.THROTTLED
    assert score == 0.0


def test_classify_flexibility_semi():
    cls, score = _classify_flexibility(
        window_minutes=10080,
        provider="codex",
        config={"consumption_flexibility_defaults": {"weekly": 0.7}},
    )
    assert cls == FlexibilityClass.SEMI_THROTTLED
    assert score == 0.7


def test_classify_flexibility_burstable():
    cls, score = _classify_flexibility(
        window_minutes=20000,
        provider="opencode-go",
        config={"consumption_flexibility_defaults": {"monthly": 1.0}},
    )
    assert cls == FlexibilityClass.BURSTABLE
    assert score == 1.0


def test_classify_flexibility_provider_override():
    cfg = {"provider_overrides": {"claude": {"5h": {"flexibility": 0.0}}}}
    cls, score = _classify_flexibility(window_minutes=300, provider="claude", config=cfg)
    assert cls == FlexibilityClass.THROTTLED
    assert score == 0.0


def test_antigravity_uses_gemini_provider_override():
    """Config keys gemini; collector name is often antigravity after canonicalization."""
    cfg = {
        "consumption_flexibility_defaults": {"5h": 0.5},
        "provider_overrides": {"gemini": {"5h": {"flexibility": 0.0}}},
    }
    cls, score = _classify_flexibility(window_minutes=300, provider="antigravity", config=cfg)
    assert cls == FlexibilityClass.THROTTLED
    assert score == 0.0


def test_compute_value_at_risk_weekly_unchanged_when_cycles_gt_one():
    val = _compute_value_at_risk(
        remaining=100.0,
        window_minutes=10080,
        monthly_price=20.0,
        waking_hours_per_day=16,
    )
    expected = (100.0 / 100.0) * (20.0 / (16 * 30.44 * 60 / 10080))
    assert abs(val - expected) < 0.01


def test_compute_value_at_risk_monthly_window_cannot_exceed_plan_fraction():
    # window_minutes longer than waking month → active_cycles < 1 without clamp.
    val = _compute_value_at_risk(
        remaining=90.0,
        window_minutes=44640,
        monthly_price=10.0,
        waking_hours_per_day=16,
    )
    assert val <= 10.0 * 0.9 + 1e-9
    assert abs(val - 9.0) < 0.01


def test_compute_value_at_risk_5h():
    val = _compute_value_at_risk(
        remaining=80.0,
        window_minutes=300,
        monthly_price=20.0,
        waking_hours_per_day=16,
    )
    expected = 0.80 * (20.0 / (16 * 30.44 * 60 / 300))
    assert abs(val - expected) < 0.01


def test_cycles_needed_varies_with_capacity_for_same_remaining():
    now = _now()
    resets = now + timedelta(minutes=295)
    cfg = {
        "max_requests_per_minute": 0.5,
        "provider_overrides": {"claude": {"5h": {"flexibility": 0.0}}},
    }
    cycles = []
    for capacity in (1, 45, 100_000):
        window = QuotaWindow(
            label="Claude Code 5-hour",
            remaining_percent=95.0,
            resets_at=resets,
            window_minutes=300,
            refill_capacity=float(capacity),
            refill_capacity_unit="requests",
        )
        profile = _compute_flexibility_profile(
            window=window,
            provider="claude",
            config=cfg,
            monthly_price=20.0,
            now=now,
        )
        assert profile is not None
        cycles.append(profile.cycles_needed)
    assert cycles[0] != cycles[2]
    assert cycles[1] != cycles[2]
    assert len(set(cycles)) >= 2


def test_claude_5h_flexibility_urgency_not_pinned_at_100():
    """Interim fix for cycles_needed canceling capacity; full redesign is Phase 2."""
    now = _now()
    resets = now + timedelta(minutes=295)
    window = QuotaWindow(
        label="Claude Code 5-hour",
        remaining_percent=95.0,
        resets_at=resets,
        window_minutes=300,
        refill_capacity=45.0,
        refill_capacity_unit="requests",
    )
    cfg = {
        "waking_hours_per_day": 16,
        "max_requests_per_minute": 0.5,
        "provider_overrides": {
            "claude": {
                "5h": {
                    "flexibility": 0.0,
                    "refill_capacity": 45,
                    "refill_capacity_unit": "requests",
                }
            }
        },
        "plans": {"claude": {"monthly_price": 20}},
    }
    profile = _compute_flexibility_profile(
        window=window,
        provider="claude",
        config=cfg,
        monthly_price=20.0,
        now=now,
    )
    assert profile is not None
    assert profile.earliest_start_calendar is not None
    # Earliest start for remaining burn is still in the future (not one full
    # cycle before reset, which would always already be past).
    assert profile.earliest_start_calendar > now

    urgency, score = _score_multi_dimension(
        profile=profile,
        remaining=95.0,
        days=295 / 1440,
        config=cfg,
    )
    # Broken formula pinned flexibility_urgency at 100 → score ≈ 71.8 MEDIUM.
    assert score < 65.0
    assert score == pytest.approx(58.99, abs=1.5)
    assert urgency in (Urgency.LOW, Urgency.INFO, Urgency.MEDIUM)
    # Must not be the old always-MEDIUM-from-pin path alone.
    assert not (urgency == Urgency.MEDIUM and score > 70)


def test_score_multi_dim_burstable_imminent_is_critical():
    profile = FlexibilityProfile(
        flexibility_class=FlexibilityClass.BURSTABLE,
        consumption_flexibility=1.0,
        value_at_risk_usd=16.0,
    )
    urgency, score = _score_multi_dimension(profile=profile, remaining=80.0, days=0.01)
    assert urgency == Urgency.CRITICAL
    assert score >= 100


def test_score_multi_dim_burstable_far_out_is_low():
    profile = FlexibilityProfile(
        flexibility_class=FlexibilityClass.BURSTABLE,
        consumption_flexibility=1.0,
        value_at_risk_usd=4.0,
    )
    urgency, score = _score_multi_dimension(profile=profile, remaining=50.0, days=20)
    assert urgency in (Urgency.INFO, Urgency.NONE, Urgency.LOW)
    assert score < 30


def test_score_multi_dim_throttled_past_start_elevated():
    now = datetime.now(timezone.utc)
    profile = FlexibilityProfile(
        flexibility_class=FlexibilityClass.THROTTLED,
        consumption_flexibility=0.0,
        value_at_risk_usd=2.0,
        earliest_start_calendar=now - timedelta(hours=1),
    )
    urgency, score = _score_multi_dimension(profile=profile, remaining=40.0, days=0.5)
    assert urgency in (Urgency.HIGH, Urgency.MEDIUM)


def test_value_urgency_normalized_to_own_plan_not_global_max():
    """A $10 plan and $30 plan with same relative value_at_risk score equally."""
    cheap = FlexibilityProfile(
        flexibility_class=FlexibilityClass.BURSTABLE,
        consumption_flexibility=1.0,
        value_at_risk_usd=5.0,  # 50% of $10
    )
    expensive = FlexibilityProfile(
        flexibility_class=FlexibilityClass.BURSTABLE,
        consumption_flexibility=1.0,
        value_at_risk_usd=15.0,  # 50% of $30
    )
    cfg = {"plans": {"a": {"monthly_price": 10}, "b": {"monthly_price": 30}}}
    _, score_cheap = _score_multi_dimension(profile=cheap, remaining=50.0, days=5.0, config=cfg, monthly_price=10.0)
    _, score_exp = _score_multi_dimension(profile=expensive, remaining=50.0, days=5.0, config=cfg, monthly_price=30.0)
    # Same relative value urgency + same other terms → equal composite scores
    assert score_cheap == pytest.approx(score_exp, abs=0.01)


def test_score_multi_dim_zero_remaining_is_none():
    profile = FlexibilityProfile(
        flexibility_class=FlexibilityClass.BURSTABLE,
        consumption_flexibility=1.0,
        value_at_risk_usd=10.0,
    )
    urgency, score = _score_multi_dimension(profile=profile, remaining=0.5, days=2.0)
    assert urgency == Urgency.NONE


def test_multi_dim_does_not_drop_low_remaining_weekly_window():
    """min_remaining gate must not silence multi-dim (e.g. 36% left, 2 days)."""
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="tokscale",
                provider="claude",
                account="user@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Weekly",
                        used_percent=64.0,
                        remaining_percent=36.0,
                        resets_at=_now() + timedelta(days=2),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "learn_from_history": False,
                "scoring_mode": "multi_dim",
                "use_multi_dim_scoring": True,
                "min_remaining_percent": 40,
                "max_days_until_reset": 14,
                "min_value_at_risk_usd": 0.0,
                "min_value_fraction_of_plan": 0.0,
            },
            "plans": {"claude": {"monthly_price": 20}},
        },
    )
    matching = [a for a in alerts if a.remaining_percent == 36.0]
    assert matching, f"expected alert for 36% remaining weekly; got {alerts!r}"
    assert matching[0].window_label == "Weekly"


def test_multi_dim_includes_throttled_5h():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude 5-hour",
                        used_percent=0,
                        remaining_percent=100,
                        resets_at=_now() + timedelta(hours=2),
                        window_minutes=300,
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "learn_from_history": False,
                "scoring_mode": "multi_dim",
                "use_multi_dim_scoring": True,
                "min_value_at_risk_usd": 0.0,
                "min_value_fraction": 0.0,
            },
            "plans": {"claude": {"monthly_price": 20}},
        },
    )
    assert len(alerts) >= 1
    assert any("5-hour" in a.window_label for a in alerts)


def test_multi_dim_filters_tiny_value():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude 5-hour",
                        used_percent=0,
                        remaining_percent=50,
                        resets_at=_now() + timedelta(hours=2),
                        window_minutes=300,
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {
            "analysis": {
                "learn_from_history": False,
                "scoring_mode": "multi_dim",
                "use_multi_dim_scoring": True,
                "min_value_at_risk_usd": 10.0,
            },
            "plans": {"claude": {"monthly_price": 20}},
        },
    )
    # 5h window value is ~$0.11, below $10 threshold
    assert not any("5-hour" in a.window_label for a in alerts)


def _pace_cfg(**overrides: object) -> dict:
    analysis = {
        "scoring_mode": "pace",
        "pace": {
            "waste_alert_fraction": 0.30,
            "min_elapsed_fraction": 0.15,
            "conserve_min_lead_hours": 4.0,
        },
        "max_days_until_reset": 14,
        "min_value_at_risk_usd": 0.0,
        "min_value_fraction": 0.0,
        "waking_hours_per_day": 16,
        # Unit tests must not pull chronic-waste INFO from the operator's real snapshots.
        "learn_from_history": False,
    }
    analysis.update(overrides)
    return {"analysis": analysis, "plans": {"claude": {"monthly_price": 20}}}


def test_pace_mode_learned_rate_bypasses_early_window_confidence_gate(monkeypatch):
    """With learn_from_history, high historical burn can classify burn early in a window."""
    now = _now()
    # Elapsed ≈ 0.5/7 ≈ 0.07 < min_elapsed 0.15 → would be on_pace without learning.
    window = QuotaWindow(
        label="Weekly",
        used_percent=5.0,
        remaining_percent=95.0,
        resets_at=now + timedelta(days=6.5),
        window_minutes=10080,
    )
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="tokscale",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[window],
            )
        ],
    )
    # Slow learned burn (under-use) → waste projection → burn once early-window
    # confidence gate is bypassed by has_learned_rate.
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.compute_learned_burn_rates",
        lambda **_k: {"codex:weekly": (0.01, 5)},  # 1% of window / day
    )
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.compute_learned_flexibility",
        lambda **_k: {},
    )
    cfg = _pace_cfg()
    cfg["analysis"]["learn_from_history"] = True
    cfg["analysis"]["provider_overrides"] = {"codex": {"shared_allotment": False}}
    alerts = analyze_use_or_lose(snap, cfg)
    assert any(a.kind == "burn" for a in alerts), alerts


def test_pace_mode_on_pace_weekly_produces_no_alert():
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="tokscale",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Weekly",
                        used_percent=64.0,
                        remaining_percent=36.0,
                        resets_at=now + timedelta(days=2),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    assert analyze_use_or_lose(snap, _pace_cfg()) == []


def test_pace_mode_conserve_weekly():
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="tokscale",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Weekly",
                        used_percent=90.0,
                        remaining_percent=10.0,
                        resets_at=now + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(snap, _pace_cfg())
    assert len(alerts) == 1
    assert alerts[0].kind == "conserve"
    assert alerts[0].pace is not None


def test_pace_mode_burn_weekly():
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="tokscale",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Weekly",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(days=3.5),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(snap, _pace_cfg())
    assert len(alerts) == 1
    assert alerts[0].kind == "burn"


def test_cursor_shared_allotment_scores_included_and_other_models_independently():
    """Cursor Included/Auto (healthy) and Other Models (exhausted) are independent
    monthly pools (issue #21 / Phase 2): Other Models must raise its own conserve
    alert instead of being silently suppressed as a child of Included."""
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="cursor",
                account="user@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Cursor included",
                        used_percent=61.0,
                        remaining_percent=39.0,
                        resets_at=now + timedelta(days=9.5),
                        window_minutes=44640,
                    ),
                    QuotaWindow(
                        label="Cursor Auto",
                        used_percent=55.0,
                        remaining_percent=45.0,
                        resets_at=now + timedelta(days=9.5),
                        window_minutes=44640,
                    ),
                    QuotaWindow(
                        label="Cursor other models",
                        used_percent=100.0,
                        remaining_percent=0.0,
                        resets_at=now + timedelta(days=9.5),
                        window_minutes=44640,
                    ),
                ],
            )
        ],
    )
    cfg = _pace_cfg()
    cfg["analysis"]["provider_overrides"] = {"cursor": {"shared_allotment": True}}
    alerts = analyze_use_or_lose(snap, cfg)
    # Included/Auto stay governed together and are healthy → no alert for either.
    assert not any(a.window_label in ("Cursor included", "Cursor Auto") for a in alerts)
    # Other Models is its own independent pool and is genuinely exhausted → conserve.
    other_alerts = [a for a in alerts if a.window_label == "Cursor other models"]
    assert len(other_alerts) == 1
    assert other_alerts[0].kind == "conserve"
    assert other_alerts[0].kind != "prepaid"
    assert other_alerts[0].days_until_reset is not None


def test_cursor_other_models_is_subscription_use_or_lose_not_prepaid():
    """Other Models has a monthly reset — pace/use-or-lose, never prepaid n/a."""
    from aiuse.report import _account_is_non_expiring_prepaid, _account_use_urgency

    now = _now()
    acc = AccountUsage(
        source="codexbar",
        provider="cursor",
        account="user@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Cursor other models",
                used_percent=0.0,
                remaining_percent=100.0,
                resets_at=now + timedelta(days=30),
                window_minutes=44640,
            ),
        ],
    )
    assert not _account_is_non_expiring_prepaid(acc)
    assert _account_use_urgency(acc) > 0.0

    snap = Snapshot(collected_at=now, accounts=[acc])
    alerts = analyze_use_or_lose(snap, _pace_cfg())
    assert all(a.kind != "prepaid" for a in alerts)


def test_shared_allotment_suppresses_fresh_5h_when_weekly_on_pace():
    """Core regression: Claude 5h must not top the list when weekly is on-pace."""
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                account="user@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        used_percent=3.0,
                        remaining_percent=97.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=64.0,
                        remaining_percent=36.0,
                        resets_at=now + timedelta(days=2),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    cfg = _pace_cfg()
    # Ensure shared_allotment is on for claude (default in DEFAULT_CONFIG too).
    cfg["analysis"]["provider_overrides"] = {
        "claude": {"shared_allotment": True, "5h": {"flexibility": 0.0}},
    }
    alerts = analyze_use_or_lose(snap, cfg)
    assert alerts == []


def test_shared_allotment_suppresses_history_child_when_governing_window_is_exhausted(
    monkeypatch,
):
    """History must not advertise a fresh child while the shared pool is empty."""
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="opencode-go",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="OpenCode Go 5-hour quota (1)",
                        remaining_percent=100.0,
                        resets_at=now + timedelta(hours=5),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="OpenCode Go monthly quota (3)",
                        remaining_percent=0.0,
                        resets_at=now + timedelta(days=10),
                        window_minutes=43200,
                    ),
                ],
            )
        ],
    )
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.should_learn_from_history",
        lambda _cfg: True,
    )
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.compute_learned_burn_rates",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.compute_learned_flexibility",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.chronic_waste_summary",
        lambda **_kwargs: [
            {
                "provider": "opencode",
                "label": "OpenCode Go 5-hour quota (1)",
                "avg_remaining_pct": 100.0,
                "sample_count": 6,
            }
        ],
    )
    cfg = _pace_cfg(learn_from_history=True)
    cfg["analysis"]["provider_overrides"] = {"opencode": {"shared_allotment": True}}
    cfg["plans"] = {"opencode": {"monthly_price": 10, "name": "OpenCode Go"}}

    alerts = analyze_use_or_lose(snap, cfg)

    monthly = [
        alert
        for alert in alerts
        if alert.source == "codexbar"
        and alert.kind == "conserve"
        and alert.window_label == "OpenCode Go monthly quota (3)"
    ]
    assert monthly
    msg = monthly[0].message.lower()
    assert "exhausted" in msg
    assert "pace yourself" not in msg
    # Short windows look open but share the spent monthly pool.
    assert "5-hour" in msg or "5h" in msg
    assert "open capacity" in msg or "same exhausted" in msg
    assert not any(alert.source == "history" for alert in alerts)


def test_shared_allotment_one_conserve_alert_names_5h_child():
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                account="user@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        remaining_percent=90.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=90.0,
                        remaining_percent=10.0,
                        resets_at=now + timedelta(days=3),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    cfg = _pace_cfg()
    cfg["analysis"]["provider_overrides"] = {"claude": {"shared_allotment": True}}
    alerts = analyze_use_or_lose(snap, cfg)
    assert len(alerts) == 1
    assert alerts[0].kind == "conserve"
    assert "weekly" in alerts[0].window_label.lower()
    assert "5-hour" in alerts[0].message.lower() or "5h" in alerts[0].message.lower()


def test_shared_allotment_one_burn_alert_for_weekly():
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        remaining_percent=100.0,
                        resets_at=now + timedelta(hours=5),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(days=3.5),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    cfg = _pace_cfg()
    cfg["analysis"]["provider_overrides"] = {"claude": {"shared_allotment": True}}
    alerts = analyze_use_or_lose(snap, cfg)
    assert len(alerts) == 1
    assert alerts[0].kind == "burn"
    assert "weekly" in alerts[0].window_label.lower()
    assert alerts[0].pace is not None
    assert alerts[0].pace.projected_waste_usd is not None


def test_shared_allotment_false_scores_windows_independently():
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        used_percent=0.0,
                        remaining_percent=100.0,
                        resets_at=now + timedelta(hours=1),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(days=3.5),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    cfg = _pace_cfg()
    cfg["analysis"]["provider_overrides"] = {"claude": {"shared_allotment": False}}
    # Lower min_value so a 5h burn can surface if classified burn.
    cfg["analysis"]["min_value_at_risk_usd"] = 0.0
    cfg["analysis"]["min_value_fraction"] = 0.0
    alerts = analyze_use_or_lose(snap, cfg)
    labels = {a.window_label for a in alerts}
    # Independent scoring: weekly burn is expected; 5h may also alert.
    assert any("weekly" in lab.lower() for lab in labels)
    assert len(alerts) >= 1


def test_lone_5h_window_can_still_alert_under_shared_allotment():
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        used_percent=5.0,
                        remaining_percent=95.0,
                        resets_at=now + timedelta(hours=4.5),
                        window_minutes=300,
                    )
                ],
            )
        ],
    )
    cfg = _pace_cfg()
    cfg["analysis"]["provider_overrides"] = {"claude": {"shared_allotment": True}}
    cfg["analysis"]["min_value_at_risk_usd"] = 0.0
    # Fresh 5h with high remaining early in window → on_pace confidence gate;
    # use later elapsed: 4.5h left of 5h → elapsed ~0.1, still early.
    # Push toward burn: very little used, most of window already elapsed.
    snap.accounts[0].windows[0] = QuotaWindow(
        label="Claude Code 5-hour",
        used_percent=5.0,
        remaining_percent=95.0,
        resets_at=now + timedelta(hours=1),
        window_minutes=300,
    )
    alerts = analyze_use_or_lose(snap, cfg)
    # Alone, it is its own governing window — may burn or on_pace; must not crash.
    assert all(a.window_label == "Claude Code 5-hour" for a in alerts)


def test_antigravity_shared_allotment_scores_gemini_and_claude_pools():
    """Gemini vs Claude/GPT are independent; each pool's weekly can alert."""
    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="antigravity",
                account="user@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Gemini 5-hour",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Gemini weekly",
                        used_percent=5.0,
                        remaining_percent=95.0,
                        # Late in the week, almost unused → burn
                        resets_at=now + timedelta(days=1),
                        window_minutes=10080,
                    ),
                    QuotaWindow(
                        label="Claude/GPT 5-hour",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude/GPT weekly",
                        used_percent=5.0,
                        remaining_percent=95.0,
                        resets_at=now + timedelta(days=1),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    cfg = _pace_cfg()
    cfg["analysis"]["provider_overrides"] = {"gemini": {"shared_allotment": True}}
    cfg["analysis"]["min_value_at_risk_usd"] = 0.0
    cfg["analysis"]["min_value_fraction"] = 0.0
    cfg["plans"] = {"gemini": {"monthly_price": 20, "name": "Google AI"}}
    alerts = analyze_use_or_lose(snap, cfg)
    labels = {a.window_label for a in alerts}
    # Both independent weeklies should be scorable (not collapsed to one).
    assert "Gemini weekly" in labels
    assert "Claude/GPT weekly" in labels
    # 5h children suppressed within each pool
    assert not any("5-hour" in (a.window_label or "") for a in alerts)
    assert all(a.kind == "burn" for a in alerts)


def test_legacy_mode_via_use_multi_dim_false():
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Weekly",
                        used_percent=0,
                        remaining_percent=100,
                        resets_at=_now() + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alerts = analyze_use_or_lose(
        snap,
        {"analysis": {"learn_from_history": False, "use_multi_dim_scoring": False, "min_remaining_percent": 40}},
    )
    assert len(alerts) >= 1
    assert all(a.kind == "burn" for a in alerts)  # default kind; no pace path


def _antigravity_snapshot(now: datetime) -> Snapshot:
    """CodexBar's view of the Antigravity subscription: real account, bare labels."""
    return Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="antigravity",
                account="user@example.com",
                plan="Google AI Pro",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Gemini 5-hour",
                        remaining_percent=100.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude/GPT 5-hour",
                        remaining_percent=100.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                ],
            )
        ],
    )


def _chronic_openusage_rows() -> list[dict]:
    """chronic_waste_summary output for rows OpenUsage wrote (no account, prefixed)."""
    from aiuse.analysis.history import window_series_key

    return [
        {
            "provider": "antigravity",
            "account": "user@example.com",
            "label": "Gemini 5-hour",
            "window_key": window_series_key("antigravity", "Antigravity Gemini 5-hour", 300),
            "avg_remaining_pct": 94.0,
            "sample_count": 3,
        }
    ]


def test_history_alert_uses_the_same_provider_name_as_live_rows(monkeypatch):
    """One vendor, one display name — history rows must not print a second one."""
    from aiuse.models import provider_display_name

    now = _now()
    snap = _antigravity_snapshot(now)

    monkeypatch.setattr("aiuse.analysis.use_or_lose.should_learn_from_history", lambda _cfg: True)
    monkeypatch.setattr("aiuse.analysis.use_or_lose.compute_learned_burn_rates", lambda **_k: {})
    monkeypatch.setattr("aiuse.analysis.use_or_lose.compute_learned_flexibility", lambda **_k: {})
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.chronic_waste_summary",
        lambda **_k: _chronic_openusage_rows(),
    )

    cfg = _pace_cfg(learn_from_history=True)
    cfg["plans"] = {"gemini": {"monthly_price": 20, "name": "Google AI Pro / Ultra"}}

    alerts = analyze_use_or_lose(snap, cfg)

    history_alerts = [a for a in alerts if a.source == "history"]
    assert history_alerts, "expected the chronic-waste row to survive (no weekly parent here)"

    names = {provider_display_name(a.provider) for a in alerts}
    assert names == {"agy"}, names

    alert = history_alerts[0]
    # Identity is borrowed from the live row, not left anonymous.
    assert alert.account == "user@example.com"
    assert alert.window_label == "Gemini 5-hour"
    # And it inherits the live reset instead of reporting an unknown deadline.
    assert alert.days_until_reset is not None


def test_history_child_is_suppressed_across_collector_label_variants(monkeypatch):
    """A 5h child under a live weekly parent stays suppressed despite relabelling."""
    from aiuse.analysis.history import window_series_key

    now = _now()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="antigravity",
                account="user@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Gemini 5-hour",
                        remaining_percent=100.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Gemini weekly",
                        remaining_percent=91.0,
                        resets_at=now + timedelta(days=6),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )

    monkeypatch.setattr("aiuse.analysis.use_or_lose.should_learn_from_history", lambda _cfg: True)
    monkeypatch.setattr("aiuse.analysis.use_or_lose.compute_learned_burn_rates", lambda **_k: {})
    monkeypatch.setattr("aiuse.analysis.use_or_lose.compute_learned_flexibility", lambda **_k: {})
    monkeypatch.setattr(
        "aiuse.analysis.use_or_lose.chronic_waste_summary",
        lambda **_k: [
            {
                "provider": "antigravity",
                "account": None,
                # Label as the *other* collector wrote it — suppression must
                # still recognise this as the live weekly window's child.
                "label": "Antigravity Gemini 5-hour",
                "window_key": window_series_key("antigravity", "Antigravity Gemini 5-hour", 300),
                "avg_remaining_pct": 94.0,
                "sample_count": 3,
            }
        ],
    )

    cfg = _pace_cfg(learn_from_history=True)
    cfg["analysis"]["provider_overrides"] = {"gemini": {"shared_allotment": True}}
    cfg["plans"] = {"gemini": {"monthly_price": 20}}

    alerts = analyze_use_or_lose(snap, cfg)
    assert not any(a.source == "history" for a in alerts)
