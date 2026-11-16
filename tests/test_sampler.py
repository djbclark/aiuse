"""Tests for per-provider sample cadence and empty-probe backoff (issue #34)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiuse import sampler
from aiuse.config import DEFAULT_CONFIG, validate_config
from aiuse.models import AccountUsage, QuotaWindow, Snapshot

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _snapshot(at: datetime, providers: dict[str, float]) -> Snapshot:
    accounts = [
        AccountUsage(
            source="codexbar",
            provider=provider,
            windows=[QuotaWindow(label="weekly", used_percent=used, window_minutes=7 * 24 * 60)],
        )
        for provider, used in providers.items()
    ]
    return Snapshot(collected_at=at, accounts=accounts)


def test_per_provider_cadence_skips_expensive_provider_during_burst():
    settings = sampler.sampling_settings(
        {
            "sampling": {
                "idle_interval": 3600,
                "active_interval": 1800,
                "burst_interval": 180,
                "provider_intervals": {"antigravity": 900},
            }
        }
    )
    state = {
        "tier": "burst",
        "last_sample_at": T0.isoformat(),
        "last_full_at": T0.isoformat(),
        "hot": [
            {"provider": "antigravity", "source": "codexbar"},
            {"provider": "claude", "source": "cswap"},
        ],
        "provider_state": {
            "antigravity": {"last_sample_at": T0.isoformat(), "empty_probes": 0},
            "claude": {"last_sample_at": T0.isoformat(), "empty_probes": 0},
        },
    }

    # At +180s, claude is due on burst_interval (180s), but antigravity is gated by 900s.
    decision = sampler.decide(state, settings, T0 + timedelta(seconds=180))
    assert decision.action == "partial"
    assert decision.hot == [{"provider": "claude", "source": "cswap"}]

    # If only antigravity is hot, +180s skips entirely instead of spawning 3x/180s.
    agy_only = dict(state, hot=[{"provider": "antigravity", "source": "codexbar"}])
    skip = sampler.decide(agy_only, settings, T0 + timedelta(seconds=180))
    assert skip.action == "skip"
    assert skip.wait_seconds > 600

    # At +900s, antigravity becomes due again.
    due = sampler.decide(agy_only, settings, T0 + timedelta(seconds=900))
    assert due.action == "partial"
    assert due.hot == [{"provider": "antigravity", "source": "codexbar"}]


def test_full_sample_excludes_providers_not_yet_due(tmp_path, monkeypatch):
    monkeypatch.setattr(sampler.history, "snapshot_dir", lambda: tmp_path / "snapshots")
    seen_configs: list[dict] = []

    def fake_collect(cfg: dict) -> Snapshot:
        seen_configs.append(cfg)
        disabled = cfg.get("disabled_services") or {}
        if "antigravity" in disabled:
            return _snapshot(T0 + timedelta(seconds=300), {"claude": 20.0})
        return _snapshot(T0, {"antigravity": 10.0, "claude": 15.0})

    config = {
        "sampling": {
            "idle_interval": 300,
            "active_interval": 300,
            "burst_interval": 180,
            "provider_intervals": {"antigravity": 900},
        },
        "attribution": {"ledger": False},
    }

    assert sampler.run_sample(config, now=T0, collect=fake_collect, quiet=True) == 0
    assert sampler.run_sample(config, now=T0 + timedelta(seconds=300), collect=fake_collect, quiet=True) == 0

    assert len(seen_configs) == 2
    assert "antigravity" not in (seen_configs[0].get("disabled_services") or {})
    assert "antigravity" in (seen_configs[1].get("disabled_services") or {})


def test_empty_probe_backoff_doubles_and_resets_on_data():
    settings = sampler.sampling_settings(
        {
            "sampling": {
                "idle_interval": 3600,
                "active_interval": 900,
                "burst_interval": 180,
                "provider_intervals": {"antigravity": 900},
                "empty_probe_backoff_factor": 2.0,
                "max_empty_probe_interval": 7200,
            }
        }
    )

    state: dict = {}
    # First probe returns data for claude, empty for antigravity.
    state = sampler.advance_state(
        state,
        _snapshot(T0, {"claude": 10.0}),
        settings,
        partial=False,
        sampled_providers=["claude", "antigravity"],
    )
    assert state["provider_state"]["claude"]["empty_probes"] == 0
    assert state["provider_state"]["antigravity"]["empty_probes"] == 1
    assert sampler.provider_sample_interval("antigravity", 900.0, state, settings) == 1800.0

    # Second empty probe doubles again (to 3600s).
    state = sampler.advance_state(
        state,
        _snapshot(T0 + timedelta(seconds=1800), {"claude": 12.0}),
        settings,
        partial=False,
        sampled_providers=["claude", "antigravity"],
    )
    assert state["provider_state"]["antigravity"]["empty_probes"] == 2
    assert sampler.provider_sample_interval("antigravity", 900.0, state, settings) == 3600.0

    # Subsequent non-empty reading resets empty_probes to 0.
    state = sampler.advance_state(
        state,
        _snapshot(T0 + timedelta(seconds=5400), {"claude": 14.0, "antigravity": 5.0}),
        settings,
        partial=False,
        sampled_providers=["claude", "antigravity"],
    )
    assert state["provider_state"]["antigravity"]["empty_probes"] == 0
    assert sampler.provider_sample_interval("antigravity", 900.0, state, settings) == 900.0


def test_default_sampling_config_includes_antigravity_cadence_and_validates():
    assert DEFAULT_CONFIG["sampling"]["provider_intervals"]["antigravity"] == 900
    assert validate_config({"sampling": DEFAULT_CONFIG["sampling"]}) == []
