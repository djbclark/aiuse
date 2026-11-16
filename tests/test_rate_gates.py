"""The durable omp rate-gate latch: evidence store, omp-log ingestion, CLI.

Each of the five 2026-10-21..22 regressions was an input blink silently
ungating omp. These tests pin the properties that end that class of bug:
recorded evidence survives missing data and headroom blinks, deadlines are
computed from event time, expiry and the manual clear tombstone are the only
things that lift a gate, and ingestion is idempotent.
"""

from __future__ import annotations

import json
from datetime import timedelta

from aiuse import rate_gates
from aiuse.models import utcnow
from aiuse.omp_rate_events import scan_omp_rate_limit_events

# Sanitized from the real ~/.omp/logs line of the 2026-10-22 19:49 EDT
# incident (req_011CgJArBbZXryGqwdN1eEUb): retry-after 36652s lands exactly on
# the 2026-10-23 10:00Z Claude weekly reset.
_TRANSPORT_429 = (
    '{"timestamp":"2026-10-22T19:49:07.928-04:00","level":"debug","pid":16756,'
    '"message":"cowork transport rejected","url":"api.anthropic.com/v1/messages",'
    '"status":429,"headers":{"cf-ray":"a4ec659298100869-BOS","server":"cloudflare",'
    '"request-id":"req_TEST011CgJArBbZXryGqwdN1eEUb","retry-after":"36652",'
    '"x-should-retry":"true"}}'
)


def _fresh_transport_429() -> str:
    """The real 429 line re-stamped to now, so its retry-after is still live.

    The verbatim line is dated 2026-10-22 with a ~10h retry-after; tests that
    need an *active* gate would otherwise start failing once that expired.
    """
    stamp = utcnow().isoformat(timespec="milliseconds")
    return _TRANSPORT_429.replace("2026-10-22T19:49:07.928-04:00", stamp)


def _record(
    resets_in_h: float, *, event_id: str = "ev-1", source: str = "omp-429-anthropic", observed_ago_min: float = 0.0
):
    now = utcnow()
    return rate_gates.record_evidence(
        "omp",
        resets_at=now + timedelta(hours=resets_in_h),
        observed_at=now - timedelta(minutes=observed_ago_min),
        source=source,
        event_id=event_id,
        note="test",
        now=now,
    )


def test_record_then_active_gate_round_trips():
    assert _record(10.0) is True
    active = rate_gates.active_gate("omp")
    assert active is not None
    assert active["source"] == "omp-429-anthropic"
    assert active["event_id"] == "ev-1"


def test_duplicate_event_id_records_once(tmp_path):
    assert _record(10.0, event_id="dup") is True
    assert _record(10.0, event_id="dup") is False
    store = rate_gates.load_store()
    assert len(store["gates"]["omp"]["evidence"]) == 1


def test_implausible_span_is_rejected_not_clamped():
    # reset 40 days after observation: rejected outright — clamping would
    # invent a month-long block that replay keeps extending.
    assert _record(24 * 39, observed_ago_min=24 * 60) is False
    assert rate_gates.active_gate("omp") is None


def test_already_expired_evidence_is_not_recorded():
    assert _record(-1.0) is False
    assert rate_gates.active_gate("omp") is None


def test_gate_expires_at_reset_plus_grace():
    now = utcnow()
    assert rate_gates.record_evidence(
        "omp",
        resets_at=now + timedelta(minutes=30),
        observed_at=now,
        source="claude-shared-window",
        event_id="grace-1",
        now=now,
    )
    just_before = now + timedelta(minutes=30) + timedelta(seconds=rate_gates.GATE_GRACE_SECONDS - 1)
    just_after = now + timedelta(minutes=30) + timedelta(seconds=rate_gates.GATE_GRACE_SECONDS + 1)
    assert rate_gates.active_gate("omp", now=just_before) is not None
    assert rate_gates.active_gate("omp", now=just_after) is None


def test_clear_tombstones_old_evidence_but_new_refusal_reGates():
    now = utcnow()
    assert rate_gates.record_evidence(
        "omp",
        resets_at=now + timedelta(hours=8),
        observed_at=now - timedelta(minutes=5),
        source="omp-429-anthropic",
        event_id="old-429",
        now=now,
    )
    cleared_at = now + timedelta(minutes=1)
    assert rate_gates.clear_gate("omp", now=cleared_at) is True
    assert rate_gates.active_gate("omp", now=cleared_at) is None
    # Replaying the same refusal after the clear stays cleared (the old
    # evidence was observed before the tombstone), even under a new id.
    assert rate_gates.record_evidence(
        "omp",
        resets_at=cleared_at + timedelta(hours=8),
        observed_at=now - timedelta(minutes=5),
        source="omp-429-anthropic",
        event_id="replayed-429",
        now=cleared_at,
    )
    assert rate_gates.active_gate("omp", now=cleared_at) is None
    # A genuinely new refusal re-gates.
    assert rate_gates.record_evidence(
        "omp",
        resets_at=cleared_at + timedelta(hours=8),
        observed_at=cleared_at + timedelta(minutes=2),
        source="omp-429-anthropic",
        event_id="new-429",
        now=cleared_at + timedelta(minutes=2),
    )
    assert rate_gates.active_gate("omp", now=cleared_at + timedelta(minutes=3)) is not None


def test_corrupt_store_reads_empty_and_is_repaired_on_next_record(tmp_path):
    path = tmp_path / "gates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert rate_gates.load_store(path)["gates"] == {}
    assert _record(5.0) is True
    assert rate_gates.active_gate("omp") is not None


def test_strongest_evidence_wins():
    now = utcnow()
    for hours, event_id in ((2.0, "short"), (9.0, "long")):
        rate_gates.record_evidence(
            "omp",
            resets_at=now + timedelta(hours=hours),
            observed_at=now,
            source="omp-429-anthropic",
            event_id=event_id,
            now=now,
        )
    active = rate_gates.active_gate("omp", now=now)
    assert active is not None and active["event_id"] == "long"


# --- omp log parser -----------------------------------------------------------


def _write_log(tmp_path, name: str, *lines: str) -> None:
    log_dir = tmp_path / "omp-logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parser_reads_real_429_line_and_lands_on_the_reset(tmp_path):
    _write_log(tmp_path, "omp.2026-10-22.16756.log", _TRANSPORT_429)
    events = scan_omp_rate_limit_events(log_dir=tmp_path / "omp-logs")
    assert len(events) == 1
    event = events[0]
    assert event.event_id == "omp-log:req_TEST011CgJArBbZXryGqwdN1eEUb"
    assert event.retry_after_seconds == 36652
    # 19:49:07.928-04:00 + 36652s = 2026-10-23T05:59:59.928-04:00 — the 6am
    # Claude weekly reset the operator's pi session was refused until.
    assert event.resets_at.isoformat() == "2026-10-23T05:59:59.928000-04:00"


def test_parser_skips_non_anthropic_non_429_and_malformed_lines(tmp_path):
    _write_log(
        tmp_path,
        "omp.2026-10-22.1.log",
        "{not json",
        '{"timestamp":"2026-10-22T19:00:00-04:00","message":"cowork transport rejected","url":"api.openai.com/v1","status":429,"headers":{"retry-after":"60"}}',
        '{"timestamp":"2026-10-22T19:00:00-04:00","message":"cowork transport rejected","url":"api.anthropic.com/v1/messages","status":500,"headers":{"retry-after":"60"}}',
        '{"timestamp":"2026-10-22T19:00:00-04:00","message":"agent turn ended with provider error","url":"api.anthropic.com/v1/messages","status":429,"headers":{"retry-after":"60"}}',
        '{"timestamp":"2026-10-22T19:00:00-04:00","message":"cowork transport rejected","url":"api.anthropic.com/v1/messages","status":429,"headers":{"retry-after":"HTTP-date"}}',
    )
    assert scan_omp_rate_limit_events(log_dir=tmp_path / "omp-logs") == []


def test_parser_deduplicates_the_same_request_across_files(tmp_path):
    _write_log(tmp_path, "omp.2026-10-22.1.log", _TRANSPORT_429)
    _write_log(tmp_path, "omp.2026-10-22.2.log", _TRANSPORT_429)
    assert len(scan_omp_rate_limit_events(log_dir=tmp_path / "omp-logs")) == 1


def test_parser_missing_directory_is_empty(tmp_path):
    assert scan_omp_rate_limit_events(log_dir=tmp_path / "nope") == []


def test_ingest_records_log_events_as_evidence(tmp_path):
    _write_log(tmp_path, "omp.2026-10-22.16756.log", _fresh_transport_429())
    assert rate_gates.ingest_omp_log_evidence(log_dir=tmp_path / "omp-logs") == 1
    assert rate_gates.ingest_omp_log_evidence(log_dir=tmp_path / "omp-logs") == 0  # idempotent
    active = rate_gates.active_gate("omp")
    assert active is not None and active["source"] == "omp-429-anthropic"


# --- CLI ----------------------------------------------------------------------


def test_gate_status_exit_codes_and_clear(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert rate_gates.run_gate_command(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["gates"] == {}

    assert _record(6.0) is True
    assert rate_gates.run_gate_command(["status", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["gates"]["omp"]["gated"] is True

    assert rate_gates.run_gate_command(["clear", "omp"]) == 0
    capsys.readouterr()
    assert rate_gates.run_gate_command(["status", "--provider", "omp"]) == 0


def test_gate_status_ingests_omp_logs_first(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AIUSE_OMP_LOG_DIR", str(tmp_path / "omp-logs"))
    _write_log(tmp_path, "omp.2026-10-22.16756.log", _fresh_transport_429())
    assert rate_gates.run_gate_command(["status", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["gates"]["omp"]["source"] == "omp-429-anthropic"


def test_omp_has_active_api_key_scenarios(tmp_path):
    import sqlite3
    import time

    from aiuse.omp_rate_events import omp_has_active_api_key

    # 1. Missing db
    assert omp_has_active_api_key(db_path=tmp_path / "missing.db") is False

    # 2. Active api_key
    db = tmp_path / "agent.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY, provider TEXT, credential_type TEXT, data TEXT, disabled_cause TEXT)"
        )
        conn.execute(
            "CREATE TABLE auth_credential_blocks (credential_id INTEGER, provider_key TEXT, block_scope TEXT, blocked_until_ms INTEGER, updated_at INTEGER)"
        )
        conn.execute("INSERT INTO auth_credentials VALUES (11, 'anthropic', 'api_key', '{}', NULL)")
    assert omp_has_active_api_key(db_path=db) is True

    # 3. Disabled api_key
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE auth_credentials SET disabled_cause = 'user' WHERE id = 11")
    assert omp_has_active_api_key(db_path=db) is False

    # 4. Blocked api_key
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE auth_credentials SET disabled_cause = NULL WHERE id = 11")
        future_ms = int((time.time() + 3600) * 1000)
        conn.execute(
            "INSERT INTO auth_credential_blocks VALUES (11, 'anthropic:api_key', '', ?, ?)",
            (future_ms, int(time.time())),
        )
    assert omp_has_active_api_key(db_path=db) is False

    # 5. Expired block
    with sqlite3.connect(db) as conn:
        past_ms = int((time.time() - 3600) * 1000)
        conn.execute("UPDATE auth_credential_blocks SET blocked_until_ms = ? WHERE credential_id = 11", (past_ms,))
    assert omp_has_active_api_key(db_path=db) is True


def test_active_api_key_ignores_claude_shared_window_latch(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "agent.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY, provider TEXT, credential_type TEXT, data TEXT, disabled_cause TEXT)"
        )
        conn.execute(
            "CREATE TABLE auth_credential_blocks (credential_id INTEGER, provider_key TEXT, block_scope TEXT, blocked_until_ms INTEGER, updated_at INTEGER)"
        )
        conn.execute("INSERT INTO auth_credentials VALUES (11, 'anthropic', 'api_key', '{}', NULL)")
    monkeypatch.setenv("AIUSE_OMP_AGENT_DB", str(db))

    # Record shared-window evidence: ignored when active api_key present
    assert (
        rate_gates.record_evidence(
            "omp",
            resets_at=utcnow() + timedelta(hours=5),
            observed_at=utcnow(),
            source="claude-shared-window",
            event_id="ev-shared",
            note="shared window",
        )
        is False
    )
    assert rate_gates.active_gate("omp") is None

    # But direct 429 evidence is still recorded and honored
    assert (
        rate_gates.record_evidence(
            "omp",
            resets_at=utcnow() + timedelta(hours=5),
            observed_at=utcnow(),
            source="omp-429-anthropic",
            event_id="ev-429",
            note="429 refusal",
        )
        is True
    )
    active = rate_gates.active_gate("omp")
    assert active is not None
    assert active["source"] == "omp-429-anthropic"


# --- Issue #51: non-resettable OpenAI API org spend-limit refusals -----------


def test_openai_spend_limit_refusal_records_non_resettable_block(tmp_path, monkeypatch):
    from aiuse import agent_notes, quota_refusals
    from aiuse.analysis.selfdescribe import OMP_OPENAI_SOURCE, enrich_snapshot, pool_entries

    now = utcnow()
    log_dir = tmp_path / "acp-run"
    log_dir.mkdir()
    (log_dir / "20261116-120000-pi-101.stderr").write_text(
        "[System Error] Your organization has reached its configured enforced spend limit. "
        "Update your limit at https://platform.openai.com/settings/organization/limits.\n",
        encoding="utf-8",
    )

    refusals = quota_refusals.scan_acp_quota_refusals(log_dir=log_dir, now=now)
    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal["provider"] == "pi"
    assert refusal["pool_family"] == "openai_api"
    assert refusal["state"] == "blocked"
    assert refusal["refusal_kind"] == "spend_limit"
    assert refusal["operator_action_required"] is True
    assert refusal["usable_now"] is False
    assert refusal["resets_at"] is None

    monkeypatch.setattr(quota_refusals, "default_acp_log_dir", lambda: log_dir)
    monkeypatch.setattr(agent_notes, "notes_dir", lambda: tmp_path / "agent-notes")

    notes = agent_notes.load_active_notes(now=now, discover_acp=True)
    assert len(notes) == 1
    assert notes[0]["state"] == "blocked"
    assert notes[0]["resets_at"] is None

    # Persisted block survives past 24h without expiring on a fake reset timer.
    later = agent_notes.load_active_notes(now=now + timedelta(days=3), discover_acp=False)
    assert len(later) == 1
    assert later[0]["refusal_kind"] == "spend_limit"

    snap = enrich_snapshot(
        {
            "collected_at": now.isoformat(),
            "accounts": [
                {
                    "provider": "pi",
                    "source": OMP_OPENAI_SOURCE,
                    "api_key_pool": {"usable": True, "available_at": None},
                    "windows": [{"label": "openai_api", "used_percent": 0.0, "remaining_percent": 100.0}],
                }
            ],
        },
        now=now,
        notes=later,
    )
    acct = snap["accounts"][0]
    assert acct["state"] == "blocked"
    assert acct["refusal_kind"] == "spend_limit"
    assert acct["operator_action_required"] is True
    assert acct["usable_now"] is False
    assert acct["available_at"] is None

    pools = pool_entries(snap, now=now)
    assert len(pools) == 1
    assert pools[0]["state"] == "blocked"
    assert pools[0]["refusal_kind"] == "spend_limit"
    assert pools[0]["operator_action_required"] is True
    assert pools[0]["usable_now"] is False
    assert pools[0]["available_at"] is None


# --- Issue #33: agy CLI burst lockout per-transport while ACP works -----------


def test_agy_cli_burst_lockout_is_per_transport_without_emptying_pool(tmp_path):
    from aiuse import quota_refusals
    from aiuse.analysis.selfdescribe import enrich_snapshot, pool_entries

    now = utcnow().replace(microsecond=0)
    acp_dir = tmp_path / "acp-run"
    acp_dir.mkdir()
    (acp_dir / "20261116-120000-agy-202.stderr").write_text(
        "Error: RESOURCE_EXHAUSTED (code 429): rate limit exceeded\n",
        encoding="utf-8",
    )
    # Burst RESOURCE_EXHAUSTED without a multi-day quotaResetTimeStamp must NOT mark the pool exhausted.
    assert quota_refusals.scan_acp_quota_refusals(log_dir=acp_dir, now=now) == []

    cli_dir = tmp_path / "agy-cli-log"
    cli_dir.mkdir()
    stamp = now.astimezone().strftime("%m%d %H:%M:%S.100000")
    day = now.astimezone().strftime("%Y%m%d")
    (cli_dir / f"cli-{day}_120000.log").write_text(
        f"I{stamp} 1001 run.go:395] Run: attempt 1 failed (RESOURCE_EXHAUSTED (code 429): burst), retrying in 4s\n",
        encoding="utf-8",
    )

    lockouts = quota_refusals.scan_transport_burst_lockouts(agy_log_dir=cli_dir, now=now)
    assert "antigravity" in lockouts
    entry = lockouts["antigravity"][0]
    assert entry["transport"] == "cli"
    assert entry["state"] == "limited"
    assert entry["usable"] is False
    assert entry["pool_exhausted"] is False

    snap = enrich_snapshot(
        {
            "collected_at": now.isoformat(),
            "accounts": [
                {
                    "provider": "antigravity",
                    "cli_binary": "agy",
                    "windows": [
                        {"label": "Gemini 5-hour", "used_percent": 5.0, "remaining_percent": 95.0},
                        {"label": "Claude/GPT weekly", "used_percent": 20.0, "remaining_percent": 80.0},
                    ],
                }
            ],
        },
        now=now,
        client_limits=lockouts,
    )
    acct = snap["accounts"][0]
    assert acct["usable_now"] is True
    assert acct["transports"]["cli"]["usable"] is False
    assert acct["transports"]["cli"]["state"] == "limited"
    assert acct["transports"]["acp"]["usable"] is True

    pools = pool_entries(snap, now=now)
    assert pools and all(p["usable_now"] is True for p in pools)
    assert all(p["transports"]["cli"]["usable"] is False for p in pools)
    assert all(p["transports"]["acp"]["usable"] is True for p in pools)
