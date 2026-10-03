"""Agent-reported exhaustion overrides (`aiuse note-exhausted`).

A 429 seen by one agent should be visible to the next agent without waiting
for the next collector pass. `aiuse note-exhausted <provider> --resets-in 4h53m`
drops a small JSON note under the aiuse state dir; snapshot enrichment honours
every unexpired note and labels it ``source: agent-reported``.

Advisory by design: each note dies at its own reset time, and a collector
reading ``ok`` after that expiry wins. Notes never edit numbers — they only
flip ``state`` and the derived account fields, visibly tagged.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiuse.analysis.history import snapshot_dir
from aiuse.models import parse_dt, utcnow

_UNIT_SECONDS = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}
_RESPEC = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])", re.IGNORECASE)


def notes_dir() -> Path:
    return snapshot_dir().parent / "agent-notes"


def parse_resets_in(text: str) -> timedelta:
    """`4h53m` / `90m` / `2h` / `1d2h` -> timedelta. Raises ValueError otherwise."""
    stripped = text.strip()
    total = 0.0
    pos = 0
    for match in _RESPEC.finditer(stripped):
        if match.start() != pos:
            raise ValueError(f"cannot parse duration {text!r} (use e.g. 4h53m, 90m, 2h)")
        pos = match.end()
        total += float(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
    if pos != len(stripped):
        raise ValueError(f"cannot parse duration {text!r} (use e.g. 4h53m, 90m, 2h)")
    return timedelta(seconds=total)


def write_note(
    *,
    provider: str,
    pool_family: str | None,
    resets_at: datetime,
    reason: str | None,
) -> Path:
    directory = notes_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    slug = provider if not pool_family else f"{provider}-{pool_family}"
    payload = {
        "schema_version": "1.0",
        "provider": provider,
        "pool_family": pool_family,
        "resets_at": resets_at.astimezone().isoformat(),
        "reason": reason,
        "created_at": utcnow().isoformat(),
        "source": "agent-reported",
    }
    path = directory / f"{slug}.json"
    tmp = directory / f".{slug}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.write("\n")
    os.rename(tmp, path)
    return path


def load_active_notes(*, now: datetime | None = None, prune: bool = True) -> list[dict[str, Any]]:
    """All unexpired notes; expired note files are removed (advisory expiry)."""
    now = now or utcnow()
    directory = notes_dir()
    if not directory.is_dir():
        return []
    notes: list[dict[str, Any]] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix.lower() != ".json":
            continue
        try:
            note = json.loads(entry.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        resets = parse_dt(note.get("resets_at"))
        if resets is None:
            continue
        if resets <= now:
            if prune:
                try:
                    entry.unlink()
                except OSError:
                    pass
            continue
        notes.append(note)
    return notes


def run_note_exhausted(argv: list[str]) -> int:
    """CLI entry: `aiuse note-exhausted <provider> [--family F] (--resets-in D | --resets-at ISO) [--reason T]`."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="aiuse note-exhausted",
        description="Record an agent-reported exhaustion override (advisory; expires at its reset time).",
    )
    parser.add_argument("provider", help="provider id as it appears in aiuse --json (e.g. codex, antigravity)")
    parser.add_argument("--family", default=None, help="pool family for split vendors (e.g. claude_gpt, gemini, fable)")
    when = parser.add_mutually_exclusive_group(required=True)
    when.add_argument("--resets-in", metavar="DURATION", help="e.g. 4h53m, 90m, 2h")
    when.add_argument("--resets-at", metavar="ISO", help="ISO timestamp of the reset")
    parser.add_argument("--reason", default=None, help="free text, e.g. the 429 message you saw")
    args = parser.parse_args(argv)

    if args.resets_in:
        try:
            delta = parse_resets_in(args.resets_in)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        resets_at = utcnow() + delta
    else:
        resets_at = parse_dt(args.resets_at)
        if resets_at is None:
            print(f"error: cannot parse --resets-at {args.resets_at!r} as ISO datetime", file=sys.stderr)
            return 1

    path = write_note(
        provider=args.provider,
        pool_family=args.family,
        resets_at=resets_at,
        reason=args.reason,
    )
    print(
        f"noted: {args.provider}{'/' + args.family if args.family else ''} exhausted until "
        f"{resets_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')} ({path})",
    )
    return 0
