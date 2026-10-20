"""omp credits: operator override, stale CodexBar readings, expiry nudge."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from aiuse import cli
from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, pool_entries, summary_line
from aiuse.collectors.codexbar import _omp_credits_from_row
from aiuse.credits_override import (
    apply_credit_overrides,
    load_overrides,
    overrides_path,
    run_set_credits,
    save_override,
)
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
EXPIRY = datetime(2026, 11, 8, tzinfo=UTC)


def _codexbar_omp(*, updated: datetime, balance: float = 200.0, total: float = 200.0) -> AccountUsage:
    pct = balance / total * 100
    return AccountUsage(
        source="codexbar",
        provider="omp",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="credits",
                used_percent=100 - pct,
                remaining_percent=pct,
                resets_at=EXPIRY,
                refill_capacity=total,
                refill_capacity_unit="usd",
                window_id="claude-cloud-credits",
            )
        ],
        balance_usd=balance,
        spent_usd=total - balance,
        expires_at=EXPIRY,
        collected_at=updated,
    )


def _snap(account: AccountUsage) -> dict:
    return Snapshot(collected_at=NOW, accounts=[account]).to_dict()


def _override(observed: datetime = NOW - timedelta(minutes=5)) -> dict:
    return {
        "omp": {
            "remaining_usd": 95.57,
            "total_usd": 200.0,
            "expires_at": EXPIRY.isoformat(),
            "note": "console 2026-10-19",
            "observed_at": observed.isoformat(),
        }
    }


def _omp(snap: dict) -> dict:
    return next(a for a in snap["accounts"] if a["provider"] == "omp")


def test_expires_at_in_account_json_and_from_codexbar_row():
    row = {
        "provider": "claude",
        "usage": {
            "updatedAt": "2026-10-19T10:00:00Z",
            "details": [
                {
                    "rows": [
                        {
                            "id": "claude-cloud-credits",
                            "usageValue": 95.57,
                            "secondaryValue": "Expires 2026-11-08T00:00:00Z",
                            "progress": {"used": 104.43, "total": 200},
                        }
                    ]
                }
            ],
        },
    }
    account = _omp_credits_from_row(row)
    assert account is not None and account.expires_at == EXPIRY
    assert account.to_dict()["expires_at"] == EXPIRY.isoformat()


def test_override_beats_older_codexbar_100_percent():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=1)))
    apply_credit_overrides(snap, _override(), now=NOW)
    omp = _omp(snap)
    assert omp["source"] == "operator-override"
    assert omp["balance_usd"] == 95.57
    assert omp["spent_usd"] == 104.43
    assert omp["observed_at"] == (NOW - timedelta(minutes=5)).isoformat()
    assert omp["superseded_reading"]["source"] == "codexbar"
    assert omp["superseded_reading"]["balance_usd"] == 200.0
    (window,) = omp["windows"]
    assert round(window["remaining_percent"], 2) == 47.78


def test_override_makes_the_row_when_the_snapshot_has_no_omp():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    enrich_snapshot(snap, now=NOW, credit_overrides=_override())
    (pool,) = available_pools(snap)
    assert pool["provider"] == "omp" and pool["source"] == "operator-override"
    assert pool["balance_usd"] == 95.57
    assert "superseded_reading" not in _omp(snap)
    assert _omp(snap)["credits_label"] == "Claude API credits"


def test_no_override_and_no_omp_row_adds_nothing():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    enrich_snapshot(snap, now=NOW, credit_overrides={})
    assert snap["accounts"] == []


def test_disabled_omp_gets_no_synthetic_row():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    snap["disabled_services"] = {"omp": "operator"}
    enrich_snapshot(snap, now=NOW, credit_overrides=_override())
    assert snap["accounts"] == []


def test_newer_codexbar_showing_more_credits_is_contradicted():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(minutes=1)))
    apply_credit_overrides(snap, _override(), now=NOW)
    assert _omp(snap)["source"] == "operator-override"


def test_newer_codexbar_showing_less_credits_wins():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(minutes=1), balance=80.0))
    apply_credit_overrides(snap, _override(), now=NOW)
    omp = _omp(snap)
    assert omp["source"] == "codexbar"
    assert omp["balance_usd"] == 80.0
    assert omp["observed_at"] == (NOW - timedelta(minutes=1)).isoformat()


def test_codexbar_older_than_24h_without_override_is_unknown_and_not_available():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=30)))
    enrich_snapshot(snap, now=NOW, credit_overrides={})
    omp = _omp(snap)
    assert omp["reading_expired"] is True
    assert omp["stale"] is True
    assert omp["balance_usd"] is None
    assert omp["windows"][0]["state"] == "unknown"
    assert [p for p in available_pools(snap) if p["provider"] == "omp"] == []


def test_fresh_codexbar_without_override_is_untouched():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=2)))
    enrich_snapshot(snap, now=NOW, credit_overrides={})
    omp = _omp(snap)
    assert "reading_expired" not in omp and omp["balance_usd"] == 200.0
    assert omp["source"] == "codexbar"


def test_override_used_through_enrich_and_pool_json_carries_source_and_observed_at():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=1)))
    enrich_snapshot(snap, now=NOW, credit_overrides=_override())
    (pool,) = available_pools(snap)
    assert pool["source"] == "operator-override"
    assert pool["observed_at"] == (NOW - timedelta(minutes=5)).isoformat()
    assert pool["expires_at"] == EXPIRY.isoformat()
    assert round(pool["headroom_percent"], 2) == 47.78
    assert "$104.43 spent / $95.57 left of $200.00" in summary_line(pool)


def test_burn_before_reset_fires_within_21_days_above_50_percent():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=1), balance=150.0))
    enrich_snapshot(snap, now=NOW, credit_overrides={})
    (pool,) = pool_entries(snap, now=NOW)
    nudge = pool["burn_before_reset"]
    assert nudge["days_left"] == 19.5
    assert nudge["remaining_usd"] == 150.0
    assert nudge["remaining_percent"] == 75.0
    assert "use it before it lapses" in nudge["suggestion"]


def test_burn_before_reset_silent_at_or_below_50_percent_or_far_expiry_or_expired():
    low = _snap(_codexbar_omp(updated=NOW - timedelta(hours=1)))
    enrich_snapshot(low, now=NOW, credit_overrides=_override())  # 47.8% left
    assert "burn_before_reset" not in pool_entries(low, now=NOW)[0]

    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=1), balance=150.0))
    enrich_snapshot(snap, now=NOW, credit_overrides={})
    assert "burn_before_reset" not in pool_entries(snap, now=NOW - timedelta(days=5))[0]
    assert "burn_before_reset" not in pool_entries(snap, now=EXPIRY + timedelta(days=1))[0]


def test_remaining_percent_semantics_unchanged():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=1), balance=150.0))
    enrich_snapshot(snap, now=NOW, credit_overrides={})
    window = _omp(snap)["windows"][0]
    assert window["remaining_percent"] == 75.0 and window["used_percent"] == 25.0


def test_set_credits_cli_round_trip(capsys):
    rc = run_set_credits(
        ["omp", "--remaining", "95.57", "--total", "200", "--expires", "2026-11-08", "--note", "console 2026-10-19"]
    )
    assert rc == 0
    assert "recorded: omp $95.57 of $200.00 left, expires 2026-11-08" in capsys.readouterr().out
    entry = load_overrides()["omp"]
    assert entry["remaining_usd"] == 95.57 and entry["total_usd"] == 200.0
    assert entry["expires_at"] == "2026-11-08T00:00:00+00:00"
    assert entry["note"] == "console 2026-10-19"
    assert datetime.fromisoformat(entry["observed_at"]).tzinfo is not None
    assert json.loads(overrides_path().read_text())["omp"] == entry


def test_set_credits_rejects_bad_input(capsys):
    assert run_set_credits(["omp", "--remaining", "300", "--total", "200"]) == 1
    assert run_set_credits(["omp", "--remaining", "1", "--total", "200", "--expires", "soon"]) == 1
    assert load_overrides() == {}
    assert "error" in capsys.readouterr().err


def test_load_overrides_tolerates_garbage():
    overrides_path().parent.mkdir(parents=True, exist_ok=True)
    overrides_path().write_text("not json")
    assert load_overrides() == {}


def test_newer_override_replaces_older_one():
    save_override("omp", remaining=90.0, total=200.0, expires=EXPIRY, note=None, now=NOW)
    save_override("omp", remaining=80.0, total=200.0, expires=EXPIRY, note="later", now=NOW)
    assert load_overrides()["omp"]["remaining_usd"] == 80.0


def test_cli_main_dispatches_set_credits(capsys):
    assert cli.main(["set-credits", "omp", "--remaining", "10", "--total", "200"]) == 0
    assert load_overrides()["omp"]["remaining_usd"] == 10.0


def test_available_prints_burn_line(monkeypatch, capsys):
    """--available prints the nudge under the pool line and carries it in --json."""
    real_now = datetime.now(UTC)
    expiry = real_now + timedelta(days=10)
    account = _codexbar_omp(updated=real_now, balance=150.0)
    account.expires_at = expiry
    account.windows[0].resets_at = expiry
    snapshot = Snapshot(collected_at=real_now, accounts=[account])
    from aiuse.analysis import history

    latest = history.snapshot_dir() / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(snapshot.to_dict()))

    assert cli.main(["--available"]) == 0
    out = capsys.readouterr().out
    assert "burn before reset: omp: $150.00 (75% of the grant) expires in 10d" in out

    assert cli.main(["--available", "--json"]) == 0
    pool = json.loads(capsys.readouterr().out)["available"][0]
    assert pool["burn_before_reset"]["remaining_usd"] == 150.0
    assert pool["source"] == "codexbar" and "observed_at" in pool
