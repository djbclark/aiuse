"""omp reads Claude Platform subscription API credits, not claude.ai cloud-session credits."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aiuse.collectors.codexbar import (
    _codexbar_query_skipped,
    _filter_omp_by_config_organization,
    _from_row,
    _live_omp_supplement,
    _omp_from_claudeplatform_row,
    _subscription_grant_from_usage,
)
from aiuse.config import organization_names_match

EXPIRY = datetime(2026, 11, 6, tzinfo=UTC)


def _claudeplatform_row(*, primary_desc: str, updated: str = "2026-11-04T13:00:00Z") -> dict:
    return {
        "provider": "claudeplatform",
        "source": "web",
        "usage": {
            "updatedAt": updated,
            "primary": {"usedPercent": 100.0, "resetDescription": primary_desc},
            "identity": {"accountOrganization": "Synthetic API Org"},
            "providerCost": {
                "balance": 0.0,
                "used": 187.25,
                "currencyCode": "USD",
                "period": "Prepaid credits",
            },
            "details": [{"title": "Billing", "rows": [{"label": "This month (UTC)", "value": "$43.21"}]}],
        },
    }


def test_primary_resets_at_wins_over_yearless_expiry_label():
    usage = {
        "updatedAt": "2026-11-07T12:00:00Z",
        "primary": {
            "usedPercent": 100.0,
            "resetDescription": "$0.00 of $200.00 left · Expires Nov 6 (UTC)",
            "resetsAt": "2026-11-06T00:00:00Z",
        },
    }
    parsed = _subscription_grant_from_usage(usage)
    assert parsed is not None
    _, _, expires_at = parsed
    assert expires_at == EXPIRY


def test_subscription_grant_from_primary():
    row = _claudeplatform_row(primary_desc="$0.00 of $200.00 left · Expires Nov 6 (UTC)")
    account = _omp_from_claudeplatform_row(row)
    assert account is not None
    assert account.balance_usd == 0.0
    assert account.spent_usd == 200.0
    assert account.expires_at == EXPIRY
    assert account.windows[0].refill_capacity == 200.0


def test_from_row_without_subscription_grant_returns_omp_error():
    row = {
        "provider": "claudeplatform",
        "usage": {
            "updatedAt": "2026-11-04T13:00:00Z",
            "primary": {"usedPercent": 12.0, "resetDescription": "Resets in 3 hours"},
        },
    }
    account = _from_row(row)
    assert account.provider == "omp"
    assert account.error
    assert account.balance_usd is None


def test_from_row_maps_claudeplatform_to_omp():
    row = _claudeplatform_row(primary_desc="$50.00 of $200.00 left · Expires Nov 6 (UTC)")
    account = _from_row(row)
    assert account.provider == "omp"
    assert account.balance_usd == 50.0


def test_organization_name_match():
    assert organization_names_match("Freedom Included", "Freedom Included")
    assert organization_names_match("Freedom Included", "  freedom   included ")
    assert not organization_names_match("Freedom Included", "Other Org")


def test_filter_drops_omp_for_wrong_console_org(capsys):
    row = _omp_from_claudeplatform_row(_claudeplatform_row(primary_desc="$0.00 of $200.00 left · Expires Nov 6 (UTC)"))
    assert row is not None
    row.account = "Wrong Org Inc"
    kept = _filter_omp_by_config_organization(
        [row],
        {"credits": {"omp": {"organization": "Freedom Included"}}},
    )
    assert kept == []
    assert "Freedom Included" in capsys.readouterr().err


def test_skip_providers_disable_claudeplatform_when_omp_is_off():
    skip = frozenset({"omp"})
    assert _codexbar_query_skipped("claudeplatform", skip)
    assert _codexbar_query_skipped("omp", skip)
    assert not _codexbar_query_skipped("claude", skip)


def test_live_omp_supplement_skips_when_omp_in_skip_providers(monkeypatch):
    """``skip_providers`` lists canonical ids; the supplement queries ``claudeplatform``."""
    called: list[bool] = []

    def boom(*_a, **_k):
        called.append(True)
        pytest.fail("claudeplatform query must not run when omp is skipped")

    monkeypatch.setattr("aiuse.collectors.codexbar._query_providers", boom)
    assert (
        _live_omp_supplement(
            [],
            skip_providers=frozenset({"omp"}),
            timeout=1.0,
            min_intervals=None,
            backoff=None,
            provider_list=["claude"],
            was_discovery=False,
        )
        == []
    )
    assert called == []


def test_primary_used_percent_alone_still_searches_other_grants():
    usage = {
        "updatedAt": "2026-11-04T13:00:00Z",
        "primary": {"usedPercent": 100.0, "resetDescription": ""},
        "details": [
            {
                "title": "Other credit grants",
                "rows": [
                    {
                        "label": "API credit (Ultra)",
                        "value": "$0.00 of $200.00 left",
                        "secondaryValue": "Expires Nov 6 (UTC)",
                    }
                ],
            }
        ],
    }
    parsed = _subscription_grant_from_usage(usage)
    assert parsed is not None
    remaining, total, expires_at = parsed
    assert remaining == 0.0 and total == 200.0 and expires_at == EXPIRY


def test_other_grants_ignore_unlabeled_promotional_api_credit():
    usage = {
        "updatedAt": "2026-11-04T13:00:00Z",
        "primary": None,
        "details": [
            {
                "title": "Other credit grants",
                "rows": [
                    {
                        "label": "API credit",
                        "value": "$25.00 of $25.00 left",
                        "secondaryValue": "Expires Nov 1 (UTC)",
                    },
                    {
                        "label": "API credit (Ultra)",
                        "value": "$0.00 of $200.00 left",
                        "secondaryValue": "Expires Nov 6 (UTC)",
                    },
                ],
            }
        ],
    }
    parsed = _subscription_grant_from_usage(usage)
    assert parsed is not None
    remaining, total, _ = parsed
    assert remaining == 0.0 and total == 200.0


def test_claudeplatform_canonicalizes_to_omp():
    from aiuse.models import canonical_provider

    assert canonical_provider("claudeplatform") == "omp"
    assert canonical_provider("claude-platform") == "omp"


def test_yearless_december_expiry_after_january_reading_uses_previous_year():
    usage = {
        "updatedAt": "2027-01-02T12:00:00Z",
        "primary": None,
        "details": [
            {
                "title": "Other credit grants",
                "rows": [
                    {
                        "label": "API credit (Ultra)",
                        "value": "$0.00 of $200.00 left",
                        "secondaryValue": "Expires Dec 6 (UTC)",
                    }
                ],
            }
        ],
    }
    parsed = _subscription_grant_from_usage(usage)
    assert parsed is not None
    _, _, expires_at = parsed
    assert expires_at == datetime(2026, 12, 6, tzinfo=UTC)


def test_yearless_january_expiry_after_december_reading_uses_next_year():
    usage = {
        "updatedAt": "2026-12-20T12:00:00Z",
        "primary": {"resetDescription": "$150.00 of $200.00 left · Expires Jan 6 (UTC)"},
    }
    parsed = _subscription_grant_from_usage(usage)
    assert parsed is not None
    _, _, expires_at = parsed
    assert expires_at == datetime(2027, 1, 6, tzinfo=UTC)


def test_yearless_expiry_in_other_grants_does_not_roll_to_next_year():
    usage = {
        "updatedAt": "2026-11-07T12:00:00Z",
        "primary": None,
        "details": [
            {
                "title": "Other credit grants",
                "rows": [
                    {
                        "label": "API credit (Ultra)",
                        "value": "$0.00 of $200.00 left",
                        "secondaryValue": "Expires Nov 6 (UTC)",
                    }
                ],
            }
        ],
    }
    parsed = _subscription_grant_from_usage(usage)
    assert parsed is not None
    _, _, expires_at = parsed
    assert expires_at == EXPIRY


def test_console_organization_falls_back_to_row_account():
    row = {
        "provider": "claudeplatform",
        "account": "Freedom Included",
        "usage": {
            "updatedAt": "2026-11-04T13:00:00Z",
            "primary": {"resetDescription": "$0.00 of $200.00 left · Expires Nov 6 (UTC)"},
        },
    }
    account = _omp_from_claudeplatform_row(row)
    assert account is not None
    assert account.console_organization == "Freedom Included"


def test_exhausted_max_plan_grant_labels_in_other_credits():
    for label in ("API credit (Max 20x)", "API credit (Max 5x)"):
        usage = {
            "updatedAt": "2026-11-04T13:00:00Z",
            "primary": None,
            "details": [
                {
                    "title": "Other credit grants",
                    "rows": [
                        {
                            "label": label,
                            "value": "$0.00 of $200.00 left",
                            "secondaryValue": "Expires Nov 6 (UTC)",
                        }
                    ],
                }
            ],
        }
        parsed = _subscription_grant_from_usage(usage)
        assert parsed is not None
        remaining, total, expires_at = parsed
        assert remaining == 0.0 and total == 200.0 and expires_at == EXPIRY


def test_exhausted_grant_in_other_credits_section():
    row = {
        "provider": "claudeplatform",
        "source": "web",
        "usage": {
            "updatedAt": "2026-11-04T13:00:00Z",
            "primary": None,
            "details": [
                {
                    "title": "Other credit grants",
                    "rows": [
                        {
                            "label": "API credit (Ultra)",
                            "value": "$0.00 of $200.00 left",
                            "secondaryValue": "Expires Nov 6 (UTC)",
                        }
                    ],
                }
            ],
        },
    }
    account = _omp_from_claudeplatform_row(row)
    assert account is not None
    assert account.balance_usd == 0.0
    assert account.expires_at == EXPIRY
