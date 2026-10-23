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
    _write_log(tmp_path, "omp.2026-10-22.16756.log", _TRANSPORT_429)
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
    _write_log(tmp_path, "omp.2026-10-22.16756.log", _TRANSPORT_429)
    assert rate_gates.run_gate_command(["status", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["gates"]["omp"]["source"] == "omp-429-anthropic"
