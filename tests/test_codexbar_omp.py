"""omp reads Claude Platform subscription API credits, not claude.ai cloud-session credits."""

from __future__ import annotations

from datetime import UTC, datetime

from aiuse.collectors.codexbar import (
    _filter_omp_by_config_organization,
    _from_row,
    _omp_from_claudeplatform_row,
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


def test_subscription_grant_from_primary():
    row = _claudeplatform_row(primary_desc="$0.00 of $200.00 left · Expires Nov 6 (UTC)")
    account = _omp_from_claudeplatform_row(row)
    assert account is not None
    assert account.balance_usd == 0.0
    assert account.spent_usd == 200.0
    assert account.expires_at == EXPIRY
    assert account.windows[0].refill_capacity == 200.0


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
