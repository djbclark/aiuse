"""Regression tests for cswap's canonical schema-v1 Claude data."""

import time

import pytest

from aiuse.collectors.cswap import _account_from_item
from aiuse.models import BillingKind


def test_missing_number_does_not_mark_slot_active():
    account = _account_from_item(
        {
            "email": "a@example.com",
            "usageStatus": "ok",
            "usage": {"fiveHour": {"pct": 1}},
        },
        None,
    )
    assert any("cswap slot None" in n or "cswap slot" in n for n in account.notes)
    assert not any("; active" in n for n in account.notes)


def test_pct_is_parsed_as_used_percentage():
    account = _account_from_item(
        {
            "number": 2,
            "email": "work-account@example.org",
            "active": True,
            "usageStatus": "ok",
            "usageFetchedAt": "2026-07-21T22:00:00Z",
            "usageAgeSeconds": 12.5,
            "usage": {
                "fiveHour": {"pct": 25, "resetsAt": "2099-01-01T00:00:00Z"},
                "sevenDay": {"pct": 16, "resetsAt": "2099-01-02T00:00:00Z"},
            },
        },
        2,
    )
    assert account.account == "work-account@example.org"
    assert [window.label for window in account.windows] == [
        "Claude Code 5-hour",
        "Claude Code weekly",
    ]
    assert account.windows[0].used_percent == 25
    assert account.windows[0].remaining() == 75
    assert account.windows[1].remaining() == 84


def test_distinct_cswap_emails_remain_distinct_accounts():
    gmail = _account_from_item(
        {
            "number": 3,
            "email": "personal-account@example.com",
            "usageStatus": "no_credentials",
            "usage": None,
        },
        2,
    )
    mit = _account_from_item(
        {
            "number": 2,
            "email": "work-account@example.org",
            "usageStatus": "keychain_unavailable",
            "usage": None,
        },
        2,
    )
    assert gmail.account != mit.account
    assert "no readable credentials" in gmail.error
    assert "Keychain" in mit.error


def test_scoped_model_limit_has_semantic_name():
    account = _account_from_item(
        {
            "number": 2,
            "email": "work-account@example.org",
            "usageStatus": "ok",
            "usage": {
                "scoped": [
                    {
                        "name": "Claude Opus",
                        "pct": 40,
                        "resetsAt": "2099-01-02T00:00:00Z",
                    }
                ]
            },
        },
        2,
    )
    assert account.windows[0].label == "Claude Code weekly — Claude Opus"


def test_spend_block_becomes_structured_usage_credits():
    account = _account_from_item(
        {
            "number": 1,
            "email": "work@example.org",
            "usageStatus": "ok",
            "usage": {
                "fiveHour": {"pct": 0},
                "spend": {
                    "used": 110.76,
                    "limit": 200.0,
                    "pct": 55.38,
                    "currency": "USD",
                    "resetsAt": "2099-08-01T00:00:00Z",
                },
            },
        },
        1,
    )
    assert account.usage_credits is not None
    assert account.usage_credits.used == 110.76
    assert account.usage_credits.limit == 200.0
    assert account.usage_credits.remaining == pytest.approx(89.24)
    assert account.usage_credits.currency == "USD"
    assert account.usage_credits.used_percent == pytest.approx(55.38)
    assert account.usage_credits.resets_at is not None
    # Headroom mirrored for generic balance UI without reclassifying billing.
    assert account.balance_usd == pytest.approx(89.24)
    assert account.billing_kind.value == "subscription_window"
    assert any("Usage credits:" in n for n in account.notes)
    d = account.to_dict()
    assert d["usage_credits"]["used"] == 110.76
    assert d["usage_credits"]["remaining"] == pytest.approx(89.24)


def test_api_key_account_has_no_error_and_payg_billing():
    account = _account_from_item(
        {
            "number": 5,
            "email": "svc@example.com",
            "usageStatus": "api_key",
            "usage": None,
        },
        2,
    )
    assert account.billing_kind == BillingKind.PAYG_API
    assert account.error is None


def test_generic_label_windowminutes_bucketing_boundary():
    account = _account_from_item(
        {
            "number": 2,
            "email": "work-account@example.org",
            "usageStatus": "ok",
            "usage": {
                "primary": {
                    "pct": 10,
                    "windowMinutes": 44640,
                    "resetsAt": "2099-01-01T00:00:00Z",
                }
            },
        },
        2,
    )
    assert account.windows[0].label == "Claude Code monthly"


def test_description_only_block_is_retained_not_discarded():
    account = _account_from_item(
        {
            "number": 2,
            "email": "work-account@example.org",
            "usageStatus": "ok",
            "usage": {"primary": {"countdown": "resets in 2 days"}},
        },
        2,
    )
    assert account.windows[0].reset_description == "resets in 2 days"


def test_unavailable_json_row_hydrates_from_cswap_usage_cache():
    """`cswap list --json` drops decision-stale lastGood; we recover it for reports."""
    fetched_at = time.time() - 7200  # 2h old — past cswap TRUST_MAX_AGE_S
    cache = {
        "accounts": {
            "2": {
                "email": "personal-account@example.com",
                "lastGood": {
                    "five_hour": {"pct": 0.0},
                    "seven_day": {
                        "pct": 100.0,
                        "resets_at": "2099-01-02T00:00:00+00:00",
                        "countdown": "1d",
                    },
                },
                "fetchedAt": fetched_at,
            }
        }
    }
    account = _account_from_item(
        {
            "number": 2,
            "email": "personal-account@example.com",
            "usageStatus": "unavailable",
            "usage": None,
        },
        1,
        cache=cache,
    )
    assert account.error is None
    assert [window.label for window in account.windows] == [
        "Claude Code 5-hour",
        "Claude Code weekly",
    ]
    assert account.windows[0].used_percent == 0.0
    assert account.windows[1].used_percent == 100.0
    assert any("last-known quota" in note for note in account.notes)
    assert any("decision-stale" in note for note in account.notes)


def test_unavailable_json_row_prefers_official_last_good_usage():
    """cswap >=0.24 avoids local-cache coupling when it supplies the data."""
    cache = {
        "accounts": {
            "2": {
                "lastGood": {"seven_day": {"pct": 15.0}},
                "fetchedAt": time.time() - 7200,
            }
        }
    }
    account = _account_from_item(
        {
            "number": 2,
            "email": "personal-account@example.com",
            "usageStatus": "unavailable",
            "usage": None,
            "lastGoodUsage": {
                "sevenDay": {"pct": 100.0, "resetsAt": "2099-01-02T00:00:00Z"},
            },
            "lastGoodFetchedAt": "2026-07-30T11:00:00Z",
            "lastGoodAgeSeconds": 1800,
        },
        1,
        cache=cache,
    )

    assert account.error is None
    assert account.windows[0].used_percent == 100.0
    assert any("display-grade JSON" in note for note in account.notes)
    assert any("1800s old" in note for note in account.notes)


def test_malformed_official_last_good_usage_falls_back_to_cache():
    """An additive field must not break recovery on partial/future schemas."""
    cache = {
        "accounts": {
            "2": {
                "lastGood": {"five_hour": {"pct": 12.0}},
                "fetchedAt": time.time() - 600,
            }
        }
    }
    account = _account_from_item(
        {
            "number": 2,
            "email": "personal-account@example.com",
            "usageStatus": "unavailable",
            "usage": None,
            "lastGoodUsage": {"unknownFutureField": True},
        },
        1,
        cache=cache,
    )

    assert account.error is None
    assert account.windows[0].used_percent == 12.0
    assert any("local cache" in note for note in account.notes)


def test_unavailable_without_cache_still_errors():
    account = _account_from_item(
        {
            "number": 2,
            "email": "personal-account@example.com",
            "usageStatus": "unavailable",
            "usage": None,
        },
        1,
        cache={"accounts": {}},
    )
    assert account.error is not None
    assert account.windows == []
    assert "live Claude quota could not be fetched" in account.error


def test_cache_hydration_matches_by_email_when_slot_number_missing_in_cache():
    cache = {
        "accounts": {
            "9": {
                "email": "work-account@example.org",
                "lastGood": {"five_hour": {"pct": 12.0}},
                "fetchedAt": time.time() - 600,
            }
        }
    }
    account = _account_from_item(
        {
            "number": 2,
            "email": "Work-Account@example.org",
            "usageStatus": "unavailable",
            "usage": None,
        },
        1,
        cache=cache,
    )
    assert account.error is None
    assert account.windows[0].used_percent == 12.0


def test_named_five_hour_block_gets_nominal_window_minutes():
    account = _account_from_item(
        {
            "number": 1,
            "email": "a@example.com",
            "usageStatus": "ok",
            "usage": {"fiveHour": {"pct": 10, "resetsAt": "2099-01-01T00:00:00Z"}},
        },
        1,
    )
    assert account.windows[0].label == "Claude Code 5-hour"
    assert account.windows[0].window_minutes == 300


def test_named_seven_day_keeps_explicit_window_minutes():
    account = _account_from_item(
        {
            "number": 1,
            "email": "a@example.com",
            "usageStatus": "ok",
            "usage": {
                "sevenDay": {
                    "pct": 20,
                    "windowMinutes": 10079,
                    "resetsAt": "2099-01-02T00:00:00Z",
                }
            },
        },
        1,
    )
    assert account.windows[0].label == "Claude Code weekly"
    assert account.windows[0].window_minutes == 10079


def test_stale_cached_countdown_is_recomputed_from_resets_at():
    """lastGood freezes countdown at fetch; we must not report a 17h string ~2h later."""
    from datetime import datetime, timedelta, timezone

    from aiuse.collectors.cswap import _countdown_from_reset, _window_from_block

    now = datetime(2026, 7, 23, 18, 50, 0, tzinfo=timezone.utc)
    resets = now + timedelta(hours=15, minutes=10)
    frozen = "17h 8m"  # what cswap stored when the row was ~2h younger
    window = _window_from_block(
        "Claude Code weekly",
        {"pct": 100.0, "resets_at": resets.isoformat(), "countdown": frozen},
        now=now,
    )
    assert window is not None
    assert window.reset_description != frozen
    assert window.reset_description == _countdown_from_reset(resets, now=now)
    assert window.reset_description == "15h 10m"
