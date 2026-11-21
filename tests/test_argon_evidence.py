"""Positive evidence that an Antigravity plan has no Argon-own window (schema 1.8 review, finding 3)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aiuse.analysis.argon_evidence import (
    MIN_ABSENT_READINGS,
    MIN_ABSENT_SECONDS,
    account_key,
    argon_hidden,
    has_argon_window,
    record_readings,
    store_path,
)

T0 = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
FLASH = {"label": "Gemini weekly", "used_percent": 5.0, "window_id": "antigravity-quota-summary-gemini-weekly"}
ARGON = {
    "label": "Gemini 4 Argon weekly",
    "used_percent": 5.0,
    "window_id": "antigravity-quota-summary-gemini-argon-wtu-weekly",
}


def _reading(hours: float, *windows: dict, account: str = "acct") -> dict:
    return {
        "provider": "antigravity",
        "account": account,
        "collected_at": (T0 + timedelta(hours=hours)).isoformat(),
        "windows": list(windows),
    }


def _feed(hours: float, *windows: dict, account: str = "acct", hidden: object = None) -> bool:
    now = T0 + timedelta(hours=hours)
    proven = record_readings([_reading(hours, *windows, account=account)], hidden_pools=hidden, now=now)
    return proven[account_key("antigravity", account)]


def test_one_reading_without_argon_proves_nothing():
    assert _feed(0, FLASH) is False


def test_enough_readings_over_enough_time_prove_absence():
    assert MIN_ABSENT_READINGS == 3
    hours = MIN_ABSENT_SECONDS / 3600
    assert _feed(0, FLASH) is False
    assert _feed(hours / 2, FLASH) is False
    assert _feed(hours, FLASH) is True


def test_many_readings_inside_a_short_span_prove_nothing():
    results = [_feed(h / 60, FLASH) for h in range(0, 30, 5)]
    assert not any(results)


def test_a_reading_with_an_argon_window_resets_the_evidence():
    for hours in (0, 4, 8):
        _feed(hours, FLASH)
    assert _feed(8, FLASH) is True
    assert _feed(9, FLASH, ARGON) is False  # the window is back: it was only missing
    assert _feed(10, FLASH) is False  # and a single new absence starts the clock again


def test_an_unreadable_argon_window_still_counts_as_present():
    unreadable = {**ARGON, "used_percent": None}
    for hours in (0, 4, 8):
        assert _feed(hours, FLASH, unreadable) is False


def test_hidden_argon_family_is_never_evidence():
    hidden = {"antigravity/gemini_argon": "operator hides Argon"}
    assert argon_hidden(hidden) is True
    for hours in (0, 4, 8, 12):
        assert _feed(hours, FLASH, hidden=hidden) is False


def test_an_older_reading_does_not_advance_the_evidence():
    for hours in (0, 4, 8):
        _feed(hours, FLASH)
    before = store_path().read_text()
    assert _feed(2, FLASH, account="acct") is True  # state already proves it; the old reading adds nothing
    assert store_path().read_text() == before


def test_accounts_are_judged_independently_and_the_store_holds_no_account_name():
    for hours in (0, 4, 8):
        _feed(hours, FLASH, account="ultra-user@example.test")
    assert _feed(8, FLASH, ARGON, account="pro-user@example.test") is False
    text = store_path().read_text()
    assert "ultra-user" not in text
    assert "pro-user" not in text
    assert len(json.loads(text)["accounts"]) == 2


def test_missing_or_corrupt_store_reads_as_no_evidence():
    store_path().write_text("{not json")
    assert _feed(0, FLASH) is False


def test_has_argon_window_reads_label_id_and_family():
    assert has_argon_window([ARGON])
    assert has_argon_window([{"label": "x", "pool_family": "gemini_argon"}])
    assert not has_argon_window([FLASH])
