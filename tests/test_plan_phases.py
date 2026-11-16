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


def test_set_credits_accepts_claude_api_and_openai_api(tmp_path, monkeypatch, capsys):
    from aiuse import credits_override as co

    monkeypatch.setattr(co, "overrides_path", lambda: tmp_path / "credit-overrides.json")
    rc = co.run_set_credits(["claude-api", "--remaining", "50", "--total", "100", "--expires", "2027-01-01"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "recorded: claude-api $50.00 of $100.00 left" in out
    assert co.load_overrides()["omp"]["remaining_usd"] == 50.0


def test_codexbar_parses_reset_credits_confidence_and_zai_rate():
    from aiuse.collectors.codexbar import _from_row

    codex = _from_row(
        {
            "provider": "codex",
            "source": "oauth",
            "usage": {
                "updatedAt": "2026-11-16T16:41:53Z",
                "dataConfidence": "exact",
                "codexResetCredits": {"availableCount": 3},
                "primary": {"usedPercent": 10, "windowMinutes": 300},
            },
        }
    )
    assert any("Data confidence: exact." in n for n in codex.notes)
    assert any("Codex reset credits: 3 available." in n for n in codex.notes)

    zai = _from_row(
        {
            "provider": "zai",
            "source": "api",
            "usage": {
                "updatedAt": "2026-11-16T16:41:53Z",
                "details": [
                    {
                        "title": "Quota details",
                        "rows": [{"label": "Quota rate", "value": "Off-peak", "secondaryValue": "peak in 13h 19m"}],
                    }
                ],
                "primary": {"usedPercent": 9, "windowMinutes": 300},
            },
        }
    )
    assert any("Z.ai quota rate: Off-peak (peak in 13h 19m)." in n for n in zai.notes)


def test_pi_session_refusal_scanner_extracts_429(tmp_path):
    import json

    from aiuse.quota_refusals import scan_pi_quota_refusals

    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    sess = tmp_path / "proj" / "session.jsonl"
    sess.parent.mkdir(parents=True)
    sess.write_text(
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "provider": "zai",
                    "model": "glm-5.3-flash",
                    "stopReason": "error",
                    "errorMessage": "429 QUOTA_EXHAUSTED quotaResetTimeStamp:2026-11-20T00:00:00Z",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    hits = scan_pi_quota_refusals(sessions_dir=tmp_path, now=now)
    assert len(hits) == 1
    assert hits[0]["provider"] == "zai"
    assert hits[0]["source"] == "pi-session"


def test_claudeplatform_prepaid_fallback_without_promo_grant():
    from aiuse.collectors.codexbar import _from_row
    from aiuse.models import BillingKind

    acc = _from_row(
        {
            "provider": "claudeplatform",
            "source": "web",
            "usage": {
                "updatedAt": "2026-11-16T17:11:58Z",
                "providerCost": {"balance": -0.07, "used": -0.07, "currencyCode": "USD", "period": "Prepaid credits"},
                "details": [{"title": "Billing", "rows": [{"label": "This month (UTC)", "value": "$325.38"}]}],
                "identity": {"accountOrganization": "Freedom Included"},
            },
        }
    )
    assert acc.provider == "omp"
    assert acc.error is None
    assert acc.billing_kind == BillingKind.PREPAID_BALANCE
    assert acc.balance_usd == -0.07
    assert acc.spent_usd == 325.38
