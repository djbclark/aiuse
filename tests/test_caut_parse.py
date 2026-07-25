"""Unit tests for caut collector parsing (no live CLI required)."""

from aiuse.collectors.caut import _from_row, _row_has_live_quota, collect_caut
from aiuse.models import AccountUsage, BillingKind, QuotaWindow


def test_caut_claude_windows():
    acc = _from_row(
        {
            "provider": "claude",
            "account": "user@example.com",
            "source": "oauth",
            "usage": {
                "primary": {
                    "usedPercent": 78.0,
                    "windowMinutes": 300,
                    "resetsAt": "2026-07-25T14:10:00Z",
                },
                "secondary": {
                    "usedPercent": 8.0,
                    "windowMinutes": 10080,
                    "resetsAt": "2026-07-31T10:00:00Z",
                },
                "identity": {"accountEmail": "user@example.com"},
            },
        }
    )
    assert acc.source == "caut"
    assert acc.provider == "claude"
    assert acc.account == "user@example.com"
    assert acc.billing_kind == BillingKind.SUBSCRIPTION_WINDOW
    assert len(acc.windows) == 2
    assert acc.windows[0].remaining() == 22.0
    assert acc.windows[1].remaining() == 92.0
    assert _row_has_live_quota(acc)


def test_caut_auth_warning_with_windows_notes_quirk():
    acc = _from_row(
        {
            "provider": "claude",
            "source": "oauth",
            "authWarning": "Auth missing! Run: claude auth login",
            "usage": {
                "primary": {
                    "usedPercent": 10.0,
                    "windowMinutes": 300,
                    "resetsAt": "2026-07-25T14:10:00Z",
                },
            },
        }
    )
    assert any("quirk" in n.lower() or "windows were returned" in n for n in acc.notes)


def test_caut_error_row():
    acc = _from_row({"provider": "gemini", "error": "unsupported source"})
    assert acc.source == "caut"
    assert acc.error == "unsupported source"
    assert not acc.windows
    assert not _row_has_live_quota(acc)


def test_collect_caut_retries_when_identity_only(monkeypatch):
    """One automatic retry when first fetch has no live windows."""
    empty = {
        "schemaVersion": "caut.v1",
        "data": [
            {
                "provider": "claude",
                "source": "oauth",
                "authWarning": "Auth missing! Run: claude auth login",
                "usage": {"primary": None, "secondary": None},
            }
        ],
        "errors": [],
    }
    filled = {
        "schemaVersion": "caut.v1",
        "data": [
            {
                "provider": "claude",
                "source": "oauth",
                "authWarning": "Auth missing! Run: claude auth login",
                "usage": {
                    "primary": {
                        "usedPercent": 50.0,
                        "windowMinutes": 300,
                        "resetsAt": "2026-07-25T14:10:00Z",
                    }
                },
            }
        ],
        "errors": [],
    }
    calls = {"n": 0}

    def fake_run_json(argv, *, timeout=90.0):
        calls["n"] += 1
        return empty if calls["n"] == 1 else filled

    monkeypatch.setattr("aiuse.collectors.caut.which", lambda _c: "/usr/bin/caut")
    monkeypatch.setattr("aiuse.collectors.caut.run_json", fake_run_json)
    accounts = collect_caut()
    assert calls["n"] == 2
    assert len(accounts) == 1
    assert accounts[0].windows[0].remaining() == 50.0
