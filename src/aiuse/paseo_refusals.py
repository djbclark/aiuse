"""Passive Paseo-agent-error scan: a provider refusal marks its pool exhausted.

Paseo agents are often the FIRST thing to hit a drained pool: their vendor
stream refuses while aiuse still reports the cached percentages (2026-11-12:
agents saw ``Individual quota reached. ... Resets in 141h24m34s.`` for the
antigravity pools while the quota endpoint still served yesterday's numbers).
The daemon keeps one state file per agent under
``~/.paseo/agents/<cwd-slug>/<uuid>.json`` carrying ``lastStatus``,
``lastError``, ``provider`` and ``runtimeInfo.model``; this module reads those
files only (no paseo call, no probe) and turns multi-day quota refusals into
the same exhaustion notes the acp-log scan (:mod:`aiuse.quota_refusals`)
produces, so ``--available`` fails closed after the first refusal. The reset
is parsed from the refusal text (``Resets in XhYmZs``) and anchored to the
file's ``updatedAt`` — the moment the daemon recorded the error — never to the
scan time.

A short vendor rate limit is NOT an exhaustion: the Pi z.ai 429
``{"code":"1302","message":"Rate limit reached for requests"}`` clears in
minutes. It is surfaced as a ``client_limits`` entry with a short cooldown
(state ``limited``; quota numbers and ``usable_now`` stay untouched — the
pi/z.ai route may already work again) and is never turned into an exhausted
pool note.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiuse.models import parse_dt, utcnow
from aiuse.quota_refusals import pool_family_for_acp_model

# How far back an error still counts as evidence for its pool. The note itself
# expires at its own reset time, so the lookback only bounds scan cost and
# staleness of the *evidence*, not the flip.
DEFAULT_LOOKBACK_HOURS = 24.0
_MAX_FILES = 200
# Multi-day reset threshold, same rationale as quota_refusals._MIN_RESET_HORIZON:
# shorter resets are burst/5h windows that client_limits surfaces without
# flipping the pool. The observed refusals are 141h (Gemini Argon weekly) and
# 42h (Claude/GPT weekly), both far above it.
_MIN_RESET_HORIZON = timedelta(hours=20)
# A vendor 429 without a multi-day reset is a short rate limit: bounded
# cooldown, never an exhausted pool.
THROTTLE_COOLDOWN_MINUTES = 10.0
_THROTTLE_COOLDOWN = timedelta(minutes=THROTTLE_COOLDOWN_MINUTES)
# How long an unknown-reset exhaustion (the OpenAI enforced spend limit) keeps
# the pool flipped: notes are advisory and expire, so the block re-opens after
# a day even if the operator never touches the limit — a fresh refusal (or the
# operator's own note-exhausted) re-closes it.
_UNKNOWN_RESET_FALLBACK = timedelta(hours=24)

_EXHAUSTED_MARKERS = (
    "individual quota reached",
    "quota_exhausted",
    "resource_exhausted",
)
# Pi on the metered OpenAI API platform: "[System Error] Your organization has
# reached its configured enforced spend limit. Update your limit at
# https://platform.openai.com/settings/organization/limits." — an exhaustion
# with NO reset time: the block stands until the operator raises the limit, so
# the note carries a bounded fallback reset (see _UNKNOWN_RESET_FALLBACK).
_SPEND_LIMIT_MARKER = "enforced spend limit"
# Pi z.ai: `429: {"code":"1302","message":"Rate limit reached for requests"}`.
_THROTTLE_MARKERS = (
    "rate limit reached for requests",
    "1302",
)
_RESETS_IN = re.compile(r"resets in\s+((?:\d+\s*[dhms])+)", re.IGNORECASE)
_UNIT = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}


def pool_family_for_paseo_model(provider: str, model: str | None) -> str | None:
    """Map a Paseo agent's provider+model to an aiuse pool_family.

    Pi models named ``openai/*`` are the metered OpenAI API platform, not the
    Codex subscription and not pi's other routes: they get the same
    ``openai_api`` family the omp OpenAI-key route uses. Everything else goes
    through the antigravity split (claude-* / gpt-* / gemini-*); providers
    without a split return None (one residual pool).
    """
    if provider == "pi" and model and model.casefold().startswith("openai/"):
        return "openai_api"
    return pool_family_for_acp_model(provider, model)


def paseo_agents_dir() -> Path:
    """The Paseo daemon's per-agent state files (read-only for this module)."""
    return Path.home() / ".paseo" / "agents"


def parse_resets_in_horizon(text: str) -> timedelta | None:
    """``Resets in 141h25m15s.`` -> the duration; None when the text has none."""
    match = _RESETS_IN.search(text)
    if match is None:
        return None
    total = 0.0
    for unit in _UNIT.finditer(match.group(1)):
        total += float(unit.group(1)) * _UNIT_SECONDS[unit.group(2).lower()]
    return timedelta(seconds=total)


def _recent_files(root: Path, since: datetime, limit: int) -> list[Path]:
    # The daemon nests state files one level deep: agents/<cwd-slug>/<uuid>.json.
    try:
        paths = [path for path in root.rglob("*.json") if path.is_file()]
    except OSError:
        return []
    recent: list[tuple[float, Path]] = []
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= since.timestamp():
            recent.append((mtime, path))
    recent.sort(reverse=True)
    return [path for _mtime, path in recent[:limit]]


def _iter_error_docs(
    agents_dir: Path, *, lookback: timedelta, now: datetime, limit: int = _MAX_FILES
) -> list[tuple[Path, dict[str, Any]]]:
    """``(path, doc)`` per recent Paseo agent state file whose last run errored."""
    docs: list[tuple[Path, dict[str, Any]]] = []
    for path in _recent_files(agents_dir, now - lookback, limit):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        if str(doc.get("lastStatus") or "") != "error":
            continue
        error = doc.get("lastError")
        if not isinstance(error, str) or not error.strip():
            continue
        docs.append((path, doc))
    return docs


def _provider_of(doc: dict[str, Any]) -> str:
    raw_runtime = doc.get("runtimeInfo")
    runtime = raw_runtime if isinstance(raw_runtime, dict) else {}
    return str(doc.get("provider") or runtime.get("provider") or "").strip().lower()


def _model_of(doc: dict[str, Any]) -> str | None:
    raw_runtime = doc.get("runtimeInfo")
    runtime = raw_runtime if isinstance(raw_runtime, dict) else {}
    raw_config = doc.get("config")
    config = raw_config if isinstance(raw_config, dict) else {}
    model = runtime.get("model") or config.get("model")
    return str(model).strip() if isinstance(model, str) and model.strip() else None


def _event_time(doc: dict[str, Any]) -> datetime | None:
    return parse_dt(doc.get("updatedAt") or doc.get("lastActivityAt"))


def scan_paseo_quota_refusals(
    *,
    agents_dir: str | Path | None = None,
    lookback: timedelta = timedelta(hours=DEFAULT_LOOKBACK_HOURS),
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Multi-day quota refusals seen by Paseo agents, newest first per pool.

    Shaped like :func:`aiuse.quota_refusals.scan_acp_quota_refusals` so
    ``load_active_notes`` can persist both into the same exhaustion-note store.
    """
    now = now or utcnow()
    root = Path(agents_dir).expanduser() if agents_dir else paseo_agents_dir()
    if not root.is_dir() or lookback.total_seconds() <= 0:
        return []
    found: dict[tuple[str, str | None], tuple[datetime, dict[str, Any]]] = {}
    for path, doc in _iter_error_docs(root, lookback=lookback, now=now):
        text = str(doc.get("lastError") or "")
        low = text.casefold()
        if not any(marker in low for marker in _EXHAUSTED_MARKERS) and _SPEND_LIMIT_MARKER not in low:
            continue
        if any(marker in low for marker in _THROTTLE_MARKERS):
            continue  # a short rate limit is never an exhausted pool
        provider = _provider_of(doc)
        if not provider:
            continue
        family = pool_family_for_paseo_model(provider, _model_of(doc))
        if _SPEND_LIMIT_MARKER in low:
            # OpenAI platform enforced spend limit: no reset in the text — the
            # block stands until the operator raises the limit. Flip the
            # openai_api pool (never codex) with a bounded fallback reset.
            event = _event_time(doc) or now
            resets_at = now + _UNKNOWN_RESET_FALLBACK
            reason = (
                f"paseo agent error ({path.name}): enforced spend limit reached "
                "(metered OpenAI API platform; reset unknown until the operator raises the limit; "
                "note expires after 24h)"
            )
        else:
            delta = parse_resets_in_horizon(text)
            if delta is None or delta < _MIN_RESET_HORIZON:
                continue
            event = _event_time(doc)
            if event is None:
                continue
            resets_at = event + delta
            if resets_at <= now:
                continue  # already reset; a fresh collector reading wins anyway
            reason = f"paseo agent error ({path.name}): {text[:160]}"
        key = (provider, family)
        if key in found and found[key][0] >= event:
            continue
        found[key] = (
            event,
            {
                "schema_version": "1.0",
                "provider": provider,
                "pool_family": family,
                "resets_at": resets_at.astimezone().isoformat(),
                "reason": reason,
                "created_at": now.isoformat(),
                "source": "agent-reported",
                "evidence": str(path),
            },
        )
    ordered = sorted(found.values(), key=lambda pair: pair[0], reverse=True)
    return [refusal for _event, refusal in ordered]


def paseo_note_evidence_gone(evidence: str) -> bool:
    """True when a note's Paseo evidence file no longer shows the refusal.

    The evidence file is the daemon's per-agent state: when the agent's next
    run succeeds (or the daemon clears the error), ``lastError`` is gone and
    the refusal is superseded by a fresh successful probe — the exhaustion
    note must stop holding the pool out before its reset (2026-11-12 goal).
    Non-Paseo evidence paths (acp logs) are never "gone" here: their notes
    expire by reset only.
    """
    try:
        path = Path(evidence).expanduser()
    except (OSError, ValueError):
        return False
    if paseo_agents_dir() not in path.parents and paseo_agents_dir() != path.parent:
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return True  # evidence deleted or unreadable: the refusal no longer stands
    if not isinstance(doc, dict) or str(doc.get("lastStatus") or "") != "error":
        return True
    text = str(doc.get("lastError") or "").casefold()
    if not text:
        return True
    refusal_standing = (
        any(marker in text for marker in _EXHAUSTED_MARKERS) or _SPEND_LIMIT_MARKER in text
    ) and not any(marker in text for marker in _THROTTLE_MARKERS)
    return not refusal_standing


def scan_paseo_throttle(
    *,
    agents_dir: str | Path | None = None,
    cooldown: timedelta = _THROTTLE_COOLDOWN,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """A recent short Paseo-agent rate limit as one ``client_limits`` entry.

    The Pi z.ai 429 (``code 1302``, "Rate limit reached for requests") clears
    in minutes, so the entry ages out of the cooldown window exactly like the
    agy CLI lockout ages out of its lookback — and never flips the pool.
    """
    now = now or utcnow()
    root = Path(agents_dir).expanduser() if agents_dir else paseo_agents_dir()
    if not root.is_dir() or cooldown.total_seconds() <= 0:
        return None
    best: tuple[datetime, Path, dict[str, Any]] | None = None
    for path, doc in _iter_error_docs(root, lookback=cooldown, now=now):
        text = str(doc.get("lastError") or "")
        low = text.casefold()
        if not any(marker in low for marker in _THROTTLE_MARKERS):
            continue
        if any(marker in low for marker in _EXHAUSTED_MARKERS):
            continue  # an exhaustion is the refusal scan's job, not a cooldown
        event = _event_time(doc)
        if event is None or event < now - cooldown:
            continue
        if best is None or event > best[0]:
            best = (event, path, doc)
    if best is None:
        return None
    event, path, doc = best
    provider = _provider_of(doc) or "pi"
    model = _model_of(doc)
    cooldown_until = event + cooldown
    label = f"{provider}" + (f"/{model}" if model else "")
    minutes_ago = max(0, int((now - event).total_seconds() // 60))
    return {
        "client": "paseo-agent",
        "state": "limited",
        "source": "paseo-agent-error",
        "provider_hint": provider,
        "last_limited_at": event.astimezone().isoformat(),
        "cooldown_until": cooldown_until.astimezone().isoformat(),
        "cooldown_minutes": round(cooldown.total_seconds() / 60, 1),
        "error": str(doc.get("lastError"))[:240],
        "evidence": str(path),
        "message": (
            f"a paseo agent on {label} got a 429 rate limit {minutes_ago}m ago "
            f"(limited until {cooldown_until.astimezone().isoformat()}); a short rate limit, "
            "not quota exhaustion — the pool's quota numbers and usable_now are untouched; "
            "retry after fresh quota and client-availability checks, or wait for the cooldown"
        ),
    }
