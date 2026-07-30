"""Tests for tokscale window inference cleanups (Step 27)."""

from aiuse.collectors.tokscale import _from_row, _infer_window_minutes
from aiuse.models import WINDOW_NOMINAL_MINUTES, keep_copilot_report_window


def test_premium_label_monthly_only_for_copilot():
    assert _infer_window_minutes("copilot", "premium", "Premium") == WINDOW_NOMINAL_MINUTES["monthly"]
    assert _infer_window_minutes("grok", "premium", "Premium") is None


def test_digit_7_in_label_does_not_force_weekly():
    assert _infer_window_minutes("grok", "usage", "Grok7 usage") is None


def test_5h_uses_nominal_300_not_bucket_max():
    assert _infer_window_minutes("claude", "session", "Session") == 300
    assert _infer_window_minutes("claude", "5h", "5-hour") == WINDOW_NOMINAL_MINUTES["5h"]


def test_15h_label_does_not_match_5h_bucket():
    assert _infer_window_minutes("claude", "15h window", "15h window") is None


def test_keep_copilot_report_window_premium_only():
    assert keep_copilot_report_window("GitHub Copilot premium requests")
    assert not keep_copilot_report_window("GitHub Copilot completions")
    assert not keep_copilot_report_window("GitHub Copilot chat messages")


def test_tokscale_copilot_drops_completions_and_chat():
    acc = _from_row(
        {
            "provider": "Copilot",
            "plan": "Individual",
            "metrics": [
                {"label": "Chat", "used_percent": 36, "remaining_percent": 64, "resets_at": "2026-08-01"},
                {"label": "Completions", "used_percent": 0, "remaining_percent": 100, "resets_at": "2026-08-01"},
                {"label": "Premium", "used_percent": 100, "remaining_percent": 0, "resets_at": "2026-08-01"},
            ],
        }
    )
    assert [w.label for w in acc.windows] == ["GitHub Copilot premium requests"]
    assert acc.windows[0].remaining_percent == 0


def test_date_only_reset_is_marked_as_an_estimate():
    acc = _from_row(
        {
            "provider": "Copilot",
            "metrics": [
                {"label": "Premium", "used_percent": 10, "resets_at": "2026-08-01"},
            ],
        }
    )

    assert not acc.windows[0].reset_time_is_precise()
