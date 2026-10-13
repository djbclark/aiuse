"""Test CodexBar widget snapshot cache reading, staleness handling, and CLI overrides."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from aiuse.cli import build_parser
from aiuse.collectors.codexbar import (
    _parse_widget_snapshot_entry,
    _read_widget_snapshot,
    collect_codexbar,
)
from aiuse.models import BillingKind

SAMPLE_SNAPSHOT = {
    "usageBarsShowUsed": False,
    "generatedAt": "2026-10-13T12:00:00Z",
    "enabledProviders": ["codex", "claude", "antigravity", "opencodego"],
    "entries": [
        {
            "provider": "codex",
            "updatedAt": "2026-10-13T11:58:00Z",
            "primary": {
                "usedPercent": 25.0,
                "resetDescription": "1:47 PM",
                "windowMinutes": 300,
                "resetsAt": "2026-10-13T17:47:40Z",
            },
            "secondary": {
                "usedPercent": 20.0,
                "resetDescription": "Oct 20 at 3:34 AM",
                "windowMinutes": 10080,
                "resetsAt": "2026-10-20T07:34:14Z",
            },
            "creditsRemaining": 0.0,
            "usageRows": [
                {
                    "id": "session",
                    "title": "Session",
                    "percentLeft": 75.0,
                    "window": {
                        "windowMinutes": 300,
                        "usedPercent": 25.0,
                        "resetsAt": "2026-10-13T17:47:40Z",
                    },
                },
                {
                    "id": "weekly",
                    "title": "Weekly",
                    "percentLeft": 80.0,
                    "window": {
                        "windowMinutes": 10080,
                        "usedPercent": 20.0,
                        "resetsAt": "2026-10-20T07:34:14Z",
                    },
                },
            ],
        },
        {
            "provider": "claude",
            "updatedAt": "2026-10-13T11:59:00Z",
            "quotaOwnerKey": "claude/swap:2:0e9924b7c9",
            "primary": {
                "usedPercent": 2.0,
                "windowMinutes": 300,
                "resetsAt": "2026-10-13T16:50:00Z",
            },
            "secondary": {
                "usedPercent": 91.0,
                "windowMinutes": 10080,
                "resetsAt": "2026-10-16T10:00:00Z",
            },
            "usageRows": [
                {"id": "primary", "title": "Session", "percentLeft": 98.0},
                {"id": "secondary", "title": "Weekly", "percentLeft": 9.0},
            ],
        },
        {
            "provider": "antigravity",
            "updatedAt": "2026-10-13T11:57:00Z",
            "primary": {
                "usedPercent": 92.62,
                "windowMinutes": 10080,
                "resetsAt": "2026-10-15T19:09:49Z",
            },
            "secondary": {
                "usedPercent": 100.0,
                "windowMinutes": 10080,
                "resetsAt": "2026-10-17T18:18:05Z",
            },
            "usageRows": [
                {"id": "antigravity-quota-summary-gemini-5h", "title": "Gemini 5-hour", "percentLeft": 99.0},
                {"id": "antigravity-quota-summary-gemini-weekly", "title": "Gemini weekly", "percentLeft": 7.38},
                {"id": "antigravity-quota-summary-3p-5h", "title": "Claude/GPT 5-hour", "percentLeft": None},
                {"id": "antigravity-quota-summary-3p-weekly", "title": "Claude/GPT weekly", "percentLeft": 0.0},
            ],
        },
        {
            "provider": "opencodego",
            "updatedAt": "2026-10-13T11:55:00Z",
            "providerCost": {
                "used": 15.5,
                "limit": 0.0,
                "period": "Zen balance",
            },
            "primary": {
                "usedPercent": 10.0,
                "windowMinutes": 300,
            },
        },
    ],
}


def test_parse_widget_snapshot_entry():
    entry = SAMPLE_SNAPSHOT["entries"][0]
    accounts = _parse_widget_snapshot_entry(entry, age_seconds=42.0)
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.provider == "codex"
    assert acc.source == "codexbar"
    assert len(acc.windows) == 2
    assert acc.windows[0].label == "Codex 5-hour quota (1)"
    assert acc.windows[0].used_percent == 25.0
    assert any("Cached widget snapshot age: 42s" in note for note in acc.notes)


def test_read_widget_snapshot_fresh(tmp_path: Path):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = dict(SAMPLE_SNAPSHOT)
    snap["generatedAt"] = now_iso
    file_path = tmp_path / "widget-snapshot.json"
    file_path.write_text(json.dumps(snap), encoding="utf-8")

    accounts = _read_widget_snapshot(
        cache_path=file_path,
        max_age=300.0,
        provider_list=[None],
        skip_providers=None,
    )
    assert accounts is not None
    # codex, claude, antigravity, opencodego, plus opencode-zen
    providers = [a.provider for a in accounts]
    assert "codex" in providers
    assert "claude" in providers
    assert "antigravity" in providers
    assert "opencodego" in providers
    assert "opencode-zen" in providers

    # Check antigravity has 4 windows
    ag = next(a for a in accounts if a.provider == "antigravity")
    assert len(ag.windows) == 4
    ag_labels = [w.label for w in ag.windows]
    assert "Gemini 5-hour" in ag_labels
    assert "Gemini weekly" in ag_labels
    assert "Claude/GPT 5-hour" in ag_labels
    assert "Claude/GPT weekly" in ag_labels

    # Check zen has PREPAID_BALANCE
    zen = next(a for a in accounts if a.provider == "opencode-zen")
    assert zen.billing_kind == BillingKind.PREPAID_BALANCE
    assert zen.balance_usd == 15.5


def test_read_widget_snapshot_stale(tmp_path: Path):
    file_path = tmp_path / "widget-snapshot.json"
    # Older timestamp (2026-10-10)
    file_path.write_text(json.dumps(SAMPLE_SNAPSHOT), encoding="utf-8")

    accounts = _read_widget_snapshot(
        cache_path=file_path,
        max_age=300.0,
        provider_list=[None],
        skip_providers=None,
    )
    assert accounts is None


def test_read_widget_snapshot_missing_file(tmp_path: Path):
    missing_path = tmp_path / "nonexistent.json"
    assert (
        _read_widget_snapshot(cache_path=missing_path, max_age=300.0, provider_list=[None], skip_providers=None) is None
    )


def test_read_widget_snapshot_corrupted_json(tmp_path: Path):
    corrupted_path = tmp_path / "bad.json"
    corrupted_path.write_text("not json", encoding="utf-8")
    assert (
        _read_widget_snapshot(cache_path=corrupted_path, max_age=300.0, provider_list=[None], skip_providers=None)
        is None
    )


def test_read_widget_snapshot_provider_filter(tmp_path: Path):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = dict(SAMPLE_SNAPSHOT)
    snap["generatedAt"] = now_iso
    file_path = tmp_path / "widget-snapshot.json"
    file_path.write_text(json.dumps(snap), encoding="utf-8")

    # Only codex requested
    accounts = _read_widget_snapshot(
        cache_path=file_path,
        max_age=300.0,
        provider_list=["codex"],
        skip_providers=None,
    )
    assert accounts is not None
    assert len(accounts) == 1
    assert accounts[0].provider == "codex"

    # Missing provider requested -> falls back to None so live query can handle it
    accounts_missing = _read_widget_snapshot(
        cache_path=file_path,
        max_age=300.0,
        provider_list=["deepseek"],
        skip_providers=None,
    )
    assert accounts_missing is None


def test_collect_codexbar_with_cache(tmp_path: Path):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = dict(SAMPLE_SNAPSHOT)
    snap["generatedAt"] = now_iso
    file_path = tmp_path / "widget-snapshot.json"
    file_path.write_text(json.dumps(snap), encoding="utf-8")

    # With use_cache=True, reads from snapshot without calling CLI
    accounts = collect_codexbar(
        cache_path=file_path,
        use_cache=True,
        max_cache_age=300.0,
        providers=["codex"],
    )
    assert len(accounts) == 1
    assert accounts[0].provider == "codex"


def test_cli_codexbar_live_flag():
    parser = build_parser()
    args = parser.parse_args(["--codexbar-live"])
    assert args.codexbar_live is True

    args_default = parser.parse_args([])
    assert args_default.codexbar_live is False
