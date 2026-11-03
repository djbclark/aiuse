"""Provider-restricted routing must not lend Codex subscription quota to Pi."""

import json
from datetime import timedelta

import pytest

from aiuse import cli, credits_override
from aiuse.analysis import history
from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, pool_entries
from aiuse.collectors import codexbar
from aiuse.config import requested_providers
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow


def _snapshot():
    now = utcnow()
    pi = codexbar._from_row(
        {
            "provider": "openai",
            "account": "api-org",
            "usage": {
                "updatedAt": now.isoformat(),
                "providerCost": {"used": 2.8, "limit": 200, "currencyCode": "USD"},
                "primary": {"usedPercent": 0},
            },
            "credits": {"remaining": 200},
        }
    )
    codex = AccountUsage(
        source="codexbar",
        provider="codex",
        account="subscription",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        collected_at=now,
        windows=[
            QuotaWindow(
                label="5h",
                used_percent=10,
                remaining_percent=90,
                window_minutes=300,
                resets_at=now + timedelta(hours=2),
            )
        ],
    )
    return Snapshot(collected_at=now, accounts=[pi, codex])


def test_spend_only_pi_never_has_a_pool_even_with_codex_capacity():
    snapshot = enrich_snapshot(_snapshot().to_dict(), credit_overrides={})
    pi = snapshot["accounts"][0]
    assert pi["provider"] == "pi"
    assert pi["usable_now"] is False
    assert pi["windows"] == []
    assert pi["balance_usd"] is None
    assert pi["usage_credits"]["remaining"] is None
    assert pi["usage_credits"]["limit"] is None
    assert {p["provider"] for p in pool_entries(snapshot)} == {"codex"}
    assert {p["provider"] for p in available_pools(snapshot)} == {"codex"}
    snapshot["accounts"] = [pi]
    assert available_pools(snapshot) == []


@pytest.mark.parametrize("provider", ["pi", "openai", "codex", "openai-codex"])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("via_cli", [False, True])
@pytest.mark.parametrize("cached_pi_name", ["pi", "openai"])
def test_available_provider_scope_and_partial_save(
    tmp_path, monkeypatch, capsys, provider, live, via_cli, cached_pi_name
):
    snapshot = _snapshot()
    cached = snapshot.to_dict()
    cached["accounts"][0]["provider"] = cached_pi_name
    cache = history.snapshot_dir()
    cache.mkdir()
    latest = cache / "latest.json"
    original = json.dumps(cached)
    latest.write_text(original)
    config = tmp_path / "config.toml"
    configured = "enabled" if via_cli else provider
    config.write_text(f'[collectors.codexbar]\nproviders = "{configured}"\n[analysis]\npersist_snapshots = true\n')
    monkeypatch.setattr(credits_override, "load_overrides", lambda *a, **k: {})
    calls = []
    expected = "pi" if provider in {"pi", "openai"} else "codex"

    def dependencies(cfg):
        assert requested_providers(cfg) == frozenset({expected})
        return []

    def collect(cfg):
        calls.append(requested_providers(cfg))
        # Even an over-broad collector result must not leak into the routing
        # response or a partial save. Production runner also filters this.
        return snapshot

    monkeypatch.setattr(cli, "check_dependencies", dependencies)
    monkeypatch.setattr(cli, "run_collectors", collect)
    argv = ["--available", "--json", "-q", "--config", str(config)]
    if via_cli:
        argv += ["--providers", provider]
    if live:
        argv += ["--live"]
    assert cli.main(argv) == (3 if expected == "pi" else 0)
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == ("live" if live else "cache")
    assert {p["provider"] for p in payload["available"]} == (set() if expected == "pi" else {"codex"})
    assert payload["excluded"] == []
    assert calls == ([frozenset({expected})] if live else [])
    # A provider-specific refresh never replaces the full shared cache.
    assert latest.read_text() == original
    samples = list(history.partial_sample_dir().glob("*.json"))
    if live:
        assert len(samples) == 1
        saved = json.loads(samples[0].read_text())
        assert saved["partial_providers"] == [expected]
        assert saved["complete"] is False
        assert {a["provider"] for a in saved["accounts"]} == {expected}
    else:
        assert samples == []


@pytest.mark.parametrize("providers", [("openai", "openai"), ("pi", "openai")])
def test_available_alias_accounts_cannot_evade_unbound_grant_guard(tmp_path, monkeypatch, capsys, providers):
    snapshot = _snapshot()
    cached = snapshot.to_dict()
    pi = cached["accounts"][0]
    cached["accounts"] = [{**pi, "provider": p, "account": str(i)} for i, p in enumerate(providers)]
    cached["accounts"].append(snapshot.accounts[1].to_dict())
    cache = history.snapshot_dir()
    cache.mkdir()
    latest = cache / "latest.json"
    original = json.dumps(cached)
    latest.write_text(original)
    config = tmp_path / "config.toml"
    config.write_text('[collectors.codexbar]\nproviders = "enabled"\n')
    override = {
        "remaining_usd": 70,
        "total_usd": 100,
        "observed_at": snapshot.collected_at.isoformat(),
        "expires_at": None,
    }
    monkeypatch.setattr(credits_override, "load_overrides", lambda *a, **k: {"pi": override})
    monkeypatch.setattr(cli, "check_dependencies", lambda cfg: [])
    assert cli.main(["--available", "--json", "-q", "--config", str(config), "--providers", "pi"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] == []
    assert payload["excluded"] == []
    assert latest.read_text() == original
