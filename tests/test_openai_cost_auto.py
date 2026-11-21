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
    assert pi.balance_usd is None
    assert pi.credit_estimate["estimated_remaining_usd"] == 65
    assert pi.collected_at == now - timedelta(hours=14)
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


def test_r2_lagged_estimate_never_becomes_available_cash(setup_auto):
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot

    cfg, calls, now = setup_auto
    snapshot = runner._run_collectors(cfg)
    co.apply_credit_overrides_to_snapshot(snapshot, cfg, now=now)
    data = enrich_snapshot(snapshot.to_dict(), now=now, config=cfg)
    pi = next(a for a in data["accounts"] if a["provider"] == "pi")
    assert pi["balance_usd"] is None
    assert pi["binding_headroom_percent"] is None
    assert pi["usable_now"] is False
    assert pi["fresh"] is False
    assert pi["credit_estimate"]["estimated_remaining_usd"] == 65
    assert pi["credit_estimate"]["costs_observed_at"] == now.isoformat()
    assert available_pools(data) == []
    # A new cash observation, unlike polling Costs, can restore actual evidence.
    co.save_override(
        "pi",
        remaining=20,
        total=100,
        expires=None,
        note=None,
        granted=now - timedelta(days=2),
        now=now + timedelta(seconds=1),
    )
    newer = now + timedelta(seconds=2)
    fresh = enrich_snapshot(
        snapshot.to_dict(), now=newer, config=cfg, credit_overrides=co.load_overrides(cfg, now=newer)
    )
    assert fresh["accounts"][0]["balance_usd"] == 20
    assert available_pools(fresh)


def test_r2_expired_console_anchor_rejected_before_fetch(setup_auto):
    cfg, calls, now = setup_auto
    co.overrides_path().unlink()
    co.save_override(
        "pi",
        remaining=70,
        total=100,
        expires=None,
        note=None,
        granted=now - timedelta(days=20),
        now=now - timedelta(days=8),
    )
    snapshot = runner._run_collectors(cfg)
    assert calls == []
    assert any("console" in e for e in snapshot.collector_errors)


def test_r2_attempt_reserved_before_fetch_and_crash_cooldown(setup_auto, monkeypatch):
    cfg, calls, now = setup_auto

    def crash(*args):
        state = json.loads((throttle.throttle_dir() / "pi-openai-cost.json").read_text())
        assert state["queried_at"] == throttle.utcnow().isoformat()
        calls.append(1)
        raise SystemExit("simulated process death")

    monkeypatch.setattr(oc, "_http_get", crash)
    with pytest.raises(SystemExit):
        runner._run_collectors(cfg)
    # A separate gate instance reads the durable reservation after uncertain exit.
    runner._run_collectors(cfg)
    assert len(calls) == 1
    monkeypatch.setattr(throttle, "utcnow", lambda: now + timedelta(seconds=900))
    with pytest.raises(SystemExit):
        runner._run_collectors(cfg)
    assert len(calls) == 2


def test_r2_reservation_write_failure_prevents_network(setup_auto, monkeypatch):
    cfg, calls, now = setup_auto
    import os

    def fail(*args):
        raise OSError("untrusted write failure")

    monkeypatch.setattr(os, "fsync", fail)
    snapshot = runner._run_collectors(cfg)
    assert calls == []
    assert snapshot.collector_errors


def test_r2_legacy_manual_admin_row_has_no_machine_cash(setup_auto):
    from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot

    cfg, calls, now = setup_auto
    # Represents an older manual refresh/cache created before this safety fix.
    legacy = {
        "collected_at": now.isoformat(),
        "accounts": [
            {
                "provider": "pi",
                "source": "admin-api",
                "balance_usd": 65,
                "windows": [{"label": "credits", "remaining_percent": 65}],
                "collected_at": now.isoformat(),
            }
        ],
    }
    data = enrich_snapshot(legacy, config=cfg, now=now)
    assert data["accounts"][0]["balance_usd"] is None
    assert data["accounts"][0]["usable_now"] is False
    assert data["accounts"][0]["fresh"] is False
    assert available_pools(data) == []


def test_r2_crash_after_response_preserves_attempt(setup_auto, monkeypatch):
    cfg, calls, now = setup_auto

    def crash(*args, **kwargs):
        raise SystemExit("simulated crash before credit save")

    monkeypatch.setattr(co, "save_credit_entry", crash)
    with pytest.raises(SystemExit):
        runner._run_collectors(cfg)
    assert len(calls) == 1
    runner._run_collectors(cfg)
    assert len(calls) == 1
    assert not co.readings_file().exists()


def test_r2_concurrent_auto_callers_only_one_fetch(setup_auto, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    cfg, calls, now = setup_auto
    entered, release = Event(), Event()
    original = oc._http_get

    def waiting(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(oc, "_http_get", waiting)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runner._run_collectors, cfg)
        try:
            assert entered.wait(5)
            second = pool.submit(runner._run_collectors, cfg)
            second.result(timeout=5)
        finally:
            release.set()
        first.result(timeout=5)
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["replace", "directory_sync"])
def test_r2_reserve_publication_failure_is_closed(setup_auto, monkeypatch, stage):
    import os

    cfg, calls, now = setup_auto
    if stage == "replace":

        def fail(*args):
            raise OSError("synthetic failure")

        monkeypatch.setattr(os, "replace", fail)
    else:
        original = os.fsync
        count = 0

        def fail_sync(fd):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic directory sync failure")
            return original(fd)

        monkeypatch.setattr(os, "fsync", fail_sync)
    snapshot = runner._run_collectors(cfg)
    assert calls == []
    assert snapshot.collector_errors


def test_r2_process_exit_releases_lock_but_keeps_cooldown(setup_auto, monkeypatch):
    import subprocess
    import sys

    cfg, calls, now = setup_auto
    directory = throttle.throttle_dir()
    code = """import os, sys
from pathlib import Path
from aiuse.collectors.throttle import QueryGate
with QueryGate('crash-proof', min_interval=900, wait=0, directory=Path(sys.argv[1])) as gate:
    gate.reserve('openai_cost')
    os._exit(73)
"""
    import os

    result = subprocess.run(
        [sys.executable, "-c", code, str(directory)],
        timeout=10,
        env=dict(os.environ, PYTHONPATH=str(__import__("pathlib").Path(oc.__file__).resolve().parents[1])),
    )
    assert result.returncode == 73
    monkeypatch.setattr(throttle, "utcnow", utcnow)
    with throttle.QueryGate("crash-proof", min_interval=900, wait=0, directory=directory) as gate:
        assert gate.locked
        assert not gate.allowed
        assert gate.last_queried_at is not None


def test_r2_available_cli_excludes_lagged_cash(setup_auto, monkeypatch, capsys):
    import copy

    from aiuse import agent_notes, cli

    cfg, calls, now = setup_auto
    monkeypatch.setenv("AIUSE_SKIP_HOMEBREW_FORMULA", "1")
    monkeypatch.setattr(cli, "load_config", lambda *args: copy.deepcopy(cfg))
    monkeypatch.setattr(cli, "check_dependencies", lambda *args: [])
    monkeypatch.setattr(cli, "should_persist_snapshots", lambda *args: False)
    monkeypatch.setattr(cli, "run_collectors", runner._run_collectors)
    monkeypatch.setattr(agent_notes, "load_active_notes", lambda: [])
    result = cli.main(["--available", "--live", "--json"])
    assert result == 3
    data = json.loads(capsys.readouterr().out)
    assert data["available"] == []
    assert calls
