"""Tests for the deterministic rich chat-format renderer (``--for-chat``).

Covers the full spec from https://github.com/djbclark/aiuse/issues/29:
- Status emoji with severity aggregation (remaining-% × pace)
- Pace interpretation language
- Section grouping (subscription / prepaid / long-cycle / action)
- Governing-window warnings
- Account labels
- Deterministic ordering
- No ANSI codes, no hard wraps
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiuse.chat_format import (
    EMOJI_GREEN,
    EMOJI_ORANGE,
    EMOJI_PREPAID,
    EMOJI_RED,
    EMOJI_YELLOW,
    _apply_governing_warnings,
    _build_action_items,
    _chat_deadline,
    _format_remaining,
    _monthly_pacing_note,
    _select_long_cycle_highlights,
    _WindowRow,
    pace_interpretation,
    render_chat_report,
    status_emoji,
)
from aiuse.models import (
    AccountUsage,
    BillingKind,
    PaceProfile,
    QuotaWindow,
    RoutingContext,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
)

# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

NOW = datetime(2026, 8, 4, 1, 0, 0, tzinfo=timezone.utc)


def _window(
    label: str = "weekly",
    remaining: float = 50.0,
    resets_hours: float = 48.0,
    window_minutes: int = 10080,
) -> QuotaWindow:
    return QuotaWindow(
        label=label,
        remaining_percent=remaining,
        resets_at=NOW + __import__("datetime").timedelta(hours=resets_hours),
        window_minutes=window_minutes,
    )


def _account(
    provider: str = "claude",
    account: str | None = "user@example.com",
    windows: list[QuotaWindow] | None = None,
    billing_kind: BillingKind = BillingKind.SUBSCRIPTION_WINDOW,
    balance_usd: float | None = None,
    error: str | None = None,
) -> AccountUsage:
    return AccountUsage(
        source="test",
        provider=provider,
        account=account,
        billing_kind=billing_kind,
        windows=windows or [_window()],
        balance_usd=balance_usd,
        error=error,
    )


def _alert(
    provider: str = "claude",
    account: str | None = "user@example.com",
    window_label: str = "weekly",
    remaining: float = 50.0,
    kind: str = "burn",
    urgency: Urgency = Urgency.MEDIUM,
    pace_ratio: float | None = None,
    projected_used_fraction: float | None = None,
    projected_exhaust_at: datetime | None = None,
    projected_waste_fraction: float | None = None,
    has_overage: bool = False,
    window_minutes: int | None = 10080,
) -> UseOrLoseAlert:
    pace = None
    if pace_ratio is not None:
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.5,
            pace_ratio=pace_ratio,
            projected_used_fraction=projected_used_fraction,
            projected_waste_fraction=projected_waste_fraction,
            projected_waste_usd=None,
            projected_exhaust_at=projected_exhaust_at,
            has_overage=has_overage,
        )
    return UseOrLoseAlert(
        urgency=urgency,
        provider=provider,
        account=account,
        window_label=window_label,
        remaining_percent=remaining,
        days_until_reset=2.0,
        plan=None,
        message=f"Test alert for {provider}",
        source="test",
        score=50.0,
        kind=kind,
        pace=pace,
        window_minutes=window_minutes,
    )


def _snapshot(
    accounts: list[AccountUsage] | None = None,
    collected_at: datetime | None = None,
    collector_errors: list[str] | None = None,
) -> Snapshot:
    return Snapshot(
        collected_at=collected_at or NOW,
        accounts=accounts or [],
        collector_errors=collector_errors or [],
    )


# -----------------------------------------------------------------------
# 1. Status emoji thresholds
# -----------------------------------------------------------------------


class TestStatusEmoji:
    """Spec §1: Severity aggregation from remaining-% and pace."""

    def test_green_high_remaining(self):
        assert status_emoji(80.0) == EMOJI_GREEN

    def test_yellow_moderate(self):
        assert status_emoji(50.0) == EMOJI_YELLOW

    def test_orange_low(self):
        assert status_emoji(15.0) == EMOJI_ORANGE

    def test_red_critical(self):
        assert status_emoji(5.0) == EMOJI_RED

    def test_boundary_70_is_green(self):
        assert status_emoji(70.0) == EMOJI_GREEN

    def test_boundary_69_is_yellow(self):
        assert status_emoji(69.99) == EMOJI_YELLOW

    def test_boundary_25_is_yellow(self):
        assert status_emoji(25.0) == EMOJI_YELLOW

    def test_boundary_24_is_orange(self):
        assert status_emoji(24.99) == EMOJI_ORANGE

    def test_boundary_10_is_orange(self):
        assert status_emoji(10.0) == EMOJI_ORANGE

    def test_boundary_9_is_red(self):
        assert status_emoji(9.99) == EMOJI_RED

    def test_zero_is_red(self):
        assert status_emoji(0.0) == EMOJI_RED

    # Pace overrides.
    def test_pace_override_high_pace_65_remaining(self):
        """65% remaining, 1.5× pace → 🟠 (pace overrides to orange)."""
        assert status_emoji(65.0, 1.50) == EMOJI_ORANGE

    def test_pace_override_moderate_pace(self):
        """65% remaining, 1.18× pace → 🟡 (pace → yellow, matches remaining)."""
        assert status_emoji(65.0, 1.18) == EMOJI_YELLOW

    def test_pace_low_remaining_high(self):
        """65% remaining, 0.6× pace → 🟡 (pace → green, remaining → yellow wins)."""
        assert status_emoji(65.0, 0.60) == EMOJI_YELLOW

    def test_pace_low_remaining_high_green(self):
        """72% remaining, 0.38× pace → 🟢 (both green)."""
        assert status_emoji(72.0, 0.38) == EMOJI_GREEN

    def test_zero_overrides_low_pace(self):
        """0% remaining, 0.4× pace → 🔴 (zero always wins)."""
        assert status_emoji(0.0, 0.40) == EMOJI_RED

    def test_no_pace_override_normal_range(self):
        """Normal pace range (0.80-1.10) gives no override."""
        assert status_emoji(50.0, 0.95) == EMOJI_YELLOW


# -----------------------------------------------------------------------
# 2. Pace interpretation language
# -----------------------------------------------------------------------


class TestPaceInterpretation:
    """Spec §4: Pace interpretation language with threshold lookup."""

    def test_no_pace(self):
        assert pace_interpretation(None, None) is None

    def test_projected_exhaustion(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.8,
            pace_ratio=1.47,
            projected_used_fraction=1.2,
            projected_waste_fraction=None,
            projected_waste_usd=None,
            projected_exhaust_at=None,
        )
        result = pace_interpretation(pace, NOW)
        assert "projected to exhaust before reset" in result
        assert "1.47×" in result

    def test_projected_exhaustion_with_overage(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.8,
            pace_ratio=1.47,
            projected_used_fraction=1.2,
            projected_waste_fraction=None,
            projected_waste_usd=None,
            projected_exhaust_at=None,
            has_overage=True,
        )
        result = pace_interpretation(pace, NOW)
        assert "overage may create real spending" in result

    def test_no_overage_no_spending(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.8,
            pace_ratio=1.47,
            projected_used_fraction=1.2,
            projected_waste_fraction=None,
            projected_waste_usd=None,
            projected_exhaust_at=None,
            has_overage=False,
        )
        result = pace_interpretation(pace, NOW)
        assert "overage" not in result

    def test_waste_from_fraction(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.3,
            pace_ratio=0.60,
            projected_used_fraction=0.60,
            projected_waste_fraction=0.40,
            projected_waste_usd=None,
            projected_exhaust_at=None,
        )
        result = pace_interpretation(pace, NOW)
        assert "likely unused capacity" in result

    def test_low_pace(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.3,
            pace_ratio=0.50,
            projected_used_fraction=0.60,
            projected_waste_fraction=0.10,
            projected_waste_usd=None,
            projected_exhaust_at=None,
        )
        result = pace_interpretation(pace, NOW)
        assert "likely unused capacity" in result

    def test_significantly_ahead(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.7,
            pace_ratio=1.40,
            projected_used_fraction=0.95,
            projected_waste_fraction=None,
            projected_waste_usd=None,
            projected_exhaust_at=None,
        )
        result = pace_interpretation(pace, NOW)
        assert "significantly ahead" in result

    def test_slightly_ahead(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.6,
            pace_ratio=1.15,
            projected_used_fraction=0.85,
            projected_waste_fraction=None,
            projected_waste_usd=None,
            projected_exhaust_at=None,
        )
        result = pace_interpretation(pace, NOW)
        assert "slightly ahead" in result

    def test_on_pace(self):
        pace = PaceProfile(
            elapsed_fraction=0.5,
            used_fraction=0.5,
            pace_ratio=1.0,
            projected_used_fraction=0.95,
            projected_waste_fraction=0.05,
            projected_waste_usd=None,
            projected_exhaust_at=None,
        )
        result = pace_interpretation(pace, NOW)
        assert "roughly on sustainable pace" in result


# -----------------------------------------------------------------------
# 3. Deadline formatting
# -----------------------------------------------------------------------


class TestChatDeadline:
    """Chat-specific deadline formatting."""

    def test_none(self):
        assert _chat_deadline(None) == "reset time unavailable"

    def test_zero(self):
        assert _chat_deadline(0) == "reset imminent"

    def test_negative(self):
        assert _chat_deadline(-0.5) == "reset imminent"

    def test_hours(self):
        result = _chat_deadline(0.5)  # 12 hours
        assert "12h" in result

    def test_hours_with_minutes(self):
        result = _chat_deadline(0.22)  # ~5h 17m
        assert "h" in result

    def test_days(self):
        result = _chat_deadline(3.0)
        assert "3d" in result

    def test_days_with_hours(self):
        result = _chat_deadline(2.5)  # 2d 12h
        assert "2d" in result
        assert "12h" in result


class TestFormatRemaining:
    def test_zero(self):
        assert _format_remaining(0.0) == "0%"

    def test_fraction(self):
        assert _format_remaining(0.5) == "<1%"

    def test_normal(self):
        assert _format_remaining(74.0) == "74%"


# -----------------------------------------------------------------------
# 4. Governing-window warnings
# -----------------------------------------------------------------------


class TestGoverningWarnings:
    """Spec §3: Governing window exhausted, siblings have capacity."""

    def test_warning_on_exhausted_governing(self):
        monthly = _window("monthly", remaining=0.0, resets_hours=168, window_minutes=43200)
        weekly = _window("weekly", remaining=100.0, resets_hours=168, window_minutes=10080)
        five_h = _window("5h", remaining=100.0, resets_hours=5, window_minutes=300)

        rows = [
            _WindowRow("opencode_go", None, five_h, None),
            _WindowRow("opencode_go", None, weekly, None),
            _WindowRow("opencode_go", None, monthly, None),
        ]
        _apply_governing_warnings(rows)

        # Monthly row should have the warning.
        monthly_row = [r for r in rows if r.window.label == "monthly"][0]
        assert monthly_row.governing_warning is not None
        assert "exhausted 'monthly' budget" in monthly_row.governing_warning

        # Shorter windows should NOT have warnings.
        for r in rows:
            if r.window.label != "monthly":
                assert r.governing_warning is None

    def test_no_warning_when_governing_has_capacity(self):
        monthly = _window("monthly", remaining=50.0, resets_hours=168, window_minutes=43200)
        weekly = _window("weekly", remaining=100.0, resets_hours=168, window_minutes=10080)

        rows = [
            _WindowRow("opencode_go", None, weekly, None),
            _WindowRow("opencode_go", None, monthly, None),
        ]
        _apply_governing_warnings(rows)

        for r in rows:
            assert r.governing_warning is None

    def test_no_warning_when_single_window(self):
        monthly = _window("monthly", remaining=0.0, resets_hours=168, window_minutes=43200)
        rows = [_WindowRow("opencode_go", None, monthly, None)]
        _apply_governing_warnings(rows)
        assert rows[0].governing_warning is None

    def test_no_warning_when_all_exhausted(self):
        monthly = _window("monthly", remaining=0.0, resets_hours=168, window_minutes=43200)
        weekly = _window("weekly", remaining=0.0, resets_hours=168, window_minutes=10080)

        rows = [
            _WindowRow("opencode_go", None, weekly, None),
            _WindowRow("opencode_go", None, monthly, None),
        ]
        _apply_governing_warnings(rows)
        for r in rows:
            assert r.governing_warning is None


# -----------------------------------------------------------------------
# 5. Long-cycle highlight selection
# -----------------------------------------------------------------------


class TestLongCycleHighlights:
    """Spec §2: Monthly/long-cycle section is a derived highlight."""

    def test_monthly_window_selected(self):
        monthly = _window("monthly", remaining=50.0, window_minutes=43200)
        row = _WindowRow("provider", None, monthly, None)
        selected = _select_long_cycle_highlights([row], [])
        assert len(selected) == 1

    def test_non_monthly_non_alerted_excluded(self):
        weekly = _window("weekly", remaining=80.0, window_minutes=10080)
        row = _WindowRow("provider", None, weekly, None)
        selected = _select_long_cycle_highlights([row], [])
        assert len(selected) == 0

    def test_weekly_conserve_included(self):
        weekly = _window("weekly", remaining=20.0, window_minutes=10080)
        alert = _alert(kind="conserve", remaining=20.0, urgency=Urgency.HIGH)
        row = _WindowRow("claude", "user@example.com", weekly, alert)
        selected = _select_long_cycle_highlights([row], [alert])
        assert len(selected) == 1

    def test_weekly_with_high_waste_included(self):
        weekly = _window("weekly", remaining=80.0, window_minutes=10080)
        alert = _alert(
            kind="burn",
            remaining=80.0,
            pace_ratio=0.5,
            projected_waste_fraction=0.40,
        )
        row = _WindowRow("claude", "user@example.com", weekly, alert)
        selected = _select_long_cycle_highlights([row], [alert])
        assert len(selected) == 1


# -----------------------------------------------------------------------
# 6. Action items
# -----------------------------------------------------------------------


class TestActionItems:
    """Spec §7: Deterministic action section."""

    def test_conserve_alert(self):
        alerts = [_alert(kind="conserve", remaining=15.0, urgency=Urgency.HIGH)]
        items = _build_action_items(alerts)
        assert len(items) >= 1
        assert "Conserve" in items[0]

    def test_burn_alert(self):
        alerts = [
            _alert(
                kind="burn",
                remaining=80.0,
                urgency=Urgency.MEDIUM,
                pace_ratio=0.5,
            )
        ]
        items = _build_action_items(alerts)
        assert len(items) >= 1
        assert "wasted" in items[0] or "capacity" in items[0]

    def test_max_5_items(self):
        alerts = [
            _alert(
                provider=f"provider_{i}",
                account=f"acct_{i}@example.com",
                kind="conserve",
                remaining=10.0,
                urgency=Urgency.HIGH,
            )
            for i in range(10)
        ]
        items = _build_action_items(alerts)
        assert len(items) <= 5

    def test_dedup_same_pool(self):
        alerts = [
            _alert(window_label="weekly", kind="conserve", remaining=10.0, urgency=Urgency.HIGH),
            _alert(window_label="5h", kind="conserve", remaining=5.0, urgency=Urgency.HIGH),
        ]
        items = _build_action_items(alerts)
        # Same provider+account should be deduped.
        assert len(items) == 1

    def test_routing_action_aliasing(self):
        # Hermes uses 'openai-codex', aiuse account uses 'codex'
        routing = RoutingContext(
            primary_model="gpt-5",
            primary_provider="openai-codex",
        )
        weekly = _window("weekly", remaining=49.0, window_minutes=10080)
        row = _WindowRow("codex", "user@example.com", weekly, None)
        items = _build_action_items([], routing_context=routing, sub_rows=[row])
        assert len(items) >= 1
        assert "Keep Codex as primary" in items[0]
        assert "49%" in items[0]

    def test_prepaid_excluded(self):
        alerts = [
            _alert(kind="prepaid", remaining=50.0, urgency=Urgency.MEDIUM),
        ]
        items = _build_action_items(alerts)
        assert len(items) == 0


# -----------------------------------------------------------------------
# 7. Full renderer
# -----------------------------------------------------------------------


class TestRenderChatReport:
    """Integration tests for the full ``render_chat_report``."""

    def test_no_ansi_codes(self):
        """Spec: no ANSI escape sequences."""
        snap = _snapshot([_account()])
        alerts = [_alert()]
        output = render_chat_report(snap, alerts)
        assert "\033[" not in output
        assert "\x1b[" not in output

    def test_has_header(self):
        snap = _snapshot([_account()])
        output = render_chat_report(snap, [])
        assert "AI USAGE" in output
        assert "🤖" in output

    def test_subscription_section(self):
        snap = _snapshot([_account()])
        alerts = [_alert()]
        output = render_chat_report(snap, alerts)
        assert "SUBSCRIPTION WINDOWS" in output

    def test_prepaid_section(self):
        acc = _account(
            provider="deepseek",
            billing_kind=BillingKind.PREPAID_BALANCE,
            balance_usd=4.03,
            windows=[],
        )
        snap = _snapshot([acc])
        output = render_chat_report(snap, [])
        assert "PREPAID" in output
        assert "$4.03" in output
        assert "no expiry" in output
        assert EMOJI_PREPAID in output

    def test_negative_prepaid_warning(self):
        acc = _account(
            provider="opencode_zen",
            billing_kind=BillingKind.PREPAID_BALANCE,
            balance_usd=-0.04,
            windows=[],
        )
        snap = _snapshot([acc])
        output = render_chat_report(snap, [])
        assert "no expiry" in output
        assert "Negative balance reported; do not treat this as available capacity." in output
        assert "-$0.04" in output
        assert "Balance: -$0.04" in output

    def test_zero_prepaid_balance(self):
        acc = _account(
            provider="opencode_zen",
            billing_kind=BillingKind.PREPAID_BALANCE,
            balance_usd=0.00,
            windows=[],
        )
        snap = _snapshot([acc])
        output = render_chat_report(snap, [])
        assert "empty" in output
        assert "$0.00" not in output
        assert "Negative balance" not in output

    def test_account_email_shown(self):
        """Spec §10: always show account email when known."""
        acc = _account(account="djbclark@gmail.com")
        snap = _snapshot([acc])
        output = render_chat_report(snap, [])
        assert "djbclark@gmail.com" in output

    def test_account_omitted_when_none(self):
        """Spec §10: omit (default) when no real account identity."""
        acc = _account(account=None)
        snap = _snapshot([acc])
        output = render_chat_report(snap, [])
        assert "(default)" not in output

    def test_continuation_markers(self):
        """All sub-lines use ↳ continuation markers."""
        snap = _snapshot([_account()])
        alerts = [_alert(pace_ratio=1.0)]
        output = render_chat_report(snap, alerts)
        # Every non-heading detail line should have ↳
        for line in output.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith(
                ("🤖", "📊", "📅", "💳", "📌", "⚠️", "ℹ️", "━", "🟢", "🟡", "🟠", "🔴", "No ", "")
            ):
                if stripped and not stripped.startswith("**"):
                    assert "↳" in stripped, f"Missing ↳ in detail line: {stripped!r}"

    def test_governing_warning_in_output(self):
        """Governing-budget warning appears in the rendered output."""
        monthly = _window("monthly", remaining=0.0, resets_hours=168, window_minutes=43200)
        weekly = _window("weekly", remaining=100.0, resets_hours=168, window_minutes=10080)
        acc = _account(
            provider="opencode_go",
            account=None,
            windows=[monthly, weekly],
        )
        snap = _snapshot([acc])
        output = render_chat_report(snap, [])
        assert "exhausted 'monthly' budget" in output

    def test_action_section_appears(self):
        alerts = [
            _alert(kind="conserve", remaining=10.0, urgency=Urgency.HIGH),
        ]
        snap = _snapshot([_account()])
        output = render_chat_report(snap, alerts)
        assert "ACTION" in output

    def test_errors_section(self):
        snap = _snapshot(
            accounts=[_account(error="timeout")],
            collector_errors=["cswap: connection refused"],
        )
        output = render_chat_report(snap, [])
        assert "ERRORS" in output
        assert "connection refused" in output

    def test_empty_snapshot(self):
        snap = _snapshot()
        output = render_chat_report(snap, [])
        assert "No usage data collected" in output

    def test_multiple_providers_sorted(self):
        """Worst-first ordering: red before green."""
        red_acc = _account(
            provider="opencode_go",
            account=None,
            windows=[_window("monthly", remaining=5.0, window_minutes=43200)],
        )
        green_acc = _account(
            provider="claude",
            account="user@example.com",
            windows=[_window("weekly", remaining=80.0, window_minutes=10080)],
        )
        snap = _snapshot([green_acc, red_acc])
        output = render_chat_report(snap, [])
        # Red should appear before green in the subscription section.
        red_pos = output.index(EMOJI_RED)
        green_pos = output.index(EMOJI_GREEN)
        assert red_pos < green_pos

    def test_bold_markdown(self):
        """Section headings use **bold** markdown."""
        snap = _snapshot([_account()])
        output = render_chat_report(snap, [])
        assert "**SUBSCRIPTION WINDOWS**" in output

    def test_pace_line_present(self):
        """Pace data from alert is rendered when available."""
        acc = _account()
        alert = _alert(pace_ratio=1.15)
        snap = _snapshot([acc])
        output = render_chat_report(snap, [alert])
        assert "Pace:" in output
        assert "1.15×" in output


# -----------------------------------------------------------------------
# 8. Monthly pacing note
# -----------------------------------------------------------------------


class TestMonthlyPacingNote:
    """Spec §8: Emit when important providers lack monthly data."""

    def test_note_when_weekly_only_in_longcycle(self):
        weekly = _window("weekly", remaining=30.0, window_minutes=10080)
        alert = _alert(kind="conserve", remaining=30.0)
        row = _WindowRow("claude", "user@example.com", weekly, alert)
        note = _monthly_pacing_note([row], [row])
        assert note is not None
        assert "weekly" in note

    def test_no_note_when_monthly_exists(self):
        monthly = _window("monthly", remaining=50.0, window_minutes=43200)
        row = _WindowRow("claude", None, monthly, None)
        note = _monthly_pacing_note([row], [row])
        assert note is None

    def test_no_note_when_no_longcycle(self):
        weekly = _window("weekly", remaining=80.0, window_minutes=10080)
        row = _WindowRow("claude", None, weekly, None)
        note = _monthly_pacing_note([row], [])
        assert note is None
