"""Controlled interleavings for shared grant persistence; never calls a live API."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, current_thread

import pytest

from aiuse import credits_override as co

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


@pytest.fixture
def credit_files(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "overrides_path", lambda: tmp_path / "credit-overrides.json")
    return tmp_path


def entry(remaining=70, observed=NOW, **changes):
    return {
        "total_usd": 100,
        "remaining_usd": remaining,
        "granted_at": (NOW - timedelta(days=2)).isoformat(),
        "observed_at": observed.isoformat(),
        "expires_at": None,
        **changes,
    }


@pytest.mark.parametrize("provider", ["omp", "pi"])
@pytest.mark.parametrize("delta", [0, 1])
def test_old_or_equal_writer_finishing_last_keeps_accepted_entry(credit_files, provider, delta):
    older_ready, newer_saved = Event(), Event()
    accepted_at = NOW + timedelta(minutes=delta)

    def older():
        value = entry(80)
        older_ready.set()
        assert newer_saved.wait(5)
        return co.save_credit_entry(co.readings_file(), provider, value, "admin-api")

    def newer():
        assert older_ready.wait(5)
        try:
            return co.save_credit_entry(co.readings_file(), provider, entry(60, accepted_at), "admin-api")
        finally:
            newer_saved.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(older), pool.submit(newer)
        assert b.result(timeout=10)["remaining_usd"] == 60
        assert a.result(timeout=10)["remaining_usd"] == 60
    stored = co.load_file_entry(co.readings_file(), "admin-api", provider)
    assert stored["observed_at"] == accepted_at.isoformat()


@pytest.mark.parametrize("provider", ["omp", "pi"])
def test_newer_same_grant_cost_regression_is_capped(credit_files, provider):
    co.save_credit_entry(co.readings_file(), provider, entry(40), "admin-api")
    saved = co.save_credit_entry(co.readings_file(), provider, entry(70, NOW + timedelta(minutes=1)), "admin-api")
    assert saved["remaining_usd"] == 40
    assert saved["observed_at"] == (NOW + timedelta(minutes=1)).isoformat()


@pytest.mark.parametrize("provider,expected", [("omp", 70), ("pi", 40)])
def test_legacy_purchased_credit_calibration_is_omp_only(credit_files, provider, expected):
    co.save_credit_entry(co.readings_file(), provider, entry(40), "admin-api")
    saved = co.save_credit_entry(
        co.readings_file(),
        provider,
        entry(70, NOW + timedelta(minutes=1)),
        "admin-api",
        allow_calibration=True,
    )
    assert saved["remaining_usd"] == expected


def test_sibling_writer_waits_for_locked_read_merge_replace(credit_files, monkeypatch):
    inside_read, second_started, second_inside = Event(), Event(), Event()
    original = co._load_file

    def paused_read(path, source):
        data = original(path, source)
        if current_thread().name.startswith("first"):
            inside_read.set()
            assert second_started.wait(5)
            assert not second_inside.wait(0.1), "sibling entered read/merge before lock release"
        elif current_thread().name.startswith("second"):
            second_inside.set()
        return data

    monkeypatch.setattr(co, "_load_file", paused_read)

    def second():
        assert inside_read.wait(5)
        second_started.set()
        return co.save_credit_entry(co.readings_file(), "pi", entry(50), "admin-api")

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first") as first_pool:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="second") as second_pool:
            a = first_pool.submit(co.save_credit_entry, co.readings_file(), "omp", entry(30), "admin-api")
            b = second_pool.submit(second)
            a.result(timeout=10)
            b.result(timeout=10)
    data = json.loads(co.readings_file().read_text())
    assert {k: v["remaining_usd"] for k, v in data.items()} == {"omp": 30, "pi": 50}


@pytest.mark.parametrize("delta", [-1, 0])
def test_older_or_equal_operator_anchor_cannot_replace_state(credit_files, delta):
    original = co.save_credit_entry(co.overrides_path(), "pi", entry(30), co.OVERRIDE_SOURCE)
    result = co.save_credit_entry(
        co.overrides_path(), "pi", entry(90, NOW + timedelta(seconds=delta)), co.OVERRIDE_SOURCE
    )
    assert result == original


def test_newer_operator_anchor_can_restore_credit_even_for_same_grant(credit_files):
    anchor = co.save_credit_entry(co.overrides_path(), "pi", entry(), co.OVERRIDE_SOURCE)
    co.save_credit_entry(
        co.readings_file(), "pi", entry(20, anchor_observed_at=anchor["observed_at"]), "admin-api", anchor=anchor
    )
    assert co.load_overrides(now=NOW)["pi"]["remaining_usd"] == 20
    later = NOW + timedelta(minutes=1)
    replacement = co.save_credit_entry(co.overrides_path(), "pi", entry(80, later), co.OVERRIDE_SOURCE)
    saved = co.save_credit_entry(
        co.readings_file(),
        "pi",
        entry(75, later + timedelta(minutes=1), anchor_observed_at=replacement["observed_at"]),
        "admin-api",
        anchor=replacement,
    )
    assert saved["remaining_usd"] == 75
    assert co.load_overrides(now=later + timedelta(minutes=1))["pi"]["remaining_usd"] == 75


def test_superseded_anchor_is_not_selected_by_later_api_observation(credit_files):
    anchor = co.save_credit_entry(co.overrides_path(), "pi", entry(), co.OVERRIDE_SOURCE)
    co.save_credit_entry(
        co.readings_file(),
        "pi",
        entry(20, NOW + timedelta(minutes=2), anchor_observed_at=anchor["observed_at"]),
        "admin-api",
        anchor=anchor,
    )
    co.save_credit_entry(co.overrides_path(), "pi", entry(80, NOW + timedelta(minutes=1)), co.OVERRIDE_SOURCE)
    assert co.load_overrides(now=NOW + timedelta(minutes=2))["pi"]["remaining_usd"] == 80


def test_stale_anchor_cannot_overwrite_replacement_in_flight(credit_files):
    anchor = co.save_credit_entry(co.overrides_path(), "pi", entry(), co.OVERRIDE_SOURCE)
    co.save_credit_entry(co.overrides_path(), "pi", entry(90, NOW + timedelta(minutes=1)), co.OVERRIDE_SOURCE)
    with pytest.raises(ValueError, match="anchor changed"):
        co.save_credit_entry(
            co.readings_file(),
            "pi",
            entry(20, NOW + timedelta(minutes=2), anchor_observed_at=anchor["observed_at"]),
            "admin-api",
            anchor=anchor,
        )
    assert not co.readings_file().exists()


def test_oversized_serialization_preserves_both_providers(credit_files):
    for provider in ("omp", "pi"):
        co.save_credit_entry(co.readings_file(), provider, entry(), "admin-api")
    before = co.readings_file().read_bytes()
    with pytest.raises(ValueError, match="64 KiB"):
        co.save_credit_entry(
            co.readings_file(),
            "pi",
            entry(60, NOW + timedelta(minutes=1), note="x" * co.MAX_FILE_BYTES),
            "admin-api",
        )
    assert co.readings_file().read_bytes() == before


@pytest.mark.parametrize("expiry", [None, (NOW + timedelta(days=60)).isoformat()])
def test_new_anchor_replaces_grant_metadata_and_expiry(credit_files, expiry):
    old_expiry = (NOW + timedelta(days=1)).isoformat()
    anchor = co.save_credit_entry(co.overrides_path(), "pi", entry(expires_at=old_expiry), co.OVERRIDE_SOURCE)
    co.save_credit_entry(
        co.readings_file(),
        "pi",
        entry(20, expires_at=old_expiry, anchor_observed_at=anchor["observed_at"]),
        "admin-api",
        anchor=anchor,
    )
    later = NOW + timedelta(minutes=1)
    replacement = co.save_credit_entry(
        co.overrides_path(),
        "pi",
        entry(150, later, total_usd=200, granted_at=later.isoformat(), expires_at=expiry),
        co.OVERRIDE_SOURCE,
    )
    saved = co.save_credit_entry(
        co.readings_file(),
        "pi",
        {**replacement, "remaining_usd": 140, "anchor_observed_at": replacement["observed_at"]},
        "admin-api",
        anchor=replacement,
    )
    assert saved["remaining_usd"] == 140
    assert saved["expires_at"] == expiry
    assert saved["granted_at"] == later.isoformat()


def test_omitted_grant_start_inherits_current_operator_metadata(credit_files):
    original = co.save_override(
        "pi", remaining=70, total=100, expires=None, note=None, now=NOW, granted=NOW - timedelta(days=2)
    )
    saved = co.save_override("pi", remaining=60, total=100, expires=None, note=None, now=NOW + timedelta(minutes=1))
    assert saved["granted_at"] == original["granted_at"]
