"""Tests for the Paseo daemon usage collector."""

import pytest

import aiuse.collectors.paseo as paseo_mod
from aiuse.collectors.base import CollectorError
from aiuse.collectors.paseo import _from_entry, collect_paseo
from aiuse.models import WINDOW_NOMINAL_MINUTES, claude_model_scope
from aiuse.usage_sources import usage_source_report


def _entry(source_id: str, **report_extras: object) -> dict[str, object]:
    report: dict[str, object] = {
        "status": "available",
        "windows": [],
        "balances": [],
        "details": [],
    }
    report.update(report_extras)
    return {
        "id": f"{source_id}:xyz",
        "sourceId": source_id,
        "sourceLabel": source_id.title(),
        "icon": "<svg/>",
        "account": {"label": "djbclark@gmail.com"},
        "fetchedAt": "2026-10-21T16:41:24.850Z",
        "successfulAt": "2026-10-21T16:41:24.973Z",
        "report": report,
    }


def test_claude_windows_map_with_fable_sublimit():
    account = _from_entry(
        _entry(
            "claude",
            planLabel="Max 20x",
            windows=[
                {"id": "five_hour", "label": "Session", "usedPct": 0, "remainingPct": 100, "resetsAt": None},
                {
                    "id": "weekly",
                    "label": "Weekly",
                    "usedPct": 100,
                    "remainingPct": 0,
                    "resetsAt": "2026-10-23T10:00:00.022301+00:00",
                },
                {
                    "id": "weekly_model_fable",
                    "label": "Fable",
                    "usedPct": 99,
                    "remainingPct": 1,
                    "resetsAt": "2026-10-23T10:00:00.022464+00:00",
                },
            ],
        )
    )
    assert account is not None
    assert account.source == "paseo"
    assert account.provider == "claude"
    assert account.account == "djbclark@gmail.com"
    assert account.plan == "Max 20x"
    assert [w.label for w in account.windows] == [
        "Claude Code 5-hour",
        "Claude Code weekly",
        "Claude Code weekly — Fable",
    ]
    assert [w.window_minutes for w in account.windows] == [
        WINDOW_NOMINAL_MINUTES["5h"],
        WINDOW_NOMINAL_MINUTES["weekly"],
        WINDOW_NOMINAL_MINUTES["weekly"],
    ]
    assert claude_model_scope(account.windows[2].label) == "Fable"
    assert account.windows[0].resets_at is None  # session not started: clock starts on first use
    assert account.windows[1].resets_at is not None
    assert account.collected_at is not None and account.collected_at.year == 2026
    assert "icon" not in (account.raw or {})


def test_unavailable_entry_is_skipped():
    entry = _entry("claude")
    entry["report"] = {
        "status": "unavailable",
        "problem": {"kind": "expired", "refreshedBy": "pi"},
    }
    assert _from_entry(entry) is None


@pytest.mark.parametrize("source_id", ["cursor", "omp", "opencode", "pi", "muse", "unknown-vendor"])
def test_unmapped_providers_are_skipped(source_id: str):
    entry = _entry(source_id, balances=[{"id": "plan_usage", "used": 816.32, "limit": 70, "unit": "usd"}])
    assert _from_entry(entry) is None


def test_codex_windows_and_credits_balance_note():
    account = _from_entry(
        _entry(
            "codex",
            planLabel="prolite",
            windows=[
                {
                    "id": "weekly",
                    "label": "Weekly",
                    "usedPct": 49,
                    "remainingPct": 51,
                    "resetsAt": "2026-10-27T06:47:47.000Z",
                }
            ],
            balances=[{"id": "credits", "used": None, "remaining": 0, "limit": None, "unit": "credits"}],
        )
    )
    assert account is not None
    assert [w.label for w in account.windows] == ["Codex Weekly"]
    assert account.windows[0].window_minutes == WINDOW_NOMINAL_MINUTES["weekly"]
    assert "paseo balance credits: 0 credits left" in account.notes


def test_copilot_keeps_premium_only():
    account = _from_entry(
        _entry(
            "copilot",
            windows=[
                {"id": "chat", "label": "Chat", "usedPct": 36, "remainingPct": 64},
                {"id": "completions", "label": "Completions", "usedPct": 0, "remainingPct": 100},
                {"id": "premium", "label": "Premium requests", "usedPct": 100, "remainingPct": 0},
            ],
        )
    )
    assert account is not None
    assert [w.label for w in account.windows] == ["GitHub Copilot Premium requests"]


def test_grok_weekly_window():
    account = _from_entry(
        _entry(
            "grok",
            windows=[
                {
                    "id": "weekly",
                    "label": "Weekly",
                    "usedPct": 84,
                    "remainingPct": 16,
                    "resetsAt": "2026-10-22T06:41:20.351443+00:00",
                }
            ],
        )
    )
    assert account is not None
    assert [w.label for w in account.windows] == ["Grok Weekly"]
    assert account.windows[0].remaining_percent == 16


def test_collect_paseo_runs_cli_and_maps_rows(monkeypatch):
    monkeypatch.setattr(paseo_mod, "which", lambda cmd: f"/bin/{cmd}")
    monkeypatch.setattr(
        paseo_mod,
        "run_json",
        lambda argv, timeout: [_entry("grok", windows=[{"id": "weekly", "label": "Weekly", "usedPct": 84}])],
    )
    accounts = collect_paseo()
    assert [a.provider for a in accounts] == ["grok"]


def test_collect_paseo_missing_binary(monkeypatch):
    monkeypatch.setattr(paseo_mod, "which", lambda cmd: None)
    with pytest.raises(CollectorError):
        collect_paseo()


def test_usage_sources_lists_paseo_as_active_tool():
    report = usage_source_report({"collectors": {}}, which_fn=lambda cmd: f"/bin/{cmd}")
    claude = next(row for row in report["providers"] if row["provider"] == "claude")
    paseo_row = next(source for source in claude["sources"] if source["id"] == "paseo")
    assert paseo_row["status"] == "active"
