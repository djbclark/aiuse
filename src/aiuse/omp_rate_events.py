"""Passive scan of omp (oh-my-pi) logs for Anthropic rate-limit refusals.

omp is the CLI that actually spends the Claude cloud-session credits, and it
logs every HTTP refusal from ``api.anthropic.com`` as a JSON line with the
response's ``retry-after`` header. That header is Anthropic's own statement of
when the shared account window can spend again — it landed exactly on the
Claude weekly reset on every refusal observed 2026-10-21..22 — which makes it
the strongest available evidence for the omp rate gate. This module only
parses; ``aiuse.rate_gates`` records what it returns.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A refusal older than this cannot still block (longest supported gate is a
# monthly window, ~31d). Bound the bootstrap scan, not the evidence lifetime —
# rate_gates validates spans at record time.
_MAX_LOG_AGE_DAYS = 35
_MAX_FILES = 40
_DEFAULT_LOG_DIR = "~/.omp/logs"
_REJECTED_MESSAGE = "cowork transport rejected"
_ANTHROPIC_HOST = "api.anthropic.com"


@dataclass(frozen=True)
class OmpRateEvent:
    """One observed Anthropic 429 against the account omp spends through."""

    event_id: str
    observed_at: datetime
    retry_after_seconds: float
    resets_at: datetime
    request_id: str | None
    log_path: str


def resolve_omp_log_dir(log_dir: str | Path | None = None) -> Path:
    if log_dir is not None:
        return Path(log_dir)
    override = os.environ.get("AIUSE_OMP_LOG_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser(_DEFAULT_LOG_DIR))


def _parse_retry_after(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            seconds = float(value.strip())
        except ValueError:
            return None  # an HTTP-date or unrecognized format; not ours to guess
        return seconds
    return None


def _event_id(record: dict, headers: dict, line: str, log_path: Path) -> str:
    for key in ("request-id", "request_id"):
        value = headers.get(key) or record.get("request_id") or record.get("requestId")
        if value:
            return f"omp-log:{value}"
    ray = headers.get("cf-ray")
    if ray:
        return f"omp-log:cf-{ray}"
    digest = hashlib.sha256(f"{log_path.name}:{line}".encode("utf-8")).hexdigest()[:16]
    return f"omp-log:hash-{digest}"


def scan_omp_rate_limit_events(
    *,
    log_dir: str | Path | None = None,
    max_age_days: float = _MAX_LOG_AGE_DAYS,
    max_files: int = _MAX_FILES,
    now: datetime | None = None,
) -> list[OmpRateEvent]:
    """Deduplicated 429 events from recent omp logs, newest first.

    Missing directory, unreadable files, and malformed lines are skipped
    quietly: an absent log can never prove anything, and one truncated final
    line must not discard the complete records around it.
    """
    root = resolve_omp_log_dir(log_dir)
    if not root.is_dir():
        return []
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(days=max_age_days)
    try:
        files = [p for p in root.glob("omp.*.log") if cutoff <= datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    by_id: dict[str, OmpRateEvent] = {}
    for path in files[:max_files]:
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or _ANTHROPIC_HOST not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("message") != _REJECTED_MESSAGE:
                continue
            url = str(record.get("url") or "")
            if _ANTHROPIC_HOST not in url:
                continue
            if record.get("status") != 429:
                continue
            headers = record.get("headers")
            headers = headers if isinstance(headers, dict) else {}
            retry_after = _parse_retry_after(headers.get("retry-after"))
            if retry_after is None or retry_after <= 0:
                continue
            try:
                observed_at = datetime.fromisoformat(str(record.get("timestamp") or ""))
            except ValueError:
                continue
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            event = OmpRateEvent(
                event_id=_event_id(record, headers, line, path),
                observed_at=observed_at,
                retry_after_seconds=retry_after,
                resets_at=observed_at + timedelta(seconds=retry_after),
                request_id=str(headers.get("request-id") or "") or None,
                log_path=str(path),
            )
            existing = by_id.get(event.event_id)
            if existing is None or event.observed_at > existing.observed_at:
                by_id[event.event_id] = event
    return sorted(by_id.values(), key=lambda e: e.observed_at, reverse=True)
