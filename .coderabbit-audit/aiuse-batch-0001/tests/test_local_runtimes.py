"""Local runtime probe (issue #7) — advisory only."""

from __future__ import annotations

from aiuse.analysis.local_runtimes import maybe_local_runtime_alerts
from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot, utcnow


def test_disabled_by_default():
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(label="weekly", remaining_percent=0.0, window_minutes=10080),
                ],
            )
        ],
    )
    assert maybe_local_runtime_alerts(snap, config={}) == []


def test_probes_when_empty_and_enabled(monkeypatch):
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(label="weekly", remaining_percent=0.0, window_minutes=10080),
                ],
            )
        ],
    )
    monkeypatch.setattr(
        "aiuse.analysis.local_runtimes._tcp_open",
        lambda host, port, timeout=0.35: port == 11434,
    )
    cfg = {
        "analysis": {
            "local_runtimes": {
                "enabled": True,
                "when": "empty",
                "endpoints": [{"name": "Ollama", "host": "127.0.0.1", "port": 11434}],
            }
        }
    }
    alerts = maybe_local_runtime_alerts(snap, config=cfg)
    assert len(alerts) == 1
    assert alerts[0].provider == "local"
    assert "Ollama" in alerts[0].message
    assert alerts[0].urgency.value == "info"
    assert alerts[0].kind == "prepaid"


def test_skips_when_subscription_still_has_headroom(monkeypatch):
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(label="weekly", remaining_percent=40.0, window_minutes=10080),
                ],
            )
        ],
    )
    monkeypatch.setattr("aiuse.analysis.local_runtimes._tcp_open", lambda *a, **k: True)
    cfg = {"analysis": {"local_runtimes": {"enabled": True, "when": "empty"}}}
    assert maybe_local_runtime_alerts(snap, config=cfg) == []


def test_refuses_non_loopback_by_default(monkeypatch):
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="cswap",
                provider="claude",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[QuotaWindow(label="w", remaining_percent=0.0)],
            )
        ],
    )
    monkeypatch.setattr("aiuse.analysis.local_runtimes._tcp_open", lambda *a, **k: True)
    cfg = {
        "analysis": {
            "local_runtimes": {
                "enabled": True,
                "when": "empty",
                "endpoints": [{"name": "Remote", "host": "10.0.0.5", "port": 11434}],
            }
        }
    }
    assert maybe_local_runtime_alerts(snap, config=cfg) == []
