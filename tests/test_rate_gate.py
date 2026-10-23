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
    line = next(line for line in snap["summary_lines"] if line.startswith("omp"))
    assert "credits: ok" not in line
    assert "credits: GATED" in line
    assert "NOT usable now" in line


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


# --- durable latch: the historical incidents stay fixed (2026-10-22) ----------


def test_latched_gate_survives_missing_claude_row():
    """2026-10-22 incident 1: the Claude row blinked out; omp went ungated."""
    gated = enrich_snapshot(_snap(_claude_account(100.0), _omp_account()))
    assert gated["accounts"][0]["provider"] == "claude"
    later = enrich_snapshot(_snap(_omp_account()))
    omp = _omp_of(later)
    assert omp["rate_gated_by"] is not None
    assert omp["rate_gated_by"]["window_label"] == "shared Claude window (latched)"
    assert omp["usable_now"] is False


def test_latched_gate_survives_a_headroom_blink():
    """A cswap headroom blip mid-window must not ungate omp (fail closed)."""
    enrich_snapshot(_snap(_claude_account(100.0), _omp_account()))
    blinked = enrich_snapshot(_snap(_claude_account(60.0), _omp_account()))
    omp = _omp_of(blinked)
    assert omp["rate_gated_by"] is not None
    assert omp["usable_now"] is False


def test_latch_expires_once_the_reset_really_passes():
    enrich_snapshot(_snap(_claude_account(100.0), _omp_account()))
    later = utcnow() + timedelta(days=3)
    refreshed = enrich_snapshot(_snap(_claude_account(60.0), _omp_account()), now=later)
    assert "rate_gated_by" not in _omp_of(refreshed)


def test_omp_429_evidence_alone_gates_without_claude_data():
    """Anthropic's own refusal is enough; no Claude dashboard row required."""
    from aiuse.rate_gates import record_evidence

    now = utcnow()
    assert record_evidence(
        "omp",
        resets_at=now + timedelta(hours=10),
        observed_at=now,
        source="omp-429-anthropic",
        event_id="test-429",
        now=now,
    )
    snap = enrich_snapshot(_snap(_omp_account()))
    omp = _omp_of(snap)
    assert omp["rate_gated_by"] is not None
    assert omp["rate_gated_by"]["resets_at"] == (now + timedelta(hours=10)).isoformat()
    assert omp["usable_now"] is False
    from aiuse.analysis.selfdescribe import available_pools

    assert not any(p["provider"] == "omp" for p in available_pools(snap))


def test_watch_credit_override_does_not_skip_the_gate(monkeypatch):
    """2026-10-22 watch regression: `overrides or gate` short-circuited the gate."""
    from aiuse import watch

    _write_admin_reading()  # overrides path synthesizes the omp row and returns True
    monkeypatch.setattr("aiuse.watch.run_collectors", lambda _c: _claude_only_snapshot())
    monkeypatch.setattr("aiuse.watch.analyze_use_or_lose", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.watch.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    snapshot, _alerts = watch.collect_watch_frame({}, persist=False)
    omp = next(a for a in snapshot.accounts if a.provider == "omp")
    assert omp.rate_gated_by is not None


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
    from aiuse.rate_gates import clear_gate

    snap = _model_snapshot(100.0)
    assert apply_shared_rate_gate_to_snapshot(snap) is True
    omp = next(a for a in snap.accounts if a.provider == "omp")
    assert omp.rate_gated_by is not None
    assert omp.rate_gated_by["window_label"] == "Claude weekly"
    assert any(n.startswith("Rate-gated") for n in omp.notes)
    # Headroom on the shared window no longer clears the gate: the first apply
    # recorded durable evidence, and a single later reading must not ungate
    # omp while Anthropic may still be refusing (2026-10-22).
    weekly = next(w for a in snap.accounts if a.provider == "claude" for w in a.windows)
    weekly.used_percent, weekly.remaining_percent = 40.0, 60.0
    assert apply_shared_rate_gate_to_snapshot(snap) is True
    omp = next(a for a in snap.accounts if a.provider == "omp")
    assert omp.rate_gated_by is not None
    assert omp.rate_gated_by["window_label"] == "shared Claude window (latched)"
    # The operator escape hatch clears it (tombstones the evidence).
    assert clear_gate("omp") is True
    assert apply_shared_rate_gate_to_snapshot(snap) is False
    omp = next(a for a in snap.accounts if a.provider == "omp")
    assert omp.rate_gated_by is None
    assert not any(str(n).startswith("Rate-gated") for n in omp.notes)


def _claude_only_snapshot():
    from aiuse.models import AccountUsage, BillingKind, QuotaWindow, Snapshot

    now = utcnow()
    claude = AccountUsage(
        provider="claude",
        account="person@example.com",
        source="cswap",
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
        windows=[
            QuotaWindow(
                label="Claude Code weekly",
                used_percent=100.0,
                remaining_percent=0.0,
                resets_at=now + timedelta(hours=20),
                window_minutes=10080,
            )
        ],
    )
    return Snapshot(collected_at=now, accounts=[claude])


def _write_admin_reading() -> None:
    import json

    from aiuse.credits_override import readings_file
    from aiuse.models import utcnow

    now = utcnow()
    path = readings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "omp": {
                    "remaining_usd": 40.0,
                    "total_usd": 100.0,
                    "expires_at": (now + timedelta(days=20)).isoformat(),
                    "granted_at": (now - timedelta(days=10)).isoformat(),
                    "observed_at": now.isoformat(),
                    "note": "cache-file gate test",
                }
            }
        ),
        encoding="utf-8",
    )


def test_cache_file_carries_a_rate_gated_admin_api_omp_row():
    """A reader of latest.json must see the gate even when CodexBar omitted omp."""
    import json

    from aiuse.analysis.history import save_snapshot, snapshot_dir
    from aiuse.analysis.selfdescribe import available_pools

    _write_admin_reading()
    save_snapshot(_claude_only_snapshot(), [])
    saved = json.loads((snapshot_dir() / "latest.json").read_text(encoding="utf-8"))
    omp = next(account for account in saved["accounts"] if account["provider"] == "omp")
    assert saved["account_count"] == len(saved["accounts"]) == 2
    assert omp["source"] == "admin-api"
    assert omp["usable_now"] is False
    assert omp["rate_gated_by"]["provider"] == "claude"
    assert omp["rate_gated_by"]["window_label"] == "Claude Code weekly"
    assert not any(pool["provider"] == "omp" for pool in available_pools(saved))
    line = next(line for line in saved["summary_lines"] if line.startswith("omp"))
    assert "credits: ok" not in line
    assert "credits: GATED" in line
    assert "NOT usable now" in line
    assert "rate-gated" in line


def test_partial_snapshot_does_not_invent_an_omp_row():
    import json

    from aiuse.analysis.history import partial_sample_dir, save_snapshot, snapshot_dir

    _write_admin_reading()
    save_snapshot(_claude_only_snapshot(), [], partial_providers=["claude"])
    assert not (snapshot_dir() / "latest.json").exists()
    saved = json.loads(next(partial_sample_dir().glob("*.json")).read_text(encoding="utf-8"))
    assert all(account["provider"] != "omp" for account in saved["accounts"])


def test_cache_file_does_not_resurrect_a_disabled_omp():
    import json

    from aiuse.analysis.history import save_snapshot, snapshot_dir

    _write_admin_reading()
    snap = _claude_only_snapshot()
    snap.disabled_services = {"omp": "operator"}
    save_snapshot(snap, [])
    saved = json.loads((snapshot_dir() / "latest.json").read_text(encoding="utf-8"))
    assert all(account["provider"] != "omp" for account in saved["accounts"])


def test_chat_report_renders_the_gate_line():
    from aiuse.analysis.selfdescribe import apply_shared_rate_gate_to_snapshot
    from aiuse.chat_format import render_chat_report

    snap = _model_snapshot(100.0)
    apply_shared_rate_gate_to_snapshot(snap)
    text = render_chat_report(snap, [])
    assert "Rate-gated: shared Claude Claude weekly exhausted until" in text
    assert "credits cannot be spent before it resets" in text
