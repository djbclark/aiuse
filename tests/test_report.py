from datetime import datetime, timedelta, timezone

import pytest

from aiuse.models import (
    AccountUsage,
    BillingKind,
    CrossCheck,
    FlexibilityClass,
    FlexibilityProfile,
    PaceProfile,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.report import (
    _BAND_TAG,
    ACTION_PLAN_MAX_LINES,
    ACTION_PLAN_WIDTH,
    TABLE_MAX_WIDTH,
    _action_plan_line,
    _advance_matrix_layout,
    _build_matrix_rows,
    _format_reset_span,
    _human_deadline,
    _MatrixLayout,
    _physical_line_count,
    _render_brief_action_plan,
    _sorted_accounts,
    _strip_ansi,
    _Style,
    _throttled_waste_line,
    render_clock_matrix,
    render_priority_ladder,
    render_report,
    render_status_line,
)

# Every band tag that can start a table row, e.g. {"error", "empty", "mid", ...}.
_BAND_TAGS = {tag.strip() for tag, _color in _BAND_TAG.values()}


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0.5, "within ~12h"),
        (1.0, "within 1.0 days"),
        (1.01, "within 1.0 days"),
        (1.38, "within 1.4 days"),
        (2.01, "within 2.0 days"),
    ],
)
def test_human_deadline_shows_remaining_days_to_one_decimal(days: float, expected: str):
    assert _human_deadline(days) == expected


def test_human_deadline_marks_date_only_reset_as_an_estimate():
    assert _human_deadline(1.38, estimated=True) == "within ~1 day"


def test_ladder_includes_waste_forecast_fragment():

    from aiuse.report import render_priority_ladder

    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="claude",
        account="a@x.com",
        window_label="Claude Code weekly",
        remaining_percent=80.0,
        days_until_reset=2.0,
        plan=None,
        message="burn",
        source="cswap",
        score=70.0,
        kind="burn",
        pace=PaceProfile(
            elapsed_fraction=0.7,
            used_fraction=0.2,
            pace_ratio=0.3,
            projected_used_fraction=0.3,
            projected_waste_fraction=0.70,
            projected_waste_usd=None,
            projected_exhaust_at=None,
        ),
    )
    text = render_priority_ladder([alert], color=False)
    assert "waste" in text
    assert "70" in text


@pytest.mark.parametrize(
    ("remaining", "expected_band"),
    [
        (0.0, "empty"),
        (0.01, "mid"),
        (1.0, "mid"),
        (50.0, "mid"),
        (100.0, "mid"),
    ],
)
def test_ladder_classifies_every_unalerted_remaining_capacity_band(remaining: float, expected_band: str):
    """Unalerted subscription windows are empty only when capacity is gone."""
    snapshot = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="grok",
                account="djbclark@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Grok usage limit",
                        used_percent=100.0 - remaining,
                        remaining_percent=remaining,
                        resets_at=utcnow() + timedelta(days=1),
                    )
                ],
            )
        ],
    )

    text = render_priority_ladder([], snapshot=snapshot, color=False)

    assert text.split()[0] == expected_band
    assert "grok · djbclark@gmail.com · Grok usage limit:" in text
    expected_remaining = "<1%" if 0.0 < remaining < 1.0 else f"{remaining:.0f}%"
    assert f"{expected_remaining} left" in text
    if remaining <= 0.0:
        # Depleted rows name the reset, not "ok" / "pace".
        assert "· resets within" in text
        assert "· ok within" not in text
    else:
        assert "· ok within" in text


def test_ladder_keeps_opencode_zen_separate_from_go_quota_alert():
    snapshot = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="opencode-go",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[QuotaWindow(label="OpenCode Go monthly quota", used_percent=100, remaining_percent=0)],
            ),
            AccountUsage(
                source="codexbar",
                provider="opencode-zen",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=-0.04,
            ),
        ],
    )
    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="opencode-go",
        account=None,
        window_label="OpenCode Go monthly quota",
        remaining_percent=0,
        days_until_reset=1,
        plan="OpenCode Go",
        message="conserve",
        source="codexbar",
        score=100,
        kind="conserve",
    )

    text = render_priority_ladder([alert], snapshot=snapshot, color=False)

    lines = text.splitlines()
    go_line = next(line for line in lines if "oc-go" in line)
    assert go_line.startswith("empty")
    assert "0% left" in go_line
    assert "resets" in go_line
    # Empty tag must not also claim pace / upcoming lockout.
    assert " pace " not in go_line
    assert "~lockout" not in go_line
    zen_line = next(line for line in lines if "oc-zen" in line)
    assert zen_line.startswith("empty")
    assert "balance $-0.04" in zen_line
    assert "no expiry" in zen_line


def test_ladder_empty_conserve_skips_pace_and_lockout_forecast():
    """Fully spent conserve alerts are empty, not 'pace yourself' copy."""
    from aiuse.report import render_priority_ladder

    exhaust = utcnow()  # already exhausted — projected_exhaust_at is "now"
    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="opencode-go",
        account=None,
        window_label="OpenCode Go monthly quota (3)",
        remaining_percent=0.0,
        days_until_reset=10.0,
        plan="OpenCode Go",
        message="exhausted",
        source="codexbar",
        score=100.0,
        kind="conserve",
        pace=PaceProfile(
            elapsed_fraction=0.67,
            used_fraction=1.0,
            pace_ratio=1.5,
            projected_used_fraction=1.0,
            projected_waste_fraction=0.0,
            projected_waste_usd=None,
            projected_exhaust_at=exhaust,
            governing=True,
        ),
    )
    text = render_priority_ladder([alert], color=False, width=120)
    assert text.startswith("empty")
    assert "0% left" in text
    assert "resets within" in text
    assert "pace" not in text
    assert "~lockout" not in text


def test_ladder_includes_lockout_forecast_for_conserve():
    from aiuse.report import render_priority_ladder

    exhaust = utcnow() + timedelta(hours=6)
    alert = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="claude",
        account="a@x.com",
        window_label="Claude Code 5-hour",
        remaining_percent=5.0,
        days_until_reset=0.2,
        plan=None,
        message="conserve",
        source="cswap",
        score=70.0,
        kind="conserve",
        pace=PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.95,
            pace_ratio=1.9,
            projected_used_fraction=1.0,
            projected_waste_fraction=0.0,
            projected_waste_usd=None,
            projected_exhaust_at=exhaust,
        ),
    )
    text = render_priority_ladder([alert], color=False)
    assert "~lockout" in text


def test_ladder_history_alert_without_live_reset_uses_cycle_guidance():
    from aiuse.report import render_priority_ladder

    alert = UseOrLoseAlert(
        urgency=Urgency.INFO,
        provider="claude",
        account=None,
        window_label="Claude Code 5-hour",
        remaining_percent=90.0,
        days_until_reset=None,
        plan=None,
        message="consistently underused",
        source="history",
        score=4.0,
        kind="burn",
    )

    text = render_priority_ladder([alert], color=False)

    assert "use more each cycle" in text
    assert "time unknown" not in text


def test_status_line_nothing_urgent():
    snap = Snapshot(collected_at=utcnow(), accounts=[AccountUsage(provider="codex", source="codexbar")])
    line = render_status_line(snap, [])
    assert line.startswith("ok:")
    assert "nothing urgent" in line
    assert "\n" not in line


def test_status_line_top_burn():
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="claude",
            account="a@x.com",
            window_label="Claude Code weekly",
            remaining_percent=91.0,
            days_until_reset=2.0,
            plan=None,
            message="burn",
            source="cswap",
            score=80.0,
            kind="burn",
        ),
        UseOrLoseAlert(
            urgency=Urgency.MEDIUM,
            provider="codex",
            account=None,
            window_label="Codex weekly",
            remaining_percent=50.0,
            days_until_reset=3.0,
            plan=None,
            message="burn2",
            source="codexbar",
            score=40.0,
            kind="burn",
        ),
    ]
    snap = Snapshot(collected_at=utcnow(), accounts=[])
    line = render_status_line(snap, alerts)
    assert line.startswith("use:")
    assert "Claude Code weekly" in line
    assert "91%" in line
    assert "2 burns" in line


def test_per_provider_accounts_are_sorted_by_display_name():
    accounts = [
        AccountUsage(provider="antigravity", source="codexbar"),
        AccountUsage(provider="copilot", source="tokscale"),
        AccountUsage(provider="codex", source="codexbar"),
        AccountUsage(provider="claude", source="cswap", error="unavailable"),
    ]

    # Order follows the *display* name, not the canonical provider id, so
    # antigravity leads: its display name is "agy".
    assert [account.provider for account in _sorted_accounts(accounts)] == [
        "antigravity",
        "claude",
        "codex",
        "copilot",
    ]


def test_accounts_for_same_provider_are_sorted_by_account_then_source():
    accounts = [
        AccountUsage(provider="claude", account="z@example.com", source="cswap"),
        AccountUsage(provider="claude", account="A@example.com", source="cswap"),
    ]

    assert [account.account for account in _sorted_accounts(accounts)] == [
        "A@example.com",
        "z@example.com",
    ]


def _alert_with_value(*, window_minutes: int, value_usd: float) -> UseOrLoseAlert:
    return UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="claude",
        account="user@example.com",
        window_label="Claude 5-hour",
        remaining_percent=50.0,
        days_until_reset=0.1,
        plan=None,
        message="test",
        source="cswap",
        score=50.0,
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.THROTTLED,
            consumption_flexibility=0.0,
            value_at_risk_usd=value_usd,
        ),
        window_minutes=window_minutes,
    )


def test_throttled_waste_5h_uses_real_cycles_per_month():
    # 16h * 30.44 * 60 / 300 ≈ 97.408 cycles/month
    line = _throttled_waste_line(
        _alert_with_value(window_minutes=300, value_usd=0.18),
        _Style(False),
        waking_hours_per_day=16.0,
    )
    assert "~$0.18/cycle" in line
    assert "~$17.53/month" in line
    assert "$5.40/month" not in line  # old value_usd * 30


def test_throttled_waste_monthly_window_not_overstated():
    # 16h * 30.44 * 60 / 43800 ≈ 0.668 cycles/month
    line = _throttled_waste_line(
        _alert_with_value(window_minutes=43800, value_usd=0.18),
        _Style(False),
        waking_hours_per_day=16.0,
    )
    assert "~$0.18/cycle" in line
    assert "~$0.12/month" in line
    assert "$5.40/month" not in line


def test_render_report_shows_conserve_before_burn_buckets():
    now = utcnow()
    burn = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@example.com",
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=2.0,
        plan=None,
        message="burn me",
        source="codexbar",
        score=80.0,
        kind="burn",
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.BURSTABLE,
            consumption_flexibility=1.0,
            value_at_risk_usd=5.0,
        ),
        pace=PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.1,
            pace_ratio=0.2,
            projected_used_fraction=0.2,
            projected_waste_fraction=0.8,
            projected_waste_usd=4.0,
            projected_exhaust_at=None,
        ),
    )
    conserve = UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="claude",
        account="b@example.com",
        window_label="Claude Code weekly",
        remaining_percent=10.0,
        days_until_reset=3.0,
        plan=None,
        message="slow down",
        source="cswap",
        score=70.0,
        kind="conserve",
        pace=PaceProfile(
            elapsed_fraction=0.6,
            used_fraction=0.9,
            pace_ratio=1.5,
            projected_used_fraction=1.0,
            projected_waste_fraction=0.0,
            projected_waste_usd=0.0,
            projected_exhaust_at=now + timedelta(days=1),
        ),
    )
    snap = Snapshot(collected_at=now, accounts=[])
    text = render_report(snap, [burn, conserve], config={}, color=False, full=True)
    assert "CONSERVE" in text
    conserve_at = text.index("CONSERVE")
    # Burn buckets appear after conserve when present
    assert "THIS WEEK" in text or "use within" in text.lower() or "90%" in text
    if "THIS WEEK" in text:
        assert conserve_at < text.index("THIS WEEK")
    # Conserve alert not in a burn action-plan style "burn" numbered list only
    assert "slow down" in text
    assert "burn me" not in text or "90%" in text  # burn line may use remaining


def test_render_report_shows_usage_credits_section():
    from aiuse.models import BillingKind, UsageCredits

    now = utcnow()
    acc = AccountUsage(
        source="cswap",
        provider="claude",
        account="a@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        usage_credits=UsageCredits(
            used=50.0,
            limit=100.0,
            remaining=50.0,
            currency="USD",
            used_percent=50.0,
            resets_at=now + timedelta(days=5),
        ),
    )
    text = render_report(Snapshot(collected_at=now, accounts=[acc]), [], config={}, color=False, full=True)
    assert "usage credits" in text.lower()
    assert "50 of 100 USD" in text or "spent: 50" in text
    assert "remaining headroom" in text.lower()


def test_action_plan_line_includes_pace_fragment():
    alert = UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="codex",
        account=None,
        window_label="Weekly",
        remaining_percent=80.0,
        days_until_reset=2.0,
        plan=None,
        message="x",
        source="tokscale",
        score=50.0,
        kind="burn",
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.SEMI_THROTTLED,
            consumption_flexibility=0.5,
            value_at_risk_usd=3.0,
        ),
        pace=PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.2,
            pace_ratio=0.4,
            projected_used_fraction=0.4,
            projected_waste_fraction=0.6,
            projected_waste_usd=2.0,
            projected_exhaust_at=None,
        ),
    )
    line = _action_plan_line(alert, _Style(False))
    assert "pace 0.4x" in line
    assert "projected 60% unused" in line


def test_action_plan_line_notes_blended_history():
    alert = UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="codex",
        account=None,
        window_label="Weekly",
        remaining_percent=80.0,
        days_until_reset=2.0,
        plan=None,
        message="x",
        source="tokscale",
        score=50.0,
        kind="burn",
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.SEMI_THROTTLED,
            consumption_flexibility=0.5,
            value_at_risk_usd=3.0,
        ),
        pace=PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.2,
            pace_ratio=0.4,
            projected_used_fraction=0.4,
            projected_waste_fraction=0.6,
            projected_waste_usd=2.0,
            projected_exhaust_at=None,
            learned_sample_count=5,
        ),
    )
    line = _action_plan_line(alert, _Style(False))
    assert "blended with history (5 samples)" in line


def test_render_report_full_includes_history_section():
    snap = Snapshot(collected_at=datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc), accounts=[])
    text = render_report(
        snap,
        [],
        config={"analysis": {"learn_from_history": False}},
        color=False,
        full=True,
    )
    assert "## History" in text
    assert "learning off" in text
    assert "Learning disabled" in text
    now = utcnow()
    burn = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@example.com",
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=2.0,
        plan=None,
        message="burn me",
        source="codexbar",
        score=80.0,
        kind="burn",
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.BURSTABLE,
            consumption_flexibility=1.0,
            value_at_risk_usd=5.0,
        ),
    )
    acc = AccountUsage(provider="codex", source="codexbar", account="a@example.com")
    text = render_report(
        Snapshot(collected_at=now, accounts=[acc]),
        [burn],
        config={},
        color=False,
        full=True,
    )
    assert text.index("## Per-provider usage") < text.index("## Cross-checks")
    assert text.index("## Cross-checks") < text.index("## Tips")
    assert text.index("## Tips") < text.index("## Action plan")
    # Action plan is the last section heading
    last_heading = max(
        text.rfind("## Per-provider"),
        text.rfind("## Cross-checks"),
        text.rfind("## Tips"),
        text.rfind("## Action plan"),
        text.rfind("## Collector"),
    )
    assert last_heading == text.rfind("## Action plan")
    assert "1 alert" in text or "alerts" in text


def test_action_plan_section_fits_viewport_when_short():
    now = utcnow()
    burn = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@example.com",
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=2.0,
        plan=None,
        message="burn me",
        source="codexbar",
        score=80.0,
        kind="burn",
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.BURSTABLE,
            consumption_flexibility=1.0,
            value_at_risk_usd=5.0,
        ),
    )
    text = render_report(
        Snapshot(collected_at=now, accounts=[]),
        [burn],
        config={},
        color=False,
        full=True,
    )
    # Only one action-plan heading when it fits
    assert text.count("## Action plan") == 1
    assert "at a glance" not in text
    plan_start = text.index("## Action plan")
    plan_lines = text[plan_start:].splitlines()
    assert len(plan_lines) <= ACTION_PLAN_MAX_LINES
    assert all(len(line) <= ACTION_PLAN_WIDTH + 5 for line in plan_lines)  # small slack


def test_long_action_plan_appends_brief_at_end():
    """Many alerts → detailed plan + trailing at-a-glance brief ≤ 23 lines."""
    now = utcnow()
    alerts: list[UseOrLoseAlert] = []
    for i in range(12):
        alerts.append(
            UseOrLoseAlert(
                urgency=Urgency.MEDIUM,
                provider="codex",
                account=f"user{i}@example.com",
                window_label="Weekly",
                remaining_percent=80.0 + i,
                days_until_reset=2.0 + (i % 5),
                plan=None,
                message=f"burn {i}",
                source="codexbar",
                score=50.0 + i,
                kind="burn",
                flexibility_profile=FlexibilityProfile(
                    flexibility_class=FlexibilityClass.BURSTABLE,
                    consumption_flexibility=1.0,
                    value_at_risk_usd=1.0 + i,
                ),
            )
        )
    text = render_report(
        Snapshot(collected_at=now, accounts=[]),
        alerts,
        config={},
        color=False,
        full=True,
    )
    assert "## Action plan (detailed)" in text
    assert "## Action plan — at a glance" in text
    assert text.index("(detailed)") < text.index("at a glance")
    # Trailing brief block is last and viewport-sized
    glance_start = text.index("## Action plan — at a glance")
    glance_lines = text[glance_start:].splitlines()
    assert len(glance_lines) <= ACTION_PLAN_MAX_LINES
    assert text.strip().endswith(glance_lines[-1].strip()) or glance_lines[-1] in text[-200:]


def test_brief_action_plan_respects_max_lines():
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.LOW,
            provider="codex",
            account=f"u{i}@x.com",
            window_label="Weekly",
            remaining_percent=50.0,
            days_until_reset=3.0,
            plan=None,
            message="x",
            source="codexbar",
            score=float(i),
            kind="burn",
        )
        for i in range(30)
    ]
    body = _render_brief_action_plan(alerts, _Style(False), clamp_width=80, max_lines=10)
    assert _physical_line_count(body) <= 10
    assert any("more" in line for line in body)


def test_brief_action_plan_caps_lines_per_provider():
    from aiuse.report import BRIEF_MAX_LINES_PER_PROVIDER

    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="claude",
            account=f"a{i}@x.com",
            window_label=f"window-{i}",
            remaining_percent=50.0,
            days_until_reset=2.0,
            plan=None,
            message="x",
            source="cswap",
            score=float(100 - i),
            kind="burn",
        )
        for i in range(8)
    ] + [
        UseOrLoseAlert(
            urgency=Urgency.MEDIUM,
            provider="codex",
            account="c@x.com",
            window_label="Weekly",
            remaining_percent=80.0,
            days_until_reset=3.0,
            plan=None,
            message="y",
            source="codexbar",
            score=10.0,
            kind="burn",
        )
    ]
    body = _render_brief_action_plan(alerts, _Style(False), clamp_width=80, max_lines=40)
    plain = [_strip_ansi(line) for line in body]
    claude_alert_lines = [line for line in plain if "claude" in line and "window-" in line]
    assert len(claude_alert_lines) == BRIEF_MAX_LINES_PER_PROVIDER
    assert any("more" in line for line in plain)
    assert any("codex" in line for line in plain)


def test_default_report_is_clock_matrix():
    from aiuse.report import render_stderr_meta

    now = utcnow()
    acc = AccountUsage(provider="codex", source="codexbar", account="a@x.com")
    broken = AccountUsage(
        provider="grok",
        source="codexbar",
        account="b@x.com",
        error="session expired",
    )
    burn = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@x.com",
        window_label="Weekly",
        remaining_percent=90.0,
        days_until_reset=2.0,
        plan=None,
        message="burn",
        source="codexbar",
        score=80.0,
        kind="burn",
    )
    empty = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="copilot",
        account="default",
        window_label="premium",
        remaining_percent=0.0,
        days_until_reset=7.0,
        plan=None,
        message="gone",
        source="tokscale",
        score=90.0,
        kind="conserve",
    )
    snap = Snapshot(
        collected_at=now,
        accounts=[acc, broken],
        collector_errors=["tokscale: boom"],
    )
    text = render_report(snap, [empty, burn], config={}, color=False)
    assert "(full)" not in text
    assert "## Per-provider usage" not in text
    assert "Detail: ai --full" not in text
    assert "\n\n" not in text
    lines = text.splitlines()
    # Header first, then one tagged row per account/pool, then any legend.
    # No account here has independent pools, so SCOPE earns no column.
    assert lines[0].split() == ["SERVICE", "ACCT", "5H", "WEEK", "MONTH", "$", "UNUSED"]
    rows = [line for line in lines[1:] if line[:5].strip() in _BAND_TAGS]
    assert rows[0].startswith("error")
    assert "session expired" in rows[0]
    # Order by row tag, not by raw substring position — the header note
    # ("% used …") contains "use" and would satisfy a naive text.index check.
    tags = [row[:5].strip() for row in rows]
    assert tags.index("error") < tags.index("empty") < tags.index("use")
    assert rows[-1].startswith("use")
    # Every account appears (broken as error; codex via burn alert)
    assert "grok" in text.lower()
    assert "codex" in text
    meta = render_stderr_meta(snap, [empty, burn], color=False)
    assert "Collected at" in meta
    assert "tokscale: boom" in meta
    assert "Detail: ai --full" in meta


def test_priority_ladder_sorts_by_use_urgency_not_alphabet():
    """Sooner high-score burn sorts below a later lower-score burn (tags may match)."""
    from aiuse.report import alert_use_urgency, render_priority_ladder

    later = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="aaa",
        account="a",
        window_label="Weekly",
        remaining_percent=80.0,
        days_until_reset=6.0,
        plan=None,
        message="later",
        source="codexbar",
        score=40.0,
        kind="burn",
    )
    sooner = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="zzz",
        account="z",
        window_label="Weekly",
        remaining_percent=80.0,
        days_until_reset=1.0,
        plan=None,
        message="sooner",
        source="codexbar",
        score=90.0,
        kind="burn",
    )
    assert alert_use_urgency(sooner) > alert_use_urgency(later)
    text = render_priority_ladder([later, sooner], color=False)
    assert text.index("Zzz") > text.index("Aaa")
    assert text.strip().splitlines()[-1].startswith("use")
    assert "Zzz" in text.strip().splitlines()[-1]


def test_priority_ladder_includes_on_pace_providers():
    from datetime import timedelta

    from aiuse.models import BillingKind, QuotaWindow
    from aiuse.report import render_priority_ladder

    now = utcnow()
    ok = AccountUsage(
        provider="cursor",
        source="codexbar",
        account="c@x.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Cursor included",
                used_percent=40.0,
                remaining_percent=60.0,
                resets_at=now + timedelta(days=10),
                window_minutes=44640,
            )
        ],
    )
    snap = Snapshot(collected_at=now, accounts=[ok])
    text = render_priority_ladder([], snapshot=snap, color=False)
    assert text.startswith("mid")
    assert "Cursor" in text
    assert "60%" in text
    assert "\n\n" not in text


def test_priority_ladder_lists_antigravity_pools_separately():
    """Gemini and Claude/GPT budgets must each get a ladder row."""
    from datetime import timedelta

    from aiuse.models import BillingKind, QuotaWindow, Urgency, UseOrLoseAlert
    from aiuse.report import render_priority_ladder

    now = utcnow()
    acc = AccountUsage(
        provider="antigravity",
        source="codexbar",
        account="user@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Gemini 5-hour",
                used_percent=99.0,
                remaining_percent=1.0,
                resets_at=now + timedelta(hours=1),
                window_minutes=300,
            ),
            QuotaWindow(
                label="Gemini weekly",
                used_percent=34.0,
                remaining_percent=66.0,
                resets_at=now + timedelta(days=4),
                window_minutes=10080,
            ),
            QuotaWindow(
                label="Claude/GPT 5-hour",
                used_percent=57.0,
                remaining_percent=43.0,
                resets_at=now + timedelta(hours=3),
                window_minutes=300,
            ),
            QuotaWindow(
                label="Claude/GPT weekly",
                used_percent=19.0,
                remaining_percent=81.0,
                resets_at=now + timedelta(days=6),
                window_minutes=10080,
            ),
        ],
    )
    snap = Snapshot(collected_at=now, accounts=[acc])
    text = render_priority_ladder([], snapshot=snap, color=False)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2
    joined = "\n".join(lines)
    assert "Gemini weekly" in joined
    assert "Claude/GPT weekly" in joined
    assert "66%" in joined
    assert "81%" in joined

    # One pool burn must not swallow the other pool's mid row.
    gemini_burn = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="antigravity",
        account="user@example.com",
        window_label="Gemini weekly",
        remaining_percent=66.0,
        days_until_reset=4.0,
        plan="Google AI",
        message="burn Gemini",
        source="codexbar",
        score=80.0,
        kind="burn",
    )
    text2 = render_priority_ladder([gemini_burn], snapshot=snap, color=False)
    assert "Gemini weekly" in text2
    assert "Claude/GPT weekly" in text2


def test_negative_prepaid_is_empty_band():
    """Negative/zero prepaid balance should be classified as empty, not n/a."""
    from aiuse.models import AccountUsage, BillingKind, Snapshot, utcnow
    from aiuse.report import render_priority_ladder

    acc = AccountUsage(
        source="codexbar",
        provider="opencode-zen",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=-0.04,
        windows=[],
    )
    snap = Snapshot(collected_at=utcnow(), accounts=[acc])
    text = render_priority_ladder([], snapshot=snap, color=False)
    assert text.startswith("empty")
    assert "n/a" not in text
    assert "-0.04" in text


def test_deepseek_prepaid_has_no_use_urgency():
    """Deepseek CodexBar row is prepaid tokens — not '100% left · use before reset'."""
    from aiuse.models import BillingKind, QuotaWindow
    from aiuse.report import (
        _account_use_urgency,
        alert_use_urgency,
        render_priority_ladder,
    )

    deepseek = AccountUsage(
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
            )
        ],
    )
    # Fake 100% window must not score like a subscription burn candidate.
    assert _account_use_urgency(deepseek) == 0.0

    sub = AccountUsage(
        source="codexbar",
        provider="cursor",
        account="c@x.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Cursor included",
                used_percent=40.0,
                remaining_percent=60.0,
                resets_at=utcnow() + timedelta(days=10),
                window_minutes=44640,
            )
        ],
    )
    assert _account_use_urgency(sub) > _account_use_urgency(deepseek)

    empty = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="copilot",
        account="default",
        window_label="premium",
        remaining_percent=0.0,
        days_until_reset=7.0,
        plan=None,
        message="gone",
        source="tokscale",
        score=90.0,
        kind="conserve",
    )
    slow = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="a@x.com",
        window_label="Weekly",
        remaining_percent=10.0,
        days_until_reset=3.0,
        plan=None,
        message="pace",
        source="codexbar",
        score=70.0,
        kind="conserve",
    )
    snap = Snapshot(collected_at=utcnow(), accounts=[deepseek, sub])
    text = render_priority_ladder([empty, slow], snapshot=snap, color=False)
    deep_line = next(line for line in text.splitlines() if "eepseek" in line)
    assert deep_line.startswith("n/a")
    assert "no expiry" in deep_line
    assert "balance $4.99" in deep_line
    assert "100%" not in deep_line
    assert "use before" not in deep_line.casefold()
    # Lane order: empty → n/a → slow → mid
    assert text.index("empty") < text.index("n/a")
    assert text.index("n/a") < text.index("slow")
    cursor_line = next(line for line in text.splitlines() if "Cursor" in line)
    assert text.index(deep_line) < text.index(cursor_line)

    large = UseOrLoseAlert(
        urgency=Urgency.INFO,
        provider="openrouter",
        account="default",
        window_label="balance $18.90",
        remaining_percent=0.0,
        days_until_reset=None,
        plan=None,
        message="prepaid",
        source="codexbar",
        score=0.0,
        kind="prepaid",
    )
    assert alert_use_urgency(large) == 0.0
    prepaid_text = render_priority_ladder([large], color=False)
    assert prepaid_text.startswith("n/a")
    assert "no expiry" in prepaid_text
    assert "use before" not in prepaid_text.casefold()
    assert "100%" not in prepaid_text


def test_brief_aliases_default_priority_ladder():
    now = utcnow()
    acc = AccountUsage(provider="codex", source="codexbar", account="a@x.com")
    snap = Snapshot(collected_at=now, accounts=[acc])
    default = render_report(snap, [], config={}, color=False)
    brief = render_report(snap, [], config={}, color=False, brief=True)
    assert default == brief
    assert "## Per-provider usage" not in brief


def test_full_report_includes_providers():
    now = utcnow()
    acc = AccountUsage(provider="codex", source="codexbar", account="a@x.com")
    text = render_report(
        Snapshot(collected_at=now, accounts=[acc]),
        [],
        config={},
        color=False,
        full=True,
    )
    assert "(full)" in text
    assert "## Per-provider usage" in text
    assert "## Tips" in text
    assert "Detail: ai --full" not in text
    assert "History:" in text
    assert "learning" in text


def test_brief_report_omits_usage_and_tips():
    from datetime import timedelta

    from aiuse.models import BillingKind, QuotaWindow

    now = utcnow()
    acc = AccountUsage(
        provider="codex",
        source="codexbar",
        account="a@x.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Weekly",
                used_percent=10.0,
                remaining_percent=90.0,
                resets_at=now + timedelta(days=5),
                window_minutes=10080,
            )
        ],
    )
    snap = Snapshot(collected_at=now, accounts=[acc], collector_errors=["tokscale: boom"])
    text = render_report(snap, [], config={}, color=False, brief=True)
    assert "## Per-provider usage" not in text
    assert "## Cross-checks" not in text
    assert "## Tips" not in text
    lines = text.splitlines()
    assert lines[0].split()[0] == "SERVICE"
    assert lines[1].startswith("mid")
    assert "codex" in text
    # Percentages are consumption, not headroom: 90% left prints as 10%.
    assert "10%" in lines[1]


def test_glance_respects_custom_width():
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="codex",
            account="long.email.address@example.com",
            window_label="Codex weekly quota with a long label",
            remaining_percent=88.0,
            days_until_reset=4.4,
            plan=None,
            message="x",
            source="codexbar",
            score=50.0,
            kind="burn",
        )
    ]
    narrow = _render_brief_action_plan(alerts, _Style(False), clamp_width=50, max_lines=10)
    wide = _render_brief_action_plan(alerts, _Style(False), clamp_width=120, max_lines=10)
    assert all(len(_strip_ansi(line)) <= 50 for line in narrow)
    assert any(len(_strip_ansi(line)) > 50 for line in wide)


def test_at_a_glance_clamps_to_the_terminal_not_the_rule_width(monkeypatch):
    """A wide terminal must not have its alert rows cut at the 80-column rule.

    The plain renderer draws its section rules at ACTION_PLAN_WIDTH regardless
    of terminal size, and used to pass that same 80 down as the truncation
    width. On a 200-column terminal the at-a-glance rows were therefore cut with
    "…" for no reason. Reachable whenever the detailed plan exceeds
    ACTION_PLAN_MAX_LINES, which is what the 12 alerts below force.
    """
    now = utcnow()
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.MEDIUM,
            provider="codex",
            # Sized so a row lands between ACTION_PLAN_WIDTH and TABLE_MAX_WIDTH:
            # long enough that the old 80-column clamp cut it, short enough that
            # the new one does not.
            account=f"user{i}@example.com",
            window_label=f"Codex weekly quota {i}",
            remaining_percent=70.0 + i,
            days_until_reset=4.0,
            plan=None,
            message="x",
            source="codexbar",
            score=float(30 - i),
            kind="burn",
        )
        for i in range(12)
    ]

    def _glance_rows(columns: str) -> list[str]:
        monkeypatch.setenv("COLUMNS", columns)
        text = render_report(
            Snapshot(collected_at=now, accounts=[]),
            alerts,
            config={},
            color=False,
            full=True,
        )
        start = text.index("## Action plan — at a glance")
        return [line for line in text[start:].splitlines() if line.startswith("  ") and "·" in line]

    wide_rows = _glance_rows("200")
    assert wide_rows, "expected the at-a-glance block to be reached"
    # Nothing truncated, and rows genuinely exceed the 80-column rule width.
    assert not any(row.endswith("…") for row in wide_rows)
    assert any(len(row) > ACTION_PLAN_WIDTH for row in wide_rows)
    # Still bounded — a very wide terminal does not mean unbounded rows.
    assert all(len(row) <= TABLE_MAX_WIDTH for row in wide_rows)

    # A narrow terminal still clamps, so the block keeps fitting its viewport.
    narrow_rows = _glance_rows("60")
    assert all(len(row) <= 60 for row in narrow_rows)


def test_render_cross_checks_use_soft_labels():
    now = utcnow()
    snap = Snapshot(
        collected_at=now,
        cross_checks=[
            CrossCheck(
                provider="claude",
                account="a@x.com",
                status="warning",
                sources=["cswap", "CodexBar"],
                message="Tools disagree on some live quota figures: weekly differs. Small gaps are often expected.",
            ),
            CrossCheck(
                provider="codex",
                account=None,
                status="consistent",
                sources=["CodexBar", "tokscale"],
                message="Agree on 1 overlapping live quota measurement within tolerance.",
            ),
        ],
    )
    text = render_report(snap, [], config={}, color=False, full=True)
    assert "[NOTE]" in text
    assert "[OK]" in text
    assert "WARNING" not in text
    assert "informational" in text.lower()
    assert "cswap-only" in text or "poll" in text.lower()


def test_throttled_monthly_waste_stays_near_plan_scale_for_realistic_value():
    """Property: monthly waste ≈ value_usd * cycles; for value from clamp, not absurd *30."""
    monthly_price = 20.0
    remaining_frac = 0.5
    value_usd = monthly_price * remaining_frac  # max per-cycle after Fix #4 clamp
    window_minutes = 300
    waking = 16.0
    from aiuse.analysis.use_or_lose import DAYS_PER_MONTH

    cycles = (waking * DAYS_PER_MONTH * 60) / window_minutes
    monthly_waste = value_usd * cycles
    # 5h windows can waste more than one month's sticker price if chronically underused
    # (many cycles) — but must not use the old *30 formula ($300 for $10/cycle).
    assert monthly_waste != value_usd * 30
    line = _throttled_waste_line(
        _alert_with_value(window_minutes=window_minutes, value_usd=value_usd),
        _Style(False),
        waking_hours_per_day=waking,
    )
    assert f"~${monthly_waste:.2f}/month" in line


def _matrix_snapshot():
    """One account per shape the table has to handle."""
    from aiuse.models import BillingKind

    now = utcnow()
    return Snapshot(
        collected_at=now,
        accounts=[
            # Nested windows on two clocks, one account.
            AccountUsage(
                provider="claude",
                source="cswap",
                account="me@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        used_percent=75.0,
                        remaining_percent=25.0,
                        resets_at=now + timedelta(hours=4),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=3.0,
                        remaining_percent=97.0,
                        resets_at=now + timedelta(days=7),
                        window_minutes=10080,
                    ),
                ],
            ),
            # Two hard-separated pools under one account.
            AccountUsage(
                provider="antigravity",
                source="codexbar",
                account="me@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Gemini weekly",
                        used_percent=12.0,
                        remaining_percent=88.0,
                        resets_at=now + timedelta(days=6),
                        window_minutes=10080,
                    ),
                    QuotaWindow(
                        label="Claude/GPT weekly",
                        used_percent=0.0,
                        remaining_percent=100.0,
                        resets_at=now + timedelta(days=6),
                        window_minutes=10080,
                    ),
                ],
            ),
            AccountUsage(
                provider="openrouter",
                source="codexbar",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=4.30,
            ),
            AccountUsage(provider="grok", source="codexbar", error="session expired"),
        ],
    )


def test_clock_matrix_puts_each_window_under_its_own_clock():
    text = render_clock_matrix([], snapshot=_matrix_snapshot(), color=False)
    rows = {line.split()[1]: line for line in text.splitlines() if line[:5].strip() in _BAND_TAGS}

    # Claude reports both clocks; the monthly cell is empty, not fabricated.
    claude = rows["claude"].split()
    assert claude[4:7] == ["75%/4h", "3%/7d", "<-"]


def test_clock_matrix_shows_used_not_remaining():
    """0% must mean untouched and 100% exhausted — the inverse of the old ladder."""
    text = render_clock_matrix([], snapshot=_matrix_snapshot(), color=False)
    claude = next(line for line in text.splitlines() if " claude " in line)
    assert "75%" in claude and "25%" not in claude  # 25% left renders as 75% used
    assert "3%" in claude and "97%" not in claude


def test_clock_matrix_splits_independent_pools_into_their_own_rows():
    text = render_clock_matrix([], snapshot=_matrix_snapshot(), color=False)
    agy = [line for line in text.splitlines() if " agy " in line]
    assert len(agy) == 2
    scopes = sorted(line.split()[3] for line in agy)
    assert scopes == ["claude/gpt", "gemini"]


def test_clock_matrix_keeps_non_window_accounts_as_notes():
    text = render_clock_matrix([], snapshot=_matrix_snapshot(), color=False)
    assert "balance $4.30 remaining (counts down · no expiry)" in text
    assert "session expired" in text


def test_clock_matrix_labels_expired_opencode_go_subscription():
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="opencode_go",
                provider="opencode-go",
                plan="expired",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="OpenCode Go",
                        remaining_percent=0.0,
                        reset_description="subscription expired",
                    )
                ],
            )
        ],
    )
    matrix = render_clock_matrix([], snapshot=snap, color=False)
    go_line = next(line for line in matrix.splitlines() if "oc-go" in line)
    assert go_line.startswith("empty")
    assert "subscription expired" in go_line
    assert "98%" not in go_line
    assert "2%" not in go_line

    ladder = render_priority_ladder([], snapshot=snap, color=False)
    assert "subscription expired" in ladder
    assert ladder.startswith("empty")
    assert "0% left" not in ladder


def test_clock_matrix_labels_config_lapsed_subscription():
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                account="me@mit.edu",
                plan="expired",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="claude subscription",
                        remaining_percent=0.0,
                        reset_description="subscription not renewed (not renewed 2026-08)",
                    )
                ],
            )
        ],
    )
    matrix = render_clock_matrix([], snapshot=snap, color=False)
    claude_line = next(line for line in matrix.splitlines() if "claude" in line)
    assert claude_line.startswith("empty")
    assert "subscription not renewed" in claude_line

    ladder = render_priority_ladder([], snapshot=snap, color=False)
    assert "subscription not renewed" in ladder
    assert ladder.startswith("empty")
    assert "0% left" not in ladder


def test_clock_matrix_sheds_columns_before_truncating_numbers():
    snap = _matrix_snapshot()
    wide = render_clock_matrix([], snapshot=snap, color=False, width=120)
    narrow = render_clock_matrix([], snapshot=snap, color=False, width=52)

    assert "$ UNUSED" in wide
    assert "$ UNUSED" not in narrow
    assert "NEXT" not in wide
    # The clock columns are the point of the table and survive the squeeze.
    for header in ("5H", "WEEK", "MONTH"):
        assert header in narrow
    assert all(len(_strip_ansi(line)) <= 60 for line in narrow.splitlines())


def test_clock_matrix_shortens_emails_to_their_domain():
    text = render_clock_matrix([], snapshot=_matrix_snapshot(), color=False)
    assert "gmail" in text
    assert "me@gmail.com" not in text


def test_clock_matrix_keeps_full_account_when_short_names_collide():
    from aiuse.models import BillingKind

    now = utcnow()
    windows = [
        QuotaWindow(
            label="Claude Code weekly",
            used_percent=10.0,
            remaining_percent=90.0,
            resets_at=now + timedelta(days=3),
            window_minutes=10080,
        )
    ]
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                provider="claude",
                source="cswap",
                account=who,
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=list(windows),
            )
            for who in ("a@gmail.com", "b@gmail.com")
        ],
    )
    text = render_clock_matrix([], snapshot=snap, color=False)
    # Both would shorten to "gmail", so neither may.
    assert "a@gmail.com" in text
    assert "b@gmail.com" in text


@pytest.mark.parametrize(
    ("days", "kwargs", "expected"),
    [
        (None, {}, None),
        (0.0, {}, "now"),
        (-0.1, {}, "now"),
        (45 / 86400, {}, "45s"),
        (6 / 1440, {}, "6m"),
        ((3 * 60 + 43) / 1440, {}, "3h43m"),
        (3 / 24, {}, "3h"),
        (14 / 24, {}, "14h"),
        (1 + 15 / 24, {}, "1d15h"),
        (2 + 14 / 24, {}, "2d14h"),
        (4 + 4 / 24, {}, "4d4h"),
        (27 + 4 / 24, {}, "27d4h"),
        (27.0, {}, "27d"),
        (12.2, {"estimated": True}, "~12d"),
        (0.4, {"estimated": True}, "~10h"),
        ((3 * 60 + 43) / 1440, {"compact": True}, "3h"),
        (2 + 14 / 24, {"compact": True}, "2d"),
        (4 / 24, {}, "4h"),
    ],
)
def test_format_reset_span_option_b(days, kwargs, expected):
    span = _format_reset_span(days, **kwargs)
    assert (None if span is None else span.plain()) == expected


def test_format_reset_span_omits_minutes_once_days_are_showing():
    # 1d 0h 30m rounds to the hour and then drops the zero hour.
    span = _format_reset_span(1 + 30 / 1440)
    assert span is not None
    assert span.plain() == "1d"


def test_clock_matrix_puts_reset_after_slash_not_in_next_column():
    now = utcnow()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                provider="claude",
                source="cswap",
                account="me@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(hours=3, minutes=43),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=16.0,
                        remaining_percent=84.0,
                        resets_at=now + timedelta(days=2, hours=14),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    text = render_clock_matrix([], snapshot=snap, color=False, width=120)
    assert "NEXT" not in text
    assert "10%/3h43m" in text
    assert "16%/2d14h" in text


def test_clock_matrix_omits_slash_when_clock_has_no_timestamp():
    now = utcnow()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                provider="zai",
                source="codexbar",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="z.ai 5-hour",
                        used_percent=0.0,
                        remaining_percent=100.0,
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="z.ai weekly",
                        used_percent=0.0,
                        remaining_percent=100.0,
                        resets_at=now + timedelta(days=6, hours=20),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    text = render_clock_matrix([], snapshot=snap, color=False, width=120)
    zai = next(line for line in text.splitlines() if " zai/crush " in line)
    assert "0%/6d20h" in zai
    # The 5h cell is a bare percent, not 0%/—.
    assert "0%/—" not in zai
    tokens = zai.split()
    assert "0%" in tokens


def test_clock_matrix_compacts_deadline_before_dropping_identity():
    now = utcnow()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                provider="claude",
                source="cswap",
                account="me@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code 5-hour",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(hours=3, minutes=43),
                        window_minutes=300,
                    )
                ],
            )
        ],
    )
    wide = render_clock_matrix([], snapshot=snap, color=False, width=120)
    mid = render_clock_matrix([], snapshot=snap, color=False, width=40)
    assert "10%/3h43m" in wide
    assert "10%/3h" in mid
    assert "3h43m" not in mid
    assert "claude" in mid
    assert "gmail" in mid


def test_clock_matrix_shortens_then_folds_colliding_identity():
    now = utcnow()
    windows_5h = QuotaWindow(
        label="Gemini 5-hour",
        used_percent=4.0,
        remaining_percent=96.0,
        resets_at=now + timedelta(hours=2),
        window_minutes=300,
    )
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                provider="antigravity",
                source="codexbar",
                account="a@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    windows_5h,
                    QuotaWindow(
                        label="Gemini weekly",
                        used_percent=12.0,
                        remaining_percent=88.0,
                        resets_at=now + timedelta(days=6),
                        window_minutes=10080,
                    ),
                    QuotaWindow(
                        label="Claude/GPT 5-hour",
                        used_percent=100.0,
                        remaining_percent=0.0,
                        resets_at=now + timedelta(hours=2),
                        window_minutes=300,
                    ),
                    QuotaWindow(
                        label="Claude/GPT weekly",
                        used_percent=82.0,
                        remaining_percent=18.0,
                        resets_at=now + timedelta(days=6),
                        window_minutes=10080,
                    ),
                ],
            ),
            AccountUsage(
                provider="claude",
                source="cswap",
                account="x@mit.edu",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=89.0,
                        remaining_percent=11.0,
                        resets_at=now + timedelta(days=2),
                        window_minutes=10080,
                    )
                ],
            ),
            AccountUsage(
                provider="claude",
                source="cswap",
                account="y@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Claude Code weekly",
                        used_percent=94.0,
                        remaining_percent=6.0,
                        resets_at=now + timedelta(days=1, hours=15),
                        window_minutes=10080,
                    )
                ],
            ),
        ],
    )
    wide = render_clock_matrix([], snapshot=snap, color=False, width=120)
    short = render_clock_matrix([], snapshot=snap, color=False, width=48)
    folded = render_clock_matrix([], snapshot=snap, color=False, width=40)

    assert "claude/gpt" in wide
    assert "gemini" in wide
    assert "c/gpt" in short
    assert "gem" in short
    assert "claude/gpt" not in short
    # Last resort folds the leftover disambiguator into SERVICE.
    assert "agy/gem" in folded or "agy/c/gpt" in folded
    assert "claude/mit" in folded or "claude/gmail" in folded
    header = folded.splitlines()[0]
    assert "SCOPE" not in header.split()
    assert "ACCT" not in header.split()


def test_advance_matrix_layout_skips_identity_still_needed_for_uniqueness():
    now = utcnow()
    snap = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                provider="antigravity",
                source="codexbar",
                account="a@gmail.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Gemini weekly",
                        used_percent=10.0,
                        remaining_percent=90.0,
                        resets_at=now + timedelta(days=6),
                        window_minutes=10080,
                    ),
                    QuotaWindow(
                        label="Claude/GPT weekly",
                        used_percent=20.0,
                        remaining_percent=80.0,
                        resets_at=now + timedelta(days=6),
                        window_minutes=10080,
                    ),
                ],
            )
        ],
    )
    built = _build_matrix_rows([], snap, {})
    layout = _MatrixLayout(show_value=False, compact_deadline=True, short_scope=True, show_scope=True)
    # Unique account can go; SCOPE still distinguishes the two agy rows.
    assert _advance_matrix_layout(layout, built) is True
    assert not layout.show_account
    assert layout.show_scope
    assert not layout.fold_identity
    # Next step folds rather than dropping the still-needed SCOPE column.
    assert _advance_matrix_layout(layout, built) is True
    assert layout.fold_identity
    assert not layout.show_scope


# ---------------------------------------------------------------------------
# Zebra striping
# ---------------------------------------------------------------------------


class TestZebraStriping:
    """Alternating row backgrounds in the clock matrix and priority ladder."""

    def test_zebra_bg_adds_background_when_color_enabled(self):
        sty = _Style(enabled=True)
        line = "hello world"
        result = sty.zebra_bg(line)
        # Should contain the 256-color dark-gray BG escape and a BG-reset.
        assert "\033[48;5;236m" in result
        assert "\033[49m" in result
        # Original text must be present.
        assert "hello world" in result

    def test_zebra_bg_noop_when_color_disabled(self):
        sty = _Style(enabled=False)
        line = "hello world"
        assert sty.zebra_bg(line) == line

    def test_zebra_bg_pads_to_width(self):
        sty = _Style(enabled=True)
        line = "short"
        result = sty.zebra_bg(line, width=20)
        plain = _strip_ansi(result)
        # The visible content should be padded with spaces to reach width.
        assert len(plain) >= 20

    def test_zebra_bg_preserves_embedded_styles(self):
        """Embedded \\033[0m resets must not punch holes in the stripe."""
        sty = _Style(enabled=True)
        inner = sty.bold("HELLO")  # contains \033[1m...\033[0m
        result = sty.zebra_bg(inner)
        # The full-reset inside should have been replaced with reset+re-bg.
        assert "\033[0;48;5;236m" in result

    def test_clock_matrix_zebra_stripes_with_color(self):
        """When color=True, alternating data rows get background escapes."""
        text = render_clock_matrix([], snapshot=_matrix_snapshot(), color=True, width=120)
        data_lines = [
            line for line in text.splitlines() if any(tag.strip() in _strip_ansi(line)[:6] for tag in _BAND_TAGS)
        ]
        assert len(data_lines) >= 2, "need ≥2 data rows for zebra test"
        # Odd-indexed rows (0-based: indices 1, 3, …) should have the BG code.
        for idx, line in enumerate(data_lines):
            if idx % 2:
                assert "\033[48;5;236m" in line, f"row {idx} should be striped"
            else:
                assert "\033[48;5;236m" not in line, f"row {idx} should NOT be striped"

    def test_clock_matrix_no_zebra_without_color(self):
        """When color=False, no ANSI escapes at all (clean pipe output)."""
        text = render_clock_matrix([], snapshot=_matrix_snapshot(), color=False, width=120)
        assert "\033[" not in text

    def test_priority_ladder_zebra_stripes_with_color(self):
        """Priority ladder alternating rows get background escapes."""
        alerts = [
            UseOrLoseAlert(
                provider="claude",
                account="a@gmail.com",
                window_label=f"Claude Code weekly {i}",
                remaining_percent=50.0 + i * 10,
                score=30.0 - i * 5,
                days_until_reset=5.0,
                kind="burn",
                urgency=Urgency.MEDIUM,
                plan="Max",
                message="burn it",
                source="cswap",
            )
            for i in range(4)
        ]
        text = render_priority_ladder(alerts, color=True, width=100)
        lines = text.splitlines()
        assert len(lines) >= 2
        for idx, line in enumerate(lines):
            if idx % 2:
                assert "\033[48;5;236m" in line, f"ladder row {idx} should be striped"
            else:
                assert "\033[48;5;236m" not in line, f"ladder row {idx} should NOT be striped"

    def test_priority_ladder_no_zebra_without_color(self):
        """Priority ladder with color=False produces no ANSI escapes."""
        alerts = [
            UseOrLoseAlert(
                provider="claude",
                account="default",
                window_label="Claude Code weekly",
                remaining_percent=50.0,
                score=30.0,
                days_until_reset=5.0,
                kind="burn",
                urgency=Urgency.MEDIUM,
                plan="Max",
                message="burn it",
                source="cswap",
            )
        ]
        text = render_priority_ladder(alerts, color=False, width=100)
        assert "\033[" not in text
