"""omp (Claude cloud-session credits) in --for-chat, --json and the summary lines."""

from __future__ import annotations

from datetime import UTC, datetime

from aiuse.analysis.selfdescribe import SEMANTICS, enrich_snapshot
from aiuse.chat_format import render_chat_report
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot
from aiuse.serve import _snapshot_from_accounts_dict

NOW = datetime(2026, 10, 15, 11, 0, tzinfo=UTC)
EXPIRY = datetime(2026, 11, 5, 7, 59, tzinfo=UTC)


def _omp(*, spent: float | None = 0.73, window: bool = True) -> AccountUsage:
    windows = []
    if window:
        windows.append(
            QuotaWindow(
                label="credits",
                used_percent=0.29,
                remaining_percent=99.71,
                resets_at=EXPIRY,
                refill_capacity=250.0,
                refill_capacity_unit="usd",
                window_id="claude-cloud-credits",
            )
        )
    return AccountUsage(
        source="codexbar",
        provider="omp",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW if window else BillingKind.PREPAID_BALANCE,
        windows=windows,
        balance_usd=249.27,
        spent_usd=spent,
        collected_at=NOW,
    )


def _chat(account: AccountUsage) -> list[str]:
    return render_chat_report(Snapshot(collected_at=NOW, accounts=[account]), []).splitlines()


def test_chat_line_shows_the_expiring_grant_in_dollars():
    lines = _chat(_omp())

    i = next(n for n, line in enumerate(lines) if "**omp · credits**" in line)
    assert lines[i + 1] == ("   ↳ `100% left · ↑ $0.73 spent · ↓ $249.27 left of $250.00 plan · expires in 20d 20h`")


def test_chat_line_without_spent_keeps_the_plain_window_form():
    lines = _chat(_omp(spent=None))

    i = next(n for n, line in enumerate(lines) if "**omp · credits**" in line)
    assert lines[i + 1] == "   ↳ `100% left · resets in 20d 20h`"


def test_chat_plain_balance_omp_shows_spent_and_left():
    lines = _chat(_omp(window=False))

    assert any("**omp**: `↑ $0.73 spent · ↓ $249.27 plan`" in line for line in lines)


def test_json_carries_spent_usd_only_when_set():
    assert _omp().to_dict()["spent_usd"] == 0.73
    assert "spent_usd" not in _omp(spent=None).to_dict()
    assert "spent_usd" not in AccountUsage(source="codexbar", provider="claude").to_dict()


def test_summary_line_and_semantics_describe_the_grant():
    snap = Snapshot(collected_at=NOW, accounts=[_omp()]).to_dict()

    enrich_snapshot(snap, now=NOW)

    (line,) = [line for line in snap["summary_lines"] if line.startswith("omp")]
    assert line.startswith(
        "omp claude: credits: ok (<1% used / 100% left, pace -99%, ↑ $0.73 spent · ↓ $249.27 left of $250.00 plan"
    )
    assert ", expires " in line
    (window,) = snap["accounts"][0]["windows"]
    assert window["pool_family"] == "claude"
    assert "spent_usd" in SEMANTICS and "spent_usd" in snap["semantics"]


def test_cached_snapshot_round_trip_keeps_the_grant():
    snap = Snapshot(collected_at=NOW, accounts=[_omp()]).to_dict()

    (account,) = _snapshot_from_accounts_dict(snap).accounts

    assert account.spent_usd == 0.73
    (window,) = account.windows
    assert (window.refill_capacity, window.refill_capacity_unit) == (250.0, "usd")
    assert window.window_id == "claude-cloud-credits"


def test_cached_snapshot_round_trip_keeps_wallets():
    from aiuse.models import UsageCredits

    grok = AccountUsage(
        source="codexbar",
        provider="grok",
        usage_credits=UsageCredits(used=0.36, limit=2.0, remaining=1.64, used_percent=18.0),
    )
    snap = Snapshot(collected_at=NOW, accounts=[grok]).to_dict()

    (account,) = _snapshot_from_accounts_dict(snap).accounts

    assert account.usage_credits is not None
    assert (account.usage_credits.remaining, account.usage_credits.limit) == (1.64, 2.0)
