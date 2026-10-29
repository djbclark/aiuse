"""Bedrock Cost Explorer is held: at most one $0.01 read per six hours.

These tests never call CodexBar or AWS. The store and the activity snapshot
are redirected for the whole module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aiuse.chat_format import _render_prepaid_row
from aiuse.collectors.base import CollectorError
from aiuse.collectors.bedrock_gate import (
    activity_fingerprint,
    cached_payload,
    gated_bedrock_payload,
    load_activity_fingerprint,
    load_store,
    refresh_due,
    snapshot_path,
)
from aiuse.models import AccountUsage, BillingKind, Snapshot
from aiuse.report import _api_inventory_note, render_clock_matrix, render_priority_ladder
from aiuse.timefmt import format_clock

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
READ_AT = "2026-10-29T01:02:00+00:00"


@pytest.fixture(autouse=True)
def _bedrock_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("AIUSE_BEDROCK_STORE", str(tmp_path / "bedrock-spend.json"))
    monkeypatch.setenv("AIUSE_BEDROCK_SNAPSHOT", str(tmp_path / "latest.json"))
    return tmp_path


def _live_row(*, used: float = 16.89, limit: float = 0, updated_at: str = READ_AT) -> dict:
    return {
        "provider": "bedrock",
        "source": "cli",
        "usage": {
            "loginMethod": f"Spend: ${used:.2f}",
            "providerCost": {
                "used": used,
                "limit": limit,
                "currencyCode": "USD",
                "period": "Monthly",
            },
            "updatedAt": updated_at,
            "accountEmail": "secret@example.com",
        },
    }


def _snapshot(used_percent: float) -> dict:
    return {
        "accounts": [
            {
                "provider": "bedrock",
                "balance_usd": 1,
                "windows": [{"used_percent": 99}],
            },
            {
                "provider": "codex",
                "account": "a",
                "balance_usd": None,
                "spent_usd": None,
                "windows": [{"used_percent": used_percent}],
            },
        ]
    }


def test_refresh_due_holds_for_six_hours_then_follows_other_activity():
    assert refresh_due({}, "abc", NOW) is True

    young = {
        "fetched_at": (NOW - timedelta(hours=1)).isoformat(),
        "spent_usd": 16.89,
        "fingerprint": "abc",
    }
    assert refresh_due(young, "moved", NOW) is False

    aged = {
        "fetched_at": (NOW - timedelta(hours=7)).isoformat(),
        "spent_usd": 16.89,
        "fingerprint": "abc",
    }
    assert refresh_due(aged, "abc", NOW) is False
    assert refresh_due(aged, "moved", NOW) is True

    failed = {"fetched_at": (NOW - timedelta(hours=7)).isoformat(), "error": "down"}
    assert refresh_due(failed, "abc", NOW) is True
    failed_young = {"fetched_at": (NOW - timedelta(hours=1)).isoformat(), "error": "down"}
    assert refresh_due(failed_young, "abc", NOW) is False


def test_fingerprint_ignores_bedrock_and_a_missing_snapshot():
    assert activity_fingerprint(None) == ""
    assert activity_fingerprint({"accounts": [{"provider": "bedrock", "balance_usd": 9}]}) == ""
    assert activity_fingerprint(_snapshot(10)) == activity_fingerprint(_snapshot(10))
    assert activity_fingerprint(_snapshot(10)) != activity_fingerprint(_snapshot(11))
    assert load_activity_fingerprint() == ""


def test_gate_calls_live_once_and_keeps_the_email_out_of_the_store():
    calls: list[str] = []

    def live():
        calls.append("live")
        return [_live_row()]

    first = gated_bedrock_payload(live, now=NOW)
    assert calls == ["live"]
    usage = first[0]["usage"]
    assert usage["bedrockReadAt"] == READ_AT
    assert usage["updatedAt"] == NOW.isoformat()
    store = load_store()
    assert store["spent_usd"] == 16.89
    assert store["read_at"] == READ_AT
    assert "secret" not in str(store)

    held = gated_bedrock_payload(live, now=NOW + timedelta(hours=1))
    assert calls == ["live"]
    assert held[0]["usage"]["bedrockReadAt"] == READ_AT
    assert held[0]["usage"]["updatedAt"] == (NOW + timedelta(hours=1)).isoformat()
    assert "accountEmail" not in held[0]["usage"]


def test_failed_read_retries_only_after_six_hours_and_keeps_the_old_clock():
    calls: list[str] = []

    def live():
        calls.append("ok")
        return [_live_row()]

    gated_bedrock_payload(live, now=NOW)

    def fail():
        calls.append("fail")
        raise CollectorError("cost explorer down")

    # Inside the quiet period a failure is not even attempted.
    quiet = gated_bedrock_payload(fail, now=NOW + timedelta(hours=1))
    assert calls == ["ok"]
    assert quiet[0]["usage"]["providerCost"]["used"] == 16.89

    # After six hours, unchanged other activity reuses the stored spend.
    gated_bedrock_payload(fail, now=NOW + timedelta(hours=7))
    assert calls == ["ok"]

    snapshot_path().write_text('{"accounts":[{"provider":"codex","windows":[{"used_percent":3}]}]}', "utf-8")
    held = gated_bedrock_payload(fail, now=NOW + timedelta(hours=7))
    assert calls == ["ok", "fail"]
    assert held[0]["usage"]["providerCost"]["used"] == 16.89
    assert held[0]["usage"]["bedrockReadAt"] == READ_AT
    assert held[0]["usage"]["updatedAt"] == (NOW + timedelta(hours=7)).isoformat()
    assert load_store()["read_at"] == READ_AT
    assert "cost explorer down" in load_store()["error"]

    gated_bedrock_payload(fail, now=NOW + timedelta(hours=8))
    assert calls == ["ok", "fail"]


def test_failure_with_no_prior_spend_is_returned_and_not_retried_immediately():
    calls: list[str] = []

    def fail():
        calls.append("fail")
        raise CollectorError("cost explorer down")

    first = gated_bedrock_payload(fail, now=NOW)
    assert isinstance(first, CollectorError)
    assert calls == ["fail"]
    assert cached_payload(NOW) is None

    second = gated_bedrock_payload(fail, now=NOW + timedelta(minutes=5))
    assert isinstance(second, CollectorError)
    assert calls == ["fail"]


def test_from_row_spend_is_payg_and_not_a_prepaid_balance():
    from aiuse.collectors.codexbar import _from_row

    row = _live_row()
    row["usage"]["bedrockReadAt"] = READ_AT
    account = _from_row(row)
    assert account.balance_usd is None
    assert account.spent_usd is None
    assert account.billing_kind == BillingKind.PAYG_API
    assert account.plan == "PAYG"
    assert account.windows == []
    assert account.usage_credits is not None
    assert account.usage_credits.used == 16.89
    assert account.usage_credits.limit is None
    note = _api_inventory_note(account)
    assert note == "↑ $16.89 PAYG @" + format_clock(datetime.fromisoformat(READ_AT))
    assert any("Bedrock month-to-date spend: $16.89 PAYG." in item for item in account.notes)

    budgeted = _from_row(_live_row(limit=50))
    assert budgeted.usage_credits is not None
    assert budgeted.usage_credits.limit == 50
    assert budgeted.windows == []
    assert _api_inventory_note(budgeted).startswith("↑ $16.89 of $50.00 PAYG")

    raw = _live_row(updated_at=(datetime.fromisoformat(READ_AT) + timedelta(minutes=3)).isoformat())
    raw["usage"]["bedrockReadAt"] = READ_AT
    held = _from_row(raw)
    assert any("held until other usage moves" in item for item in held.notes)


def test_discovery_asks_bedrock_once_even_when_codexbar_lists_it_disabled(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):  # noqa: ARG001
        calls.append(list(argv))
        if "config" in argv:
            return [
                {"provider": "codex", "enabled": True},
                {"provider": "bedrock", "enabled": False},
            ]
        provider = argv[argv.index("--provider") + 1]
        if provider == "bedrock":
            return [_live_row()]
        return [{"provider": provider, "usage": {"primary": {"usedPercent": 1}}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.which", lambda _cmd: "/usr/bin/codexbar")
    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    from aiuse.collectors.codexbar import collect_codexbar

    def bedrock_calls() -> list[list[str]]:
        return [call for call in calls if "bedrock" in call]

    first = collect_codexbar()
    assert len(bedrock_calls()) == 1
    bedrock = next(account for account in first if account.provider == "bedrock")
    assert bedrock.usage_credits is not None and bedrock.usage_credits.used == 16.89
    assert bedrock.balance_usd is None

    calls.clear()
    second = collect_codexbar()
    assert bedrock_calls() == []
    again = next(account for account in second if account.provider == "bedrock")
    assert again.usage_credits is not None and again.usage_credits.used == 16.89
    assert again.account is None


def test_explicit_provider_list_does_not_touch_bedrock(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_json(argv, *, timeout=90.0, allow_empty=False):  # noqa: ARG001
        calls.append(list(argv))
        return [{"provider": "codex", "usage": {"primary": {"usedPercent": 1}}}]

    monkeypatch.setattr("aiuse.collectors.codexbar.which", lambda _cmd: "/usr/bin/codexbar")
    monkeypatch.setattr("aiuse.collectors.codexbar.run_json", fake_run_json)

    from aiuse.collectors.codexbar import collect_codexbar

    accounts = collect_codexbar(providers=["codex"])
    assert [account.provider for account in accounts] == ["codex"]
    assert not any("config" in call or "bedrock" in call for call in calls)


def _bedrock_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "bedrock" in line.lower()]


def test_zero_spend_is_unknown_payg_not_a_prepaid_balance():
    from aiuse.collectors.codexbar import _from_row

    account = _from_row(_live_row(used=0.0))
    assert account.billing_kind == BillingKind.PAYG_API
    assert account.plan == "PAYG"
    assert account.balance_usd is None
    assert account.usage_credits is None
    assert account.error == "UNKNOWN"
    assert _api_inventory_note(account) == "UNKNOWN"
    assert all("$0" not in note and "prepaid" not in note.lower() for note in account.notes)

    spoken = _live_row(used=0.0)
    spoken["usage"]["loginMethod"] = "Spend: $16.89 - Claude 14d: 0 tokens - Requests: 0"
    spoken["usage"]["providerCost"]["used"] = 0
    recovered = _from_row(spoken)
    assert recovered.error is None
    assert recovered.balance_usd is None
    assert recovered.billing_kind == BillingKind.PAYG_API
    assert recovered.usage_credits is not None and recovered.usage_credits.used == 16.89
    assert _api_inventory_note(recovered).startswith("↑ $16.89 PAYG")
    assert "prepaid" not in _api_inventory_note(recovered).lower()


def test_zero_reading_is_not_stored_and_is_not_retried_within_six_hours():
    calls: list[str] = []

    def live():
        calls.append("live")
        return [_live_row(used=0.0)]

    from aiuse.collectors.codexbar import _from_row

    first = gated_bedrock_payload(live, now=NOW)
    assert calls == ["live"]
    assert isinstance(first, list)
    assert load_store().get("spent_usd") is None
    assert load_store().get("error") == "UNKNOWN"
    assert _from_row(first[0]).error == "UNKNOWN"
    assert _from_row(first[0]).billing_kind == BillingKind.PAYG_API

    second = gated_bedrock_payload(live, now=NOW + timedelta(hours=1))
    assert calls == ["live"]
    assert isinstance(second, list)
    assert _from_row(second[0]).error == "UNKNOWN"


def test_board_shows_payg_spend_or_unknown_never_prepaid():
    from aiuse.collectors.codexbar import _from_row

    zero = _from_row(_live_row(used=0.0))
    spent = _from_row(_live_row())
    # The shape the previous parser stored: Spend: $0.00 turned into a prepaid balance.
    legacy_zero = AccountUsage(
        source="codexbar",
        provider="bedrock",
        plan="Spend: $0.00 - Claude 14d: 0 tokens - Requests: 0",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=0.0,
    )
    legacy_spent = AccountUsage(
        source="codexbar",
        provider="bedrock",
        plan="Spend: $16.89",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=16.89,
    )
    when = datetime(2026, 10, 29, 17, 0, tzinfo=UTC)

    def board(account: AccountUsage) -> str:
        snapshot = Snapshot(collected_at=when, accounts=[account])
        return render_clock_matrix([], snapshot=snapshot, color=False, width=160, ai_note=False)

    zero_lines = _bedrock_lines(board(zero))
    assert zero_lines
    assert any("UNKNOWN" in line for line in zero_lines)
    assert all("prepaid" not in line.lower() and "$0" not in line for line in zero_lines)

    spent_lines = _bedrock_lines(board(spent))
    assert any("↑ $16.89 PAYG" in line for line in spent_lines)
    assert all("prepaid" not in line.lower() for line in spent_lines)

    legacy_lines = _bedrock_lines(board(legacy_zero))
    assert any("UNKNOWN" in line for line in legacy_lines)
    assert all("prepaid" not in line.lower() and "$0" not in line for line in legacy_lines)

    legacy_spent_lines = _bedrock_lines(board(legacy_spent))
    assert any("↑ $16.89 PAYG" in line for line in legacy_spent_lines)
    assert all("prepaid" not in line.lower() for line in legacy_spent_lines)

    ladder = render_priority_ladder([], snapshot=Snapshot(collected_at=when, accounts=[legacy_zero]), color=False)
    ladder_lines = _bedrock_lines(ladder)
    assert any("UNKNOWN" in line for line in ladder_lines)
    assert all("prepaid" not in line.lower() and "$0" not in line for line in ladder_lines)

    chat = "\n".join(_render_prepaid_row(legacy_zero, include_account=False))
    assert "UNKNOWN" in chat
    assert "prepaid" not in chat.lower()
    assert "$0" not in chat
    chat_spent = "\n".join(_render_prepaid_row(spent, include_account=False))
    assert "↑ $16.89 PAYG" in chat_spent
    assert "prepaid" not in chat_spent.lower()
