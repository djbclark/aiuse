"""Regression: frozen OpenUsage/CodexBar 41% weekly left, then agy 429.

Incident 2026-10-18 (see docs/antigravity-pools.md):
- Cache at 13:48Z advertised antigravity claude_gpt weekly 59% used / 41% left
  (OpenUsage nonGeminiWeekly), marked fresh.
- CodexBar had served the identical float 59.054479002952576 since 12:01Z.
- At 13:55Z agy 429 Individual quota / QUOTA_EXHAUSTED, resets
  2026-10-24T18:18:05Z (same weekly clock).
- At 14:00Z CodexBar flipped to 100% used on that weekly window.

Upstream lag cannot be fixed here; these tests lock the fail-safes. Frozen
readings only fail closed when an acp-run turn spent that pool during the
flat span — idle flats stay routable.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiuse.analysis.frozen_quota import apply_frozen_measurements
from aiuse.analysis.selfdescribe import available_pools, enrich_snapshot
from aiuse.quota_refusals import scan_acp_quota_refusals

RESET = "2026-10-24T18:18:05+00:00"
CODEXBAR_USED = 59.054479002952576
NOW = datetime(2026, 10, 18, 13, 48, 18, tzinfo=timezone.utc)


def _agy_claude_gpt_account(*, used: float = 59.0, remaining: float = 41.0, source: str = "openusage_ai") -> dict:
    return {
        "source": source,
        "provider": "antigravity",
        "account": None,
        "plan": None,
        "billing_kind": "subscription_window",
        "windows": [
            {
                "label": "Claude/GPT 5-hour",
                "used_percent": 16.0,
                "remaining_percent": 84.0,
                "resets_at": "2026-10-18T14:55:24+00:00",
                "window_minutes": 300,
                "reset_description": "OpenUsage resource nonGeminiSession",
            },
            {
                "label": "Claude/GPT weekly",
                "used_percent": used,
                "remaining_percent": remaining,
                "resets_at": RESET,
                "window_minutes": 10080,
                "reset_description": "OpenUsage resource nonGeminiWeekly",
            },
        ],
        "collected_at": "2026-10-18T13:34:20.594000+00:00",
        "notes": ["Live data fetched by OpenUsage via cli."],
    }


def _snap_at(collected: datetime, *, used: float = CODEXBAR_USED) -> dict:
    remaining = max(0.0, 100.0 - used)
    account = _agy_claude_gpt_account(used=used, remaining=remaining, source="codexbar")
    account["collected_at"] = collected.isoformat()
    account["windows"][1]["used_percent"] = used
    account["windows"][1]["remaining_percent"] = remaining
    return {"collected_at": collected.isoformat(), "accounts": [account]}


def _write_agy_turn(log_dir: Path, *, when: datetime, model: str, pid: int = 1) -> Path:
    """Write a minimal acp-run jsonl named in local wall time for ``when``."""
    local = when.astimezone()
    name = f"{local.strftime('%Y%m%d-%H%M%S')}-agy-{pid}.jsonl"
    path = log_dir / name
    events = [
        {"t": when.timestamp(), "kind": "initialize", "data": {}},
        {"t": when.timestamp() + 0.1, "kind": "set_config", "data": {"id": "model", "value": model}},
        {"t": when.timestamp() + 1.0, "kind": "prompt_response", "data": {"stopReason": "end_turn"}},
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def test_idle_flat_pool_stays_usable(tmp_path):
    """(a) Flat for 3 h with no acp-run activity must stay routable."""
    t0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 18, 11, 30, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 18, 13, 0, 0, tzinfo=timezone.utc)
    history = [_snap_at(t0, used=59.0), _snap_at(t1, used=59.0), _snap_at(t2, used=59.0)]
    current = _snap_at(t2, used=59.0)
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    snap = enrich_snapshot(
        json.loads(json.dumps(current)),
        now=NOW,
        history=history,
        check_frozen=True,
        min_frozen_seconds=2700,
        acp_log_dir=str(log_dir),
    )
    assert snap["accounts"][0]["usable_now"] is True
    assert snap["accounts"][0].get("measurement_frozen") is not True
    assert any(p.get("pool_family") == "claude_gpt" for p in available_pools(snap))


def test_incident_flat_with_agy_claude_turn_is_frozen(tmp_path):
    """(b) Flat 12:01–13:45 with an agy claude/opus turn inside → frozen."""
    t0 = datetime(2026, 10, 18, 12, 1, 57, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 18, 13, 14, 20, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 18, 13, 34, 20, tzinfo=timezone.utc)
    history = [_snap_at(t0), _snap_at(t1), _snap_at(t2, used=59.0)]
    current = _snap_at(t2, used=59.0)
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    _write_agy_turn(log_dir, when=datetime(2026, 10, 18, 13, 0, 0, tzinfo=timezone.utc), model="claude-opus-5-5")

    plain = enrich_snapshot(json.loads(json.dumps(current)), now=NOW, check_frozen=False)
    assert plain["accounts"][0]["usable_now"] is True

    snap = enrich_snapshot(
        json.loads(json.dumps(current)),
        now=NOW,
        history=history,
        check_frozen=True,
        min_frozen_seconds=2700,
        acp_log_dir=str(log_dir),
    )
    account = snap["accounts"][0]
    assert account["usable_now"] is False
    assert account.get("measurement_frozen") is True
    weekly = next(w for w in account["windows"] if "weekly" in w["label"])
    assert weekly.get("measurement_frozen") is True
    assert weekly["remaining_percent"] == 41.0
    assert not any(p.get("pool_family") == "claude_gpt" for p in available_pools(snap))


def test_gemini_activity_does_not_freeze_claude_gpt(tmp_path):
    """(c) Gemini acp-run turns must not freeze the Claude/GPT pool."""
    t0 = datetime(2026, 10, 18, 12, 1, 57, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 18, 13, 14, 20, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 18, 13, 34, 20, tzinfo=timezone.utc)
    history = [_snap_at(t0, used=59.0), _snap_at(t1, used=59.0), _snap_at(t2, used=59.0)]
    current = _snap_at(t2, used=59.0)
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    _write_agy_turn(
        log_dir,
        when=datetime(2026, 10, 18, 13, 0, 0, tzinfo=timezone.utc),
        model="gemini-3.8-flash",
        pid=2,
    )
    snap = enrich_snapshot(
        json.loads(json.dumps(current)),
        now=NOW,
        history=history,
        check_frozen=True,
        min_frozen_seconds=2700,
        acp_log_dir=str(log_dir),
    )
    assert snap["accounts"][0].get("measurement_frozen") is not True
    weekly = next(w for w in snap["accounts"][0]["windows"] if "weekly" in w["label"])
    assert weekly.get("measurement_frozen") is not True
    assert snap["accounts"][0]["usable_now"] is True
    assert any(p.get("pool_family") == "claude_gpt" for p in available_pools(snap))


def test_apply_frozen_measurements_requires_span(tmp_path):
    """A short span must not freeze even with matching ACP activity."""
    t0 = datetime(2026, 10, 18, 13, 30, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 18, 13, 34, 0, tzinfo=timezone.utc)
    history = [_snap_at(t0), _snap_at(t1)]
    snap = _snap_at(t1)
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    _write_agy_turn(log_dir, when=t0 + timedelta(minutes=1), model="claude-sonnet-5-5")
    apply_frozen_measurements(snap, now=NOW, history=history, min_frozen_seconds=2700, log_dir=log_dir)
    assert snap["accounts"][0].get("measurement_frozen") is not True


def test_acp_quota_refusal_overrides_cached_41_percent(tmp_path, monkeypatch):
    """A fresh Individual-quota 429 with the weekly reset flips the pool."""
    from aiuse import agent_notes

    monkeypatch.setattr(agent_notes, "notes_dir", lambda: tmp_path / "agent-notes")
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    log = log_dir / "20261018-095528-agy-99999.jsonl"
    payload = {
        "t": 1792331728.815,
        "kind": "update",
        "data": {
            "status": "failed",
            "rawOutput": (
                "Error 429, Message: Individual quota reached. Please upgrade your "
                "subscription to increase your limits. Resets in 148h22m36s., "
                "Status: RESOURCE_EXHAUSTED, Details: "
                "[map[@type:type.googleapis.com/google.rpc.ErrorInfo "
                "domain:cloudcode-pa.googleapis.com "
                "metadata:map[model:claude-opus-5-5-high "
                "quotaResetDelay:148h22m36.269038677s "
                "quotaResetTimeStamp:2026-10-24T18:18:05Z uiMessage:true] "
                "reason:QUOTA_EXHAUSTED]]"
            ),
        },
    }
    log.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    refusals = scan_acp_quota_refusals(log_dir=log_dir, now=NOW)
    assert len(refusals) == 1
    assert refusals[0]["provider"] == "antigravity"
    assert refusals[0]["pool_family"] == "claude_gpt"

    monkeypatch.setattr("aiuse.quota_refusals.default_acp_log_dir", lambda: log_dir)
    notes = agent_notes.load_active_notes(now=NOW, discover_acp=True)
    assert any(n.get("provider") == "antigravity" and n.get("pool_family") == "claude_gpt" for n in notes)

    snap = enrich_snapshot(
        {"collected_at": NOW.isoformat(), "accounts": [_agy_claude_gpt_account()]},
        now=NOW,
        notes=notes,
        check_frozen=False,
    )
    account = snap["accounts"][0]
    assert account["usable_now"] is False
    weekly = next(w for w in account["windows"] if "weekly" in w["label"])
    assert weekly["state"] == "exhausted"
    assert weekly["state_source"] == "agent-reported"
    assert weekly["remaining_percent"] == 41.0
    assert not any(p.get("pool_family") == "claude_gpt" for p in available_pools(snap))


def test_short_reset_refusal_is_ignored(tmp_path):
    """A 5h-style reset must not exhaust the weekly pool via this scanner."""
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    soon = (NOW + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = log_dir / "20261018-100000-agy-1.jsonl"
    log.write_text(
        json.dumps(
            {
                "data": {
                    "rawOutput": (
                        f"Individual quota reached. quotaResetTimeStamp:{soon} "
                        "model:claude-opus-5-5-high reason:QUOTA_EXHAUSTED"
                    )
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert scan_acp_quota_refusals(log_dir=log_dir, now=NOW) == []
