"""Freshness is honest per pool — the 2026-11-12 agy incident.

`aiuse --available --json` reported top-level `fresh: true` with `usable_now:
true` antigravity entries whose `collected_at` was 15-20 minutes old: one
global 30-min threshold judged every pool while a burst of agents emptied the
antigravity pools inside the collector's 15-min query gate. Per-provider fresh
limits, clock-skew clamping, and a `stale_pools` list on the envelope are the
fix; these tests pin each case.
"""

from __future__ import annotations

import json
from datetime import timedelta

from aiuse import cli
from aiuse.analysis import history
from aiuse.analysis.selfdescribe import (
    enrich_account,
    enrich_snapshot,
    envelope_freshness,
    pool_entries,
)
from aiuse.config import DEFAULT_CONFIG, validate_config
from aiuse.models import utcnow


def _fixture(provider: str, *, age: timedelta | None = None) -> dict:
    now = utcnow()
    account: dict = {
        "provider": provider,
        "account": None,
        "source": "codexbar",
        "billing_kind": "subscription_window",
        "cli_binary": "agy" if provider == "antigravity" else "codex",
        "windows": [
            {
                "label": f"{provider} weekly",
                "used_percent": 30.0,
                "remaining_percent": 70.0,
                "resets_at": (now + timedelta(days=4)).isoformat(),
                "window_minutes": 10080,
            }
        ],
    }
    if age is not None:
        account["collected_at"] = (now - age).isoformat()
    return {"collected_at": now.isoformat(), "accounts": [account]}


BY_PROVIDER = {"antigravity": 600.0}


# ── per-provider threshold ──────────────────────────────────────────────────


def test_fast_draining_pool_reads_stale_before_the_global_limit():
    now = utcnow()
    # 700s: fresh under the 1800s global limit, stale under antigravity's 600s.
    account = enrich_account(
        _fixture("antigravity", age=timedelta(seconds=700))["accounts"][0],
        collected_at=None,
        now=now,
        threshold_seconds=1800.0,
        threshold_by_provider=BY_PROVIDER,
    )
    assert account["fresh"] is False
    assert account["stale"] is True
    assert account["age_seconds"] == 700.0


def test_slow_pool_still_uses_the_global_limit():
    now = utcnow()
    account = enrich_account(
        _fixture("codex", age=timedelta(seconds=700))["accounts"][0],
        collected_at=None,
        now=now,
        threshold_seconds=1800.0,
        threshold_by_provider=BY_PROVIDER,
    )
    assert account["fresh"] is True
    assert account["stale"] is False


def test_enrich_snapshot_derives_per_provider_limits_from_config():
    now = utcnow()
    fix = _fixture("antigravity", age=timedelta(seconds=700))
    snap = enrich_snapshot(fix, now=now, config={"analysis": {"fresh_threshold_seconds_by_provider": BY_PROVIDER}})
    account = snap["accounts"][0]
    assert account["fresh"] is False
    assert account["stale_reason"]


def test_shipped_default_covers_antigravity():
    assert DEFAULT_CONFIG["analysis"]["fresh_threshold_seconds_by_provider"] == {"antigravity": 600.0}


def test_validate_config_rejects_bad_per_provider_limits():
    assert validate_config({}) == []
    issues = validate_config(
        {"analysis": {"fresh_threshold_seconds_by_provider": {"antigravity": "soon", "codex": -5}}}
    )
    assert any("fresh_threshold_seconds_by_provider.antigravity" in issue for issue in issues)
    assert any("fresh_threshold_seconds_by_provider.codex" in issue for issue in issues)


# ── envelope freshness ──────────────────────────────────────────────────────


def _entries(now, *, agy_age: timedelta | None, codex_age: timedelta | None):
    accounts = [
        enrich_account(
            _fixture("antigravity", age=agy_age)["accounts"][0],
            now=now,
            threshold_seconds=1800.0,
            threshold_by_provider=BY_PROVIDER,
        ),
        enrich_account(
            _fixture("codex", age=codex_age)["accounts"][0],
            now=now,
            threshold_seconds=1800.0,
            threshold_by_provider=BY_PROVIDER,
        ),
    ]
    snap = {"collected_at": now.isoformat(), "accounts": accounts}
    enrich_snapshot(snap, now=now, threshold_by_provider=BY_PROVIDER)
    return [p for p in pool_entries(snap) if p.get("usable_now")]


def test_envelope_fresh_when_every_usable_pool_is_fresh():
    now = utcnow()
    fresh = envelope_freshness(now.isoformat(), _entries(now, agy_age=None, codex_age=None), now=now)
    assert fresh["fresh"] is True
    assert fresh["stale_pools"] == []


def test_envelope_stale_when_one_usable_pool_is_stale():
    now = utcnow()
    entries = _entries(now, agy_age=timedelta(seconds=700), codex_age=None)
    fresh = envelope_freshness(now.isoformat(), entries, now=now)
    assert fresh["fresh"] is False
    assert [p["provider"] for p in fresh["stale_pools"]] == ["antigravity"]
    assert fresh["stale_pools"][0]["age_seconds"] == 700.0


def test_envelope_stale_when_all_usable_pools_are_stale():
    now = utcnow()
    entries = _entries(now, agy_age=timedelta(seconds=700), codex_age=timedelta(seconds=1900))
    fresh = envelope_freshness(now.isoformat(), entries, now=now)
    assert fresh["fresh"] is False
    assert {p["provider"] for p in fresh["stale_pools"]} == {"antigravity", "codex"}
    assert all("age_seconds" in p for p in fresh["stale_pools"])


def test_envelope_stale_when_only_the_snapshot_is_old():
    now = utcnow()
    collected = (now - timedelta(seconds=1900)).isoformat()
    fresh = envelope_freshness(collected, _entries(now, agy_age=None, codex_age=None), now=now)
    assert fresh["fresh"] is False
    assert fresh["stale_pools"] == []


# ── clock skew ──────────────────────────────────────────────────────────────


def test_future_reading_is_clock_skew_not_time_travel():
    now = utcnow()
    account = enrich_account(
        _fixture("antigravity", age=-timedelta(minutes=5))["accounts"][0],
        now=now,
        threshold_seconds=1800.0,
        threshold_by_provider=BY_PROVIDER,
    )
    assert account["age_seconds"] == 0.0
    assert account["fresh"] is True
    assert account["stale"] is False


def test_envelope_clamps_a_future_snapshot_clock():
    now = utcnow()
    fresh = envelope_freshness((now + timedelta(minutes=5)).isoformat(), [], now=now)
    assert fresh["age_seconds"] == 0.0
    assert fresh["fresh"] is True


# ── --available end to end over the cache ───────────────────────────────────


def _write_cache(accounts: list[dict]) -> None:
    snap = {"collected_at": utcnow().isoformat(), "accounts": accounts}
    cache = history.snapshot_dir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "latest.json").write_text(json.dumps(snap))


def _available_payload(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text("")
    assert cli.main(["--available", "--json", "-q", "--config", str(config)]) == 0
    return json.loads(capsys.readouterr().out)


def test_available_fresh_is_false_when_one_usable_pool_is_stale(tmp_path, capsys):
    now = utcnow()
    agy = _fixture("antigravity")["accounts"][0]
    agy["collected_at"] = (now - timedelta(seconds=700)).isoformat()
    _write_cache([agy, _fixture("codex")["accounts"][0]])

    payload = _available_payload(tmp_path, capsys)
    assert payload["fresh"] is False
    assert [p["provider"] for p in payload["stale_pools"]] == ["antigravity"]
    assert payload["stale_pools"][0]["age_seconds"] == 700.0
    assert {p["provider"] for p in payload["available"]} == {"antigravity", "codex"}


def test_available_fresh_when_every_pool_is_fresh(tmp_path, capsys):
    _write_cache([_fixture("antigravity")["accounts"][0], _fixture("codex")["accounts"][0]])

    payload = _available_payload(tmp_path, capsys)
    assert payload["fresh"] is True
    assert payload["stale_pools"] == []
