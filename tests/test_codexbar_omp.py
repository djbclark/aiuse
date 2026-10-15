from datetime import UTC, datetime

from aiuse.collectors import codexbar
from aiuse.collectors.codexbar import _omp_credits_from_row, collect_codexbar
from aiuse.models import AccountUsage, BillingKind


def _claude_row(**details_row):
    row = {
        "id": "claude-cloud-credits",
        "label": "Cloud credits",
        "usageValue": 249.269521,
        "secondaryValue": "Expires 2026-11-05T07:59:00Z",
        "progress": {"used": 0.730479, "total": 250},
    }
    row.update(details_row)
    return {
        "provider": "claude",
        "source": "web",
        "usage": {
            "updatedAt": "2026-10-15T10:58:55Z",
            "details": [{"title": "Cloud credits", "rows": [row]}],
        },
    }


def test_omp_row_carries_balance_and_expiry_window():
    account = _omp_credits_from_row(_claude_row())

    assert account is not None
    assert account.provider == "omp"
    assert account.source == "codexbar"
    assert account.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert account.balance_usd == 249.269521
    (window,) = account.windows
    assert window.label == "credits"
    assert window.resets_at == datetime(2026, 11, 5, 7, 59, tzinfo=UTC)
    assert window.refill_capacity == 250.0
    assert round(window.remaining_percent or 0, 2) == 99.71
    assert account.spent_usd == 0.730479


def test_omp_spent_falls_back_to_total_minus_remaining():
    account = _omp_credits_from_row(_claude_row(progress={"total": 250}))

    assert account is not None
    assert account.spent_usd == 0.730479


def test_omp_row_without_total_is_a_plain_balance():
    row = _claude_row(progress=None)

    account = _omp_credits_from_row(row)

    assert account is not None
    assert account.windows == []
    assert account.billing_kind == BillingKind.PREPAID_BALANCE
    assert account.balance_usd == 249.269521
    assert account.spent_usd is None


def test_omp_plain_balance_keeps_spent_when_codexbar_reports_it():
    account = _omp_credits_from_row(_claude_row(progress={"used": 0.73}))

    assert account is not None
    assert account.windows == []
    assert account.spent_usd == 0.73


def test_omp_row_absent_without_cloud_credits_or_for_other_providers():
    assert _omp_credits_from_row({"provider": "claude", "usage": {"details": []}}) is None
    assert _omp_credits_from_row({"provider": "claude", "usage": {}}) is None
    other = _claude_row()
    other["provider"] = "codex"
    assert _omp_credits_from_row(other) is None


def test_cached_run_gets_omp_from_one_live_claude_query(monkeypatch):
    cached = [AccountUsage(source="codexbar", provider="claude")]
    monkeypatch.setattr(codexbar, "_read_widget_snapshot", lambda **_: cached)
    queried: list[list[str | None]] = []

    def fake_query(provider_list, **_):
        queried.append(provider_list)
        return [("claude", [_claude_row()])]

    monkeypatch.setattr(codexbar, "_query_providers", fake_query)

    accounts = collect_codexbar(providers=["claude"], use_cache=True)

    assert [a.provider for a in accounts] == ["claude", "omp"]
    assert queried == [["claude"]]


def test_cached_run_skips_the_live_query_when_omp_is_disabled(monkeypatch):
    monkeypatch.setattr(
        codexbar, "_read_widget_snapshot", lambda **_: [AccountUsage(source="codexbar", provider="claude")]
    )
    monkeypatch.setattr(codexbar, "_query_providers", lambda *a, **k: (_ for _ in ()).throw(AssertionError))

    accounts = collect_codexbar(providers=["claude"], use_cache=True, skip_providers=frozenset({"omp"}))

    assert [a.provider for a in accounts] == ["claude"]


def test_cached_run_survives_a_failed_live_query(monkeypatch):
    monkeypatch.setattr(
        codexbar, "_read_widget_snapshot", lambda **_: [AccountUsage(source="codexbar", provider="claude")]
    )
    monkeypatch.setattr(codexbar, "_query_providers", lambda *a, **k: [("claude", codexbar.CollectorError("boom"))])

    assert [a.provider for a in collect_codexbar(providers=["claude"], use_cache=True)] == ["claude"]


def test_omp_scope_is_claude():
    from aiuse.analysis.pace import independent_pool_key, matrix_pool_scope

    pool_id = independent_pool_key("credits", provider="omp")

    assert pool_id == "omp_claude"
    assert matrix_pool_scope(pool_id, provider="omp") == "claude"
    assert independent_pool_key("credits", provider="claude") is None
