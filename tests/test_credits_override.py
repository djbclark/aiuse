"""omp credits: operator override, stale CodexBar readings, expiry nudge."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

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


def test_operator_override_alone_never_makes_a_row():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    enrich_snapshot(snap, now=NOW, credit_overrides=_override())
    assert snap["accounts"] == []


def test_admin_api_reading_may_make_the_row_when_none_exists():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    entry = dict(_override()["omp"], source="admin-api")
    enrich_snapshot(snap, now=NOW, credit_overrides={"omp": entry})
    (pool,) = available_pools(snap)
    assert pool["provider"] == "omp" and pool["source"] == "admin-api"
    assert pool["balance_usd"] == 95.57
    assert "superseded_reading" not in _omp(snap)
    assert _omp(snap)["credits_label"] == "Claude API credits"


def test_admin_api_reading_does_not_resurrect_a_hidden_omp():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    snap["hidden_pools"] = {"omp": "operator"}
    enrich_snapshot(snap, now=NOW, credit_overrides={"omp": dict(_override()["omp"], source="admin-api")})
    assert snap["accounts"] == []


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
    assert {k: v for k, v in json.loads(overrides_path().read_text())["omp"].items() if k != "source"} == {
        k: v for k, v in entry.items() if k != "source"
    }


def test_set_credits_rejects_bad_input(capsys):
    assert run_set_credits(["omp", "--remaining", "300", "--total", "200"]) == 2
    assert run_set_credits(["omp", "--remaining", "1", "--total", "200", "--expires", "soon"]) == 2
    assert load_overrides() == {}
    assert "error" in capsys.readouterr().err


def test_load_overrides_tolerates_garbage():
    overrides_path().parent.mkdir(parents=True, exist_ok=True)
    overrides_path().write_text("not json")
    assert load_overrides() == {}


def test_newer_override_replaces_older_one():
    save_override("omp", remaining=90.0, total=200.0, expires=EXPIRY, note=None, now=NOW)
    save_override("omp", remaining=80.0, total=200.0, expires=EXPIRY, note="later", now=NOW)
    assert load_overrides(now=NOW)["omp"]["remaining_usd"] == 80.0


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


# --- hardening: malformed, expired, stale, hidden ----------------------------------


def _bad(**changes):
    entry = dict(_override()["omp"])
    entry.update(changes)
    return {"omp": entry}


def _applied(overrides, *, snap=None, updated=None):
    snap = snap or _snap(_codexbar_omp(updated=updated or NOW - timedelta(hours=1)))
    enrich_snapshot(snap, now=NOW, credit_overrides=overrides)
    return snap


@pytest.mark.parametrize(
    "changes",
    [
        {"remaining_usd": float("nan")},
        {"remaining_usd": float("inf")},
        {"total_usd": float("inf")},
        {"remaining_usd": "95.57"},
        {"remaining_usd": True, "total_usd": True},
        {"remaining_usd": -1.0},
        {"remaining_usd": 1e9},
        {"total_usd": 0},
        {"observed_at": 12345},
        {"observed_at": ["x"]},
        {"observed_at": "garbage"},
        {"expires_at": 5},
        {"expires_at": {"a": 1}},
        {"observed_at": (NOW + timedelta(days=3)).isoformat()},
    ],
)
def test_malformed_override_is_ignored_and_never_raises(changes):
    snap = _applied(_bad(**changes))
    omp = _omp(snap)
    assert omp["source"] == "codexbar"
    assert omp["balance_usd"] == 200.0
    assert any("credit override ignored" in n for n in omp["notes"])


def test_malformed_override_with_no_omp_row_adds_no_row():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    enrich_snapshot(snap, now=NOW, credit_overrides=_bad(observed_at=12345))
    assert snap["accounts"] == []


def test_wrong_pool_name_is_ignored():
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    enrich_snapshot(snap, now=NOW, credit_overrides={"claude": _override()["omp"]})
    assert snap["accounts"] == []


def test_load_rejects_bad_entries_with_one_stderr_line_each(capsys):
    overrides_path().parent.mkdir(parents=True, exist_ok=True)
    overrides_path().write_text(
        '{"omp": {"remaining_usd": NaN, "total_usd": 200, "observed_at": "2026-10-19T00:00:00Z"}, '
        '"claude": {"remaining_usd": 1}}'
    )
    assert load_overrides() == {}
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 2
    assert "finite" in err[0] and "unknown pool" in err[1]


def test_load_rejects_non_object_and_garbage(capsys):
    overrides_path().parent.mkdir(parents=True, exist_ok=True)
    overrides_path().write_text("[1, 2]")
    assert load_overrides() == {}
    overrides_path().write_text("\x00not json")
    assert load_overrides() == {}
    assert capsys.readouterr().err.count("aiuse: ignoring") == 2


def test_override_after_grant_expiry_is_ignored():
    snap = _applied(_bad(expires_at=(NOW - timedelta(days=1)).isoformat()))
    omp = _omp(snap)
    assert omp["source"] == "codexbar"
    assert any("expires_at has passed" in n for n in omp["notes"])


def test_override_older_than_7_days_is_ignored_then_codexbar_rules_apply():
    old = (NOW - timedelta(days=8)).isoformat()
    snap = _applied(_bad(observed_at=old), updated=NOW - timedelta(hours=30))
    omp = _omp(snap)
    assert omp["source"] == "codexbar" and omp["reading_expired"] is True
    assert omp["windows"][0]["state"] == "unknown"
    assert [p for p in available_pools(snap) if p["provider"] == "omp"] == []


def test_override_just_under_7_days_still_applies():
    snap = _applied(_bad(observed_at=(NOW - timedelta(days=6, hours=23)).isoformat()))
    assert _omp(snap)["source"] == "operator-override"


def test_override_percent_never_exceeds_100():
    snap = _applied(_bad(remaining_usd=200.0))
    assert _omp(snap)["windows"][0]["remaining_percent"] == 100.0


def test_fresher_codexbar_with_less_wins_over_override():
    snap = _applied(_override(), snap=_snap(_codexbar_omp(updated=NOW - timedelta(minutes=1), balance=50.0)))
    omp = _omp(snap)
    assert omp["source"] == "codexbar" and omp["windows"][0]["remaining_percent"] == 25.0


@pytest.mark.parametrize("section", ["hidden_pools", "disabled_services"])
@pytest.mark.parametrize("key", ["omp", "omp/claude"])
def test_hidden_or_disabled_omp_is_not_resurrected(section, key):
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    snap[section] = {key: "operator"}
    enrich_snapshot(snap, now=NOW, credit_overrides=_override())
    assert snap["accounts"] == []


def test_hidden_omp_row_is_not_rewritten():
    snap = _snap(_codexbar_omp(updated=NOW - timedelta(hours=1)))
    snap["hidden_pools"] = {"omp": "operator"}
    apply_credit_overrides(snap, _override(), now=NOW)
    assert _omp(snap)["source"] == "codexbar"


def test_timezone_naive_timestamps_are_read_as_utc():
    naive_obs = (NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    naive_exp = EXPIRY.replace(tzinfo=None).isoformat()
    snap = _applied(_bad(observed_at=naive_obs, expires_at=naive_exp))
    omp = _omp(snap)
    assert omp["source"] == "operator-override"
    assert omp["observed_at"] == (NOW - timedelta(minutes=5)).isoformat()
    assert omp["expires_at"] == EXPIRY.isoformat()


def _exit_code(argv):
    try:
        return run_set_credits(argv)
    except SystemExit as exc:  # argparse rejects non-numbers and inf/nan itself
        return exc.code


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "-5", "abc"])
def test_set_credits_refuses_nonfinite_and_negative(value):
    assert _exit_code(["omp", "--remaining", value, "--total", "200"]) == 2
    assert _exit_code(["omp", "--remaining", "10", "--total", value]) == 2
    assert load_overrides() == {}


def test_set_credits_remaining_above_total_exits_2():
    assert run_set_credits(["omp", "--remaining", "201", "--total", "200"]) == 2
    assert load_overrides() == {}


def test_burn_nudge_slides_down_near_expiry_and_prints_fraction_of_a_day():
    account = _codexbar_omp(updated=NOW, balance=90.0)  # 45% left
    snap = _snap(account)
    enrich_snapshot(snap, now=NOW, credit_overrides={})
    (pool,) = pool_entries(snap, now=EXPIRY - timedelta(days=2))
    assert "burn_before_reset" in pool  # 45% > 10% bar at 2 days
    (pool,) = pool_entries(snap, now=EXPIRY - timedelta(hours=10))
    assert "expires in 0.4d" in pool["burn_before_reset"]["suggestion"]
    (pool,) = pool_entries(snap, now=EXPIRY - timedelta(days=19))
    assert "burn_before_reset" not in pool  # 45% <= 50% bar at 19 days


# --- third pass: huge ints, config-hidden omp, mismatched totals -------------------


def _write_file(text):
    overrides_path().parent.mkdir(parents=True, exist_ok=True)
    overrides_path().write_text(text)


def test_400_digit_integer_is_ignored_not_fatal(capsys):
    huge = "1" + "0" * 400
    _write_file('{"omp": {"remaining_usd": %s, "total_usd": 200, "observed_at": "%s"}}' % (huge, NOW.isoformat()))
    assert load_overrides() == {}
    assert "finite" in capsys.readouterr().err
    snap = _applied({"omp": {"remaining_usd": int(huge), "total_usd": 200, "observed_at": NOW.isoformat()}})
    assert _omp(snap)["source"] == "codexbar"


def test_dollar_cap_rejects_values_over_a_billion():
    snap = _applied(_bad(remaining_usd=2e9, total_usd=3e9))
    assert _omp(snap)["source"] == "codexbar"


def test_oversized_override_file_is_not_read(capsys):
    _write_file('{"omp": {"note": "' + "x" * 70000 + '"}}')
    assert load_overrides() == {}
    assert "larger than 64 KiB" in capsys.readouterr().err


def test_config_hiding_omp_means_no_override_at_all():
    save_override("omp", remaining=95.57, total=200.0, expires=EXPIRY, note=None, now=datetime.now(UTC))
    assert "omp" in load_overrides()
    for section in ("hidden_pools", "disabled_services"):
        for key in ("omp", "omp/claude"):
            assert load_overrides({section: {key: "operator"}}) == {}
    assert "omp" in load_overrides({"hidden_pools": {"codex": "x"}})


def test_mismatched_total_lets_the_fresher_codexbar_reading_win_and_flags_the_override():
    account = _codexbar_omp(updated=NOW - timedelta(minutes=1), balance=150.0, total=200.0)  # 75%
    ov = _bad(remaining_usd=140.0, total_usd=150.0)  # 93.3%, a mistyped total
    snap = _applied(ov, snap=_snap(account))
    omp = _omp(snap)
    assert omp["source"] == "codexbar"
    assert omp["windows"][0]["remaining_percent"] == 75.0
    assert omp["superseded_override"]["remaining_usd"] == 140.0
    assert omp["superseded_override"]["source"] == "operator-override"


def test_fresher_lower_codexbar_flags_superseded_override():
    snap = _applied(_override(), snap=_snap(_codexbar_omp(updated=NOW - timedelta(minutes=1), balance=50.0)))
    assert "superseded_override" in _omp(snap)


def test_matching_total_keeps_the_contradicted_rule():
    snap = _applied(_override(), snap=_snap(_codexbar_omp(updated=NOW - timedelta(minutes=1))))
    assert _omp(snap)["source"] == "operator-override"


# --- Codex review: grant expiry independent of reading age; set-credits cap; serve cache -------


def test_fresh_reading_for_an_expired_grant_is_unknown_and_not_routable():
    account = _codexbar_omp(updated=NOW - timedelta(minutes=5))
    account.expires_at = NOW - timedelta(hours=1)
    account.windows[0].resets_at = NOW - timedelta(hours=1)
    snap = _applied({}, snap=_snap(account))
    omp = _omp(snap)
    assert omp["grant_expired"] is True and omp["reading_expired"] is True
    assert omp["balance_usd"] is None and omp["windows"][0]["state"] == "unknown"
    assert [p for p in available_pools(snap) if p["provider"] == "omp"] == []


def test_override_without_expires_inherits_a_past_codexbar_expiry():
    account = _codexbar_omp(updated=NOW - timedelta(hours=2))
    account.expires_at = NOW - timedelta(hours=1)
    account.windows[0].resets_at = NOW - timedelta(hours=1)
    ov = _bad(expires_at=None)
    snap = _applied(ov, snap=_snap(account))
    assert _omp(snap).get("grant_expired") is True
    assert [p for p in available_pools(snap) if p["provider"] == "omp"] == []


def test_unexpired_grant_is_untouched():
    snap = _applied({}, snap=_snap(_codexbar_omp(updated=NOW - timedelta(minutes=5))))
    assert "grant_expired" not in _omp(snap) and _omp(snap)["balance_usd"] == 200.0


def test_set_credits_refuses_values_above_the_dollar_cap():
    save_override("omp", remaining=5.0, total=10.0, expires=None, note=None, now=datetime.now(UTC))
    assert run_set_credits(["omp", "--remaining", "2000000000", "--total", "3000000000"]) == 2
    assert load_overrides()["omp"]["remaining_usd"] == 5.0


def test_serve_rebuilds_its_in_memory_cache_when_set_credits_runs(monkeypatch):
    from aiuse import serve

    calls = []

    def fake_live(self):
        calls.append(1)
        return {"snapshot": {"collected_at": datetime.now(UTC).isoformat(), "accounts": []}}

    monkeypatch.setattr(serve._ServeState, "_collect_live", fake_live)
    monkeypatch.setattr(serve._ServeState, "_from_disk_if_fresh", lambda self: None)
    state = serve._ServeState(config={}, max_age_seconds=3600)
    state.get_payload(refresh=False)
    state.get_payload(refresh=False)
    assert len(calls) == 1
    save_override("omp", remaining=5.0, total=10.0, expires=None, note=None, now=datetime.now(UTC))
    state.get_payload(refresh=False)
    assert len(calls) == 2
    state.get_payload(refresh=False)
    assert len(calls) == 2


# --- Codex late findings: time-dependent evaluation behind serve's cache; file source --------------


def test_serve_cached_payload_becomes_unknown_once_expires_at_passes_without_a_file_change(monkeypatch):
    from aiuse import serve

    clock = [NOW.timestamp()]
    monkeypatch.setattr(serve, "_clock", lambda: clock[0])
    save_override(
        "omp",
        remaining=95.0,
        total=200.0,
        expires=NOW + timedelta(seconds=90),
        note=None,
        now=NOW,
        granted=NOW - timedelta(days=5),
    )

    def fake_live(self):
        at = datetime.fromtimestamp(clock[0], UTC)
        account = _codexbar_omp(updated=NOW - timedelta(minutes=30))
        account.expires_at = NOW + timedelta(seconds=90)
        account.windows[0].resets_at = NOW + timedelta(seconds=90)
        snap = _snap(account)
        enrich_snapshot(snap, now=at, credit_overrides=load_overrides({}, now=at))
        snap["collected_at"] = datetime.now(UTC).isoformat()
        return {"snapshot": snap}

    monkeypatch.setattr(serve._ServeState, "_collect_live", fake_live)
    monkeypatch.setattr(serve._ServeState, "_from_disk_if_fresh", lambda self: None)
    state = serve._ServeState(config={}, max_age_seconds=10**9)
    first = _omp(state.get_payload(refresh=False)["snapshot"])
    assert first["balance_usd"] == 95.0 and not first.get("reading_expired")
    clock[0] += 150  # now past expires_at, no file touched
    later = _omp(state.get_payload(refresh=False)["snapshot"])
    assert later["balance_usd"] is None and later["grant_expired"] is True


def test_hand_edited_admin_api_source_in_the_overrides_file_cannot_create_a_row():
    _write_file(
        '{"omp": {"remaining_usd": 50, "total_usd": 200, "source": "admin-api", "observed_at": "%s"}}'
        % datetime.now(UTC).isoformat()
    )
    loaded = load_overrides()
    assert loaded["omp"]["source"] == "operator-override"
    snap = Snapshot(collected_at=NOW, accounts=[]).to_dict()
    enrich_snapshot(snap, now=NOW, credit_overrides=loaded)
    assert not [a for a in snap["accounts"] if a["provider"] == "omp"]


def test_apply_credit_overrides_to_snapshot_adds_admin_api_omp_and_respects_provider_filter():
    from aiuse.credits_override import apply_credit_overrides_to_snapshot

    admin_ov = {"omp": {**_override()["omp"], "source": "admin-api"}}
    snap = Snapshot(collected_at=NOW, accounts=[])
    assert apply_credit_overrides_to_snapshot(snap, {}, overrides=admin_ov, now=NOW) is True
    assert len(snap.accounts) == 1
    assert snap.accounts[0].provider == "omp"
    assert snap.accounts[0].source == "admin-api"
    assert snap.accounts[0].balance_usd == 95.57
    assert snap.accounts[0].spent_usd == 104.43
    assert snap.accounts[0].credits_label == "Claude API credits"

    filtered = Snapshot(collected_at=NOW, accounts=[])
    assert (
        apply_credit_overrides_to_snapshot(
            filtered,
            {"collectors": {"codexbar": {"providers": ["claude"]}}},
            overrides=admin_ov,
            now=NOW,
        )
        is False
    )
    assert filtered.accounts == []


def test_cli_partial_providers_writes_to_samples_not_latest(monkeypatch):
    from aiuse.analysis import history

    latest = history.snapshot_dir() / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text('{"marker": "full"}', encoding="utf-8")

    monkeypatch.setattr(cli, "check_dependencies", lambda _c: [])
    monkeypatch.setattr(
        cli,
        "run_collectors",
        lambda _c: Snapshot(
            collected_at=datetime.now(UTC),
            accounts=[
                AccountUsage(
                    source="openusage_ai",
                    provider="claude",
                    billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                    windows=[QuotaWindow(label="Claude weekly", used_percent=100.0, remaining_percent=0.0)],
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "aiuse.sampler.collect_with_ledger",
        lambda cfg, fn: (fn(cfg), None, None),
    )
    rc = cli.main(["--providers", "claude", "--json", "-q"])
    assert rc in (0, 2)
    assert json.loads(latest.read_text(encoding="utf-8")) == {"marker": "full"}
    samples = list(history.partial_sample_dir().glob("*.json"))
    assert samples
