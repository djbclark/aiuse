from aiuse.collectors.clinepass import _clinepass_window, collect_clinepass
from aiuse.models import BillingKind


def test_clinepass_window_types():
    assert _clinepass_window("five_hour") == ("ClinePass 5-hour", 300)
    assert _clinepass_window("weekly") == ("ClinePass weekly", 10080)
    assert _clinepass_window("monthly") == ("ClinePass monthly", 43200)


def test_collect_clinepass_parses_official_limits(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "limits": [
                        {"type": "five_hour", "percentUsed": 4, "resetsAt": "2026-08-19T22:17:09Z"},
                        {"type": "weekly", "percentUsed": 32, "resetsAt": "2026-08-23T22:36:15Z"},
                        {"type": "monthly", "percentUsed": 16, "resetsAt": "2026-09-15T22:36:15Z"},
                    ]
                },
            }

    monkeypatch.setattr("aiuse.collectors.clinepass.requests.get", lambda *_args, **_kwargs: FakeResponse())

    accounts = collect_clinepass(environ={"AIUSE_CLINE_API_KEY": "test-key"})
    assert len(accounts) == 1
    account = accounts[0]
    assert account.source == "clinepass"
    assert account.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert [window.label for window in account.windows] == [
        "ClinePass 5-hour",
        "ClinePass weekly",
        "ClinePass monthly",
    ]
    assert [window.used_percent for window in account.windows] == [4.0, 32.0, 16.0]
    assert [window.window_minutes for window in account.windows] == [300, 10080, 43200]
    assert "test-key" not in str(account)
