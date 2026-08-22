import pytest

from aiuse.collectors.bailian import _account_from_payload, _epoch_ms, collect_bailian
from aiuse.models import BillingKind


def test_token_plan_payload_parses_windows():
    account = _account_from_payload(
        {"per1WeekPercentage": 0.0178846404166, "per1WeekResetTime": 1788016260000},
        plan="Token Plan",
    )
    assert account is not None
    assert account.provider == "alibaba"
    assert account.plan == "Token Plan"
    assert account.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert len(account.windows) == 1
    window = account.windows[0]
    assert window.label == "alibaba weekly"
    assert window.window_minutes == 10080
    assert window.used_percent == pytest.approx(1.78846404166)
    assert window.remaining_percent == pytest.approx(98.21153595834)
    assert window.resets_at is not None
    assert window.resets_at.year == 2026


def test_payload_with_5h_and_weekly():
    account = _account_from_payload(
        {
            "per5HourPercentage": 0.42,
            "per5HourResetTime": 1788000000000,
            "per1WeekPercentage": 0.0,
            "per1WeekResetTime": 1788016260000,
        },
        plan="Coding Plan",
    )
    assert account is not None
    assert [w.label for w in account.windows] == ["alibaba 5-hour", "alibaba weekly"]
    assert [w.used_percent for w in account.windows] == [42.0, 0.0]


def test_empty_or_non_dict_payload_is_none():
    assert _account_from_payload({}, plan="Token Plan") is None
    assert _account_from_payload(None, plan="Token Plan") is None
    # Percentages missing entirely -> no windows -> no account.
    assert _account_from_payload({"unrelated": 1}, plan="Token Plan") is None


def test_epoch_ms_conversion():
    assert _epoch_ms(1788016260000) is not None
    assert _epoch_ms(0) is None
    assert _epoch_ms(None) is None
    assert _epoch_ms("not-a-number") is None


def test_collect_quiet_when_cli_absent(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.bailian.which", lambda _cmd: None)
    assert collect_bailian() == []


def test_collect_quiet_when_unauthenticated(monkeypatch):
    from aiuse.collectors.base import CollectorError

    def fail(argv, **_kwargs):
        raise CollectorError("no JSON from bl usage token-plan: Error: No console access token found.")

    monkeypatch.setattr("aiuse.collectors.bailian.which", lambda _cmd: "/opt/homebrew/bin/bl")
    monkeypatch.setattr("aiuse.collectors.bailian.run_json", fail)
    assert collect_bailian() == []


def test_collect_merges_token_and_coding_plans(monkeypatch):
    def fake_run_json(argv, **_kwargs):
        if "token-plan" in argv:
            return {"per1WeekPercentage": 0.0178, "per1WeekResetTime": 1788016260000}
        assert "coding-plan" in argv
        return {}

    monkeypatch.setattr("aiuse.collectors.bailian.which", lambda _cmd: "/opt/homebrew/bin/bl")
    monkeypatch.setattr("aiuse.collectors.bailian.run_json", fake_run_json)
    accounts = collect_bailian()
    assert len(accounts) == 1
    assert accounts[0].plan == "Token Plan"
    assert accounts[0].source == "bailian"


def test_collect_raises_when_both_plans_error(monkeypatch):
    from aiuse.collectors.base import CollectorError

    def fail(argv, **_kwargs):
        raise CollectorError("HTTP 500")

    monkeypatch.setattr("aiuse.collectors.bailian.which", lambda _cmd: "/opt/homebrew/bin/bl")
    monkeypatch.setattr("aiuse.collectors.bailian.run_json", fail)
    try:
        collect_bailian()
        raise AssertionError("expected CollectorError")
    except Exception as exc:  # noqa: BLE001
        assert "HTTP 500" in str(exc)
        assert not isinstance(exc, AssertionError)
