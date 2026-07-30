"""Tests for styled report builders and CLI display gate."""

from __future__ import annotations

from datetime import timedelta

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.tui import should_use_tui, textual_available
from aiuse.tui.builders import alert_headline, build_report_sections


def _snap_with_account() -> Snapshot:
    return Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="Codex weekly quota",
                        used_percent=10,
                        remaining_percent=90,
                        resets_at=utcnow() + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )


def _burn_alert() -> UseOrLoseAlert:
    return UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="codex",
        account="user@example.com",
        window_label="Codex weekly quota",
        remaining_percent=90.0,
        days_until_reset=3.0,
        plan=None,
        message="burn it",
        source="codexbar",
        score=80.0,
        kind="burn",
    )


def test_build_report_sections_default_is_glance_first():
    sections = build_report_sections(_snap_with_account(), [_burn_alert()], full=False)
    kinds = [s.kind for s in sections]
    assert "providers" not in kinds
    assert "plan" not in kinds
    assert "plan-glance" in kinds
    assert "footer" in kinds
    assert kinds[-2] == "plan-glance"
    assert any("Detail: ai --full" in line for s in sections for line in s.lines)


def test_build_report_sections_full_includes_providers():
    sections = build_report_sections(_snap_with_account(), [_burn_alert()], full=True)
    kinds = [s.kind for s in sections]
    assert "header" in kinds
    assert "providers" in kinds
    assert "tips" in kinds
    assert "plan" in kinds
    assert "footer" not in kinds
    assert any(s.title == "History" for s in sections)
    plan = next(s for s in sections if s.kind == "plan")
    assert any("Codex" in line for line in plan.lines)


def test_build_report_sections_brief_aliases_default():
    default = build_report_sections(_snap_with_account(), [_burn_alert()])
    brief = build_report_sections(_snap_with_account(), [_burn_alert()], brief=True)
    assert [s.kind for s in default] == [s.kind for s in brief]


def test_build_report_sections_includes_collector_errors():
    snap = _snap_with_account()
    snap.collector_errors.append("tokscale: timeout")
    sections = build_report_sections(snap, [], full=False)
    errors = next(s for s in sections if s.kind == "errors")
    assert any("tokscale" in line for line in errors.lines)


def test_alert_headline_contains_provider_and_percent():
    line = alert_headline(_burn_alert())
    assert "Codex" in line
    assert "90%" in line


def test_should_use_tui_false_for_json_and_no_tui(monkeypatch):
    class TTY:
        def isatty(self) -> bool:
            return True

    assert should_use_tui(as_json=True, stream=TTY()) is False
    assert should_use_tui(alerts_only=True, stream=TTY()) is False
    assert should_use_tui(no_tui=True, stream=TTY()) is False


def test_should_use_tui_false_when_not_tty():
    class Pipe:
        def isatty(self) -> bool:
            return False

    assert should_use_tui(stream=Pipe()) is False


def test_should_use_tui_true_on_tty_when_rich_present():
    class TTY:
        def isatty(self) -> bool:
            return True

    if not textual_available():
        return
    assert should_use_tui(stream=TTY()) is True


def test_should_use_tui_honors_force_color_when_piped(monkeypatch):
    """FORCE_COLOR should opt a piped/non-tty stream into the styled report.

    Mirrors Rich's own ``Console.is_terminal`` behavior so capturing a demo
    or redirecting output doesn't silently downgrade to the plain-text
    fallback despite the operator explicitly asking for color.
    """

    class Pipe:
        def isatty(self) -> bool:
            return False

    if not textual_available():
        return
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert should_use_tui(stream=Pipe()) is True


def test_should_use_tui_honors_tty_compatible_override(monkeypatch):
    class TTY:
        def isatty(self) -> bool:
            return True

    class Pipe:
        def isatty(self) -> bool:
            return False

    if not textual_available():
        return
    monkeypatch.setenv("TTY_COMPATIBLE", "1")
    assert should_use_tui(stream=Pipe()) is True

    monkeypatch.setenv("TTY_COMPATIBLE", "0")
    assert should_use_tui(stream=TTY()) is False


def test_run_usage_app_default_prints_priority_ladder(capsys):
    from aiuse.tui.app import run_usage_app

    run_usage_app(_snap_with_account(), [_burn_alert()], full=False, quiet=True)
    out = capsys.readouterr().out
    assert "use" in out
    assert "Codex" in out
    assert "Per-provider usage" not in out
    assert "Detail: ai --full" not in out


def test_run_usage_app_full_includes_providers(capsys):
    from aiuse.tui.app import run_usage_app

    run_usage_app(_snap_with_account(), [_burn_alert()], full=True)
    out = capsys.readouterr().out
    assert "Per-provider usage" in out
    assert "Detail: ai --full" not in out
