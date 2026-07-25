"""Loopback serve API (issue #5)."""

from __future__ import annotations

from datetime import timedelta

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.serve import _payload_from_disk_row, _ServeState


def test_payload_from_disk_row_picks_suggestion():
    row = {
        "collected_at": utcnow().isoformat(),
        "accounts": [],
        "alerts": [
            {
                "urgency": "high",
                "provider": "claude",
                "account": "a@x.com",
                "window_label": "Claude Code weekly",
                "remaining_percent": 90,
                "days_until_reset": 2,
                "plan": None,
                "message": "burn",
                "source": "cswap",
                "score": 80,
                "kind": "burn",
            }
        ],
    }
    payload = _payload_from_disk_row(row, config={"analysis": {"learn_from_history": False}})
    assert payload["source"] == "cache"
    assert payload["suggestion"]["provider"] == "claude"
    assert payload["suggestion"]["kind"] == "burn"


def test_serve_state_live_collect(monkeypatch):
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="weekly",
                        remaining_percent=50,
                        resets_at=utcnow() + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="codex",
            account=None,
            window_label="weekly",
            remaining_percent=50,
            days_until_reset=3,
            plan=None,
            message="burn",
            source="codexbar",
            score=70,
            kind="burn",
        )
    ]
    monkeypatch.setattr("aiuse.serve.run_collectors", lambda _c: snap)
    monkeypatch.setattr("aiuse.serve.analyze_use_or_lose", lambda _s, _c: list(alerts))
    monkeypatch.setattr("aiuse.serve.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.serve.should_persist_snapshots", lambda _c: False)
    monkeypatch.setattr("aiuse.serve.load_recent_snapshots", lambda **_k: [])

    state = _ServeState(config={}, max_age_seconds=3600)
    payload = state.get_payload(refresh=True)
    assert payload["source"] == "live"
    assert payload["suggestion"]["provider"] == "codex"

    # Second call without refresh uses in-process cache
    payload2 = state.get_payload(refresh=False)
    assert payload2["source"] == "live"
    assert payload2["suggestion"]["score"] == 70


def test_http_handler_health(monkeypatch):
    from aiuse.serve import DEFAULT_HOST

    # Smoke: import path and host guard
    assert DEFAULT_HOST == "127.0.0.1"
    # refuse non-loopback
    from aiuse import serve as serve_mod

    code = serve_mod.run_serve(host="0.0.0.0", port=1)
    assert code == 1
