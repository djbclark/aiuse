"""Durable rate-gate evidence: the omp shared-Claude-window latch.

Five display-layer patches (8373bd5, db4a6e9, c65e51a, 180a79f, 018bcfc) each
re-derived the omp gate from whatever Claude quota data happened to be present
at that moment, and every input blink — the omp row vanishing from a snapshot,
cswap going stale, one headroom reading — silently ungated omp while Anthropic
was still answering 429 (2026-10-21..22). This module makes an observed gate
durable: evidence is recorded when seen and keeps omp gated until its own
reset passes, whatever later readings say (fail closed). Two evidence sources,
strongest first:

1. ``omp-429-anthropic`` — an actual 429 from ``api.anthropic.com`` in omp's
   own logs, with Anthropic's ``retry-after`` (via ``omp_rate_events``).
   Nothing downstream may clear it; only its deadline (or ``gate clear``).
2. ``claude-shared-window`` — every observed Claude account's shared
   weekly/monthly window exhausted with a future reset (the schema-1.4 rule).

A scraper headroom reading can no longer clear recorded evidence; missing
Claude data can no longer prevent a recorded gate from applying. Semantics
reviewed by codex gpt-6-astra 2026-10-22 (see docs/handoffs for the chain):
evidence records stay individually invalidable, deadlines are computed from
event time, and a manual clear writes a tombstone so replaying old logs cannot
recreate the cleared gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GATE_GRACE_SECONDS = 120.0
# Longest block any source may claim (monthly windows are ~31d; a weekly
# reset is 7d). Longer spans are rejected outright, never clamped — a clamped
# deadline invents a block that replay would keep extending.
_MAX_BLOCK_SPAN = timedelta(days=32)
_MAX_EVIDENCE_RECORDS = 50
_PRUNE_AFTER = timedelta(days=1)
_DEFAULT_STORE = "~/.local/state/aiuse/rate-gates.json"

GATED_PROVIDERS = ("omp",)


def store_path() -> Path:
    override = os.environ.get("AIUSE_RATE_GATE_STORE")
    if override:
        return Path(override)
    return Path(os.path.expanduser(_DEFAULT_STORE))


def _empty_store() -> dict[str, Any]:
    return {"schema_version": 1, "gates": {}}


def load_store(path: str | Path | None = None) -> dict[str, Any]:
    """The evidence store; a missing or corrupt file reads as empty.

    Corrupt state must not fabricate a gate, and must not wedge the gate
    either: the next recorded evidence replaces the file.
    """
    target = Path(path) if path is not None else store_path()
    try:
        data = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError):
        return _empty_store()
    gates = data.get("gates") if isinstance(data, dict) else None
    if not isinstance(gates, dict):
        return _empty_store()
    return {"schema_version": 1, "gates": gates}


def _write_store(store: dict[str, Any], path: str | Path | None) -> bool:
    target = Path(path) if path is not None else store_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(store, indent=1, sort_keys=True) + "\n", "utf-8")
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except OSError:
        return False  # the in-process decision still applies; only durability is lost
    return True


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def record_evidence(
    provider: str,
    *,
    resets_at: datetime,
    observed_at: datetime,
    source: str,
    event_id: str,
    note: str = "",
    now: datetime | None = None,
    path: str | Path | None = None,
) -> bool:
    """Merge one evidence record; True when the store changed.

    Idempotent by ``event_id`` — steady-state re-reads of an unchanged gate
    do not rewrite the file. Rejects already-expired and implausibly long
    blocks rather than clamping them.
    """
    moment = now or datetime.now(timezone.utc)
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    if resets_at + timedelta(seconds=GATE_GRACE_SECONDS) <= moment:
        return False
    if resets_at - observed_at > _MAX_BLOCK_SPAN:
        return False
    if observed_at - moment > timedelta(minutes=5):
        return False
    store = load_store(path)
    gate = store["gates"].setdefault(provider, {"cleared_through": None, "evidence": []})
    evidence = gate.setdefault("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
        gate["evidence"] = evidence
    if any(str(e.get("event_id") or "") == event_id for e in evidence if isinstance(e, dict)):
        return False
    evidence.append(
        {
            "event_id": event_id,
            "source": source,
            "observed_at": observed_at.isoformat(),
            "resets_at": resets_at.isoformat(),
            "note": note,
        }
    )
    keep_after = moment - _PRUNE_AFTER
    kept = [e for e in evidence if isinstance(e, dict) and (_parse(e.get("resets_at")) or moment) >= keep_after]
    kept.sort(key=lambda e: str(e.get("observed_at") or ""))
    gate["evidence"] = kept[-_MAX_EVIDENCE_RECORDS:]
    return _write_store(store, path)


def active_gate(
    provider: str,
    *,
    now: datetime | None = None,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """The strongest unexpired evidence record for ``provider``, or None.

    A tombstoned clear (``cleared_through``) suppresses evidence observed at
    or before it, so replaying an old log after ``gate clear`` stays cleared
    while a genuinely new refusal re-gates.
    """
    moment = now or datetime.now(timezone.utc)
    gate = load_store(path)["gates"].get(provider)
    if not isinstance(gate, dict):
        return None
    cleared_through = _parse(gate.get("cleared_through"))
    best: dict[str, Any] | None = None
    best_reset: datetime | None = None
    for entry in gate.get("evidence") or []:
        if not isinstance(entry, dict):
            continue
        resets_at = _parse(entry.get("resets_at"))
        observed_at = _parse(entry.get("observed_at"))
        if resets_at is None or observed_at is None:
            continue
        if resets_at + timedelta(seconds=GATE_GRACE_SECONDS) <= moment:
            continue
        if cleared_through is not None and observed_at <= cleared_through:
            continue
        if best_reset is None or resets_at > best_reset:
            best = dict(entry)
            best_reset = resets_at
    if best is not None:
        best["grace_seconds"] = GATE_GRACE_SECONDS
    return best


def clear_gate(
    provider: str,
    *,
    now: datetime | None = None,
    path: str | Path | None = None,
) -> bool:
    """Tombstone the gate: evidence observed so far stays suppressed."""
    moment = now or datetime.now(timezone.utc)
    store = load_store(path)
    gate = store["gates"].get(provider)
    if not isinstance(gate, dict):
        return False
    cleared_through = _parse(gate.get("cleared_through"))
    if cleared_through is not None and cleared_through >= moment:
        return False
    gate["cleared_through"] = moment.isoformat()
    return _write_store(store, path)


def ingest_omp_log_evidence(
    *,
    log_dir: str | Path | None = None,
    now: datetime | None = None,
    path: str | Path | None = None,
) -> int:
    """Record every omp-log Anthropic 429 as gate evidence; count new records."""
    from aiuse.omp_rate_events import scan_omp_rate_limit_events

    recorded = 0
    for event in scan_omp_rate_limit_events(log_dir=log_dir, now=now):
        changed = record_evidence(
            "omp",
            resets_at=event.resets_at,
            observed_at=event.observed_at,
            source="omp-429-anthropic",
            event_id=event.event_id,
            note=f"Anthropic 429 retry-after {event.retry_after_seconds:g}s (omp log {Path(event.log_path).name})",
            now=now,
            path=path,
        )
        recorded += 1 if changed else 0
    return recorded


def gate_status(
    *,
    now: datetime | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Machine-readable latch state for every provider with a store entry."""
    moment = now or datetime.now(timezone.utc)
    store = load_store(path)
    providers: dict[str, Any] = {}
    for provider in sorted(store["gates"]):
        active = active_gate(provider, now=moment, path=path)
        entry: dict[str, Any] = {
            "provider": provider,
            "gated": active is not None,
            "resets_at": (active or {}).get("resets_at"),
        }
        if active is not None:
            entry["source"] = active.get("source")
            entry["observed_at"] = active.get("observed_at")
            entry["event_id"] = active.get("event_id")
            entry["note"] = active.get("note")
            entry["grace_seconds"] = active.get("grace_seconds")
        providers[provider] = entry
    return {
        "schema_version": 1,
        "evaluated_at": moment.isoformat(),
        "store_path": str(Path(path) if path is not None else store_path()),
        "gates": providers,
    }


def run_gate_command(argv: list[str] | None = None) -> int:
    """``aiuse gate status|clear`` — the machine surface for spend-path tools.

    Exit codes (``status``): 0 nothing gated under current evidence, 1 at
    least one active gate, 2 usage/error. ``0`` is "no known block", not a
    promise that a request will succeed.
    """
    parser = argparse.ArgumentParser(
        prog="aiuse gate",
        description="Inspect or clear the durable omp rate-gate latch.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="show active rate gates (exit 1 while gated)")
    status.add_argument("--json", action="store_true", help="machine-readable payload")
    status.add_argument("--provider", choices=GATED_PROVIDERS, help="restrict to one provider")
    clear = sub.add_parser("clear", help="tombstone a gate so old evidence stays suppressed")
    clear.add_argument("provider", choices=GATED_PROVIDERS)
    args = parser.parse_args(argv)

    if args.cmd == "clear":
        changed = clear_gate(args.provider)
        verb = "cleared (evidence tombstoned)" if changed else "already clear"
        print(f"aiuse gate: {args.provider} {verb}; a new refusal or exhausted window re-gates")
        return 0

    ingest_omp_log_evidence()
    payload = gate_status()
    entries = list(payload["gates"].values())
    if args.provider:
        entries = [e for e in entries if e["provider"] == args.provider]
    if args.json:
        print(json.dumps(payload, indent=1, sort_keys=True))
    elif not entries:
        print("aiuse gate: no recorded rate gates (no evidence observed yet)")
    for entry in entries:
        if not args.json:
            if entry["gated"]:
                print(
                    f"aiuse gate: {entry['provider']} GATED until {entry['resets_at']} "
                    f"({entry['source']}, observed {entry['observed_at']})"
                )
            else:
                print(f"aiuse gate: {entry['provider']} not gated (evidence expired or cleared)")
    return 1 if any(e["gated"] for e in entries) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_gate_command())
