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
    old = _reading(2, FLASH)  # collected 6 h before the stored record, seen again at hour 8
    proven = record_readings([old], now=T0 + timedelta(hours=8))
    assert proven[account_key("antigravity", "acct")] is True  # state already proves it; the old reading adds nothing
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


_CORRUPT_RECORD_FIELDS = [
    ("readings_absent", '"x"'),
    ("readings_absent", "[1]"),
    ("readings_absent", '{"a": 1}'),
    ("readings_absent", "NaN"),
    ("readings_absent", "Infinity"),
    ("readings_absent", "-Infinity"),
    ("readings_absent", "1e400"),
    ("readings_absent", "true"),
    ("readings_absent", "-4"),
    ("readings_absent", "2.5"),
    ("last_reading", "NaN"),
    ("last_reading", "Infinity"),
    ("last_reading", "[]"),
    ("last_reading", '{"a": 1}'),
    ("last_reading", '"not a date"'),
    ("last_reading", "12345"),
    ("last_reading", '"0001-01-01T00:00:00+05:00"'),
    ("last_reading", '"9999-12-31T23:59:59+00:00"'),
    ("absent_since", "NaN"),
    ("absent_since", "[1, 2]"),
    ("absent_since", '"0001-01-01T00:00:00+05:00"'),
    ("absent_since", '"9999-12-31T23:59:59+00:00"'),
    ("argon_last_seen", "Infinity"),
    ("argon_last_seen", '"9999-12-31T23:59:59+00:00"'),
]


def _corrupt_store(field: str, raw: str) -> None:
    key = account_key("antigravity", "acct")
    last = (T0 - timedelta(hours=1)).isoformat()
    fields = {
        "absent_since": f'"{(T0 - timedelta(hours=9)).isoformat()}"',
        "last_reading": f'"{last}"',
        "readings_absent": "5",
        "argon_last_seen": "null",
    }
    fields[field] = raw
    body = ", ".join(f'"{name}": {value}' for name, value in fields.items())
    store_path().write_text('{"schema_version": 1, "accounts": {"' + key + '": {' + body + "}}}")


def test_a_corrupt_record_field_resets_that_record_and_never_raises():
    import pytest

    for field, raw in _CORRUPT_RECORD_FIELDS:
        _corrupt_store(field, raw)
        try:
            proven = _feed(0, FLASH)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{field}={raw} raised {exc!r}")
        assert proven is False, f"{field}={raw} must read as no evidence"
        saved = json.loads(store_path().read_text())["accounts"][account_key("antigravity", "acct")]
        assert saved["readings_absent"] == 1, f"{field}={raw}: the record restarts from this reading"


def test_corrupt_records_do_not_hide_the_good_ones():
    good = account_key("antigravity", "good")
    bad = account_key("antigravity", "bad")
    store_path().write_text(
        json.dumps(
            {
                "schema_version": 1,
                "accounts": {
                    good: {
                        "absent_since": (T0 - timedelta(hours=9)).isoformat(),
                        "last_reading": (T0 - timedelta(hours=1)).isoformat(),
                        "readings_absent": 5,
                    },
                    bad: {"readings_absent": "x", "last_reading": "y"},
                },
            }
        )
    )
    assert _feed(0, FLASH, account="good") is True
    assert _feed(0, FLASH, account="bad") is False


def test_a_store_of_any_shape_never_raises():
    shapes = [
        "[]",
        "null",
        "7",
        '"text"',
        "{}",
        '{"accounts": []}',
        '{"accounts": {"k": []}}',
        '{"accounts": {"k": 5}}',
        '{"accounts": {"k": {"last_reading": NaN}}}',
        "[" * 100000,
        "\x00\xff garbage",
    ]
    for text in shapes:
        store_path().write_text(text, encoding="utf-8", errors="replace")
        assert _feed(0, FLASH) is False


def test_a_non_finite_or_unparsable_reading_time_records_nothing(tmp_path):
    reading = _reading(0, FLASH)
    for bad in ("not a date", "0001-01-01T00:00:00+05:00", None, 7, float("nan"), float("inf")):
        reading["collected_at"] = bad
        proven = record_readings([reading], now=T0)
        assert proven[account_key("antigravity", "acct")] is False
    assert not store_path().exists()


def test_the_store_is_created_private_from_the_start_and_no_temp_file_is_left(monkeypatch):
    import os

    modes: list[int] = []
    real_replace = os.replace

    def spy(src, dst):
        modes.append(os.stat(src).st_mode & 0o777)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    _feed(0, FLASH)
    assert modes == [0o600]
    assert os.stat(store_path()).st_mode & 0o777 == 0o600


def test_a_failed_write_unlinks_its_temp_file():
    store_path().mkdir()  # os.replace onto a directory fails
    assert _feed(0, FLASH) is False
    assert [p.name for p in store_path().parent.iterdir() if p.name.endswith(".tmp")] == []
