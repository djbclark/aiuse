"""omp rate-gating by the shared Claude account window (schema 1.4).

omp spends Claude through the account's Claude API access, so an exhausted
multi-day shared Claude window blocks omp regardless of dollars left
(2026-10-21: Anthropic 429 with retry-after equal to the weekly reset while
most of the credit grant remained). `enrich_snapshot` must fail closed for
omp in that state and clear the gate once the reset passes.
"""

from __future__ import annotations

from datetime import timedelta

from aiuse.analysis.selfdescribe import enrich_snapshot, pool_entries
from aiuse.models import utcnow


def _claude_account(weekly_used: float, *, with_5h: float | None = None) -> dict:
    now = utcnow()
    windows = [
        {
            "label": "Claude Code weekly",
            "used_percent": weekly_used,
            "remaining_percent": max(0.0, 100.0 - weekly_used),
            "resets_at": (now + timedelta(days=1, hours=20)).isoformat(),
            "window_minutes": 10080,
        }
    ]
    if with_5h is not None:
        windows.insert(
            0,
            {
                "label": "Claude Code 5-hour",
                "used_percent": with_5h,
                "remaining_percent": max(0.0, 100.0 - with_5h),
                "resets_at": (now + timedelta(hours=2)).isoformat(),
                "window_minutes": 300,
            },
        )
    return {
        "provider": "claude",
        "account": "djbclark@gmail.com",
        "source": "cswap",
        "cli_binary": "claude",
        "billing_kind": "subscription_window",
        "windows": windows,
    }


def _omp_account() -> dict:
    now = utcnow()
    return {
        "provider": "omp",
        "source": "codexbar",
        "cli_binary": "omp",
        "billing_kind": "subscription_window",
        "balance_usd": 95.57,
        "spent_usd": 104.43,
        "windows": [
            {
                "label": "credits",
                "window_id": "claude-cloud-credits",
                "used_percent": 52.2,
                "remaining_percent": 47.8,
                "resets_at": (now + timedelta(days=18)).isoformat(),
                "refill_capacity": 200.0,
                "refill_capacity_unit": "usd",
                "window_minutes": None,
            }
        ],
    }


def _snap(*accounts: dict) -> dict:
    return {"collected_at": utcnow().isoformat(), "accounts": list(accounts)}


def _omp_of(snap: dict) -> dict:
    return next(a for a in snap["accounts"] if a["provider"] == "omp")


def test_exhausted_claude_weekly_gates_omp():
    snap = enrich_snapshot(_snap(_claude_account(100.0), _omp_account()))
    omp = _omp_of(snap)
    assert omp["usable_now"] is False
    assert omp["available_at"] == _omp_reset(snap)
    gate = omp["rate_gated_by"]
    assert gate["provider"] == "claude"
    assert gate["window_label"] == "Claude Code weekly"
    assert gate["resets_at"] == omp["available_at"]
    assert any(n.startswith("Rate-gated") for n in omp["notes"])
    pool = next(p for p in pool_entries(snap) if p["provider"] == "omp")
    assert pool["usable_now"] is False
    assert pool["available_at"] == gate["resets_at"]
    assert pool["rate_gated_by"]["window_label"] == "Claude Code weekly"
    from aiuse.analysis.selfdescribe import available_pools

    assert not any(p["provider"] == "omp" for p in available_pools(snap))


def _omp_reset(snap: dict) -> str:
    claude = next(a for a in snap["accounts"] if a["provider"] == "claude")
    weekly = next(w for w in claude["windows"] if w["label"] == "Claude Code weekly")
    return weekly["resets_at"]


def test_rate_gate_suppresses_burn_before_reset_nudge():
    omp = _omp_account()
    omp["balance_usd"] = 180.0
    omp["spent_usd"] = 20.0
    window = omp["windows"][0]
    window["used_percent"] = 10.0
    window["remaining_percent"] = 90.0
    window["resets_at"] = (utcnow() + timedelta(days=5)).isoformat()
    snap = enrich_snapshot(_snap(_claude_account(100.0), omp))
    pool = next(p for p in pool_entries(snap) if p["provider"] == "omp")
    assert pool["usable_now"] is False
    assert "burn_before_reset" not in pool


def test_5h_exhaustion_alone_does_not_gate():
    snap = enrich_snapshot(_snap(_claude_account(30.0, with_5h=100.0), _omp_account()))
    omp = _omp_of(snap)
    assert "rate_gated_by" not in omp
    assert omp["usable_now"] is True


def test_claude_headroom_does_not_gate():
    snap = enrich_snapshot(_snap(_claude_account(60.0), _omp_account()))
    assert "rate_gated_by" not in _omp_of(snap)


def test_missing_claude_data_does_not_gate():
    claude = _claude_account(100.0)
    claude["windows"] = []
    snap = enrich_snapshot(_snap(claude, _omp_account()))
    assert "rate_gated_by" not in _omp_of(snap)
    assert _omp_of(snap)["usable_now"] is True


def test_one_claude_account_with_headroom_blocks_the_gate():
    other = _claude_account(40.0)
    other["account"] = "second@example.org"
    snap = enrich_snapshot(_snap(_claude_account(100.0), other, _omp_account()))
    assert "rate_gated_by" not in _omp_of(snap)


def test_gate_clears_after_reset_and_does_not_duplicate_notes():
    snap = enrich_snapshot(_snap(_claude_account(100.0), _omp_account()))
    gated = _omp_of(snap)
    assert gated["usable_now"] is False
    # Re-enrich far in the future: the window has reset, so the gate lifts and
    # the previously inserted note is removed rather than duplicated.
    later = utcnow() + timedelta(days=3)
    refreshed = enrich_snapshot(snap, now=later)
    omp = _omp_of(refreshed)
    assert "rate_gated_by" not in omp
    assert not any(str(n).startswith("Rate-gated") for n in omp.get("notes") or [])


def test_fable_model_sublimit_is_not_the_gate_evidence():
    claude = _claude_account(50.0)
    now = utcnow()
    claude["windows"].append(
        {
            "label": "Claude · Fable allowance · weekly",
            "used_percent": 100.0,
            "remaining_percent": 0.0,
            "resets_at": (now + timedelta(days=2)).isoformat(),
            "window_minutes": 10080,
        }
    )
    snap = enrich_snapshot(_snap(claude, _omp_account()))
    # Shared weekly still has headroom, so the exhausted model sublimit alone
    # must not gate omp account-wide (it only restricts Fable-class models).
    assert "rate_gated_by" not in _omp_of(snap)


def _model_snapshot(weekly_used: float):
    from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot

    now = utcnow()
    claude = AccountUsage(
        provider="claude",
        account="djbclark@gmail.com",
        source="cswap",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Claude weekly",
                used_percent=weekly_used,
                remaining_percent=max(0.0, 100.0 - weekly_used),
                resets_at=now + timedelta(days=1, hours=20),
                window_minutes=10080,
            )
        ],
    )
    omp = AccountUsage(
        provider="omp",
        source="codexbar",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        balance_usd=95.57,
        spent_usd=104.43,
        windows=[
            QuotaWindow(
                label="credits",
                window_id="claude-cloud-credits",
                used_percent=52.2,
                remaining_percent=47.8,
                resets_at=now + timedelta(days=18),
            )
        ],
    )
    return Snapshot(collected_at=now, accounts=[claude, omp])


def test_snapshot_gate_sets_field_note_and_clears():
    from aiuse.analysis.selfdescribe import apply_shared_rate_gate_to_snapshot

    snap = _model_snapshot(100.0)
    assert apply_shared_rate_gate_to_snapshot(snap) is True
    omp = next(a for a in snap.accounts if a.provider == "omp")
    assert omp.rate_gated_by is not None
    assert omp.rate_gated_by["window_label"] == "Claude weekly"
    assert any(n.startswith("Rate-gated") for n in omp.notes)
    # Headroom on the shared window clears the gate and removes the note.
    weekly = next(w for a in snap.accounts if a.provider == "claude" for w in a.windows)
    weekly.used_percent, weekly.remaining_percent = 40.0, 60.0
    assert apply_shared_rate_gate_to_snapshot(snap) is False
    omp = next(a for a in snap.accounts if a.provider == "omp")
    assert omp.rate_gated_by is None
    assert not any(str(n).startswith("Rate-gated") for n in omp.notes)


def test_chat_report_renders_the_gate_line():
    from aiuse.analysis.selfdescribe import apply_shared_rate_gate_to_snapshot
    from aiuse.chat_format import render_chat_report

    snap = _model_snapshot(100.0)
    apply_shared_rate_gate_to_snapshot(snap)
    text = render_chat_report(snap, [])
    assert "Rate-gated: shared Claude Claude weekly exhausted until" in text
    assert "credits cannot be spent before it resets" in text
