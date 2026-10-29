from datetime import UTC, datetime

from aiuse.collectors import codexbar
from aiuse.models import Snapshot
from aiuse.report import _api_inventory_note
from aiuse.serve import _snapshot_from_accounts_dict


def test_native_bedrock_credit_offset_survives_snapshot_roundtrip():
    row = {
        "provider": "bedrock",
        "usage": {
            "providerCost": {"used": 16.88721901, "currencyCode": "USD"},
            "updatedAt": "2026-10-29T20:00:00Z",
            "bedrockReadAt": "2026-10-29T18:00:00Z",
            "bedrockBilling": {
                "account_id": "123456789012",
                "billing_month": "2026-10",
                "scope": "aws_account",
                "gross_usd": 16.88721901,
                "credits_usd": -16.88721901,
                "net_usd": 0.0,
                "estimated": True,
            },
        },
    }
    account = codexbar._from_row(row)
    snapshot = Snapshot(collected_at=datetime(2026, 10, 29, 20, tzinfo=UTC), accounts=[account])
    restored = _snapshot_from_accounts_dict(snapshot.to_dict()).accounts[0]
    assert restored.bedrock_billing == account.bedrock_billing
    assert restored.bedrock_billing["read_at"] == "2026-10-29T18:00:00Z"
    note = _api_inventory_note(restored)
    assert "$16.89 AWS MTD" in note
    assert _api_inventory_note(account) == note
    assert "credits" not in note
    assert "net" not in note
    assert "$3.45" not in note
    assert restored.balance_usd is None
    assert restored.source == "codexbar"  # existing source pins still select this collector


def test_native_zero_is_measured_legacy_zero_is_unknown():
    row = {"provider": "bedrock", "usage": {"providerCost": {"used": 0}}}
    assert codexbar._from_row(row).error == "UNKNOWN"
    row["usage"]["bedrockBilling"] = {"gross_usd": 0, "credits_usd": 0, "net_usd": 0}
    account = codexbar._from_row(row)
    assert account.error is None
    assert "$0.00 PAYG" in _api_inventory_note(account)


def test_http_context_is_copied_into_each_provider_worker(monkeypatch):
    client = codexbar._CodexbarHttp("http://localhost:1234", None)
    seen = []

    def query(provider, **kwargs):
        seen.append(codexbar._CODEXBAR_HTTP.get())
        return []

    monkeypatch.setattr(codexbar, "_query_provider", query)
    token = codexbar._CODEXBAR_HTTP.set(client)
    try:
        codexbar._query_providers(["claude", "codex"], timeout=1)
    finally:
        codexbar._CODEXBAR_HTTP.reset(token)
    assert seen == [client, client]


def test_widget_bedrock_row_is_replaced_by_native_billing(monkeypatch):
    from aiuse.models import AccountUsage, BillingKind

    legacy = AccountUsage(source="codexbar", provider="bedrock", error="UNKNOWN", billing_kind=BillingKind.PAYG_API)
    monkeypatch.setattr(codexbar, "_read_widget_snapshot", lambda **kw: [legacy])
    monkeypatch.setattr(codexbar, "_live_omp_supplement", lambda *a, **kw: [])
    monkeypatch.setattr(codexbar, "_codexbar_http_if_up", lambda *a: None)
    calls = []

    def native(**kwargs):
        calls.append(True)
        return [
            {
                "provider": "bedrock",
                "usage": {
                    "providerCost": {"used": 3.45},
                    "bedrockBilling": {"gross_usd": 3.45, "credits_usd": -3.45, "net_usd": 0},
                },
            }
        ]

    monkeypatch.setattr(codexbar, "native_bedrock_payload", native)
    for providers in ("enabled", ["bedrock"]):
        rows = codexbar.collect_codexbar(providers=providers, use_cache=True, timeout_backoff=None)
        assert len(rows) == 1
        assert rows[0].usage_credits.used == 3.45
    assert len(calls) == 2
