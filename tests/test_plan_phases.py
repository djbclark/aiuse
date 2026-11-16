"""Tests for scheduled plan phases (starts_at/ends_at), pricing in --available, and POOL/SCOPE names."""

from __future__ import annotations

from datetime import UTC, datetime

from aiuse.analysis.pace import matrix_pool_scope
from aiuse.analysis.selfdescribe import enrich_snapshot, pool_entries
from aiuse.config import DEFAULT_CONFIG, plan_monthly_price, validate_config
from aiuse.models import provider_display_name


def test_plan_phases_switch_on_starts_and_ends_dates():
    plans = {
        "opencode": {
            "monthly_price": 10,
            "dclark": {
                "monthly_price": 10,
                "phases": [
                    {
                        "monthly_price": 40,
                        "starts_at": "2026-10-28",
                        "ends_at": "2026-11-28",
                    }
                ],
            },
        }
    }

    before = datetime(2026, 10, 27, 23, 59, tzinfo=UTC)
    during = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    at_end = datetime(2026, 11, 28, 0, 0, tzinfo=UTC)

    assert plan_monthly_price(plans, "opencode-go", "dclark", now=before) == 10.0
    assert plan_monthly_price(plans, "opencode-go", "dclark", now=during) == 40.0
    assert plan_monthly_price(plans, "opencode-go", "dclark", now=at_end) == 10.0
    # Other accounts still use provider-level fallback.
    assert plan_monthly_price(plans, "opencode-go", "t-e", now=during) == 10.0


def test_validate_config_accepts_plan_phases_and_flags_bad_bounds():
    ok_cfg = {
        "plans": {
            "opencode": {
                "monthly_price": 10,
                "dclark": {
                    "monthly_price": 10,
                    "phases": [{"monthly_price": 40, "starts_at": "2026-10-28", "ends_at": "2026-11-28"}],
                },
            }
        }
    }
    assert validate_config(ok_cfg) == []

    bad_cfg = {
        "plans": {
            "opencode": {
                "dclark": {
                    "phases": [{"monthly_price": 40, "starts_at": "2026-11-28", "ends_at": "2026-10-28"}],
                }
            }
        }
    }
    issues = validate_config(bad_cfg)
    assert any("invalid starts_at/ends_at bounds" in msg for msg in issues)


def test_available_pool_entries_include_monthly_price_and_headroom_usd():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    snap = {
        "collected_at": now.isoformat(),
        "accounts": [
            {
                "provider": "claude",
                "account": "me@example.com",
                "windows": [
                    {
                        "label": "weekly",
                        "used_percent": 95.0,
                        "remaining_percent": 5.0,
                        "window_minutes": 10080,
                        "resets_at": "2026-11-19T00:00:00Z",
                    }
                ],
            },
            {
                "provider": "antigravity",
                "account": "me@example.com",
                "windows": [
                    {
                        "label": "Gemini weekly",
                        "used_percent": 95.0,
                        "remaining_percent": 5.0,
                        "window_minutes": 10080,
                        "resets_at": "2026-11-19T00:00:00Z",
                    }
                ],
            },
        ],
    }
    cfg = {
        "plans": {
            "claude": {"monthly_price": 200},
            "gemini": {"monthly_price": 20},
        }
    }
    enrich_snapshot(snap, now=now, config=cfg)
    entries = {e["provider"]: e for e in pool_entries(snap)}
    assert entries["claude"]["monthly_price"] == 200.0
    assert entries["antigravity"]["monthly_price"] == 20.0
    assert entries["claude"]["headroom_usd"] > entries["antigravity"]["headroom_usd"]
    assert "_waking_hours_per_day" not in snap["accounts"][0]


def test_normalized_pool_and_scope_names_and_omp_disabled():
    assert provider_display_name("antigravity") == "antigravity"
    assert provider_display_name("codex") == "openai"
    assert provider_display_name("pi") == "openai-api"
    assert provider_display_name("omp") == "claude-api"
    assert matrix_pool_scope("", provider="codex") == "codex"
    assert matrix_pool_scope("pi_codex_api", provider="pi") == "—"
    assert matrix_pool_scope("omp_claude", provider="omp") == "—"
    assert DEFAULT_CONFIG["collectors"]["omp_agent_db"]["enabled"] is False
