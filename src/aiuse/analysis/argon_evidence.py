"""Durable evidence that an Antigravity account has no Argon-own quota window.

Whether ``gemini-4-argon`` draws its own pool is a fact about the account's
plan (Google AI Pro reported "Gemini 4 Argon weekly"; Google AI Ultra shares
one Gemini pool across Carbon, Neon, Hydrogen and Argon), and CodexBar gives no
plan field. Folding the ``gemini_argon`` family into the shared ``gemini``
pool is therefore only safe on POSITIVE evidence that the plan has no Argon
window. One reading without the window proves nothing: the window can be hidden
(``[hidden_pools]``), unreadable, or missing from a partial reading, and folding
then turns an Argon refusal into a false exhaustion of the whole Flash pool
(schema 1.8 adversary review, finding 3).

This module keeps a small per-account record of consecutive readings that show
the shared Gemini (Flash) window and no Argon window. The fold is proven only after
``MIN_ABSENT_SECONDS`` and ``MIN_ABSENT_READINGS`` of such readings; one reading
that shows an Argon window resets the record. Everything else, including a
hidden Argon family and a missing or corrupt store, reads as "Argon may exist":
the family is treated as reported and nothing folds. The record is keyed by a
hash of the account name so the file holds no email address.

Only the COLLECTION path writes the store (``record_readings(record=True)``, reached
from ``history.save_snapshot``). Every read path (``--available``, cache ``--json``,
serve, watch) uses the read-only ``proven_absent``, which neither writes nor
creates the directory, so re-reading one cached snapshot cannot advance the
evidence. A reading stamped in the future (clock skew) is skipped, never
clamped. A corrupt or foreign-typed record resets to "no evidence"; nothing in
this module raises on a damaged store.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from aiuse.models import parse_dt

ARGON_FAMILY = "gemini_argon"
GEMINI_FAMILY = "gemini"
# Consecutive no-Argon readings must span this long, and number at least this many.
MIN_ABSENT_SECONDS = 6 * 3600.0
MIN_ABSENT_READINGS = 3
_PRUNE_AFTER = timedelta(days=30)
_MAX_RECORDS = 50
_DEFAULT_STORE = "~/.local/state/aiuse/argon-evidence.json"


def store_path() -> Path:
    override = os.environ.get("AIUSE_ARGON_EVIDENCE_STORE")
    return Path(override) if override else Path(os.path.expanduser(_DEFAULT_STORE))


def account_key(provider: str, account: object) -> str:
    """Stable, email-free key for one account of a provider."""
    raw = f"{provider.strip().lower()}|{str(account or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _aware(value: object) -> datetime | None:
    """A timezone-aware UTC datetime from an ISO string; anything else (or an unrepresentable date) is None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_dt(value)
        return parsed.astimezone(timezone.utc) if parsed is not None else None
    except (ValueError, OverflowError, TypeError):
        return None


_MAX_READINGS = 1_000_000


def _clean_record(record: object, now: datetime) -> dict[str, Any] | None:
    """A validated copy of one stored record, or None to reset it to "no evidence".

    The store is a plain JSON file a person, an older build or a newer one may
    have written: any field of the wrong type, a non-finite number, an
    unparsable or unrepresentable date, or a timestamp in the future (clock
    skew) throws the whole record away, so the account simply starts again.
    """
    if not isinstance(record, Mapping):
        return None
    last = _aware(record.get("last_reading"))
    if last is None or last > now:
        return None
    stamps: dict[str, str | None] = {"last_reading": last.isoformat()}
    for name in ("absent_since", "argon_last_seen"):
        raw = record.get(name)
        if raw is None:
            stamps[name] = None
            continue
        parsed = _aware(raw)
        if parsed is None or parsed > now:
            return None
        stamps[name] = parsed.isoformat()
    since = _aware(stamps["absent_since"])
    if since is not None and since > last:
        return None
    readings = record.get("readings_absent", 0)
    if readings is None:
        readings = 0
    if isinstance(readings, bool) or not isinstance(readings, int) or not 0 <= readings <= _MAX_READINGS:
        return None
    return {**stamps, "readings_absent": readings}


def _load(path: Path, now: datetime) -> dict[str, dict[str, Any]]:
    """The valid records of the store; a missing, unreadable or foreign-shaped file reads as empty."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    records = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(records, dict):
        return {}
    cleaned = {str(k): _clean_record(v, now) for k, v in records.items()}
    return {k: v for k, v in cleaned.items() if v is not None}


def _write(path: Path, records: Mapping[str, Any]) -> bool:
    """Replace the store atomically; the temp file is created 0600 and removed on any failure."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"schema_version": 1, "accounts": records}, indent=1, sort_keys=True) + "\n")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
    except (OSError, ValueError):
        tmp.unlink(missing_ok=True)
        return False  # durability only: the in-process decision still applies
    return True


def argon_hidden(hidden_pools: object) -> bool:
    """True when ``[hidden_pools]`` hides the antigravity Argon family (``snap["hidden_pools"]`` keys)."""
    if not isinstance(hidden_pools, Mapping):
        return False
    return any(
        str(key).strip().lower() in ("antigravity/gemini_argon", "antigravity", "antigravity/*") for key in hidden_pools
    )


def _has_family_window(windows: Iterable[Any], family: str, provider: str) -> bool:
    from aiuse.analysis.pace import independent_pool_key

    for window in windows:
        if not isinstance(window, Mapping):
            continue
        if window.get("pool_family") == family:
            return True
        wid = str(window.get("window_id") or "").strip().casefold() or None
        if independent_pool_key(str(window.get("label") or ""), window_id=wid, provider=provider) == family:
            return True
    return False


def has_argon_window(windows: Iterable[Any], provider: str = "antigravity") -> bool:
    """True when any window is (or names itself) an Argon-own window, whatever its reading."""
    return _has_family_window(windows, ARGON_FAMILY, provider)


def has_gemini_window(windows: Iterable[Any], provider: str = "antigravity") -> bool:
    """True when any window is (or names itself) the shared Gemini (Flash) window, whatever its reading."""
    return _has_family_window(windows, GEMINI_FAMILY, provider)


def record_readings(
    accounts: Iterable[Mapping[str, Any]],
    *,
    collected_at: object = None,
    hidden_pools: object = None,
    now: datetime,
    path: str | Path | None = None,
    record: bool = True,
) -> dict[str, bool]:
    """Judge this snapshot's antigravity readings against the store; return ``account key -> proven``.

    Evidence of absence is a reading that shows the shared Gemini (Flash) window
    and no Argon window. A reading without the Gemini window (a partial one) is
    skipped: nothing recorded, nothing proven.

    With ``record=True`` (the collection path only: ``history.save_snapshot``, where a
    reading is first collected) each new reading is also folded into the store,
    which is rewritten when it changed. With ``record=False`` (every read path:
    ``--available``, cache ``--json``, serve, watch) the store is only read: nothing is
    written and no directory is created, so re-reading one cached snapshot can
    never advance the evidence. See :func:`proven_absent`.

    A reading counts only when the account has antigravity windows. A hidden
    Argon family is never evidence of absence (the window was dropped before
    this reading), so nothing is recorded and nothing is proven. A reading
    older than the stored one is ignored. A reading stamped in the future (clock
    skew) is skipped, never clamped to ``now``: clamping let one future-stamped
    snapshot count as a fresh reading on every re-read.
    """
    target = Path(path) if path is not None else store_path()
    hidden = argon_hidden(hidden_pools)
    records = _load(target, now)
    before = json.dumps(records, sort_keys=True)
    proven: dict[str, bool] = {}
    for account in accounts:
        if str(account.get("provider") or "") != "antigravity":
            continue
        windows = [w for w in (account.get("windows") or []) if isinstance(w, Mapping)]
        key = account_key("antigravity", account.get("account"))
        read_at = _aware(account.get("collected_at")) or _aware(collected_at)
        if not windows or read_at is None or read_at > now or hidden:
            proven[key] = False
            continue
        argon = has_argon_window(windows)
        if not argon and not has_gemini_window(windows):
            # A partial reading (say, only the Claude/GPT window) says nothing about
            # Gemini: it is neither evidence of absence nor a reason to reset.
            proven[key] = False
            continue
        if not record:
            proven[key] = not argon and _span_proves(records.get(key, {}))
            continue
        entry = records.setdefault(key, {})
        last = _aware(entry.get("last_reading"))
        if last is None or read_at > last:
            if argon:
                entry.update(
                    argon_last_seen=read_at.isoformat(),
                    absent_since=None,
                    readings_absent=0,
                    last_reading=read_at.isoformat(),
                )
            else:
                since = _aware(entry.get("absent_since"))
                entry.update(
                    absent_since=(since or read_at).isoformat(),
                    readings_absent=min(int(entry.get("readings_absent") or 0) + 1, _MAX_READINGS),
                    last_reading=read_at.isoformat(),
                )
        proven[key] = not argon and _span_proves(entry)
    if not record:
        return proven
    cutoff = now - _PRUNE_AFTER
    for key in [k for k, r in records.items() if (_aware(r.get("last_reading")) or cutoff) < cutoff]:
        records.pop(key)
    if len(records) > _MAX_RECORDS:
        for key in sorted(records, key=lambda k: str(records[k].get("last_reading") or ""))[
            : len(records) - _MAX_RECORDS
        ]:
            records.pop(key)
    if json.dumps(records, sort_keys=True) != before:
        _write(target, records)
    return proven


def proven_absent(
    accounts: Iterable[Mapping[str, Any]],
    *,
    collected_at: object = None,
    hidden_pools: object = None,
    now: datetime,
    path: str | Path | None = None,
) -> dict[str, bool]:
    """Read-only :func:`record_readings`: what the stored evidence proves, with no write of any kind."""
    return record_readings(
        accounts, collected_at=collected_at, hidden_pools=hidden_pools, now=now, path=path, record=False
    )


def _span_proves(record: Mapping[str, Any]) -> bool:
    since = _aware(record.get("absent_since"))
    last = _aware(record.get("last_reading"))
    if since is None or last is None:
        return False
    readings = record.get("readings_absent")
    if isinstance(readings, bool) or not isinstance(readings, int):
        return False
    return readings >= MIN_ABSENT_READINGS and (last - since).total_seconds() >= MIN_ABSENT_SECONDS
