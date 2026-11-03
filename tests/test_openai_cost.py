"""Independent Pi API grant accounting and shared-file safety (no live calls)."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from aiuse import credits_override as co
from aiuse import openai_cost as oc
from aiuse.analysis.pace import matrix_pool_scope
from aiuse.models import AccountUsage, Snapshot

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def bucket(start, end, value, currency="usd"):
    return {
        "start_time": int(start.timestamp()),
        "end_time": int(end.timestamp()),
        "results": [{"amount": {"value": value, "currency": currency}}],
    }


def report(value=5, currency="usd"):
    return json.dumps(
        {"data": [bucket(NOW - timedelta(days=1), NOW, value, currency)], "has_more": False, "next_page": None}
    ).encode()


def fetch_report(value=5, currency="usd"):
    return lambda url, headers, timeout: (200, report(value, currency))


@pytest.fixture
def credit_files(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "overrides_path", lambda: tmp_path / "credit-overrides.json")
    return tmp_path


def grant(provider="pi", remaining=70, observed=NOW):
    return co.save_override(
        provider, remaining=remaining, total=100, expires=None, note=None, granted=NOW - timedelta(days=2), now=observed
    )


def test_costs_are_dollars_not_anthropic_cents():
    total, post = oc.fetch_spend_usd(
        "synthetic-test-token",
        NOW - timedelta(days=2),
        ending_at=NOW,
        after=NOW - timedelta(days=1),
        fetch=fetch_report(),
    )
    assert total == post == 5


@pytest.mark.parametrize(
    "value,currency", [(True, "usd"), (None, "usd"), ("5", "usd"), (-1, "usd"), (float("inf"), "usd"), (5, "eur")]
)
def test_bad_amounts_fail_closed(value, currency):
    with pytest.raises(oc.CostReportError):
        oc.fetch_spend_usd(
            "synthetic-test-token",
            NOW - timedelta(days=2),
            ending_at=NOW,
            after=NOW,
            fetch=fetch_report(value, currency),
        )


def test_pagination_does_not_double_count_repeated_buckets():
    def fetch(url, headers, timeout):
        return 200, json.dumps(
            {"data": [bucket(NOW - timedelta(days=1), NOW, 5)], "has_more": True, "next_page": "cursor"}
        ).encode()

    with pytest.raises(oc.CostReportError, match="duplicate"):
        oc.fetch_spend_usd("synthetic-test-token", NOW - timedelta(days=2), ending_at=NOW, after=NOW, fetch=fetch)


def test_network_failures_never_echo_key_or_body():
    def fetch(url, headers, timeout):
        raise ValueError("synthetic-test-token")

    with pytest.raises(oc.CostReportError) as error:
        oc.fetch_spend_usd("synthetic-test-token", NOW - timedelta(days=2), ending_at=NOW, after=NOW, fetch=fetch)
    assert "synthetic-test-token" not in str(error.value)


def test_refresh_preserves_omp_and_conservatively_charges_overlap(credit_files, monkeypatch):
    monkeypatch.setenv(oc.KEY_ENV, "synthetic-test-token")
    omp = grant("omp")
    co.save_credit_entry(co.readings_file(), "omp", omp, "admin-api")
    grant(observed=NOW - timedelta(hours=1))
    entry = oc.refresh(now=NOW, fetch=fetch_report())
    assert entry["remaining_usd"] == 65  # full overlapping day, never guessed partial spend
    readings = json.loads(co.readings_file().read_text())
    assert readings["omp"]["remaining_usd"] == 70
    assert readings["pi"]["remaining_usd"] == 65


def test_missing_key_or_grant_does_not_write_reading(credit_files, monkeypatch):
    monkeypatch.delenv(oc.KEY_ENV, raising=False)
    with pytest.raises(oc.CostReportError, match="missing"):
        oc.refresh(now=NOW)
    monkeypatch.setenv(oc.KEY_ENV, "synthetic-test-token")
    with pytest.raises(oc.CostReportError, match="verified"):
        oc.refresh(now=NOW)
    assert not co.readings_file().exists()


def test_pi_row_added_beside_existing_omp_without_mixing_balances(credit_files):
    pi = grant()
    omp = grant("omp", remaining=40)
    snap = Snapshot(collected_at=NOW, accounts=[AccountUsage(source="none", provider="omp")])
    assert co.apply_credit_overrides_to_snapshot(snap, overrides={"pi": pi, "omp": omp}, now=NOW)
    assert [(a.provider, a.balance_usd) for a in snap.accounts] == [("omp", 40), ("pi", 70)]
    assert matrix_pool_scope("pi_codex_api", provider="pi") == "codex-api"
    assert matrix_pool_scope("omp_claude", provider="omp") == "claude-api"
    assert snap.accounts[1].windows[0].window_id == "openai-api-credits"


def test_pi_visibility_and_override_updates_are_independent(credit_files):
    pi = grant()
    snap = {"accounts": [], "hidden_pools": {"pi": "operator"}}
    co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
    assert snap["accounts"] == []
    snap.pop("hidden_pools")
    co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
    co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
    assert len(snap["accounts"][0]["notes"]) == 1
    newer = {**pi, "observed_at": (NOW + timedelta(minutes=5)).isoformat(), "remaining_usd": 60}
    co.apply_credit_overrides(snap, {"pi": newer}, now=NOW + timedelta(minutes=5))
    assert snap["accounts"][0]["balance_usd"] == 60


def test_stale_already_applied_reading_does_not_stay_available(credit_files):
    pi = grant()
    snap = {"accounts": []}
    co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
    co.apply_credit_overrides(snap, {}, now=NOW + timedelta(days=8))
    account = snap["accounts"][0]
    assert account["balance_usd"] is None
    assert account["reading_expired"] is True
    assert all(w["remaining_percent"] is None for w in account["windows"])


@pytest.mark.parametrize("empty_results", [False, True])
def test_refresh_cost_regression_never_restores_consumed_credit(credit_files, monkeypatch, empty_results):
    monkeypatch.setenv(oc.KEY_ENV, "synthetic-test-token")
    grant(observed=NOW - timedelta(hours=1))
    assert oc.refresh(now=NOW, fetch=fetch_report(20))["remaining_usd"] == 50

    def regressed(url, headers, timeout):
        payload = json.loads(report(0))
        if empty_results:
            payload["data"][0]["results"] = []
        return 200, json.dumps(payload).encode()

    later = NOW + timedelta(minutes=1)
    saved = oc.refresh(now=later, fetch=regressed)
    assert saved["remaining_usd"] == 50
    assert co.load_overrides(now=later)["pi"]["remaining_usd"] == 50


def test_operator_replacement_during_network_request_fails_closed(credit_files, monkeypatch):
    monkeypatch.setenv(oc.KEY_ENV, "synthetic-test-token")
    grant(observed=NOW - timedelta(hours=1))
    fetching, replaced = Event(), Event()

    def fetch(url, headers, timeout):
        fetching.set()
        assert replaced.wait(5)
        return 200, report()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(oc.refresh, now=NOW, fetch=fetch)
        try:
            assert fetching.wait(5)
            grant(remaining=90, observed=NOW)
        finally:
            replaced.set()
        with pytest.raises(oc.CostReportError, match="anchor changed"):
            result.result(timeout=10)
    assert not co.readings_file().exists()
    assert co.load_overrides(now=NOW)["pi"]["remaining_usd"] == 90


@pytest.mark.parametrize("account_names", [("A", "B"), ("same-org", "same-org"), (None, None)])
@pytest.mark.parametrize("effective", [False, True])
def test_unbound_pi_funding_is_not_copied_to_multiple_rows(credit_files, account_names, effective):
    pi = grant()
    snap = {"accounts": []}
    if effective:
        co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
        snap["accounts"].append(copy.deepcopy(snap["accounts"][0]))
    else:
        snap["accounts"] = [{"provider": "pi", "source": "codexbar", "balance_usd": None} for _ in account_names]
    for account, name in zip(snap["accounts"], account_names, strict=True):
        account["account_name"] = name
    co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
    assert len(snap["accounts"]) == 2
    for account in snap["accounts"]:
        assert account["balance_usd"] is None
        assert all(w["remaining_percent"] is None for w in account.get("windows", []))
        assert any("ambiguous" in note for note in account["notes"])


@pytest.mark.parametrize("with_override", [False, True])
def test_multiple_pi_cost_meters_keep_measured_spend(credit_files, with_override):
    pi = grant()
    snap = {
        "accounts": [
            {"provider": "pi", "source": "codexbar", "balance_usd": None, "spent_usd": spent, "windows": []}
            for spent in (10, 20)
        ]
    }
    co.apply_credit_overrides(snap, {"pi": pi} if with_override else {}, now=NOW)
    assert [a["spent_usd"] for a in snap["accounts"]] == [10, 20]
    assert all(a["balance_usd"] is None for a in snap["accounts"])


@pytest.mark.parametrize("seconds", [-60, 0, 60])
def test_effective_pi_clock_changes_only_for_newer_metadata(credit_files, seconds):
    pi = {**grant(), "expires_at": (NOW + timedelta(days=30)).isoformat()}
    snap = {"accounts": []}
    co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
    before = copy.deepcopy(snap["accounts"][0])
    changed = {
        **pi,
        "observed_at": (NOW + timedelta(seconds=seconds)).isoformat(),
        "granted_at": (NOW - timedelta(days=30)).isoformat(),
        "remaining_usd": 60,
    }
    co.apply_credit_overrides(snap, {"pi": changed}, now=NOW + timedelta(minutes=1))
    account = snap["accounts"][0]
    if seconds <= 0:
        assert account == before
    else:
        assert account["balance_usd"] == 60
        assert account["windows"][0]["window_minutes"] == 60 * 24 * 60


@pytest.mark.parametrize("old_expired", [False, True])
@pytest.mark.parametrize("new_expires", [None, NOW + timedelta(days=60)])
def test_replacement_pi_grant_expiry_is_authoritative(credit_files, old_expired, new_expires):
    pi = {**grant(), "expires_at": (NOW + timedelta(hours=1)).isoformat()}
    snap = {"accounts": []}
    co.apply_credit_overrides(snap, {"pi": pi}, now=NOW)
    later = NOW + timedelta(hours=2 if old_expired else 0, minutes=1)
    if old_expired:
        co.apply_credit_overrides(snap, {}, now=later)
        assert snap["accounts"][0]["balance_usd"] is None
    replacement = {
        **pi,
        "observed_at": later.isoformat(),
        "granted_at": later.isoformat(),
        "total_usd": 200,
        "remaining_usd": 180,
        "expires_at": new_expires.isoformat() if new_expires else None,
    }
    co.apply_credit_overrides(snap, {"pi": replacement}, now=later)
    account = snap["accounts"][0]
    assert account["balance_usd"] == 180
    assert account["expires_at"] == replacement["expires_at"]
    assert account["windows"][0]["resets_at"] == replacement["expires_at"]
    assert account["billing_kind"] == ("subscription_window" if new_expires else "prepaid_balance")
    assert not account.get("reading_expired")
    assert not account.get("grant_expired")
