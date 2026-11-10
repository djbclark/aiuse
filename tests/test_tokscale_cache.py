"""Tests for tokscale caching, throttling, and ledger reuse."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aiuse import ledger
from aiuse.collectors import tokscale
from aiuse.collectors.base import CollectorNotInstalled

_SAMPLE_PAYLOAD = [
    {
        "provider": "Claude",
        "email": "user@example.com",
        "metrics": [
            {"label": "5h", "used_percent": 15, "remaining_percent": 85},
            {"label": "Weekly", "used_percent": 40, "remaining_percent": 60},
        ],
    }
]


def test_collect_tokscale_requires_binary_on_path(monkeypatch):
    monkeypatch.setattr(tokscale, "which", lambda _cmd: None)
    with pytest.raises(CollectorNotInstalled):
        tokscale.collect_tokscale()


def test_collect_tokscale_writes_and_reuses_file_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tokscale, "which", lambda _cmd: "/usr/local/bin/tokscale")
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=45.0):
        calls.append(cmd)
        return _SAMPLE_PAYLOAD

    cache_file = tmp_path / "tokscale-cache.json"

    # First call: executes query, writes cache
    res1 = tokscale.collect_tokscale(cache_path=cache_file, run=fake_run, max_cache_age=600.0)
    assert len(calls) == 1
    assert len(res1) == 1
    assert res1[0].provider == "claude"
    assert "Live data fetched by tokscale" in res1[0].notes[0]
    assert cache_file.is_file()

    # Second call within TTL: reuses file cache, does not execute run
    res2 = tokscale.collect_tokscale(cache_path=cache_file, run=fake_run, max_cache_age=600.0)
    assert len(calls) == 1  # No new call!
    assert len(res2) == 1
    assert res2[0].provider == "claude"
    assert "Data cached from tokscale" in res2[0].notes[0]


def test_collect_tokscale_bypasses_cache_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(tokscale, "which", lambda _cmd: "/usr/local/bin/tokscale")
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=45.0):
        calls.append(cmd)
        return _SAMPLE_PAYLOAD

    cache_file = tmp_path / "tokscale-cache.json"

    tokscale.collect_tokscale(cache_path=cache_file, run=fake_run, max_cache_age=600.0)
    assert len(calls) == 1

    # With use_cache=False, ignores cache file and queries live (min_interval=0 to allow gate)
    tokscale.collect_tokscale(cache_path=cache_file, run=fake_run, use_cache=False, min_interval=0.0)
    assert len(calls) == 2


def test_collect_tokscale_reuses_querygate_throttled_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(tokscale, "which", lambda _cmd: "/usr/local/bin/tokscale")
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=45.0):
        calls.append(cmd)
        return _SAMPLE_PAYLOAD

    cache_file = tmp_path / "tokscale-cache.json"

    # Call 1: records into query gate
    tokscale.collect_tokscale(cache_path=cache_file, run=fake_run, min_interval=600.0)
    assert len(calls) == 1

    # Remove cache file to simulate cache miss
    cache_file.unlink()

    # Call 2: cache file missing, but QueryGate throttles and reuses recorded payload
    res = tokscale.collect_tokscale(cache_path=cache_file, run=fake_run, min_interval=600.0)
    assert len(calls) == 1  # QueryGate reused payload, run was NOT called!
    assert len(res) == 1
    assert res[0].provider == "claude"


def test_capture_tokscale_ledger_reuses_recent_previous():
    now = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)
    today = now.astimezone().date().isoformat()
    previous = {
        "schema_version": "1.0",
        "source": "tokscale",
        "captured_at": (now - timedelta(seconds=120)).isoformat(),
        "days": {today: [{"client": "claude", "model": "opus", "provider": "anthropic", "input": 500}]},
    }

    calls: list[list[str]] = []

    def fake_run(cmd, timeout=45.0):
        calls.append(cmd)
        return {"entries": []}

    res = ledger.capture_tokscale_ledger(now=now, previous=previous, max_cache_age=300.0, run=fake_run)
    assert len(calls) == 0  # Reused from previous!
    assert res["days"][today][0]["input"] == 500


def test_capture_tokscale_ledger_queries_when_previous_expired():
    now = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)
    today = now.astimezone().date().isoformat()
    previous = {
        "schema_version": "1.0",
        "source": "tokscale",
        "captured_at": (now - timedelta(seconds=600)).isoformat(),  # 10 min old > 5 min max_cache_age
        "days": {today: [{"client": "claude", "model": "opus", "provider": "anthropic", "input": 500}]},
    }

    calls: list[list[str]] = []

    def fake_run(cmd, timeout=45.0):
        calls.append(cmd)
        return {"entries": [{"client": "claude", "model": "opus", "provider": "anthropic", "input": 800}]}

    res = ledger.capture_tokscale_ledger(now=now, previous=previous, max_cache_age=300.0, run=fake_run)
    assert len(calls) == 1  # Re-queried!
    assert res["days"][today][0]["input"] == 800
