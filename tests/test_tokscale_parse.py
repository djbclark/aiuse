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
    assert keep_copilot_report_window("GitHub Copilot weekly quota")
    assert keep_copilot_report_window("GitHub Copilot session quota")
    assert not keep_copilot_report_window("GitHub Copilot completions")
    assert not keep_copilot_report_window("GitHub Copilot chat messages")


def test_infer_window_minutes_copilot_windows():
    assert _infer_window_minutes("copilot", "premium", "Premium") == WINDOW_NOMINAL_MINUTES["monthly"]
    assert _infer_window_minutes("copilot", "weekly", "Weekly") == WINDOW_NOMINAL_MINUTES["weekly"]
    assert _infer_window_minutes("copilot", "7-day", "7-day") == WINDOW_NOMINAL_MINUTES["weekly"]
    assert _infer_window_minutes("copilot", "session", "Session") == WINDOW_NOMINAL_MINUTES["5h"]


def test_tokscale_copilot_keeps_weekly_and_session_drops_completions():
    acc = _from_row(
        {
            "provider": "Copilot",
            "plan": "Individual",
            "metrics": [
                {"label": "Chat", "used_percent": 36, "remaining_percent": 64, "resets_at": "2026-08-01"},
                {"label": "Completions", "used_percent": 0, "remaining_percent": 100, "resets_at": "2026-08-01"},
                {"label": "Premium", "used_percent": 100, "remaining_percent": 0, "resets_at": "2026-08-01"},
                {"label": "Weekly", "used_percent": 50, "remaining_percent": 50, "resets_at": "2026-08-08"},
                {"label": "Session", "used_percent": 10, "remaining_percent": 90, "resets_at": "2026-08-01T15:00:00Z"},
            ],
        }
    )
    labels = [w.label for w in acc.windows]
    assert labels == [
        "GitHub Copilot premium requests",
        "GitHub Copilot weekly quota",
        "GitHub Copilot session quota",
    ]
    assert acc.windows[1].window_minutes == WINDOW_NOMINAL_MINUTES["weekly"]
    assert acc.windows[2].window_minutes == WINDOW_NOMINAL_MINUTES["5h"]


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
