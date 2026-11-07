"""omp's Anthropic API-key pool read from a (fixture) agent.db."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot, pool_entries
from aiuse.collectors.base import CollectorNotInstalled
from aiuse.collectors.omp_agent_db import collect_omp_agent_db
from aiuse.collectors.runner import _select_and_cross_check
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _db(
    tmp_path: Path, creds: list[tuple[int, str, str, str | None]], blocks: list[tuple[int, int]] | None = None
) -> Path:
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY, provider TEXT, credential_type TEXT,"
            " data TEXT, disabled_cause TEXT)"
        )
        conn.execute(
            "CREATE TABLE auth_credential_blocks (credential_id INTEGER, provider_key TEXT, block_scope TEXT,"
            " blocked_until_ms INTEGER, updated_at INTEGER)"
        )
        for cid, provider, kind, cause in creds:
            conn.execute(
                "INSERT INTO auth_credentials VALUES (?, ?, ?, 'REDACTED-FIXTURE', ?)", (cid, provider, kind, cause)
            )
        for cid, until_ms in blocks or []:
            conn.execute(
                "INSERT INTO auth_credential_blocks VALUES (?, 'anthropic:api_key', '', ?, 0)", (cid, until_ms)
            )
    return path


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _pool(row: AccountUsage) -> dict:
    snap = Snapshot(collected_at=NOW, accounts=[row]).to_dict()
    enriched = enrich_snapshot(snap, now=NOW)
    (pool,) = pool_entries(enriched, now=NOW)
    return pool


def _collect(path: Path, binary: str | None = "/usr/bin/omp") -> AccountUsage:
    (row,) = collect_omp_agent_db(db_path=path, now=NOW, omp_binary=binary)
    return row


def test_enabled_api_key_is_usable_with_unknown_headroom(tmp_path):
    row = _collect(_db(tmp_path, [(10, "anthropic", "oauth", "disabled by user"), (11, "anthropic", "api_key", None)]))
    assert row.provider == "omp"
    assert row.billing_kind is BillingKind.PAYG_API
    pool = _pool(row)
    assert pool["usable_now"] is True
    assert pool["headroom_percent"] is None
    assert pool["pool_family"] == "anthropic_api"
    assert pool["cli_binary"] == "omp"
    assert pool["available_at"] is None
    snap = enrich_snapshot(Snapshot(collected_at=NOW, accounts=[row]).to_dict(), now=NOW)
    assert [p["provider"] for p in available_pools(snap)] == ["omp"]


def test_only_oauth_is_not_usable(tmp_path):
    row = _collect(_db(tmp_path, [(10, "anthropic", "oauth", None)]))
    pool = _pool(row)
    assert pool["usable_now"] is False
    assert "api_key" in pool["api_key_pool"]["reason"]
    snap = enrich_snapshot(Snapshot(collected_at=NOW, accounts=[row]).to_dict(), now=NOW)
    assert available_pools(snap) == []


def test_disabled_and_foreign_credentials_do_not_count(tmp_path):
    row = _collect(_db(tmp_path, [(1, "anthropic", "api_key", "disabled by user"), (2, "openai", "api_key", None)]))
    assert _pool(row)["usable_now"] is False


def test_active_block_reports_available_at(tmp_path):
    until = NOW + timedelta(minutes=17)
    path = _db(tmp_path, [(11, "anthropic", "api_key", None)], [(11, _ms(until))])
    pool = _pool(_collect(path))
    assert pool["usable_now"] is False
    assert datetime.fromisoformat(pool["available_at"]) == until


def test_expired_block_is_ignored(tmp_path):
    path = _db(tmp_path, [(11, "anthropic", "api_key", None)], [(11, _ms(NOW - timedelta(hours=1)))])
    assert _pool(_collect(path))["usable_now"] is True


def test_one_unblocked_key_keeps_the_pool_usable(tmp_path):
    path = _db(
        tmp_path,
        [(11, "anthropic", "api_key", None), (12, "anthropic", "api_key", None)],
        [(11, _ms(NOW + timedelta(hours=1)))],
    )
    assert _pool(_collect(path))["usable_now"] is True


def test_missing_binary_is_not_usable(tmp_path):
    row = _collect(_db(tmp_path, [(11, "anthropic", "api_key", None)]), binary="")
    pool = _pool(row)
    assert pool["usable_now"] is False
    assert "binary" in pool["api_key_pool"]["reason"]


def test_missing_database_is_an_uninstalled_tool(tmp_path):
    with pytest.raises(CollectorNotInstalled):
        collect_omp_agent_db(db_path=tmp_path / "absent.db", now=NOW)


def test_unreadable_database_gives_an_unusable_row(tmp_path):
    bad = tmp_path / "agent.db"
    bad.write_text("not sqlite")
    pool = _pool(_collect(bad))
    assert pool["usable_now"] is False
    assert "unreadable" in pool["api_key_pool"]["reason"]


def test_grant_row_from_codexbar_outranks_key_pool_row(tmp_path):
    key_row = _collect(_db(tmp_path, [(11, "anthropic", "api_key", None)]))
    grant = AccountUsage(
        source="codexbar",
        provider="omp",
        account="Org",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[QuotaWindow(label="credits", used_percent=10.0, remaining_percent=90.0)],
        collected_at=NOW,
    )
    selected, _ = _select_and_cross_check([key_row, grant], cswap_authoritative=False)
    assert [a.source for a in selected] == ["codexbar"]
    selected, _ = _select_and_cross_check([key_row], cswap_authoritative=False)
    assert [a.source for a in selected] == ["omp_agent_db"]
