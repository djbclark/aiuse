"""Tests for Cursor pool fix-ups (hidden_pools, ceilings, schema 1.3)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aiuse.analysis.history import compute_learned_burn_rates
from aiuse.analysis.pace import compute_pace
from aiuse.analysis.selfdescribe import (
    SCHEMA_VERSION,
    apply_exclusions,
    available_pools,
    enrich_snapshot,
    summary_line,
)
from aiuse.chat_format import render_chat_report
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, Urgency, UseOrLoseAlert
from aiuse.report import render_clock_matrix, render_report


def _now() -> datetime:
    return datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _cursor_account(*, auto_used: float = 30.0, third_used: float = 90.0, total_used: float = 71.0) -> AccountUsage:
    reset = _now() + timedelta(days=10)
    return AccountUsage(
        source="codexbar",
        provider="cursor",
        account="user@example.com",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="total",
                window_id="primary",
                used_percent=total_used,
                remaining_percent=100.0 - total_used,
                resets_at=reset,
                window_minutes=44640,
            ),
            QuotaWindow(
                label="auto",
                window_id="secondary",
                used_percent=auto_used,
                remaining_percent=100.0 - auto_used,
                resets_at=reset,
                window_minutes=44640,
            ),
            QuotaWindow(
                label="third-party",
                window_id="tertiary",
                used_percent=third_used,
                remaining_percent=100.0 - third_used,
                resets_at=reset,
                window_minutes=44640,
            ),
        ],
    )


def _cursor_fixture_dict() -> dict:
    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account()])
    return enrich_snapshot(snap.to_dict())


def test_for_chat_headline_pace_ignores_breakdown_rows():
    # third-party alone would be +176% pace; total is modest.
    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account(third_used=95.0, total_used=71.0)])
    text = render_chat_report(snap, [], config={})
    assert "3rd-party" in text
    assert "pace +176%" not in text


def test_for_chat_exit_zero_when_only_breakdown_would_alert(monkeypatch):
    from aiuse import cli as cli_mod

    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account(third_used=95.0)])
    monkeypatch.setattr(cli_mod, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli_mod, "analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr(cli_mod, "check_dependencies", lambda _c: [])
    monkeypatch.setattr(cli_mod, "load_config", lambda _p=None: {"analysis": {}, "collectors": {}, "timeouts": {}})
    assert cli_mod.main(["--for-chat", "-q"]) == 0


def test_full_report_shows_ceiling_binding_note():
    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account(auto_used=30.0, total_used=71.0)])
    text = render_report(snap, [], config={}, full=True, color=False)
    assert "total binds" in text
    assert "auto: own 70% left, capped by total at 29%" in text


def test_chat_shows_ceiling_binding_note():
    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account(auto_used=30.0, total_used=71.0)])
    text = render_chat_report(snap, [], config={})
    assert "total binds" in text


def _cursor_matrix_lines(text: str) -> list[str]:
    """Clock-matrix data rows only (exclude sublimit/binding note continuations)."""
    bands = {"slow", "mid", "use", "n/a", "error", "empty"}
    return [line for line in text.splitlines() if line.split() and line.split()[0] in bands]


def _total_conserve_alert(*, remaining: float = 29.0) -> UseOrLoseAlert:
    return UseOrLoseAlert(
        urgency=Urgency.HIGH,
        provider="cursor",
        account="user@example.com",
        window_label="total",
        remaining_percent=remaining,
        days_until_reset=10.0,
        plan=None,
        message="conserve total",
        source="codexbar",
        score=44.0,
        kind="conserve",
        window_minutes=44640,
    )


def test_matrix_hides_total_row_when_breakdown_present_and_alert_on_total():
    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account()])
    alerts = [_total_conserve_alert()]
    config = {"analysis": {"show_total_rows": False}}
    text = render_clock_matrix(alerts, snapshot=snap, config=config, color=False, width=160)
    matrix = _cursor_matrix_lines(text)
    assert not any(" total " in f" {line} " for line in matrix)
    assert any(" auto " in f" {line} " for line in matrix)


def test_matrix_shows_total_when_it_is_the_only_cursor_row():
    reset = _now() + timedelta(days=10)
    snap = Snapshot(
        collected_at=_now(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="cursor",
                account="user@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="total",
                        window_id="primary",
                        used_percent=71.0,
                        remaining_percent=29.0,
                        resets_at=reset,
                        window_minutes=44640,
                    )
                ],
            )
        ],
    )
    alerts = [_total_conserve_alert()]
    text = render_clock_matrix(alerts, snapshot=snap, config={"analysis": {}}, color=False, width=160)
    assert any(" total " in f" {line} " for line in _cursor_matrix_lines(text))


def test_matrix_auto_shows_effective_left_without_binding_note():
    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account(auto_used=30.0, total_used=71.0)])
    text = render_clock_matrix([], snapshot=snap, config={"analysis": {}}, color=False, width=160)
    auto_line = next(line for line in text.splitlines() if " auto " in f" {line} ")
    assert "71u/29l" in auto_line
    assert not any("total binds" in line for line in text.splitlines())


def test_no_tui_board_hides_total_row(tmp_path, monkeypatch, capsys):
    from aiuse import cli as cli_mod

    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account()])
    alerts = [_total_conserve_alert()]
    config = tmp_path / "config.toml"
    config.write_text("[analysis]\nshow_total_rows = false\n")
    monkeypatch.setattr(cli_mod, "run_collectors", lambda _c: snap)
    monkeypatch.setattr(cli_mod, "analyze_use_or_lose", lambda *_a, **_k: alerts)
    monkeypatch.setattr(cli_mod, "check_dependencies", lambda _c: [])
    assert cli_mod.main(["--config", str(config), "--providers", "cursor", "--no-tui", "-q"]) == 2
    out = capsys.readouterr().out
    matrix = _cursor_matrix_lines(out)
    assert not any(" total " in f" {line} " for line in matrix)
    assert any(" auto " in f" {line} " for line in matrix)


def test_tui_board_hides_total_row(capsys):
    from aiuse.tui.app import run_usage_app

    snap = Snapshot(collected_at=_now(), accounts=[_cursor_account()])
    run_usage_app(
        snap,
        [_total_conserve_alert()],
        config={"analysis": {"show_total_rows": False}},
        full=False,
        quiet=True,
    )
    out = capsys.readouterr().out
    matrix = _cursor_matrix_lines(out)
    assert not any(" total " in f" {line} " for line in matrix)
    assert any(" auto " in f" {line} " for line in matrix)


def test_available_cursor_breakdown_cap_within_total_monthly():
    data = _cursor_fixture_dict()
    auto_pool = next(p for p in available_pools(data) if p.get("pool_family") == "auto")
    third_pool = next(p for p in available_pools(data) if p.get("pool_family") == "third_party")
    auto_line = summary_line(auto_pool)
    third_line = summary_line(third_pool)
    assert "[cap within total (monthly)]" in auto_line
    assert "[cap within total (monthly)]" in third_line
    assert "shared weekly" not in auto_line
    assert "shared weekly" not in third_line


def test_grok_bot_models_hint():
    data = _cursor_fixture_dict()
    data["accounts"][0]["windows"].append(
        {
            "label": "grok-bot",
            "window_id": "cursor-grok-bot",
            "used_percent": 5.0,
            "remaining_percent": 95.0,
            "resets_at": (_now() + timedelta(days=3)).isoformat(),
            "window_minutes": 10080,
        }
    )
    enriched = enrich_snapshot(data)
    grok = next(w for w in enriched["accounts"][0]["windows"] if w.get("pool_family") == "grok_bot")
    assert grok["models_hint"] == (
        "Cursor Grok Bot weekly allowance (counted on your Cursor account; "
        "not shared with Grok app/CLI/grok.com usage; size may come from a linked SuperGrok plan)"
    )


def test_available_hides_ceiling_and_hidden_pools(tmp_path, monkeypatch, capsys):
    from aiuse.analysis import history
    from aiuse.cli import main

    cache = tmp_path / "snapshots"
    cache.mkdir()
    (cache / "latest.json").write_text(json.dumps(_cursor_fixture_dict()))
    monkeypatch.setattr(history, "snapshot_dir", lambda: cache)
    config = tmp_path / "config.toml"
    config.write_text('[hidden_pools]\n"cursor/grok_bot" = "hide grok"\n')

    assert main(["--config", str(config), "--available", "--json", "-q"]) == 0
    payload = json.loads(capsys.readouterr().out)
    families = {p.get("pool_family") for p in payload["available"]}
    assert "total" not in families
    assert "auto" in families or "third_party" in families

    # hidden_pools on a row not in fixture is a no-op; test apply on dict directly
    from aiuse.analysis.selfdescribe import apply_hidden_pools_dict, filter_ceiling_pools, pool_entries

    snap = _cursor_fixture_dict()
    snap["accounts"][0]["windows"].append(
        {
            "label": "grok-bot",
            "window_id": "cursor-grok-bot",
            "used_percent": 10.0,
            "remaining_percent": 90.0,
            "resets_at": (_now() + timedelta(days=3)).isoformat(),
            "window_minutes": 10080,
        }
    )
    apply_hidden_pools_dict(snap, {"hidden_pools": {"cursor/grok_bot": "hide"}})
    enrich_snapshot(snap)
    pools = filter_ceiling_pools(pool_entries(snap), show_total_rows=False)
    labels = {w.get("label") for p in pools for w in p.get("windows") or []}
    assert "grok-bot" not in labels
    assert "total" not in labels


def test_cursor_grok_alias_in_excluded_and_hidden_pools():
    snap = _cursor_fixture_dict()
    snap["accounts"][0]["windows"].append(
        {
            "label": "grok-bot",
            "window_id": "cursor-grok-bot",
            "used_percent": 5.0,
            "remaining_percent": 95.0,
            "resets_at": (_now() + timedelta(days=3)).isoformat(),
            "window_minutes": 10080,
            "pool_family": "grok_bot",
        }
    )
    enrich_snapshot(snap)
    pools = [p for p in snap["accounts"][0]["windows"] if p.get("pool_family") == "grok_bot"]
    assert pools
    entries = [
        {
            "provider": "cursor",
            "pool_family": "grok_bot",
            "headroom_percent": 95.0,
            "usable_now": True,
            "windows": pools,
        }
    ]
    _, excluded = apply_exclusions(entries, {"cursor/cursor_grok": "legacy excluded"})
    assert excluded and excluded[0]["reason"] == "legacy excluded"

    from aiuse.analysis.selfdescribe import apply_hidden_pools_dict

    snap2 = _cursor_fixture_dict()
    snap2["accounts"][0]["windows"].append(
        {
            "label": "grok-bot",
            "window_id": "cursor-grok-bot",
            "used_percent": 5.0,
            "remaining_percent": 95.0,
            "resets_at": (_now() + timedelta(days=3)).isoformat(),
            "window_minutes": 10080,
        }
    )
    hidden = apply_hidden_pools_dict(snap2, {"hidden_pools": {"cursor/cursor_grok": "legacy hidden"}})
    assert "cursor/grok_bot" in hidden


def test_json_contract_cursor_families_and_window_ids():
    data = _cursor_fixture_dict()
    account = data["accounts"][0]
    by_id = {w["window_id"]: w for w in account["windows"]}
    assert by_id["primary"]["pool_family"] == "total"
    assert by_id["primary"]["role"] == "ceiling"
    assert by_id["secondary"]["pool_family"] == "auto"
    assert by_id["secondary"]["role"] == "breakdown"
    assert by_id["tertiary"]["pool_family"] == "third_party"
    assert by_id["secondary"]["effective_left_percent"] == pytest.approx(29.0)
    assert by_id["secondary"]["ceiling_binding"] is True
    assert SCHEMA_VERSION == "1.4"


def test_compute_pace_no_exhaustion_at_zero_used():
    window = QuotaWindow(
        label="total",
        used_percent=0.0,
        remaining_percent=100.0,
        resets_at=_now() + timedelta(days=20),
        window_minutes=44640,
    )
    pace = compute_pace(window, now=_now(), learned_rate_per_day=0.5, learned_sample_count=5)
    assert pace is not None
    assert pace.projected_exhaust_at is None


def test_compute_pace_no_exhaustion_spend_cap_without_spend():
    window = QuotaWindow(
        label="on-demand",
        window_id="on-demand",
        used_percent=0.0,
        remaining_percent=100.0,
        resets_at=_now() + timedelta(days=20),
        window_minutes=44640,
        refill_capacity_unit="usd",
        refill_capacity=100.0,
    )
    pace = compute_pace(window, now=_now(), learned_rate_per_day=0.5, learned_sample_count=5)
    assert pace is not None
    assert pace.projected_exhaust_at is None


def test_learned_burn_rates_per_cursor_window(tmp_path):
    from unittest.mock import patch

    now = _now()
    reset = now + timedelta(days=10)

    def snap(rem_total: float, rem_auto: float) -> dict:
        return {
            "collected_at": now.isoformat(),
            "accounts": [
                {
                    "provider": "cursor",
                    "account": "u@example.com",
                    "windows": [
                        {
                            "label": "total",
                            "window_id": "primary",
                            "remaining_percent": rem_total,
                            "window_minutes": 44640,
                            "resets_at": reset.isoformat(),
                        },
                        {
                            "label": "auto",
                            "window_id": "secondary",
                            "remaining_percent": rem_auto,
                            "window_minutes": 44640,
                            "resets_at": reset.isoformat(),
                        },
                    ],
                }
            ],
        }

    (tmp_path / "a.json").write_text(json.dumps(snap(80.0, 70.0)))
    (tmp_path / "b.json").write_text(
        json.dumps(
            {
                **snap(60.0, 50.0),
                "collected_at": (now - timedelta(days=1)).isoformat(),
            }
        )
    )
    current = Snapshot(
        collected_at=now,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="cursor",
                account="u@example.com",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="total",
                        window_id="primary",
                        remaining_percent=40.0,
                        resets_at=reset,
                        window_minutes=44640,
                    ),
                    QuotaWindow(
                        label="auto",
                        window_id="secondary",
                        remaining_percent=20.0,
                        resets_at=reset,
                        window_minutes=44640,
                    ),
                ],
            )
        ],
    )
    # The retention cutoff is measured from the real clock; pin it to the
    # fixture's `now` so the 2026-07 snapshots never age out of the 90 days
    # (they did on 2026-10-18 and this test failed on the date alone).
    with (
        patch("aiuse.analysis.history.snapshot_dir", return_value=tmp_path),
        patch("aiuse.analysis.history.utcnow", return_value=now),
    ):
        rates = compute_learned_burn_rates(current=current, retention_days=90, min_snapshots=2)
    assert "cursor:cursor_total:monthly" in rates
    assert "cursor:cursor_auto:monthly" in rates
    assert rates["cursor:cursor_total:monthly"] != rates["cursor:cursor_auto:monthly"]


def test_cursor_on_demand_is_a_wallet_suffix_not_a_percent_row():
    from aiuse.models import UsageCredits

    account = _cursor_account(auto_used=12.0, total_used=13.0, third_used=89.0)
    account.usage_credits = UsageCredits(remaining=1.64, used=18.36, limit=20.0)
    account.windows.append(
        QuotaWindow(
            label="on-demand",
            window_id="on-demand",
            used_percent=92.0,
            remaining_percent=8.0,
            resets_at=_now() + timedelta(days=10),
            window_minutes=43200,
        )
    )
    text = render_clock_matrix(
        [],
        snapshot=Snapshot(collected_at=_now(), accounts=[account]),
        config={"analysis": {}},
        color=False,
        width=180,
    )
    auto = next(line for line in text.splitlines() if " auto " in f" {line} ")
    assert "13u/87l" in auto
    assert "92u" not in auto
    assert auto.endswith("↓$1.64 prepaid")
    assert "(counts down)" not in auto
    assert not any(" on-demand " in f" {line} " for line in text.splitlines())


def test_on_demand_does_not_bind_account_headroom():
    data = _cursor_fixture_dict()
    data["accounts"][0]["windows"].append(
        {
            "label": "on-demand",
            "window_id": "on-demand",
            "used_percent": 100.0,
            "remaining_percent": 0.0,
            "window_minutes": 43200,
        }
    )
    enriched = enrich_snapshot(data)
    account = enriched["accounts"][0]
    assert account["binding_window"] != "on-demand"
    assert account["usable_now"] is True
    assert "on_demand" not in {pool.get("pool_family") for pool in available_pools(enriched)}
