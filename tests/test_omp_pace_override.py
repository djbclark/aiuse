"""The effective omp reading keeps its grant clock through override and cache reuse."""

from datetime import UTC, datetime, timedelta

import pytest

from aiuse.analysis.pace import window_pace
from aiuse.analysis.selfdescribe import enrich_snapshot
from aiuse.credits_override import apply_credit_overrides_to_snapshot
from aiuse.models import (
    WINDOW_NOMINAL_MINUTES,
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    classify_window_minutes,
)

NOW = datetime(2026, 10, 29, 20, 8, 8, tzinfo=UTC)
START = datetime(2026, 10, 13, tzinfo=UTC)
EXPIRY = datetime(2026, 11, 4, 12, tzinfo=UTC)


def _override(*, granted=True, total=200, expiry=EXPIRY):
    return {
        "omp": {
            "source": "admin-api",
            "remaining_usd": 80,
            "total_usd": total,
            "granted_at": START.isoformat() if granted else None,
            "expires_at": expiry.isoformat(),
            "observed_at": NOW.isoformat(),
        }
    }


def _account(source="codexbar", minutes=None):
    return AccountUsage(
        provider="omp",
        source=source,
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        balance_usd=80,
        spent_usd=120,
        expires_at=EXPIRY,
        collected_at=NOW,
        windows=[
            QuotaWindow(
                label="credits",
                used_percent=60,
                remaining_percent=40,
                resets_at=EXPIRY,
                window_minutes=minutes,
                refill_capacity=200,
                refill_capacity_unit="usd",
                window_id="claude-cloud-credits",
            )
        ],
    )


@pytest.mark.parametrize("source", ["codexbar", "operator-override", "admin-api"])
def test_actual_grant_clock_survives_effective_reading_and_cached_rows(source):
    snapshot = Snapshot(collected_at=NOW, accounts=[_account(source)])
    assert apply_credit_overrides_to_snapshot(snapshot, {}, overrides=_override(), now=NOW)
    account = snapshot.accounts[0]
    assert account.windows[0].window_minutes == 32400
    assert classify_window_minutes(account.windows[0].window_minutes) == "monthly"
    assert account.balance_usd == 80
    assert window_pace(account.windows[0], NOW) == (-20, False)
    # Normalization is idempotent: a second frame does not replace the balance or clock.
    assert not apply_credit_overrides_to_snapshot(snapshot, {}, overrides=_override(), now=NOW)
    body = snapshot.to_dict()
    enrich_snapshot(body, credit_overrides=_override(), now=NOW)
    assert body["accounts"][0]["overall_pace_percent"] == -20
    assert body["accounts"][0]["windows"][0]["pace_percent"] == -20


def test_admin_reading_creates_a_clocked_row_without_codexbar():
    snapshot = Snapshot(collected_at=NOW)
    assert apply_credit_overrides_to_snapshot(snapshot, {}, overrides=_override(), now=NOW)
    assert window_pace(snapshot.accounts[0].windows[0], NOW) == (-20, False)


def test_missing_grant_start_uses_nominal_month():
    snapshot = Snapshot(collected_at=NOW, accounts=[_account("admin-api")])
    assert apply_credit_overrides_to_snapshot(snapshot, {}, overrides=_override(granted=False), now=NOW)
    assert snapshot.accounts[0].windows[0].window_minutes == WINDOW_NOMINAL_MINUTES["monthly"]
    assert window_pace(snapshot.accounts[0].windows[0], NOW)[0] is not None


@pytest.mark.parametrize("override", [_override(total=250), _override(expiry=datetime(2026, 11, 5, tzinfo=UTC))])
def test_cached_row_does_not_borrow_a_different_grants_start(override):
    snapshot = Snapshot(collected_at=NOW, accounts=[_account("admin-api", minutes=12345)])
    assert not apply_credit_overrides_to_snapshot(snapshot, {}, overrides=override, now=NOW)
    assert snapshot.accounts[0].windows[0].window_minutes == 12345


def test_hidden_omp_row_is_not_normalized():
    snapshot = Snapshot(collected_at=NOW, accounts=[_account("admin-api")], hidden_pools={"omp": "operator"})
    assert not apply_credit_overrides_to_snapshot(snapshot, {}, overrides=_override(), now=NOW)
    assert snapshot.accounts[0].windows[0].window_minutes is None


@pytest.mark.parametrize("change", ["capacity", "expiry"])
def test_active_admin_grant_beats_newer_incompatible_codexbar_promotion(change):
    account = _account()
    account.collected_at = NOW + timedelta(minutes=1)
    account.balance_usd = 249.27
    if change == "capacity":
        account.windows[0].refill_capacity = 250
    else:
        account.expires_at = EXPIRY + timedelta(days=1)
        account.windows[0].resets_at = account.expires_at
    snapshot = Snapshot(collected_at=NOW + timedelta(minutes=1), accounts=[account])
    assert apply_credit_overrides_to_snapshot(snapshot, {}, overrides=_override(), now=NOW + timedelta(minutes=1))
    effective = snapshot.accounts[0]
    assert effective.source == "admin-api"
    assert effective.balance_usd == 80
    assert effective.windows[0].refill_capacity == 200
    assert effective.windows[0].resets_at == EXPIRY
    assert window_pace(effective.windows[0], NOW) == (-20, False)


def test_newer_codexbar_spend_on_matching_admin_grant_still_wins():
    account = _account()
    account.collected_at = NOW + timedelta(minutes=1)
    account.balance_usd = 70
    snapshot = Snapshot(collected_at=NOW + timedelta(minutes=1), accounts=[account])
    apply_credit_overrides_to_snapshot(snapshot, {}, overrides=_override(), now=NOW + timedelta(minutes=1))
    assert snapshot.accounts[0].source == "codexbar"
    assert snapshot.accounts[0].balance_usd == 70
