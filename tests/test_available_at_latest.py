"""available_at is when the WHOLE pool is usable again: the latest depleted reset.

2026-11-21: OpenCode Go read `available_at` 2026-11-23 (the weekly window's
reset) while its monthly window, also depleted, stayed out until 2026-11-27.
Every depleted window (exhausted, or under NEAR_EMPTY_BELOW left) must reset
before the pool works again, so the earliest reset named a time at which the
pool was still empty. The latest one binds.
"""

from __future__ import annotations

from datetime import timedelta

from aiuse.analysis.selfdescribe import enrich_snapshot, pool_entries
from aiuse.models import parse_dt, utcnow

NOW = utcnow()


def _window(label: str, remaining: float, days: float, minutes: int) -> dict:
    return {
        "label": label,
        "used_percent": 100.0 - remaining,
        "remaining_percent": remaining,
        "resets_at": (NOW + timedelta(days=days)).isoformat(),
        "window_minutes": minutes,
    }


def _opencode(*windows: dict) -> dict:
    return {
        "collected_at": NOW.isoformat(),
        "accounts": [
            {
                "provider": "opencode-go",
                "account": "t-e",
                "source": "opencode_go",
                "billing_kind": "subscription_window",
                "collected_at": NOW.isoformat(),
                "windows": list(windows),
            }
        ],
    }


def _run(snap: dict) -> tuple[dict, dict]:
    enriched = enrich_snapshot(snap, now=NOW)
    (entry,) = pool_entries(enriched, now=NOW)
    return enriched["accounts"][0], entry


def _same_instant(a: str | None, b: str) -> bool:
    return a is not None and parse_dt(a) == parse_dt(b)


def test_near_empty_weekly_and_exhausted_monthly_wait_for_the_monthly_reset():
    weekly = _window("Weekly", 2.0, 2, 10080)
    monthly = _window("Monthly", 0.0, 6, 43200)
    account, entry = _run(_opencode(weekly, monthly))
    assert entry["usable_now"] is False
    assert _same_instant(entry["available_at"], monthly["resets_at"])
    assert _same_instant(account["available_at"], monthly["resets_at"])


def test_window_order_does_not_change_the_latest_reset():
    weekly = _window("Weekly", 0.0, 2, 10080)
    monthly = _window("Monthly", 0.0, 6, 43200)
    for ordered in ((weekly, monthly), (monthly, weekly)):
        account, entry = _run(_opencode(*[dict(w) for w in ordered]))
        assert _same_instant(entry["available_at"], monthly["resets_at"])
        assert _same_instant(account["available_at"], monthly["resets_at"])


def test_a_healthy_window_with_a_later_reset_does_not_extend_the_wait():
    weekly = _window("Weekly", 1.0, 2, 10080)
    monthly = _window("Monthly", 60.0, 6, 43200)
    account, entry = _run(_opencode(weekly, monthly))
    assert _same_instant(entry["available_at"], weekly["resets_at"])
    assert _same_instant(account["available_at"], weekly["resets_at"])


def test_single_depleted_window_keeps_its_own_reset():
    weekly = _window("Weekly", 3.0, 2, 10080)
    monthly = _window("Monthly", 40.0, 6, 43200)
    account, entry = _run(_opencode(weekly, monthly))
    assert entry["usable_now"] is False
    assert _same_instant(entry["available_at"], weekly["resets_at"])


def test_nothing_depleted_has_no_available_at():
    account, entry = _run(_opencode(_window("Weekly", 50.0, 2, 10080), _window("Monthly", 40.0, 6, 43200)))
    assert entry["usable_now"] is True
    assert entry["available_at"] is None
    assert account["available_at"] is None


def test_depleted_window_without_a_reset_time_is_ignored_for_the_latest():
    weekly = _window("Weekly", 0.0, 2, 10080)
    monthly = _window("Monthly", 0.0, 6, 43200)
    monthly.pop("resets_at")
    _, entry = _run(_opencode(weekly, monthly))
    assert _same_instant(entry["available_at"], weekly["resets_at"])
