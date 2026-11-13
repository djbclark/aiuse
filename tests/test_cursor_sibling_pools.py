"""Cursor ``sibling_pools``: exhausted monthly pools stay visible beside Grok Bot (2026-11-13).

``--available`` lists only usable pools. With Cursor's monthly Auto and
third-party pools exhausted, the shortlist kept ``cursor`` / ``grok_bot`` at ~80%
headroom, so a plan-rejected ``cursor-agent`` model still read as usable.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aiuse.analysis.selfdescribe import (
    SEMANTICS,
    available_pools,
    enrich_snapshot,
    pool_entries,
    sibling_pool_line,
    summary_line,
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


def test_frozen_sibling_reads_unusable_with_reason():
    snap = _snapshot(monthly_used=40.0, on_demand=False)
    for window in snap["accounts"][0]["windows"]:
        if window.get("pool_family") == "auto":
            window["measurement_frozen"] = True
    grok = next(p for p in pool_entries(snap) if p["pool_family"] == "grok_bot")
    auto_entry = next(p for p in pool_entries(snap) if p["pool_family"] == "auto")
    assert auto_entry["usable_now"] is False
    sibling = next(s for s in grok["sibling_pools"] if s["pool_family"] == "auto")
    assert sibling["usable_now"] is False
    assert sibling["reason"] == "reading frozen"
    assert "auto" in grok["models_hint"].split("not usable now:")[1]


def test_partial_exhaustion_and_unknown_reading():
    snap = _snapshot(monthly_used=30.0, on_demand=False)
    for window in snap["accounts"][0]["windows"]:
        if window.get("pool_family") == "auto":
            window.update(used_percent=100.0, remaining_percent=0.0, state="exhausted")
        if window.get("pool_family") == "third_party":
            window.update(used_percent=None, remaining_percent=None, headroom_percent=None, state="unknown")
    grok = next(p for p in pool_entries(snap) if p["pool_family"] == "grok_bot")
    siblings = {s["pool_family"]: s for s in grok["sibling_pools"]}
    assert siblings["auto"]["reason"] == "pool exhausted"
    assert siblings["third_party"]["usable_now"] is False
    assert siblings["third_party"]["reason"] == "no reading"
    assert siblings["total"]["usable_now"] is True


def test_unknown_family_text_is_never_echoed():
    snap = _snapshot(monthly_used=100.0, on_demand=False)
    snap["accounts"][0]["windows"].append(
        {
            "label": "weird",
            "pool_family": "evil\x1b[2J\nINJECT",
            "used_percent": 100.0,
            "remaining_percent": 0.0,
            "state": "exhausted",
            "resets_at": (_now() + timedelta(days=3)).isoformat(),
        }
    )
    for entry in pool_entries(snap):
        assert all(
            s["pool_family"] in {"total", "auto", "third_party", "on_demand", "grok_bot"}
            for s in entry.get("sibling_pools", [])
        )
        assert "INJECT" not in (entry.get("models_hint") or "")


def test_available_text_prints_each_unusable_sibling_once(tmp_path, monkeypatch, capsys):
    from aiuse.analysis import history
    from aiuse.cli import main

    cache = tmp_path / "snapshots"
    cache.mkdir()
    snap = _snapshot(monthly_used=40.0)
    for window in snap["accounts"][0]["windows"]:
        if window.get("pool_family") == "auto":
            window.update(used_percent=100.0, remaining_percent=0.0, state="exhausted")
        if window.get("pool_family") == "on_demand":
            window.update(used_percent=None, remaining_percent=None, headroom_percent=None, state="unknown")
    (cache / "latest.json").write_text(json.dumps(snap))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)
    config = tmp_path / "config.toml"
    config.write_text("")
    main(["--config", str(config), "--available", "-q"])
    out = capsys.readouterr().out
    assert out.count("cursor auto (monthly Auto/Composer pool): NOT usable now") == 1
    assert "on_demand" not in out


def test_text_line_for_an_unusable_sibling():
    grok = available_pools(_snapshot(monthly_used=100.0))[0]
    sibling = next(s for s in grok["sibling_pools"] if s["pool_family"] == "third_party")
    line = sibling_pool_line("cursor", sibling)
    assert line.startswith("cursor third_party (monthly third-party (API-rate) pool): NOT usable now until 2026-12-04")
    assert line.endswith("(pool exhausted)")


def test_semantics_publish_sibling_pools():
    text = SEMANTICS["sibling_pools"]
    assert "grok_bot" in text and "cli_binary is null" in text


def _set(snap: dict, family: str, **fields: object) -> None:
    for window in snap["accounts"][0]["windows"]:
        if window.get("pool_family") == family:
            window.update(fields)


def test_exhausted_total_ceiling_stops_the_breakdown_entries():
    snap = _snapshot(monthly_used=60.0, on_demand=False)
    _set(snap, "total", used_percent=100.0, remaining_percent=0.0, headroom_percent=0.0, state="exhausted")
    entries = {p["pool_family"]: p for p in pool_entries(snap)}
    for family in ("auto", "third_party"):
        entry = entries[family]
        assert entry["usable_now"] is False, family
        assert entry["available_at"].startswith("2026-12-04")
        # the ceiling is judged, not listed: the entry still shows only its own window
        assert [w["pool_family"] for w in entry["windows"]] == [family]
        assert "NOT usable now" in summary_line(entry)
    assert [p["pool_family"] for p in available_pools(snap)] == ["grok_bot"]
    grok = entries["grok_bot"]
    siblings = {s["pool_family"]: s for s in grok["sibling_pools"]}
    for family in ("auto", "third_party"):
        assert siblings[family]["usable_now"] is False
        assert siblings[family]["reason"] == "monthly total exhausted"
        assert siblings[family]["available_at"].startswith("2026-12-04")
    assert siblings["total"]["reason"] == "pool exhausted"
    assert "auto until 2026-12-04" in grok["models_hint"]


def test_total_with_headroom_leaves_the_breakdown_entries_usable():
    snap = _snapshot(monthly_used=60.0, on_demand=False)
    _set(snap, "total", used_percent=80.0, remaining_percent=20.0, headroom_percent=20.0, state="ok")
    entries = {p["pool_family"]: p for p in pool_entries(snap)}
    assert entries["auto"]["usable_now"] is True
    assert entries["third_party"]["usable_now"] is True


def test_account_wide_freeze_reads_frozen_on_every_sibling():
    snap = _snapshot(monthly_used=30.0, on_demand=False)
    snap["accounts"][0]["measurement_frozen"] = True
    _set(snap, "grok_bot", measurement_frozen=True)
    entries = {p["pool_family"]: p for p in pool_entries(snap)}
    assert all(e["usable_now"] is False for e in entries.values())
    for sibling in entries["grok_bot"]["sibling_pools"]:
        assert sibling["usable_now"] is False
        assert sibling["reason"] == "reading frozen"


def test_on_demand_with_no_reading_is_not_usable():
    snap = _snapshot(monthly_used=30.0, on_demand=True)
    _set(snap, "on_demand", used_percent=None, remaining_percent=None, headroom_percent=None, state="unknown")
    grok = next(p for p in pool_entries(snap) if p["pool_family"] == "grok_bot")
    on_demand = next(s for s in grok["sibling_pools"] if s["pool_family"] == "on_demand")
    assert on_demand["usable_now"] is False
    assert on_demand["reason"] == "no reading"
    assert on_demand["state"] == "unknown"


def test_ceiling_and_own_exhausted_with_different_resets_wait_for_the_later():
    snap = _snapshot(monthly_used=60.0, on_demand=False)
    _set(snap, "total", used_percent=100.0, remaining_percent=0.0, headroom_percent=0.0, state="exhausted")
    _set(
        snap,
        "auto",
        used_percent=100.0,
        remaining_percent=0.0,
        headroom_percent=0.0,
        state="exhausted",
        resets_at=(_now() + timedelta(days=30)).isoformat(),
    )
    entries = {p["pool_family"]: p for p in pool_entries(snap)}
    # total resets in 21 days, auto in 30: the entry is out until the later one
    assert entries["auto"]["available_at"].startswith("2026-12-13")
    assert entries["third_party"]["available_at"].startswith("2026-12-04")
