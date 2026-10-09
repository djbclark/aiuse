"""Issue #16: a second, native DeepSeek prepaid-balance source."""

from __future__ import annotations

import pytest
import requests

from aiuse.collectors.base import CollectorError
from aiuse.collectors.deepseek import collect_deepseek
from aiuse.collectors.runner import _consolidate_accounts
from aiuse.config import SINGLE_PROVIDER_COLLECTORS, validate_config
from aiuse.models import AccountUsage, BillingKind

KEY_ENV = {"AIUSE_DEEPSEEK_API_KEY": "sk-deepseek-example"}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def _serve(monkeypatch, payload, status_code=200):
    calls: list[tuple[str, dict, float]] = []

    def fake_get(url, timeout, headers):
        calls.append((url, headers, timeout))
        return FakeResponse(payload, status_code)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def test_quiet_until_a_key_is_supplied(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("no request without a key"))
    assert collect_deepseek(environ={}) == []


def test_usd_balance_is_a_prepaid_account(monkeypatch):
    calls = _serve(
        monkeypatch,
        {
            "is_available": True,
            "balance_infos": [
                {
                    "currency": "USD",
                    "total_balance": "110.00",
                    "granted_balance": "10.00",
                    "topped_up_balance": "100.00",
                }
            ],
        },
    )
    [account] = collect_deepseek(timeout=12, environ=KEY_ENV)
    assert calls == [
        (
            "https://api.deepseek.com/user/balance",
            {
                "Authorization": "Bearer sk-deepseek-example",
                "Accept": "application/json",
                "User-Agent": "aiuse DeepSeek collector",
            },
            12,
        )
    ]
    assert (account.source, account.provider) == ("deepseek", "deepseek")
    assert account.billing_kind == BillingKind.PREPAID_BALANCE
    assert account.balance_usd == 110.0
    assert account.credits_remaining is None
    assert "DeepSeek balance: 110.00 USD total, 100.00 topped up, 10.00 granted." in account.notes
    assert "sk-deepseek-example" not in str(account)


def test_cny_balance_is_not_converted_to_usd(monkeypatch):
    _serve(
        monkeypatch,
        {"is_available": False, "balance_infos": [{"currency": "CNY", "total_balance": "0.50"}]},
    )
    [account] = collect_deepseek(environ=KEY_ENV)
    assert account.balance_usd is None
    assert account.credits_remaining == 0.5
    assert any("0.50 CNY total" in note for note in account.notes)
    assert any("is_available: false" in note for note in account.notes)


def test_rejected_key_is_an_error_row(monkeypatch):
    _serve(monkeypatch, {"error": "unauthorized"}, status_code=401)
    [account] = collect_deepseek(environ=KEY_ENV)
    assert account.error is not None and "HTTP 401" in account.error
    assert "sk-deepseek-example" not in account.error


@pytest.mark.parametrize(
    ("payload", "status", "message"),
    [
        ({}, 200, "missing 'balance_infos'"),
        ({"balance_infos": [{"currency": "USD", "total_balance": "n/a"}]}, 200, "no numeric total_balance"),
        (ValueError("bad json"), 200, "invalid JSON"),
        ({}, 500, "HTTP 500"),
    ],
)
def test_bad_answers_raise(monkeypatch, payload, status, message):
    _serve(monkeypatch, payload, status_code=status)
    with pytest.raises(CollectorError, match=message):
        collect_deepseek(environ=KEY_ENV)


def test_registered_as_a_single_provider_collector():
    assert SINGLE_PROVIDER_COLLECTORS["deepseek"] == "deepseek"
    assert not [i for i in validate_config({"collectors": {"deepseek": {"enabled": False}}}) if "deepseek" in i]


def test_native_source_is_selected_over_codexbar():
    rows = [
        AccountUsage(source="codexbar", provider="deepseek", billing_kind=BillingKind.PREPAID_BALANCE, balance_usd=9.0),
        AccountUsage(source="deepseek", provider="deepseek", billing_kind=BillingKind.PREPAID_BALANCE, balance_usd=9.0),
    ]
    selected = _consolidate_accounts(rows, cswap_authoritative=True)
    assert [a.source for a in selected] == ["deepseek"]
