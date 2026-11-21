"""refreshable_at follows the throttle state, not the reading time (schema 1.8 review, finding 4).

A reading's own time (CodexBar's updatedAt) can lag the query that produced it
by 15-30 minutes, and the gate stretches its interval after repeated empty
answers and blocks a provider that timed out. The field must say what a --live
run would actually be allowed to do.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aiuse.analysis.selfdescribe import enrich_snapshot, pool_entries
from aiuse.collectors.throttle import next_query_at, throttle_dir
from aiuse.models import parse_dt

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
BY_PROVIDER = {"antigravity": 600.0}


def _snap(age: timedelta) -> dict:
    return {
        "collected_at": NOW.isoformat(),
        "accounts": [
            {
                "provider": "antigravity",
                "account": None,
                "source": "codexbar",
                "billing_kind": "subscription_window",
                "cli_binary": "agy",
                "collected_at": (NOW - age).isoformat(),
                "windows": [
                    {
                        "label": "Gemini weekly",
                        "used_percent": 30.0,
                        "remaining_percent": 70.0,
                        "resets_at": (NOW + timedelta(days=4)).isoformat(),
                        "window_minutes": 10080,
                    }
                ],
            }
        ],
    }


def _state(**fields: object) -> None:
    directory = throttle_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "antigravity.json").write_text(json.dumps(fields))


def _timeout(*, ago: timedelta, count: int = 1) -> None:
    directory = throttle_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "codexbar-timeouts.json").write_text(
        json.dumps({"antigravity": {"timed_out_at": (NOW - ago).isoformat(), "count": count, "timeout": 30.0}})
    )


def _run(age: timedelta, config: dict | None = None) -> dict:
    cfg = {"analysis": {"fresh_threshold_seconds_by_provider": BY_PROVIDER}, **(config or {})}
    return enrich_snapshot(_snap(age), now=NOW, config=cfg)["accounts"][0]


def test_without_throttle_state_it_falls_back_to_reading_time_plus_interval():
    account = _run(timedelta(seconds=700))
    assert account["refresh_blocked_by_gate"] is True
    assert account["gate_remaining_seconds"] == 200.0
    assert parse_dt(account["refreshable_at"]) == NOW + timedelta(seconds=200)


def test_a_query_newer_than_the_reading_keeps_the_gate_closed():
    """The CodexBar row is 40 minutes old but aiuse queried 2 minutes ago: --live would be refused."""
    _state(queried_at=(NOW - timedelta(minutes=2)).isoformat())
    account = _run(timedelta(minutes=40))
    assert account["stale"] is True
    assert account["refresh_blocked_by_gate"] is True
    assert account["gate_remaining_seconds"] == 780.0
    assert parse_dt(account["refreshable_at"]) == NOW + timedelta(seconds=780)
    (entry,) = pool_entries(enrich_snapshot(_snap(timedelta(minutes=40)), now=NOW, config={}))
    assert entry["refresh_blocked_by_gate"] is True


def test_empty_answer_backoff_stretches_the_interval():
    """Three empty answers in a row stretch 900 s to 3600 s: the gate is closed for 40 more minutes."""
    _state(queried_at=(NOW - timedelta(minutes=20)).isoformat(), empty_streak=3)
    account = _run(timedelta(minutes=40))
    assert account["refresh_blocked_by_gate"] is True
    assert account["gate_remaining_seconds"] == 2400.0
    assert parse_dt(account["refreshable_at"]) == NOW + timedelta(seconds=2400)


def test_timeout_backoff_blocks_a_refresh_the_interval_alone_would_allow():
    _state(queried_at=(NOW - timedelta(minutes=40)).isoformat())
    _timeout(ago=timedelta(minutes=5))
    account = _run(timedelta(minutes=40))
    assert account["refresh_blocked_by_gate"] is True
    assert account["gate_remaining_seconds"] == 1500.0  # 1800 s backoff, 5 minutes in


def test_timeout_backoff_follows_the_configured_base_and_can_be_turned_off():
    _state(queried_at=(NOW - timedelta(minutes=40)).isoformat())
    _timeout(ago=timedelta(minutes=5))
    off = _run(timedelta(minutes=40), {"collectors": {"codexbar": {"timeout_backoff": 0}}})
    assert off["refresh_blocked_by_gate"] is False
    short = _run(timedelta(minutes=40), {"collectors": {"codexbar": {"timeout_backoff": 240}}})
    assert short["refresh_blocked_by_gate"] is False  # a 4 minute backoff ended a minute ago


def test_next_query_at_reports_none_without_any_state_and_the_later_of_gate_and_backoff():
    assert next_query_at("antigravity", min_interval=900.0, timeout_backoff=1800.0) is None
    _state(queried_at=(NOW - timedelta(minutes=2)).isoformat())
    gate_only = next_query_at("antigravity", min_interval=900.0)
    assert gate_only == NOW - timedelta(minutes=2) + timedelta(seconds=900)


# ── absurd query_min_interval values (finding 5) ────────────────────────────


def test_an_infinite_or_huge_interval_does_not_crash_enrichment():
    for value in (float("inf"), 1e20, 1e300):
        account = _run(timedelta(minutes=40), {"query_min_interval": {"antigravity": value}})
        assert account["stale"] is True
        assert account["refresh_blocked_by_gate"] is True
        assert parse_dt(account["refreshable_at"]) > NOW + timedelta(days=6)


def test_a_nan_interval_leaves_the_gate_fields_off_instead_of_crashing():
    account = _run(timedelta(minutes=40), {"query_min_interval": {"antigravity": float("nan")}})
    assert account["stale"] is True
    assert "refreshable_at" not in account


def test_throttle_intervals_are_capped_and_non_finite_values_dropped():
    from aiuse.collectors.throttle import MAX_QUERY_MIN_INTERVAL, min_intervals

    table = min_intervals({"query_min_interval": {"antigravity": float("inf"), "x": 1e20, "y": float("nan"), "z": 30}})
    assert table["antigravity"] == MAX_QUERY_MIN_INTERVAL
    assert table["x"] == MAX_QUERY_MIN_INTERVAL
    assert "y" not in table
    assert table["z"] == 30.0


def test_validate_config_rejects_non_finite_and_absurd_intervals():
    from aiuse.config import MAX_QUERY_MIN_INTERVAL, validate_config

    for value in (float("inf"), float("nan"), 1e20, MAX_QUERY_MIN_INTERVAL + 1):
        issues = validate_config({"query_min_interval": {"antigravity": value}})
        assert any("query_min_interval.antigravity" in i and i.startswith("error") for i in issues), value
    assert validate_config({"query_min_interval": {"antigravity": 900, "codex": MAX_QUERY_MIN_INTERVAL}}) == []
