"""Unit tests for the caam collector (no live caam binary, no network)."""

import copy
import json

import pytest

from aiuse.collectors.base import CollectorError
from aiuse.collectors.caam import _from_row, collect_caam

# caam v0.1.23 `caam limits --format json` rows (usage.ProfileUsage shape).
LIMITS = [
    {
        "provider": "claude",
        "profile_name": "work-max",
        "usage": {
            "provider": "claude",
            "profile_name": "work-max",
            "plan_type": "claude_max_5x",
            "source": "vault",
            "fetched_at": "2026-10-10T11:00:00Z",
            "quota_status": "ok",
            "api_key": "sk-fixture",
            "primary_window": {
                "used_percent": 100,
                "resets_at": "2026-10-10T18:00:00Z",
                "label": "5h session",
                "refresh_token": "eyJfixture",
            },
            "secondary_window": {
                "used_percent": 47,
                "resets_at": "2026-10-13T09:00:00Z",
                "label": "7d",
            },
        },
    },
    {
        "provider": "codex",
        "profile_name": "plus-main",
        "usage": {
            "provider": "codex",
            "profile_name": "plus-main",
            "source": "vault",
            "fetched_at": "2026-10-10T11:00:00Z",
            "quota_status": "ok",
            "primary_window": {"used_percent": 12, "resets_at": "2026-10-10T15:00:00Z"},
            # Unmeasured secondary must not become a 0%-used window.
            "secondary_window": {"used_percent": 63, "unmeasured": True},
        },
    },
    {
        "provider": "grok",
        "profile_name": "supergrok-2",
        "usage": {
            "provider": "grok",
            "profile_name": "supergrok-2",
            "fetched_at": "2026-10-10T11:00:00Z",
            "error": "grok api: 401 credentials rejected",
            "primary_window": {"used_percent": 5, "resets_at": "2026-10-11T01:00:00Z"},
        },
    },
]

# caam v0.1.23 `caam status --json` (statusOutput shape); local, no network.
STATUS = {
    "tools": [
        {
            "tool": "claude",
            "logged_in": True,
            "active_profile": "work-max",
            "health": {
                "status": "warning",
                "reason": "token expires soon",
                "expires_at": "2026-10-12T00:00:00Z",
                "error_count": 1,
                "refresh_due": False,
                "login_required": False,
            },
            "identity": {"email": "me@example.com", "plan_type": "Max 5x"},
        },
        {
            "tool": "codex",
            "logged_in": True,
            "health": {"status": "ok", "refresh_due": True, "login_required": True},
            "identity": {"email": "me@example.com", "plan_type": "Plus"},
        },
    ],
    "warnings": [],
    "recommendations": [],
}


def _row(provider: str) -> dict:
    return copy.deepcopy(next(row for row in LIMITS if row["provider"] == provider))


def _fake_run_json(limits_payload, status_payload=STATUS, calls=None):
    def run(argv, **_kwargs):
        if calls is not None:
            calls.append(list(argv))
        if argv[1] == "limits":
            assert argv == ["caam", "limits", "--format", "json"]
            return limits_payload
        assert argv == ["caam", "status", "--json"]
        if isinstance(status_payload, Exception):
            raise status_payload
        return status_payload

    return run


def test_used_percent_is_consumed_share_not_inverted():
    account = _from_row(_row("claude"), status_tool=None)
    primary, secondary = account.windows
    assert primary.used_percent == 100 and primary.remaining_percent == 0
    assert secondary.used_percent == 47 and secondary.remaining_percent == 53


def test_missing_unmeasured_and_zero_time_windows_dropped():
    account = _from_row(_row("codex"), status_tool=None)
    assert len(account.windows) == 1
    window = account.windows[0]
    assert window.label == "primary"  # payload Label absent -> fallback
    assert window.used_percent == 12

    row = _row("codex")
    row["usage"]["primary_window"]["resets_at"] = "0001-01-01T00:00:00Z"
    account = _from_row(row, status_tool=None)
    assert account.windows[0].resets_at is None  # Go zero time, not a timestamp


def test_error_row_and_quota_unavailable_emit_no_account():
    assert _from_row(_row("grok"), status_tool=None) is None

    row = _row("claude")
    row["usage"]["quota_status"] = "unavailable"
    assert _from_row(row, status_tool=None) is None

    # Every window unmeasured -> nothing measured -> no account either.
    row = _row("claude")
    row["usage"]["primary_window"]["unmeasured"] = True
    del row["usage"]["secondary_window"]
    assert _from_row(row, status_tool=None) is None


def test_agy_maps_to_antigravity():
    row = {
        "provider": "agy",
        "profile_name": "ultra",
        "usage": {
            "provider": "agy",
            "primary_window": {"used_percent": 10, "resets_at": "2026-10-10T20:00:00Z"},
            "quota_status": "ok",
        },
    }
    account = _from_row(row, status_tool=None)
    assert account is not None
    assert account.provider == "antigravity"


def test_rolled_window_is_honest_zero_with_note():
    row = _row("claude")
    row["usage"]["primary_window"] = {"used_percent": 0, "rolled": True}
    account = _from_row(row, status_tool=None)
    window = account.windows[0]
    assert window.used_percent == 0 and window.remaining_percent == 100
    assert "rolled" in (window.reset_description or "")


def test_raw_is_redacted_and_notes_carry_no_secrets():
    account = _from_row(_row("claude"), status_tool=STATUS["tools"][0])
    raw_text = json.dumps(account.raw) + json.dumps(account.notes)
    assert "sk-fixture" not in raw_text
    assert "eyJfixture" not in raw_text
    assert "api_key" not in json.dumps(account.raw)
    assert "refresh_token" not in json.dumps(account.raw["usage"]["primary_window"])


def test_collect_maps_rows_and_health_notes(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("aiuse.collectors.caam.which", lambda _c: "/usr/local/bin/caam")
    monkeypatch.setattr("aiuse.collectors.caam.run_json", _fake_run_json(LIMITS, calls=calls))

    accounts = collect_caam()

    assert [account.provider for account in accounts] == ["claude", "codex"]
    assert all(account.source == "caam" for account in accounts)
    assert calls[0] == ["caam", "limits", "--format", "json"]
    assert calls[1] == ["caam", "status", "--json"]

    claude, codex = accounts
    assert claude.account == "work-max"
    assert claude.plan == "claude_max_5x"  # payload plan_type wins over status identity
    assert "caam health: warning" in " ".join(claude.notes)
    assert "caam credential expires_at 2026-10-12T00:00:00Z" in " ".join(claude.notes)
    assert "caam: refresh_due" in codex.notes
    assert "caam: login_required" in codex.notes
    assert codex.plan == "Plus"  # no payload plan_type -> status identity plan_type


def test_collect_empty_vault_is_success(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.caam.which", lambda _c: "/usr/local/bin/caam")
    monkeypatch.setattr("aiuse.collectors.caam.run_json", _fake_run_json([]))
    assert collect_caam() == []


def test_collect_status_failure_is_tolerated(monkeypatch):
    monkeypatch.setattr("aiuse.collectors.caam.which", lambda _c: "/usr/local/bin/caam")
    monkeypatch.setattr(
        "aiuse.collectors.caam.run_json",
        _fake_run_json(LIMITS, status_payload=CollectorError("caam status: no vault")),
    )
    accounts = collect_caam()
    assert [account.provider for account in accounts] == ["claude", "codex"]
    assert not any("caam health" in note for account in accounts for note in account.notes)


def test_collect_missing_binary_matches_caut_behavior(monkeypatch):
    def _fail(argv, **_kwargs):
        pytest.fail("run_json must not run when the binary is missing")

    monkeypatch.setattr("aiuse.collectors.caam.which", lambda _c: None)
    monkeypatch.setattr("aiuse.collectors.caam.run_json", _fail)
    with pytest.raises(CollectorError, match="caam not found on PATH"):
        collect_caam()
