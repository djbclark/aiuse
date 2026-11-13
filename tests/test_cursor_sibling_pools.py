"""Cursor ``sibling_pools``: exhausted monthly pools stay visible beside Grok Bot (2026-11-13).

``--available`` lists only usable pools. With Cursor's monthly Auto and
third-party pools exhausted, the shortlist kept ``cursor`` / ``grok_bot`` at ~80%
headroom, so a plan-rejected ``cursor-agent`` model still read as usable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiuse.analysis.selfdescribe import (
    SEMANTICS,
    available_pools,
    enrich_snapshot,
    pool_entries,
    sibling_pool_line,
)
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot


def _now() -> datetime:
    return datetime(2026, 11, 13, 0, 0, 0, tzinfo=timezone.utc)


def _window(label: str, window_id: str, used: float, *, days: int, minutes: int = 43200) -> QuotaWindow:
    return QuotaWindow(
        label=label,
        window_id=window_id,
        used_percent=used,
        remaining_percent=100.0 - used,
        resets_at=_now() + timedelta(days=days),
        window_minutes=minutes,
    )


def _snapshot(*, monthly_used: float, grok_used: float = 20.0, on_demand: bool = True) -> dict:
    windows = [
        _window("total", "primary", monthly_used, days=21),
        _window("auto", "secondary", monthly_used, days=21),
        _window("third-party", "tertiary", monthly_used, days=21),
        _window("grok-bot", "cursor-grok-bot", grok_used, days=5, minutes=10080),
    ]
    if on_demand:
        windows.append(_window("on-demand", "on-demand", 102.2, days=21))
    account = AccountUsage(
        source="codexbar",
        provider="cursor",
        account="user@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=windows,
    )
    return enrich_snapshot(Snapshot(collected_at=_now(), accounts=[account]).to_dict(), now=_now())


def test_exhausted_monthly_pools_ride_on_the_grok_bot_entry():
    pools = available_pools(_snapshot(monthly_used=100.0))
    assert [p["pool_family"] for p in pools] == ["grok_bot"]
    grok = pools[0]
    assert grok["usable_now"] is True
    # cursor-agent does not spend the Grok Bot allowance: no CLI to point a dispatcher at.
    assert grok["cli_binary"] is None
    siblings = {s["pool_family"]: s for s in grok["sibling_pools"]}
    assert set(siblings) == {"total", "auto", "third_party", "on_demand"}
    for family in ("auto", "third_party"):
        assert siblings[family]["usable_now"] is False
        assert siblings[family]["state"] == "exhausted"
        assert siblings[family]["reason"] == "pool exhausted"
        assert siblings[family]["headroom_percent"] == 0.0
        assert siblings[family]["available_at"].startswith("2026-12-04")
    assert siblings["auto"]["kind"] == "monthly Auto/Composer pool"
    assert siblings["third_party"]["kind"] == "monthly third-party (API-rate) pool"
    assert siblings["on_demand"]["usable_now"] is False
    assert "not usable now: " in grok["models_hint"]
    assert "auto until 2026-12-04" in grok["models_hint"]
    assert "third_party until 2026-12-04" in grok["models_hint"]
    assert "on_demand" not in grok["models_hint"]
    assert grok["models_hint"].startswith("Cursor Grok Bot weekly allowance")


def test_healthy_cursor_keeps_cli_binary_and_lists_grok_bot_as_sibling():
    pools = available_pools(_snapshot(monthly_used=30.0, on_demand=False))
    by_family = {p["pool_family"]: p for p in pools}
    assert {"auto", "third_party", "grok_bot", "total"} >= set(by_family)
    auto = by_family["auto"]
    assert auto["cli_binary"] == "cursor-agent"
    grok_sibling = next(s for s in auto["sibling_pools"] if s["pool_family"] == "grok_bot")
    assert grok_sibling["usable_now"] is True
    assert "reason" not in grok_sibling
    assert grok_sibling["available_at"] is None
    # Nothing is out: the Grok Bot hint carries the "not drawn by cursor-agent" note only.
    grok = by_family["grok_bot"]
    assert "not usable now" not in grok["models_hint"]
    assert "sibling_pools" in grok["models_hint"]


def test_single_pool_cursor_account_has_no_siblings():
    account = AccountUsage(
        source="codexbar",
        provider="cursor",
        account="user@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[_window("auto", "secondary", 10.0, days=10)],
    )
    snap = enrich_snapshot(Snapshot(collected_at=_now(), accounts=[account]).to_dict(), now=_now())
    (entry,) = pool_entries(snap)
    assert "sibling_pools" not in entry


def test_other_providers_do_not_gain_sibling_pools():
    account = AccountUsage(
        source="cswap",
        provider="codex",
        account="user@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            _window("5-hour", "5h", 100.0, days=0, minutes=300),
            _window("weekly", "weekly", 10.0, days=5, minutes=10080),
        ],
    )
    snap = enrich_snapshot(Snapshot(collected_at=_now(), accounts=[account]).to_dict(), now=_now())
    assert all("sibling_pools" not in p for p in pool_entries(snap))


def test_text_line_for_an_unusable_sibling():
    grok = available_pools(_snapshot(monthly_used=100.0))[0]
    sibling = next(s for s in grok["sibling_pools"] if s["pool_family"] == "third_party")
    line = sibling_pool_line("cursor", sibling)
    assert line.startswith("cursor third_party (monthly third-party (API-rate) pool): NOT usable now until 2026-12-04")
    assert line.endswith("(pool exhausted)")


def test_semantics_publish_sibling_pools():
    text = SEMANTICS["sibling_pools"]
    assert "grok_bot" in text and "cli_binary is null" in text
