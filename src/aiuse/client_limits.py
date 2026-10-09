"""Per-client rate limits that quota windows cannot show (issue #33).

agy (Antigravity) has two clients on one account: the ``agy`` CLI and the ACP
server (``acp-run agy``). On 2026-10-03 every CLI generation request answered
``RESOURCE_EXHAUSTED (code 429)`` for hours while both quota windows showed
headroom and the ACP server kept working. aiuse reads quota, so it reported
the pool as fully usable.

This module is a **passive** signal: it reads the agy CLI's own log files and
never sends a request (no probe, no agy launch). The CLI retries a failed
generation in-process and logs each attempt::

    I1005 06:45:54.279416    2773 run.go:395] Run: attempt 1 failed (RESOURCE_EXHAUSTED (code 429): ...), retrying in 4s

A 429 attempt within the lookback window (default 60 minutes) marks the CLI
client ``limited``. The quota numbers and ``usable_now`` are left alone: the
quota is real and the ACP client may still serve it, so hiding the pool would
also hide a working route. The signal rides along as ``client_limits`` on the
antigravity account and its routing entries, and in their summary lines.

The logs carry no positive "generation succeeded" marker, so a lockout clears
only by ageing out of the lookback window.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiuse.models import utcnow

DEFAULT_LOOKBACK_MINUTES = 60.0
# Never read more than this many recent log files per scan (each is ~200 lines).
_MAX_FILES = 50

_LINE = re.compile(
    r"^[IWEF](?P<month>\d\d)(?P<day>\d\d) (?P<clock>\d\d:\d\d:\d\d(?:\.\d+)?)\s+\d+\s+\S+\]\s+"
    r"Run: attempt (?P<attempt>\d+) failed \((?P<error>RESOURCE_EXHAUSTED[^)]*\))"
)
_FILE_DATE = re.compile(r"cli-(?P<year>\d{4})(?P<month>\d\d)(?P<day>\d\d)_")


def agy_cli_log_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "log"


def lookback_minutes(config: dict[str, Any] | None) -> float:
    """``analysis.agy_cli_lockout_minutes`` (0 disables the scan)."""
    analysis = (config or {}).get("analysis")
    value = analysis.get("agy_cli_lockout_minutes") if isinstance(analysis, dict) else None
    if value is None:
        return DEFAULT_LOOKBACK_MINUTES
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_MINUTES


def _line_time(match: re.Match[str], file_year: int, file_month: int) -> datetime | None:
    """glog stamps are local time with no year; the file name supplies the year."""
    month, day = int(match["month"]), int(match["day"])
    year = file_year + 1 if month < file_month else file_year  # Dec -> Jan rollover
    clock = match["clock"]
    try:
        naive = datetime.strptime(f"{year}-{month:02d}-{day:02d} {clock}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        try:
            naive = datetime.strptime(f"{year}-{month:02d}-{day:02d} {clock}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return naive.astimezone().astimezone(timezone.utc)


def scan_agy_cli_lockout(
    *,
    lookback: timedelta = timedelta(minutes=DEFAULT_LOOKBACK_MINUTES),
    now: datetime | None = None,
    log_dir: Path | None = None,
) -> dict[str, Any] | None:
    """The agy CLI's recent 429 evidence as one ``client_limits`` entry, or None."""
    if lookback.total_seconds() <= 0:
        return None
    now = now or utcnow()
    directory = log_dir if log_dir is not None else agy_cli_log_dir()
    since = now - lookback
    try:
        entries = [entry for entry in os.scandir(directory) if entry.name.startswith("cli-") and entry.is_file()]
    except OSError:
        return None
    recent: list[tuple[float, Path]] = []
    for entry in entries:
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime >= since.timestamp():
            recent.append((mtime, Path(entry.path)))
    recent.sort(reverse=True)

    failed_attempts = 0
    runs: set[str] = set()
    last_at: datetime | None = None
    last_error = ""
    evidence: Path | None = None
    for _mtime, path in recent[:_MAX_FILES]:
        named = _FILE_DATE.search(path.name)
        file_year = int(named["year"]) if named else now.year
        file_month = int(named["month"]) if named else now.month
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "RESOURCE_EXHAUSTED" not in line:
                continue
            match = _LINE.match(line)
            if match is None:
                continue
            at = _line_time(match, file_year, file_month)
            if at is None or at < since or at > now + timedelta(minutes=5):
                continue
            failed_attempts += 1
            runs.add(path.name)
            if last_at is None or at > last_at:
                last_at, last_error, evidence = at, match["error"], path
    if last_at is None:
        return None
    minutes_ago = max(0, int((now - last_at).total_seconds() // 60))
    run_word = "run" if len(runs) == 1 else "runs"
    return {
        "client": "cli",
        "cli_binary": "agy",
        "state": "limited",
        "source": "agy-cli-log",
        "last_limited_at": last_at.isoformat(),
        "failed_attempts": failed_attempts,
        "runs": len(runs),
        "lookback_minutes": round(lookback.total_seconds() / 60.0, 1),
        "error": last_error,
        "evidence": str(evidence) if evidence else None,
        "message": (
            f"agy CLI got RESOURCE_EXHAUSTED (429) on {failed_attempts} attempts in {len(runs)} {run_word}, "
            f"last {minutes_ago}m ago, although the quota windows may show headroom; "
            "the ACP route (acp-run agy) is a separate client and may still work"
        ),
    }


def load_client_limits(config: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Provider -> active per-client limits, read passively at report time."""
    minutes = lookback_minutes(config)
    limits: dict[str, list[dict[str, Any]]] = {}
    agy = scan_agy_cli_lockout(lookback=timedelta(minutes=minutes)) if minutes > 0 else None
    if agy is not None:
        limits["antigravity"] = [agy]
    return limits
