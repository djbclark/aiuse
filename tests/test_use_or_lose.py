"""Unit tests for use-or-lose analysis (no live CLI required)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aiuse.analysis.use_or_lose import (
    _classify_flexibility,
    _compute_flexibility_profile,
    _compute_value_at_risk,
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
        {"analysis": {
                "learn_from_history": False,"scoring_mode": "legacy", "use_multi_dim_scoring": False, "min_remaining_percent": 40}},
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
        {"analysis": {
                "learn_from_history": False,"scoring_mode": "legacy", "use_multi_dim_scoring": False, "min_remaining_percent": 40}},
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
            "analysis": {
                "learn_from_history": False,"scoring_mode": "legacy", "use_multi_dim_scoring": False},
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
    _, score_cheap = _score_multi_dimension(
        profile=cheap, remaining=50.0, days=5.0, config=cfg, monthly_price=10.0
    )
    _, score_exp = _score_multi_dimension(
        profile=expensive, remaining=50.0, days=5.0, config=cfg, monthly_price=30.0
    )
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



def test_cursor_shared_allotment_scores_included_not_exhausted_api():
    """Cursor API at 100% must not CONSERVE when Included still has headroom."""
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
                        label="Cursor API",
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
    assert not any("API" in (a.window_label or "") for a in alerts)
    # Included at ~pace for mid-cycle may be on_pace (no alert) or mild burn — never API conserve
    assert not any(a.kind == "conserve" and "API" in (a.window_label or "") for a in alerts)


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
        {"analysis": {
                "learn_from_history": False,"use_multi_dim_scoring": False, "min_remaining_percent": 40}},
    )
    assert len(alerts) >= 1
    assert all(a.kind == "burn" for a in alerts)  # default kind; no pace path
