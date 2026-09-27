from aiuse.collectors.grok import _prepaid_balance_usd, collect_grok
from aiuse.collectors.runner import _merge_grok_extra_credits
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, UsageCredits


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
        lambda _token, _timeout: {"config": {"prepaidBalance": {"val": 2000}}},
    )

    accounts = collect_grok(timeout=5)

    assert len(accounts) == 1
    assert accounts[0].provider == "grok"
    assert accounts[0].source == "grok_billing"
    assert accounts[0].account == "user@example.com"
    assert accounts[0].usage_credits is not None
    assert accounts[0].usage_credits.remaining == 20.0


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


def test_merge_grok_extra_credits_keeps_wallet_without_host_row():
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
