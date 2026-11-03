"""CodexBar's OpenAI API costs belong to Pi, never Codex subscription quota."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from aiuse import credits_override as co
from aiuse.analysis.pace import matrix_pool_scope
from aiuse.collectors import codexbar as cb
from aiuse.models import BillingKind, Snapshot, canonical_provider
from aiuse.report import render_clock_matrix

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def row(used=2.8, limit=0):
    return {
        "provider": "openai",
        "source": "admin-api",
        "account": "CodexBar",
        "usage": {
            "updatedAt": NOW.isoformat(),
            "providerCost": {"used": used, "limit": limit, "currencyCode": "USD", "period": "Last 30 days"},
            "primary": {"usedPercent": 0},
            "openAIAPIUsage": {"historyDays": 30},
        },
    }


@pytest.mark.parametrize("used,limit", [(2.8, 0), (0, 0), (2.8, 200)])
def test_api_costs_are_not_prepaid_balance_or_subscription_quota(used, limit):
    account = cb._from_row(row(used, limit))
    assert account.provider == "pi"
    assert account.billing_kind == BillingKind.PAYG_API
    assert account.spent_usd == used
    assert account.usage_credits.used == used
    assert account.usage_credits.limit is None
    assert account.balance_usd is None
    assert account.windows == []
    assert account.collected_at == NOW
    assert matrix_pool_scope("", provider=account.provider) == "codex-api"
    assert canonical_provider("openai") == "pi"
    assert canonical_provider("openai-codex") == "codex"


@pytest.mark.parametrize("used", [True, -1, float("inf"), None])
def test_bad_api_costs_remain_unknown(used):
    account = cb._from_row(row(used))
    assert account.error
    assert account.balance_usd is None
    assert account.spent_usd is None
    assert account.usage_credits is None


def test_requested_pi_queries_openai_not_codex_or_pi(monkeypatch):
    queried = []
    monkeypatch.setattr(cb, "which", lambda name: "/bin/codexbar")

    def collect(providers, **kwargs):
        queried.extend(providers)
        return [cb._from_row(row())], []

    monkeypatch.setattr(cb, "_collect_live", collect)
    accounts = cb.collect_codexbar(providers=["pi"], timeout_backoff=None)
    assert queried == ["openai"]
    assert accounts[0].provider == "pi"


def test_disabled_sweep_maps_openai_to_known_pi_and_respects_visibility():
    catalog = [{"provider": "openai", "enabled": False}]
    assert cb._disabled_known_query_ids(catalog, already=set(), skip=frozenset()) == ["openai"]
    assert cb._disabled_known_query_ids(catalog, already=set(), skip=frozenset({"pi"})) == []


def test_verified_grant_overrides_cost_meter_without_making_a_second_wallet():
    account = cb._from_row(row()).to_dict()
    override = {
        "total_usd": 100,
        "remaining_usd": 70,
        "observed_at": NOW.isoformat(),
        "expires_at": None,
        "source": "operator-override",
    }
    snap = {"accounts": [account]}
    co.apply_credit_overrides(snap, {"pi": override}, now=NOW)
    assert account["balance_usd"] == 70
    assert account["usage_credits"] is None
    assert account["billing_kind"] == "prepaid_balance"


@pytest.mark.parametrize("gated", [False, True])
def test_pi_spend_only_and_explanatory_rows_keep_scope(gated):
    now = datetime.now(UTC)
    account = cb._from_row(row())
    account.collected_at = now
    if gated:
        account.rate_gated_by = {
            "provider": "pi",
            "account": "CodexBar",
            "window_label": "API rate",
            "resets_at": (now + timedelta(hours=1)).isoformat(),
            "evidence": "synthetic test rate gate",
        }
    text = render_clock_matrix([], snapshot=Snapshot(collected_at=now, accounts=[account]), color=False, width=160)
    line = next(line for line in text.splitlines() if "codex-api" in line)
    assert "pi" in line
    if gated:
        assert line.index("codex-api") < line.index("rate-gated until")
    else:
        assert "$2.80" in line
        assert "↓" not in line  # Spend is not a countdown balance.


@pytest.mark.parametrize("provider", ["pi", "openai", "PI", "OpenAI"])
@pytest.mark.parametrize("all_accounts", ["pi", "openai"])
@pytest.mark.parametrize("response_provider", ["pi", "openai"])
def test_all_account_aliases_query_openai_and_keep_each_account(monkeypatch, provider, all_accounts, response_provider):
    calls = []
    monkeypatch.setattr(cb, "which", lambda name: "/bin/codexbar")
    monkeypatch.setattr(cb, "_codexbar_bin", lambda: "/bin/codexbar")

    def usage(argv, **kwargs):
        calls.append(argv)
        return [dict(row(used), provider=response_provider, account=name) for name, used in [("a", 2), ("b", 3)]]

    monkeypatch.setattr(cb, "_run_codexbar_usage", usage)
    accounts = cb.collect_codexbar(
        providers=[provider], all_accounts_providers=frozenset({all_accounts}), timeout_backoff=None
    )
    assert len(calls) == 1
    assert calls[0][calls[0].index("--provider") + 1] == "openai"
    assert "--all-accounts" in calls[0]
    assert [(a.provider, a.account, a.spent_usd) for a in accounts] == [("pi", "a", 2), ("pi", "b", 3)]


@pytest.mark.parametrize("providers", ["enabled", ["pi", "codex"], ["openai", "codex"]])
@pytest.mark.parametrize("all_accounts", ["pi", "openai"])
def test_cache_supplement_queries_vendor_alias_and_replaces_single_cached_account(
    tmp_path, monkeypatch, providers, all_accounts
):
    now = datetime.now(UTC)
    cache = tmp_path / "widget.json"
    cache.write_text(
        json.dumps(
            {
                "generatedAt": now.isoformat(),
                "entries": [
                    {"provider": "codex", "quotaOwnerKey": "subscription", "updatedAt": now.isoformat()},
                    {
                        "provider": "openai",
                        "quotaOwnerKey": "cached-active",
                        "updatedAt": now.isoformat(),
                        "providerCost": row()["usage"]["providerCost"],
                    },
                ],
            }
        )
    )
    calls = []
    monkeypatch.setattr(cb, "_codexbar_bin", lambda: "/bin/codexbar")

    def usage(argv, **kwargs):
        calls.append(argv)
        return [dict(row(), account=name) for name in ("a", "b")]

    monkeypatch.setattr(cb, "_run_codexbar_usage", usage)
    accounts = cb.collect_codexbar(
        providers=providers,
        use_cache=True,
        cache_path=cache,
        all_accounts_providers=frozenset({all_accounts}),
        skip_providers=frozenset({"omp", "bedrock"}),
        timeout_backoff=None,
    )
    assert len(calls) == 1
    assert calls[0][calls[0].index("--provider") + 1] == "openai"
    assert "--all-accounts" in calls[0]
    assert [(a.provider, a.account) for a in accounts] == [("codex", "subscription"), ("pi", "a"), ("pi", "b")]
