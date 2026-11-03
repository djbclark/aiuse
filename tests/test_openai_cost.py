"""Independent Pi API grant accounting and shared-file safety (no live calls)."""

import json
from datetime import UTC, datetime, timedelta

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
