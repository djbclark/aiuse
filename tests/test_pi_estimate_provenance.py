"""Cached/manual estimate provenance stays unknown unless valid; no live collection."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot
from aiuse.serve import _snapshot_from_accounts_dict

NOW = datetime(2026, 11, 17, 14, tzinfo=UTC)


def cached(console, amount=65, costs=None):
    return {
        "collected_at": NOW.isoformat(),
        "accounts": [
            {
                "provider": "openai",
                "source": "admin-api",
                "collected_at": NOW.isoformat(),
                "balance_usd": 65,
                "windows": [{"label": "credits", "remaining_percent": 65}],
                "credit_estimate": {
                    "console_observed_at": console,
                    "costs_observed_at": costs if costs is not None else NOW.isoformat(),
                    "estimated_remaining_usd": amount,
                },
            }
        ],
    }


def roundtrip(data):
    wire = json.loads(json.dumps(data))
    typed = _snapshot_from_accounts_dict(wire).to_dict()
    return enrich_snapshot(typed, now=NOW, credit_overrides={})


@pytest.mark.parametrize("console", [None, "", "invalid", 123, {}, [], "2026-11-18T14:00:00Z"])
def test_r3_invalid_console_provenance_is_stale_through_cache(console):
    data = enrich_snapshot(cached(console), now=NOW, credit_overrides={})
    for _ in range(3):
        pi = data["accounts"][0]
        assert pi["credit_estimate"]["console_observed_at"] is None
        assert pi["balance_usd"] is None
        assert pi["binding_headroom_percent"] is None
        assert pi["usable_now"] is False
        assert pi["fresh"] is False
        assert pi["stale"] is True
        assert available_pools(data) == []
        data = roundtrip(data)


@pytest.mark.parametrize("amount", [-1, True, float("nan"), float("inf"), float("-inf"), "65", None, {}, []])
def test_r3_bad_estimate_amount_is_unknown(amount):
    data = enrich_snapshot(cached((NOW - timedelta(hours=14)).isoformat(), amount), now=NOW, credit_overrides={})
    for _ in range(2):
        assert data["accounts"][0]["credit_estimate"]["estimated_remaining_usd"] is None
        assert available_pools(data) == []
        data = roundtrip(data)


@pytest.mark.parametrize("costs", ["invalid", "2026-11-18T14:00:00Z", 123, {}])
def test_r3_invalid_costs_time_not_retained(costs):
    data = enrich_snapshot(cached((NOW - timedelta(hours=14)).isoformat(), costs=costs), now=NOW, credit_overrides={})
    assert data["accounts"][0]["credit_estimate"]["costs_observed_at"] is None


def test_r3_valid_chronology_and_amount_are_preserved():
    console = "2026-11-17T00:00:00Z"
    costs = "2026-11-17T13:55:00Z"
    data = enrich_snapshot(cached(console, 65, costs), now=NOW, credit_overrides={})
    for _ in range(3):
        pi = data["accounts"][0]
        assert pi["credit_estimate"]["console_observed_at"] == console
        assert pi["collected_at"] == console or pi["collected_at"] == "2026-11-17T00:00:00+00:00"
        assert pi["credit_estimate"]["costs_observed_at"] == costs
        assert pi["credit_estimate"]["estimated_remaining_usd"] == 65
        assert pi["fresh"] is False
        data = roundtrip(data)
