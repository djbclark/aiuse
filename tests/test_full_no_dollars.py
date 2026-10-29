import re
from datetime import timedelta

from aiuse.models import (
    AccountUsage,
    BillingKind,
    FlexibilityClass,
    FlexibilityProfile,
    PaceProfile,
    QuotaWindow,
    Snapshot,
    Urgency,
    UsageCredits,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.report import _consumption_line, render_report


def test_render_report_full_has_no_estimated_dollars():
    now = utcnow()
    window = QuotaWindow(
        label="Claude 5-hour",
        used_percent=20.0,
        resets_at=now + timedelta(hours=2),
        window_minutes=300,
    )
    acc_prepaid = AccountUsage(
        source="codexbar",
        provider="jev/typesafe",
        account="default",
        plan="Pro ($20/mo)",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=5.00,
        windows=[],
    )
    acc_credits = AccountUsage(
        source="cswap",
        provider="claude",
        account="djbclark@gmail.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        usage_credits=UsageCredits(
            used=1.00,
            limit=10.00,
            remaining=2.03,
            currency="USD",
            used_percent=10.0,
            resets_at=now + timedelta(days=5),
        ),
        windows=[window],
    )

    alert_unblended = UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="claude",
        account="djbclark@gmail.com",
        window_label="Claude 5-hour",
        remaining_percent=80.0,
        days_until_reset=0.08,
        plan=None,
        message="burn",
        source="cswap",
        score=85.0,
        kind="burn",
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.BURSTABLE,
            consumption_flexibility=1.0,
            value_at_risk_usd=12.50,
        ),
        pace=PaceProfile(
            elapsed_fraction=0.6,
            used_fraction=0.2,
            pace_ratio=0.25,
            projected_used_fraction=0.25,
            projected_waste_fraction=0.75,
            projected_waste_usd=12.50,
            projected_exhaust_at=None,
            learned_sample_count=0,
        ),
    )

    alert_blended = UseOrLoseAlert(
        urgency=Urgency.MEDIUM,
        provider="claude",
        account="djbclark@gmail.com",
        window_label="Claude Code weekly",
        remaining_percent=70.0,
        days_until_reset=3.0,
        plan=None,
        message="burn weekly",
        source="cswap",
        score=60.0,
        kind="burn",
        flexibility_profile=FlexibilityProfile(
            flexibility_class=FlexibilityClass.SEMI_THROTTLED,
            consumption_flexibility=0.5,
            value_at_risk_usd=8.00,
        ),
        pace=PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.3,
            pace_ratio=1.30,
            projected_used_fraction=0.90,
            projected_waste_fraction=0.10,
            projected_waste_usd=8.00,
            projected_exhaust_at=None,
            learned_sample_count=12,
        ),
    )

    snap = Snapshot(collected_at=now, accounts=[acc_prepaid, acc_credits])
    cfg = {"plans": {"claude": {"monthly_price": 20.0}}}
    report = render_report(snap, [alert_unblended, alert_blended], config=cfg, color=False, full=True)

    # 1. Real money preserved:
    assert "↓ $5.00 prepaid" in report
    assert "plan=Pro ($20/mo)" in report
    assert "↓ $2.03 USD left" in report

    # 2. No estimated dollar figures anywhere:
    assert "$12.50" not in report
    assert "$8.00" not in report
    assert "at risk" not in report

    # Find every dollar amount in the report via regex:
    dollar_matches = re.findall(r"\$\d+(?:\.\d+)?", report)
    # Plan price, the prepaid wallet, and the reported extra-usage wallet.
    # Estimated waste ($12.50, $8.00) must stay out.
    allowed = {"$20", "$5.00", "$1.00", "$10.00", "$2.03"}
    for match in dollar_matches:
        assert match in allowed, f"Unexpected estimated dollar amount found: {match}"

    # 3. Action-plan pace formatting:
    # alert_unblended has pace_ratio=0.25 -> round((0.25 - 1) * 100) = -75
    assert "pace -75% — projected 75% unused@reset" in report
    # alert_blended has pace_ratio=1.30, learned_sample_count=12 -> round((1.30 - 1) * 100) = +30
    assert "pace +30% (history-blended) — projected 10% unused@reset" in report

    # 4. Detail line pace formatting (unblended signed percent, no history note):
    assert "blended w/ history" not in report
    assert "blended with history" not in report


def test_consumption_line_no_estimated_dollars_and_signed_pace():
    now = utcnow()
    window = QuotaWindow(
        label="5-hour",
        used_percent=20.0,
        resets_at=now + timedelta(hours=2),
        window_minutes=300,
    )
    detail = _consumption_line(
        window,
        provider="codex",
        analysis={},
    )
    assert detail is not None
    assert "$" not in detail
    assert "pace " in detail
    assert "%" in detail
    assert "blended" not in detail


def test_consumption_line_omits_pace_when_none():
    # Exhausted window (used 100%, remaining 0%) has window_pace_percent == None
    now = utcnow()
    window = QuotaWindow(
        label="5-hour",
        used_percent=100.0,
        resets_at=now + timedelta(hours=2),
        window_minutes=300,
    )
    detail = _consumption_line(
        window,
        provider="codex",
        analysis={},
    )
    assert detail is not None
    assert "pace" not in detail
