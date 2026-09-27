from datetime import datetime, timedelta, timezone

from aiuse.collectors.grok import (
    _plan_window,
    _prepaid_balance_usd,
    _product_usage_note,
    collect_grok,
)
from aiuse.collectors.runner import _merge_grok_extra_credits
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, UsageCredits

_WEEKLY_PERIOD = {
    "type": "USAGE_PERIOD_TYPE_WEEKLY",
    "start": "2026-09-24T06:41:20+00:00",
    "end": "2026-10-01T06:41:20+00:00",
}


def _billing_payload(config: dict) -> dict:
    return {"config": config}


def test_prepaid_balance_usd_from_cents():
    assert _prepaid_balance_usd({"val": 1893}) == 18.93
    assert _prepaid_balance_usd({"val": 0}) == 0.0
    assert _prepaid_balance_usd({}) is None
    assert _prepaid_balance_usd(None) is None


def test_collect_grok_returns_quiet_without_auth(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.grok._read_bearer_token", lambda: None)
    assert collect_grok() == []


def test_collect_grok_returns_extra_usage_credits(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.grok._read_bearer_token", lambda: "token")
    monkeypatch.setattr("aiuse.collectors.grok._email_from_auth", lambda: "user@example.com")
    monkeypatch.setattr(
        "aiuse.collectors.grok._fetch_billing",
        lambda _token, _timeout: _billing_payload({"prepaidBalance": {"val": 2000}}),
    )

    accounts = collect_grok(timeout=5)

    assert len(accounts) == 1
    assert accounts[0].provider == "grok"
    assert accounts[0].source == "grok_billing"
    assert accounts[0].account == "user@example.com"
    assert accounts[0].usage_credits is not None
    assert accounts[0].usage_credits.remaining == 20.0


def test_collect_grok_parses_plan_window_and_product_pools(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.grok._read_bearer_token", lambda: "token")
    monkeypatch.setattr("aiuse.collectors.grok._email_from_auth", lambda: "user@example.com")
    monkeypatch.setattr(
        "aiuse.collectors.grok._fetch_billing",
        lambda _token, _timeout: _billing_payload(
            {
                "prepaidBalance": {"val": 1446},
                "creditUsagePercent": 100.0,
                "currentPeriod": _WEEKLY_PERIOD,
                "productUsage": [
                    {"product": "GrokBuild", "usagePercent": 93.0},
                    {"product": "GrokChat", "usagePercent": 7.0},
                ],
            }
        ),
    )

    accounts = collect_grok(timeout=5)

    assert len(accounts) == 1
    row = accounts[0]
    assert row.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert len(row.windows) == 1
    window = row.windows[0]
    assert window.label == "weekly plan"
    assert window.window_minutes == 10080
    assert window.used_percent == 100.0
    assert window.remaining_percent == 0.0
    assert window.resets_at == datetime(2026, 10, 1, 6, 41, 20, tzinfo=timezone.utc)
    assert row.usage_credits is not None
    assert row.usage_credits.remaining == 14.46
    assert any("GrokBuild 93% used" in note for note in row.notes)
    assert any("GrokChat 7% used" in note for note in row.notes)


def test_collect_grok_without_period_keeps_wallet_only_row(monkeypatch):
    """Legacy payload shape (no currentPeriod): wallet row, no windows."""
    monkeypatch.setattr("aiuse.collectors.grok._read_bearer_token", lambda: "token")
    monkeypatch.setattr("aiuse.collectors.grok._email_from_auth", lambda: "user@example.com")
    monkeypatch.setattr(
        "aiuse.collectors.grok._fetch_billing",
        lambda _token, _timeout: _billing_payload({"prepaidBalance": {"val": 2000}}),
    )

    accounts = collect_grok(timeout=5)

    assert len(accounts) == 1
    row = accounts[0]
    assert row.windows == []
    assert row.billing_kind == BillingKind.UNKNOWN
    assert row.usage_credits is not None
    assert row.usage_credits.remaining == 20.0


def test_collect_grok_returns_plan_row_without_prepaid_balance(monkeypatch):
    """A subscriber with an empty wallet still has a plan window and reset."""
    monkeypatch.setattr("aiuse.collectors.grok._read_bearer_token", lambda: "token")
    monkeypatch.setattr("aiuse.collectors.grok._email_from_auth", lambda: "user@example.com")
    monkeypatch.setattr(
        "aiuse.collectors.grok._fetch_billing",
        lambda _token, _timeout: _billing_payload({"creditUsagePercent": 42.0, "currentPeriod": _WEEKLY_PERIOD}),
    )

    accounts = collect_grok(timeout=5)

    assert len(accounts) == 1
    row = accounts[0]
    assert row.usage_credits is None
    assert len(row.windows) == 1
    assert row.windows[0].used_percent == 42.0
    assert row.windows[0].resets_at is not None


def test_plan_window_maps_period_type_to_clock():
    weekly = _plan_window({"creditUsagePercent": 1.0, "currentPeriod": _WEEKLY_PERIOD})
    assert weekly is not None
    assert weekly.label == "weekly plan"
    assert weekly.window_minutes == 10080

    hourly = _plan_window({"currentPeriod": {"type": "USAGE_PERIOD_TYPE_HOURLY", "end": "2026-10-01T07:41:20+00:00"}})
    assert hourly is not None
    assert hourly.label == "hourly plan"
    assert hourly.window_minutes == 60
    assert hourly.used_percent is None

    monthly = _plan_window({"currentPeriod": {"type": "USAGE_PERIOD_TYPE_MONTHLY", "end": "2026-10-24T06:41:20+00:00"}})
    assert monthly is not None
    assert monthly.label == "monthly plan"
    assert monthly.window_minutes == 43200

    unknown = _plan_window({"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEIRD", "end": "2026-10-01T06:41:20+00:00"}})
    assert unknown is not None
    assert unknown.label == "plan"
    assert unknown.window_minutes is None
    assert unknown.resets_at is not None


def test_plan_window_none_without_period_or_reset():
    assert _plan_window({}) is None
    assert _plan_window({"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY"}}) is None
    assert _plan_window({"currentPeriod": "not-a-dict"}) is None


def test_product_usage_note_skips_malformed_entries():
    assert _product_usage_note({}) is None
    assert _product_usage_note({"productUsage": [{"product": "GrokBuild"}, {"usagePercent": 5.0}]}) is None
    note = _product_usage_note({"productUsage": [{"product": "GrokBuild", "usagePercent": 93.0}]})
    assert note == "Plan pools: GrokBuild 93% used"


def test_merge_grok_extra_credits_folds_into_codexbar_row():
    codexbar = AccountUsage(
        source="codexbar",
        provider="grok",
        account="user@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="Grok usage limit", used_percent=100.0, remaining_percent=0.0)],
        notes=["Live data fetched by CodexBar via grok-cli-proxy."],
    )
    billing = AccountUsage(
        source="grok_billing",
        provider="grok",
        account="user@example.com",
        usage_credits=UsageCredits(remaining=18.93, currency="USD"),
        notes=["Live data fetched from Grok billing (Extra Usage Credits)."],
    )
    accounts = [codexbar, billing]
    _merge_grok_extra_credits(accounts)

    assert len(accounts) == 1
    assert accounts[0].source == "codexbar"
    assert accounts[0].usage_credits is not None
    assert accounts[0].usage_credits.remaining == 18.93
    assert any("Extra Usage Credits" in note for note in accounts[0].notes)
    # Host windows win: the billing window must not duplicate beside them.
    assert [w.label for w in accounts[0].windows] == ["Grok usage limit"]


def test_merge_grok_folds_plan_window_into_windowless_host():
    host = AccountUsage(
        source="codexbar",
        provider="grok",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[],
    )
    billing = AccountUsage(
        source="grok_billing",
        provider="grok",
        windows=[
            QuotaWindow(
                label="weekly plan",
                used_percent=100.0,
                resets_at=datetime.now(timezone.utc) + timedelta(days=3),
                window_minutes=10080,
            )
        ],
        usage_credits=UsageCredits(remaining=14.46, currency="USD"),
    )
    accounts = [host, billing]
    _merge_grok_extra_credits(accounts)

    assert len(accounts) == 1
    assert accounts[0].source == "codexbar"
    assert [w.label for w in accounts[0].windows] == ["weekly plan"]
    assert accounts[0].usage_credits is not None


def test_merge_grok_keeps_wallet_without_host_row():
    """CodexBar being disabled must not hide a known prepaid wallet."""
    billing = AccountUsage(
        source="grok_billing",
        provider="grok",
        usage_credits=UsageCredits(remaining=18.93, currency="USD"),
    )
    accounts = [billing]
    _merge_grok_extra_credits(accounts)

    assert len(accounts) == 1
    assert accounts[0].source == "grok_billing"
    assert accounts[0].usage_credits is not None
    assert accounts[0].usage_credits.remaining == 18.93


def test_merge_grok_keeps_window_only_billing_row_without_host():
    """A plan window alone (no wallet, no host) is still worth a row."""
    billing = AccountUsage(
        source="grok_billing",
        provider="grok",
        windows=[QuotaWindow(label="weekly plan", used_percent=100.0, window_minutes=10080)],
    )
    accounts = [billing]
    _merge_grok_extra_credits(accounts)

    assert len(accounts) == 1
    assert accounts[0].source == "grok_billing"
    assert len(accounts[0].windows) == 1
