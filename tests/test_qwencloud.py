from aiuse.collectors.qwencloud import (
    _billing_limit,
    _coding_plan_account,
    _token_plan_account,
    collect_qwencloud,
)
from aiuse.models import BillingKind


def _summary_payload(
    *,
    coding_plan: dict | None = None,
    token_plan: dict | None = None,
    pay_as_you_go: dict | None = None,
) -> dict:
    return {
        "period": {"from": "2026-08-01", "to": "2026-08-22"},
        "free_tier": [],
        "coding_plan": coding_plan or {"subscribed": False},
        "token_plan": token_plan
        or {"subscribed": False, "planName": "Token Plan", "totalCredits": 0, "remainingCredits": 0, "usedPct": 0},
        "pay_as_you_go": pay_as_you_go or {"models": [], "total": {"cost": 0, "currency": "USD"}},
    }


def test_coding_plan_windows_parse():
    account = _coding_plan_account(
        {
            "subscribed": True,
            "plan": "pro",
            "windows": {
                "per_5h": {
                    "remaining": 480,
                    "total": 600,
                    "used_pct": 20.0,
                    "next_reset_at": "2026-08-22T18:00:00.000Z",
                },
                "weekly": {
                    "remaining": 8000,
                    "total": 10000,
                    "used_pct": 20.0,
                    "next_reset_at": "2026-08-25T00:00:00.000Z",
                },
                "monthly": {
                    "remaining": 9000,
                    "total": 12000,
                    "used_pct": 25.0,
                    "next_reset_at": "2026-09-01T00:00:00.000Z",
                },
            },
        }
    )
    assert account is not None
    assert account.provider == "qwencloud"
    assert account.plan == "pro"
    assert account.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert [w.label for w in account.windows] == ["qwen 5-hour", "qwen weekly", "qwen monthly"]
    assert [w.window_minutes for w in account.windows] == [300, 10080, 43800]
    assert account.windows[0].used_percent == 20.0
    assert account.windows[0].refill_capacity == 600.0
    assert account.windows[0].refill_capacity_unit == "credits"
    assert account.windows[0].resets_at is not None
    assert account.windows[0].resets_at.year == 2026


def test_coding_plan_missing_fields_derive_used_from_remaining():
    account = _coding_plan_account(
        {
            "subscribed": True,
            "windows": {"per_5h": {"remaining": 300, "total": 600}},
        }
    )
    assert account is not None
    assert account.windows[0].used_percent == 50.0
    # A window with no usable numbers is dropped rather than crashing.
    assert len(_coding_plan_account({"subscribed": True, "windows": {"per_5h": {}}}).windows) == 0


def test_coding_plan_not_subscribed_is_none():
    assert _coding_plan_account({"subscribed": False}) is None
    assert _coding_plan_account(None) is None


def test_token_plan_credits_parse():
    account = _token_plan_account(
        {"subscribed": True, "planName": "Token Plan Team Edition", "totalCredits": 2500, "remainingCredits": 1500}
    )
    assert account is not None
    assert account.billing_kind == BillingKind.PREPAID_BALANCE
    assert account.credits_remaining == 1500.0
    assert account.windows[0].label == "qwen token plan credits"
    assert account.windows[0].used_percent == 40.0
    assert account.windows[0].refill_capacity == 2500.0


def test_collect_reports_absence_when_nothing_active(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.qwencloud.which", lambda _cmd: "/usr/local/bin/qwencloud")
    monkeypatch.setattr(
        "aiuse.collectors.qwencloud.run_json",
        lambda argv, **_kwargs: _summary_payload(),
    )
    accounts = collect_qwencloud()
    assert len(accounts) == 1
    assert accounts[0].error is not None
    assert "No active QwenCloud" in accounts[0].error


def test_collect_quiet_when_cli_absent(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.qwencloud.which", lambda _cmd: None)
    assert collect_qwencloud() == []


def test_collect_unauthenticated_error_row(monkeypatch):
    from aiuse.collectors.base import CollectorError

    def fail(argv, **_kwargs):
        raise CollectorError("no JSON from qwencloud usage summary: Not authenticated. Run: qwencloud auth login")

    monkeypatch.setattr("aiuse.collectors.qwencloud.which", lambda _cmd: "/usr/local/bin/qwencloud")
    monkeypatch.setattr("aiuse.collectors.qwencloud.run_json", fail)
    accounts = collect_qwencloud()
    assert len(accounts) == 1
    assert "qwencloud auth login" in accounts[0].error


def test_collect_emits_all_three_accounts(monkeypatch):
    def fake_run_json(argv, **_kwargs):
        if "summary" in argv:
            return _summary_payload(
                coding_plan={
                    "subscribed": True,
                    "plan": "lite",
                    "windows": {
                        "per_5h": {"remaining": 560, "total": 700, "used_pct": 20.0},
                        "weekly": {"remaining": 2000, "total": 2500, "used_pct": 20.0},
                    },
                },
                token_plan={"subscribed": True, "planName": "Token Plan", "totalCredits": 100, "remainingCredits": 90},
                pay_as_you_go={"models": [], "total": {"cost": 1.5, "currency": "USD"}},
            )
        assert argv[:3] == ["qwencloud", "billing", "limit"]
        return {"status": "active", "limitAmount": "5.00", "currency": "USD", "alertThreshold": "80"}

    monkeypatch.setattr("aiuse.collectors.qwencloud.which", lambda _cmd: "/usr/local/bin/qwencloud")
    monkeypatch.setattr("aiuse.collectors.qwencloud.run_json", fake_run_json)

    accounts = collect_qwencloud()
    assert [a.plan for a in accounts] == ["lite", "Token Plan", "PAYG"]
    payg = accounts[2]
    assert payg.billing_kind == BillingKind.PAYG_API
    assert payg.usage_credits is not None
    assert payg.usage_credits.used == 1.5
    assert payg.usage_credits.limit == 5.0
    assert payg.usage_credits.used_percent == 30.0


def test_billing_limit_requires_active_status(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.qwencloud.run_json",
        lambda argv, **_kwargs: {"status": "inactive", "limitAmount": "5.00", "currency": "USD"},
    )
    assert _billing_limit(0.1) is None


def test_billing_limit_parse(monkeypatch):
    monkeypatch.setattr(
        "aiuse.collectors.qwencloud.run_json",
        lambda argv, **_kwargs: {"status": "active", "limitAmount": "5.00", "currency": "USD"},
    )
    assert _billing_limit(0.1) == (5.0, "USD")
