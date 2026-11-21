"""`unavailable[]` reasons reached through the real enrichment path (schema 1.8 review, nits 7-13).

The first tests in test_unavailable_pools.py set `rate_gated_by` by hand after
enrichment, so the gate's own path to `reason: rate_gated` was never exercised.
These run `enrich_snapshot` end to end for the other reasons too.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from aiuse.analysis.selfdescribe import SEMANTICS, enrich_snapshot, unavailable_pools
from aiuse.credits_override import OMP_OPENAI_SOURCE
from aiuse.models import parse_dt, utcnow

NOW = utcnow()


def _claude(weekly_used: float) -> dict:
    return {
        "provider": "claude",
        "account": "me",
        "source": "cswap",
        "billing_kind": "subscription_window",
        "collected_at": NOW.isoformat(),
        "windows": [
            {
                "label": "Claude Code weekly",
                "used_percent": weekly_used,
                "remaining_percent": 100.0 - weekly_used,
                "resets_at": (NOW + timedelta(days=2)).isoformat(),
                "window_minutes": 10080,
            }
        ],
    }


def _omp() -> dict:
    return {
        "provider": "omp",
        "source": "codexbar",
        "billing_kind": "subscription_window",
        "balance_usd": 90.0,
        "spent_usd": 10.0,
        "collected_at": NOW.isoformat(),
        "windows": [
            {
                "label": "credits",
                "window_id": "claude-cloud-credits",
                "used_percent": 10.0,
                "remaining_percent": 90.0,
                "resets_at": (NOW + timedelta(days=18)).isoformat(),
                "window_minutes": None,
            }
        ],
    }


def _enrich(*accounts: dict, **kwargs) -> dict:
    return enrich_snapshot({"collected_at": NOW.isoformat(), "accounts": list(accounts)}, now=NOW, **kwargs)


def test_the_real_gate_path_gives_reason_rate_gated_with_the_gate_window_and_reset():
    snap = _enrich(_claude(100.0), _omp())
    (item,) = [i for i in unavailable_pools(snap, now=NOW) if i["provider"] == "omp"]
    assert item["reason"] == "rate_gated"
    assert item["binding_window"] == "Claude Code weekly"
    assert item["rate_gated_by"]["window_label"] == "Claude Code weekly"
    claude = next(a for a in snap["accounts"] if a["provider"] == "claude")
    assert parse_dt(item["available_at"]) == parse_dt(claude["windows"][0]["resets_at"])
    assert item["headroom_percent"] == pytest.approx(90.0)


def _key_pool_pi(usable: bool, available_at: str | None) -> dict:
    return {
        "provider": "pi",
        "source": OMP_OPENAI_SOURCE,
        "collected_at": NOW.isoformat(),
        "api_key_pool": {"usable": usable, "available_at": available_at},
        "windows": [{"label": "openai_api", "used_percent": 0.0, "remaining_percent": 100.0}],
    }


def test_a_blocked_api_key_pool_is_rate_gated_until_its_known_time():
    until = (NOW + timedelta(hours=3)).isoformat()
    (item,) = unavailable_pools(_enrich(_key_pool_pi(False, until)), now=NOW)
    assert item["reason"] == "rate_gated"
    assert parse_dt(item["available_at"]) == parse_dt(until)


def test_a_usable_api_key_pool_is_not_listed():
    assert unavailable_pools(_enrich(_key_pool_pi(True, None)), now=NOW) == []


def test_a_frozen_reading_is_no_reading_and_its_windows_are_not_blamed():
    snap = _enrich(_claude(10.0))
    for window in snap["accounts"][0]["windows"]:
        window["measurement_frozen"] = True  # set by the frozen-quota pass (tests in test_gemini_argon_family)
    (item,) = unavailable_pools(snap, now=NOW)
    assert item["reason"] == "no_reading"
    assert item["available_at"] is None


def test_a_blocked_pool_names_its_refusal_and_that_an_operator_must_act():
    note = {
        "provider": "claude",
        "state": "blocked",
        "refusal_kind": "spend_limit",
        "operator_action_required": True,
        "reason": "monthly spend limit",
    }
    (item,) = unavailable_pools(_enrich(_claude(10.0), notes=[note]), now=NOW)
    assert item["reason"] == "exhausted"
    assert item["available_at"] is None
    assert item["state"] == "blocked"
    assert item["refusal_kind"] == "spend_limit"
    assert item["operator_action_required"] is True
    assert "operator_action_required" in SEMANTICS["unavailable"]


def test_items_of_unblocked_pools_do_not_carry_the_blocked_keys():
    (item,) = unavailable_pools(_enrich(_claude(100.0)), now=NOW)
    assert not {"state", "refusal_kind", "operator_action_required"} & set(item)


def test_unavailable_binding_window_is_documented_as_the_latest_resetting_one():
    text = SEMANTICS["unavailable"]
    assert "resets LAST" in text and "least-headroom" in text
    assert "listed in neither" in text
