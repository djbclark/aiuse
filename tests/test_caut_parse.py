"""Unit tests for caut collector parsing (no live CLI required)."""

from aiuse.collectors.caut import _from_row
from aiuse.models import BillingKind


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


def test_caut_error_row():
    acc = _from_row({"provider": "gemini", "error": "unsupported source"})
    assert acc.source == "caut"
    assert acc.error == "unsupported source"
    assert not acc.windows
