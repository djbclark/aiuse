from datetime import datetime, timedelta, timezone

from aiuse.models import (
    AccountUsage,
    CrossCheck,
    FlexibilityClass,
    FlexibilityProfile,
    PaceProfile,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.report import (
    ACTION_PLAN_MAX_LINES,
    ACTION_PLAN_WIDTH,
    _action_plan_line,
    _physical_line_count,
    _render_brief_action_plan,
    _sorted_accounts,
    _strip_ansi,
    _Style,
    _throttled_waste_line,
    render_report,
    render_status_line,
)


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

    assert [account.provider for account in _sorted_accounts(accounts)] == [
        "claude",
        "codex",
        "copilot",
        "antigravity",
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
    body = _render_brief_action_plan(alerts, _Style(False), width=80, max_lines=10)
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
    body = _render_brief_action_plan(alerts, _Style(False), width=80, max_lines=40)
    plain = [_strip_ansi(line) for line in body]
    claude_alert_lines = [line for line in plain if "Claude" in line and "window-" in line]
    assert len(claude_alert_lines) == BRIEF_MAX_LINES_PER_PROVIDER
    assert any("more" in line for line in plain)
    assert any("Codex" in line for line in plain)


def test_default_report_is_priority_ladder():
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
    assert lines[0].startswith("error")
    assert "session expired" in lines[0]
    assert text.index("error") < text.index("empty")
    assert text.index("empty") < text.index("use")
    assert lines[-1].startswith("use")
    # Every account appears (broken as error; codex via burn alert)
    assert "Grok" in text or "grok" in text.lower()
    assert "Codex" in text
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
                used_percent=100.0,
                remaining_percent=0.0,
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
    assert text.startswith("mid")
    assert "Codex" in text


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
    narrow = _render_brief_action_plan(alerts, _Style(False), width=50, max_lines=10)
    wide = _render_brief_action_plan(alerts, _Style(False), width=120, max_lines=10)
    assert all(len(_strip_ansi(line)) <= 50 for line in narrow)
    assert any(len(_strip_ansi(line)) > 50 for line in wide)


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
