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


CLAUDE_GPT = {
    "label": "Claude/GPT weekly",
    "used_percent": 3.0,
    "window_id": "antigravity-quota-summary-claude-gpt-weekly",
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


def _cached_snapshot(hours: float, *windows: dict, account: str = "acct") -> dict:
    return {
        "collected_at": (T0 + timedelta(hours=hours)).isoformat(),
        "accounts": [_reading(hours, *windows, account=account)],
    }


def test_rereading_one_future_stamped_snapshot_never_advances_the_evidence():
    """Review round 2, important B: a snapshot stamped 1 day ahead proved absence on its third re-read."""
    from aiuse.analysis.argon_evidence import proven_absent

    future = _reading(24, FLASH)
    key = account_key("antigravity", "acct")
    results = []
    for hours in (0, 3, MIN_ABSENT_SECONDS / 3600 + 0.1):
        now = T0 + timedelta(hours=hours)
        results.append(record_readings([future], now=now)[key])
        results.append(proven_absent([future], now=now)[key])
    assert results == [False] * 6
    assert not store_path().exists()  # a reading from the future is skipped, not clamped and recorded


def test_the_read_path_never_writes_the_store_or_creates_its_directory(tmp_path, monkeypatch):
    from aiuse.analysis.selfdescribe import enrich_snapshot

    target = tmp_path / "not-yet" / "state" / "argon-evidence.json"
    monkeypatch.setenv("AIUSE_ARGON_EVIDENCE_STORE", str(target))
    for hours in (0, 4, 8):
        enrich_snapshot(_cached_snapshot(hours, FLASH), now=T0 + timedelta(hours=hours))
    assert not target.parent.exists()


def test_enrich_snapshot_on_a_read_path_leaves_the_store_byte_identical():
    from aiuse.analysis.selfdescribe import enrich_snapshot

    record_readings([_reading(0, FLASH)], now=T0)
    before = store_path().read_bytes()
    for hours in (4, 8, 12):
        enrich_snapshot(_cached_snapshot(hours, FLASH), now=T0 + timedelta(hours=hours))
    assert store_path().read_bytes() == before


def test_a_read_path_looks_the_proof_up_but_cannot_build_it():
    from aiuse.analysis.argon_evidence import proven_absent

    key = account_key("antigravity", "acct")
    for hours in (0, 4):
        record_readings([_reading(hours, FLASH)], now=T0 + timedelta(hours=hours))
    for hours in (5, 8, 20):  # many cached re-reads never make the third reading
        assert proven_absent([_reading(hours, FLASH)], now=T0 + timedelta(hours=hours))[key] is False
    record_readings([_reading(8, FLASH)], now=T0 + timedelta(hours=8))
    assert proven_absent([_reading(9, FLASH)], now=T0 + timedelta(hours=9))[key] is True
    assert proven_absent([_reading(9, FLASH, ARGON)], now=T0 + timedelta(hours=9))[key] is False


def test_the_collection_path_records_and_a_read_path_then_sees_the_proof():
    from aiuse.analysis.argon_evidence import proven_absent
    from aiuse.analysis.selfdescribe import enrich_snapshot

    key = account_key("antigravity", "acct")
    seen = []
    for hours in (0, 4, 8):
        now = T0 + timedelta(hours=hours)
        snap = _cached_snapshot(hours, FLASH)
        enrich_snapshot(snap, now=now, record_argon_evidence=True)
        seen.append(proven_absent(snap["accounts"], now=now)[key])
    assert seen == [False, False, True]


def test_save_snapshot_is_the_collection_path_that_writes_the_store():
    from aiuse.analysis.argon_evidence import proven_absent
    from aiuse.analysis.history import save_snapshot
    from aiuse.models import AccountUsage, QuotaWindow, Snapshot, utcnow

    now = utcnow()
    key = account_key("antigravity", "acct")
    assert not store_path().exists()
    for ago in (8.0, 4.0, 0.0):
        row = AccountUsage(
            source="codexbar",
            provider="antigravity",
            account="acct",
            windows=[QuotaWindow(label="Gemini weekly", used_percent=5.0, remaining_percent=95.0)],
        )
        save_snapshot(Snapshot(collected_at=now - timedelta(hours=ago), accounts=[row]), [])
    reading = _reading(0, FLASH)
    reading["collected_at"] = now.isoformat()
    assert proven_absent([reading], now=now)[key] is True


def test_a_partial_reading_without_a_shared_gemini_window_is_not_evidence_of_absence():
    """Review round 2, nit 1: readings holding only the Claude/GPT window proved absence."""
    for hours in (0, 4, 8):
        assert _feed(hours, CLAUDE_GPT) is False
    assert not store_path().exists()  # nothing was recorded, so nothing can later count
    assert _feed(9, FLASH) is False  # the first reading that shows the Flash window starts the clock


def test_partial_readings_do_not_extend_or_reset_real_evidence():
    for hours in (0, 4):
        _feed(hours, FLASH)
    before = store_path().read_text()
    assert _feed(7, CLAUDE_GPT) is False  # this reading says nothing about Gemini
    assert store_path().read_text() == before
    assert _feed(8, FLASH) is True  # and the third real reading still proves it


def test_has_gemini_window_reads_label_id_and_family():
    from aiuse.analysis.argon_evidence import has_gemini_window

    assert has_gemini_window([FLASH])
    assert has_gemini_window([{"label": "x", "pool_family": "gemini"}])
    assert not has_gemini_window([CLAUDE_GPT])
    assert not has_gemini_window([ARGON])
