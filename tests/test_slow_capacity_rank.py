"""Slow-band ranking is capacity-weighted: dollars at risk in the window (aiuse-faf, aiuse-b31)."""

from datetime import timedelta

import pytest

from aiuse.analysis.ranking import _alert_queue_priority, _Pricing
from aiuse.config import DEFAULT_CONFIG, load_config, plan_monthly_price, validate_config
from aiuse.models import PaceProfile, Urgency, UseOrLoseAlert, provider_display_name, utcnow
from aiuse.report import _build_matrix_rows, _queue_score

# A weekly window is this share of the 16-waking-hours-a-day month its price is spread over.
WEEKLY_SHARE = 10080 / (16 * 30.44 * 60)


def _capacity(alert, plans=None):
    """The capacity (dollars at risk) the board orders ``alert`` by."""
    config = None if plans is None else {"plans": plans}
    return _alert_queue_priority(alert, pricing=_Pricing.from_config(config))[0]


def _slow(provider, account, remaining, days, *, lockout_in_days=None, label="weekly quota"):
    """A conserve alert; ``lockout_in_days`` sets projected exhaustion for readiness."""
    pace = None
    if lockout_in_days is not None:
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=1.0 - remaining / 100.0,
            pace_ratio=1.5,
            projected_used_fraction=1.0,
            projected_waste_fraction=0.0,
            projected_waste_usd=None,
            projected_exhaust_at=utcnow() + timedelta(days=lockout_in_days),
            governing=True,
        )
    return UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider=provider,
        account=account,
        window_label=label,
        remaining_percent=remaining,
        days_until_reset=days,
        plan=None,
        message="conserve",
        source="test",
        score=70.0,
        kind="conserve",
        pace=pace,
    )


def test_claude_200_plan_outranks_gemini_20_plan_in_slow_band():
    gemini = _slow("antigravity", None, 1.2, 2.0, label="Gemini weekly quota")
    claude = _slow("claude", "djbclark@gmail.com", 5.0, 2.7, label="Claude Code weekly")
    config = {"plans": {"gemini": {"monthly_price": 20}, "claude": {"monthly_price": 200}}}
    rows = _build_matrix_rows([gemini, claude], None, config)
    by = {row.service: row for row in rows}
    gem_row = by[provider_display_name("antigravity")]
    claude_row = by[provider_display_name("claude")]
    assert claude_row.band == gem_row.band == 3
    assert claude_row.queue_score > gem_row.queue_score
    # Rows sort ascending: the more attractive (higher score) row sits lower.
    assert rows.index(claude_row) > rows.index(gem_row)
    assert 25 <= gem_row.queue_score <= 49 and 25 <= claude_row.queue_score <= 49


def test_slow_score_is_monotonic_in_capacity_and_stays_in_band():
    # Dollars at risk, not remaining% x price: a cent or less floors the band, $100 or more tops it.
    caps = [0.001, 0.01, 0.05, 0.3, 1.0, 3.4, 10.0, 100.0, 500.0]
    scores = [_queue_score(3, (cap, 0.0, 0.0)) for cap in caps]
    assert scores == sorted(scores)
    assert min(scores) == 25 and max(scores) == 49


def test_per_account_override_beats_provider_price():
    plans = {"claude": {"monthly_price": 20, "big@x.com": {"monthly_price": 200}}}
    assert plan_monthly_price(plans, "claude", "big@x.com") == 200.0
    assert plan_monthly_price(plans, "claude", "BIG@x.com") == 200.0  # case-insensitive
    assert plan_monthly_price(plans, "claude", "small@x.com") == 20.0
    assert plan_monthly_price(plans, "claude", None) == 20.0

    big = _slow("claude", "big@x.com", 5.0, 3.0)
    small = _slow("claude", "small@x.com", 5.0, 3.0)
    assert _capacity(big, plans) == pytest.approx(200 * WEEKLY_SHARE * 0.05)
    assert _capacity(small, plans) == pytest.approx(20 * WEEKLY_SHARE * 0.05)
    rows = _build_matrix_rows([big, small], None, {"plans": plans})
    assert [r.account_full for r in rows] == ["small@x.com", "big@x.com"]
    assert rows[1].queue_score > rows[0].queue_score


def test_account_sub_table_does_not_shadow_value_multiplier_or_provider_price():
    plans = {"claude": {"monthly_price": 20, "value_multiplier": {"5h": 1.4}}}
    assert plan_monthly_price(plans, "claude", "5h") == 20.0
    assert plan_monthly_price({"claude": {"x@y": {"monthly_price": 0}}}, "claude", "x@y") is None


def test_provider_alias_resolves_plan_key():
    plans = {"gemini": {"monthly_price": 30}, "opencode": {"monthly_price": 10}}
    assert plan_monthly_price(plans, "antigravity") == 30.0
    assert plan_monthly_price(plans, "opencode-go") == 10.0


def test_unpriced_provider_falls_back_to_cheapest_configured_plan():
    plans = {"claude": {"monthly_price": 20}, "copilot": {"monthly_price": 10}, "devin": {"name": "Devin"}}
    devin = _slow("devin", None, 5.0, 3.0)
    assert _capacity(devin, plans) == pytest.approx(10 * WEEKLY_SHARE * 0.05)
    # No priced plan at all: the built-in constant.
    assert _capacity(devin, {}) == pytest.approx(10 * WEEKLY_SHARE * 0.05)
    # Never outranks a known plan with the same remaining%.
    claude = _slow("claude", "a@x", 5.0, 3.0)
    assert _capacity(devin, plans) < _capacity(claude, plans)


def test_no_config_uses_builtin_plan_prices():
    alert = _slow("claude", "a@x", 10.0, 3.0)
    assert _capacity(alert) == pytest.approx(DEFAULT_CONFIG["plans"]["claude"]["monthly_price"] * WEEKLY_SHARE * 0.10)


def test_equal_capacity_tiebreaks_on_readiness():
    plans = {"claude": {"monthly_price": 20}}
    pricing = _Pricing.from_config({"plans": plans})
    # Same provider, same remaining% => equal capacity.  Dangerous lockout (exhausts
    # in 0.1d with 6d to reset) sorts above the one that barely locks out.
    danger = _slow("claude", "danger@x", 5.0, 6.0, lockout_in_days=0.1)
    safe = _slow("claude", "safe@x", 5.0, 6.0, lockout_in_days=5.9)
    assert _alert_queue_priority(danger, pricing=pricing)[0] == _alert_queue_priority(safe, pricing=pricing)[0]
    assert _alert_queue_priority(danger, pricing=pricing) < _alert_queue_priority(safe, pricing=pricing)
    rows = _build_matrix_rows([safe, danger], None, {"plans": plans})
    assert [r.account_full for r in rows] == ["danger@x", "safe@x"]
    assert rows[0].queue_score == rows[1].queue_score


def test_empty_band_threshold_unchanged_and_slow_never_below_band():
    near_empty = _slow("antigravity", None, 1.2, 2.0)
    empty = _slow("antigravity", None, 1.0, 2.0)
    rows = _build_matrix_rows([near_empty], None, {"plans": {"gemini": {"monthly_price": 20}}})
    assert rows[0].band == 3
    rows = _build_matrix_rows([empty], None, {"plans": {"gemini": {"monthly_price": 20}}})
    assert rows[0].band == 1 and rows[0].queue_score == 0


def test_toml_per_account_plan_loads_merges_and_validates(monkeypatch, tmp_path):
    cfg_dir = tmp_path / "aiuse"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        '[plans.claude]\nmonthly_price = 100\n\n[plans.claude."me@x.com"]\nmonthly_price = 200\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = load_config()
    # Defaults survive the merge (value_multiplier, name) next to the sub-table.
    assert cfg["plans"]["claude"]["value_multiplier"] == {"5h": 1.4}
    assert plan_monthly_price(cfg["plans"], "claude", "me@x.com") == 200.0
    assert plan_monthly_price(cfg["plans"], "claude", "other@x.com") == 100.0
    assert not [i for i in validate_config(cfg) if "plans" in i]


def test_validate_config_flags_bad_plan_prices():
    issues = validate_config({"plans": {"claude": {"monthly_price": "lots", "me@x": {"monthly_price": -1, "name": 1}}}})
    joined = "\n".join(issues)
    assert "plans.claude.monthly_price" in joined
    assert "'me@x'.monthly_price" in joined
    assert "unknown plans.claude.'me@x' key 'name'" in joined
