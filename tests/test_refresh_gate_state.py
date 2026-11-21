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


def test_an_invalid_interval_falls_back_to_the_default_the_throttle_enforces():
    """Review round 2, nit 5: NaN, text or a boolean made the gate fields vanish while the throttle used 900 s."""
    from aiuse.collectors.throttle import min_intervals

    for value in (float("nan"), "abc", True, False, None, [900]):
        config = {"query_min_interval": {"antigravity": value}}
        assert min_intervals(config) == {"antigravity": 900.0}, value
        account = _run(timedelta(seconds=700), config)
        assert account["stale"] is True, value
        assert account["refresh_blocked_by_gate"] is True, value
        assert account["gate_remaining_seconds"] == 200.0, value  # reading + the 900 s default
        assert parse_dt(account["refreshable_at"]) == NOW + timedelta(seconds=200), value


def test_a_400_digit_integer_interval_is_too_large_not_a_crash():
    """Round 3, nit 3: tomllib accepts a 400-digit int; float() of it raises OverflowError."""
    from aiuse.collectors.throttle import MAX_QUERY_MIN_INTERVAL, min_intervals
    from aiuse.config import validate_config

    huge = 10**400
    config = {"query_min_interval": {"antigravity": huge, "grok": -huge, "z": 30}}
    table = min_intervals(config)
    assert table["antigravity"] == MAX_QUERY_MIN_INTERVAL
    assert "grok" not in table  # a huge negative value disables, as -1 does
    assert table["z"] == 30.0
    issues = validate_config(config)
    assert any("query_min_interval.antigravity" in i and i.startswith("error") and "too large" in i for i in issues)
    assert any("query_min_interval.grok" in i and i.startswith("error") for i in issues)
    account = _run(timedelta(minutes=40), {"query_min_interval": {"antigravity": huge}})
    assert account["stale"] is True
    assert account["refresh_blocked_by_gate"] is True
    assert parse_dt(account["refreshable_at"]) > NOW + timedelta(days=6)


def test_a_disabled_interval_still_leaves_the_gate_fields_off():
    account = _run(timedelta(minutes=40), {"query_min_interval": {"antigravity": 0}})
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


def test_validate_config_rejects_boolean_intervals():
    """float(True) is 1.0, so `antigravity = true` used to pass as a 1 s gate."""
    from aiuse.config import validate_config

    for value in (True, False):
        issues = validate_config({"query_min_interval": {"antigravity": value}})
        assert any("query_min_interval.antigravity" in i and i.startswith("error") for i in issues), value


def test_a_future_queried_at_is_clock_skew_not_a_closed_gate():
    """Review round 2, nit 4: QueryGate.__enter__ allows a query whose queried_at is in the future; so must this."""
    from aiuse.models import utcnow

    ahead = (utcnow() + timedelta(hours=1)).isoformat()
    _state(queried_at=ahead)
    assert next_query_at("antigravity", min_interval=900.0) is None
    account = _run(timedelta(minutes=40))
    assert account["refresh_blocked_by_gate"] is False


def test_a_future_queried_at_still_honours_the_timeout_backoff():
    from aiuse.models import utcnow

    _state(queried_at=(utcnow() + timedelta(hours=1)).isoformat())
    stamp = utcnow() - timedelta(minutes=1)
    directory = throttle_dir()
    (directory / "codexbar-timeouts.json").write_text(
        json.dumps({"antigravity": {"timed_out_at": stamp.isoformat(), "count": 1, "timeout": 30.0}})
    )
    until = next_query_at("antigravity", min_interval=900.0, timeout_backoff=300.0)
    assert until == stamp + timedelta(seconds=300)


def test_a_huge_empty_streak_cannot_overflow_the_interval():
    """Review round 2, nit 9: 4.0 ** 600 raised OverflowError in QueryGate.__enter__ on every collection."""
    from aiuse.collectors.throttle import EMPTY_BACKOFF_CAP, QueryGate

    for streak in (515, 600, 10**6, 10**30):
        gate = QueryGate("antigravity", min_interval=900.0, wait=0.0)
        gate.state = {"empty_streak": streak, "queried_at": (NOW - timedelta(minutes=5)).isoformat()}
        assert gate.interval == EMPTY_BACKOFF_CAP == 3600.0, streak
        assert gate.next_allowed_at is not None
    below = QueryGate("antigravity", min_interval=900.0, wait=0.0)
    below.state = {"empty_streak": 3}
    assert below.interval == 3600.0
    for junk in (float("inf"), float("nan"), "x", [1], None):
        gate = QueryGate("antigravity", min_interval=900.0, wait=0.0)
        gate.state = {"empty_streak": junk}
        assert gate.interval == 900.0, junk
    assert next_query_at("antigravity", min_interval=900.0) is None  # no state: nothing raises either
