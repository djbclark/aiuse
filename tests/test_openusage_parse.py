"""Unit tests for OpenUsage limits.v1 parsing (no live app required)."""

import pytest

from aiuse.collectors.base import CollectorError
from aiuse.collectors.openusage import _from_provider, _http_limits
from aiuse.collectors.openusage_sh import collect_openusage_sh
from aiuse.models import BillingKind


def test_openusage_http_rejects_non_http_base_url():
    with pytest.raises(CollectorError, match=r"must use http\(s\)"):
        _http_limits(base_url="file:///etc", timeout=1.0)


def test_openusage_claude_session_weekly():
    acc = _from_provider(
        "claude",
        {
            "displayName": "Claude",
            "plan": "Pro",
            "stale": False,
            "resources": {
                "session": {
                    "kind": "consumption",
                    "unit": "percent",
                    "used": 78,
                    "limit": 100,
                    "remaining": 22,
                    "resetsAt": "2026-07-25T14:10:00Z",
                    "windowSeconds": 18000,
                },
                "weekly": {
                    "kind": "consumption",
                    "unit": "percent",
                    "used": 8,
                    "limit": 100,
                    "remaining": 92,
                    "resetsAt": "2026-07-31T10:00:00Z",
                    "windowSeconds": 604800,
                },
            },
        },
        via="http",
    )
    assert acc.source == "openusage_ai"
    assert acc.provider == "claude"
    assert acc.plan == "Pro"
    assert acc.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert len(acc.windows) == 2
    assert any(w.remaining() == 22.0 for w in acc.windows)
    assert any(w.remaining() == 92.0 for w in acc.windows)


def test_openusage_skips_copilot_chat_completions():
    acc = _from_provider(
        "copilot",
        {
            "displayName": "Copilot",
            "resources": {
                "chat": {
                    "kind": "consumption",
                    "unit": "percent",
                    "used": 10,
                    "remaining": 90,
                    "resetsAt": "2026-08-01T00:00:00Z",
                    "windowSeconds": 2592000,
                },
                "premiumCredits": {
                    "kind": "consumption",
                    "unit": "percent",
                    "used": 5,
                    "remaining": 95,
                    "resetsAt": "2026-08-01T00:00:00Z",
                    "windowSeconds": 2592000,
                },
            },
        },
        via="cli",
    )
    assert all("premium" in w.label.lower() for w in acc.windows)
    assert not any("chat" in w.label.lower() for w in acc.windows)


def test_openusage_balance_resource():
    acc = _from_provider(
        "openrouter",
        {
            "displayName": "OpenRouter",
            "resources": {
                "balance": {"kind": "balance", "unit": "usd", "available": 12.5},
            },
        },
        via="http",
    )
    assert acc.billing_kind == BillingKind.PREPAID_BALANCE
    assert acc.balance_usd == 12.5


def test_openusage_sh_uses_only_explicit_quota_metrics(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.openusage_sh.run_json",
        lambda *_args, **_kwargs: {
            "snapshots": [
                {
                    "provider_id": "codex",
                    "account_id": "codex-cli",
                    "status": "OK",
                    "metrics": {
                        "rate_limit_primary": {"unit": "%", "remaining": 75, "used": 25, "window": "7d"},
                        "cache_hit_ratio": {"unit": "%", "remaining": 99, "used": 1, "window": "all-time"},
                    },
                    "resets": {"rate_limit_primary": "2026-08-01T00:00:00Z"},
                }
            ]
        },
    )
    accounts = collect_openusage_sh()
    assert len(accounts) == 1
    assert accounts[0].source == "openusage_sh"
    assert accounts[0].provider == "codex"
    assert [window.label for window in accounts[0].windows] == ["7d"]
    assert accounts[0].windows[0].remaining() == 75.0
