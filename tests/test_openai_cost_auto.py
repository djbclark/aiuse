"""Opt-in Pi refresh orchestration; synthetic credentials and isolated files only."""

import json
from datetime import timedelta

import pytest

from aiuse import credits_override as co
from aiuse import openai_cost as oc
from aiuse.collectors import runner, throttle
from aiuse.config import DEFAULT_CONFIG, KNOWN_COLLECTOR_KEYS
from aiuse.models import AccountUsage, utcnow


@pytest.fixture
def setup_auto(tmp_path, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(co, "overrides_path", lambda: tmp_path / "credit-overrides.json")
    monkeypatch.setattr(throttle, "throttle_dir", lambda: tmp_path / "throttle")
    monkeypatch.setattr(oc, "utcnow", lambda: now)
    monkeypatch.setattr(throttle, "utcnow", lambda: now)
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "synthetic-test-token")
    co.save_override(
        "pi",
        remaining=70,
        total=100,
        expires=None,
        note=None,
        granted=now - timedelta(days=2),
        now=now - timedelta(hours=14),
    )
    calls = []

    def fetch(url, headers, timeout):
        calls.append(timeout)
        return 200, json.dumps(
            {
                "data": [
                    {
                        "start_time": int((now - timedelta(days=1)).timestamp()),
                        "end_time": int(now.timestamp()),
                        "results": [{"amount": {"value": 5, "currency": "usd"}}],
                    }
                ],
                "has_more": False,
            }
        ).encode()

    monkeypatch.setattr(oc, "_http_get", fetch)
    monkeypatch.setattr(runner, "_carry_forward_resets", lambda snapshot: None)
    monkeypatch.setattr(runner, "_apply_client_limit_notes", lambda *args: None)
    cfg = {"collectors": {name: {"enabled": False} for name in KNOWN_COLLECTOR_KEYS}}
    cfg["collectors"]["openai_cost"] = {"enabled": True}
    return cfg, calls, now


def test_auto_refresh_repairs_old_balance_and_deduplicates(setup_auto):
    cfg, calls, now = setup_auto
    snapshot = runner._run_collectors(cfg)
    co.apply_credit_overrides_to_snapshot(snapshot, cfg, now=now)
    pi = next(a for a in snapshot.accounts if a.provider == "pi")
    assert calls and all(0 < t <= 20 for t in calls)
    assert pi.source == "admin-api"
    assert pi.balance_usd == 65
    assert pi.collected_at == now
    assert any("grant-derived" in note for note in pi.notes)
    runner._run_collectors(cfg)
    assert len(calls) == 1


def test_auto_failure_preserves_timestamp_and_throttles(setup_auto, monkeypatch):
    cfg, calls, now = setup_auto

    def fail(*args):
        calls.append(1)
        return 503, b"untrusted-body-with-secret"

    monkeypatch.setattr(oc, "_http_get", fail)
    snapshot = runner._run_collectors(cfg)
    co.apply_credit_overrides_to_snapshot(snapshot, cfg, now=now)
    pi = next(a for a in snapshot.accounts if a.provider == "pi")
    assert pi.source == "operator-override"
    assert pi.collected_at == now - timedelta(hours=14)
    assert any("HTTP 503" in error for error in snapshot.collector_errors)
    assert "untrusted-body" not in str(snapshot.to_dict())
    runner._run_collectors(cfg)
    assert len(calls) == 1


@pytest.mark.parametrize("gate", ["off", "absent", "disabled", "hidden", "filter", "ambiguous", "pin"])
def test_auto_gates_do_not_fetch(setup_auto, monkeypatch, gate):
    cfg, calls, now = setup_auto
    if gate == "off":
        cfg["collectors"]["openai_cost"] = False
    elif gate == "absent":
        del cfg["collectors"]["openai_cost"]
    elif gate == "disabled":
        cfg["disabled_services"] = {"pi": "off"}
    elif gate == "hidden":
        cfg["hidden_pools"] = {"pi": "off"}
    elif gate == "filter":
        cfg["collectors"]["codexbar"] = {"enabled": False, "providers": "codex"}
    elif gate == "pin":
        cfg["usage_sources"] = {"pi": "codexbar"}
    elif gate == "ambiguous":
        cfg["collectors"]["codexbar"] = {"enabled": True}
        monkeypatch.setattr(
            runner,
            "collect_codexbar",
            lambda **kw: [AccountUsage(source="codexbar", provider="pi", account=name) for name in ("one", "two")],
        )
    runner._run_collectors(cfg)
    assert calls == []


def test_auto_default_off_and_known_config():
    assert DEFAULT_CONFIG["collectors"]["openai_cost"]["enabled"] is False
    assert "openai_cost" in KNOWN_COLLECTOR_KEYS


@pytest.mark.parametrize("reason", ["no_key", "no_grant", "expired", "fresh"])
def test_auto_prerequisites_never_fetch(setup_auto, monkeypatch, reason):
    cfg, calls, now = setup_auto
    if reason == "no_key":
        monkeypatch.delenv("OPENAI_ADMIN_KEY")
    elif reason == "no_grant":
        co.overrides_path().unlink()
    else:
        co.save_override(
            "pi",
            remaining=60,
            total=100,
            expires=now - timedelta(seconds=1) if reason == "expired" else None,
            note=None,
            granted=now - timedelta(days=2),
            now=now - timedelta(seconds=10),
        )
    snapshot = runner._run_collectors(cfg)
    assert calls == []
    if reason != "fresh":
        assert any("refresh unavailable" in error for error in snapshot.collector_errors)


def test_auto_sweep_does_not_enable_or_persist_refresh(setup_auto):
    from aiuse.watch import all_providers_config

    cfg, calls, now = setup_auto
    for enabled in (False, True):
        cfg["collectors"]["openai_cost"] = {"enabled": enabled}
        sweep = all_providers_config(cfg)
        assert sweep["collectors"]["openai_cost"]["enabled"] is False
        assert cfg["collectors"]["openai_cost"]["enabled"] is enabled


def test_auto_sequence_deadline_preserves_old_observation(setup_auto, monkeypatch):
    cfg, calls, now = setup_auto
    clock = iter((0.0, 0.0, 46.0))
    from types import SimpleNamespace

    monkeypatch.setattr(oc, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    snapshot = runner._run_collectors(cfg)
    co.apply_credit_overrides_to_snapshot(snapshot, cfg, now=now)
    pi = next(a for a in snapshot.accounts if a.provider == "pi")
    assert pi.collected_at == now - timedelta(hours=14)
    assert any("deadline exceeded" in error for error in snapshot.collector_errors)
