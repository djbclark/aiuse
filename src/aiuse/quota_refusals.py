"""Passive ACP-log scan that turns a fresh vendor 429 into an agent note.

Google's Antigravity quota endpoint can serve a frozen used% for an hour or
more while real spend continues (2026-10-18: Claude/GPT weekly stuck at
~59% until a 429, then CodexBar flipped to 100%). When an agent hits
``Individual quota reached`` / ``QUOTA_EXHAUSTED`` with a multi-day reset,
that refusal is stronger evidence than the cached percent — but only if
something records it. ``acp-dispatch`` calls ``aiuse note-exhausted`` on a
classified quota failure; bare ``acp-run`` (and some exit-0 end_turn paths)
do not. This module reads recent acp-run JSONL/stderr and persists the same
note shape so ``enrich_snapshot`` fails closed for ``--available``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiuse.agent_notes import load_active_notes
from aiuse.collectors.acp_usage import ACP_AGENT_PROVIDERS, default_acp_log_dir
from aiuse.models import parse_dt, utcnow

DEFAULT_LOOKBACK_HOURS = 6.0
_MAX_FILES = 80

# Google / cloudcode-pa: Individual quota + quotaResetTimeStamp (weekly).
_RESET_STAMP = re.compile(
    r"quotaResetTimeStamp:(?P<reset>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
    re.IGNORECASE,
)
_MODEL = re.compile(
    r"model:(?P<model>claude-[A-Za-z0-9._+-]+|gpt-[A-Za-z0-9._+-]+|gemini-[A-Za-z0-9._+-]+)",
    re.IGNORECASE,
)
_QUOTA_MARKERS = (
    "individual quota reached",
    "reason:quota_exhausted",
    "QUOTA_EXHAUSTED",
)
_LOG_NAME = re.compile(r"^(\d{8})-(\d{6})-(.+)-(\d+)\.(jsonl|stderr)$")
# Multi-day reset: shorter resets are often the 5h / burst window, which the
# existing agy CLI client_limits path already surfaces without flipping the pool.
_MIN_RESET_HORIZON = timedelta(hours=20)


def _agent_of(name: str) -> str | None:
    match = _LOG_NAME.match(name)
    return match.group(3) if match else None


def pool_family_for_acp_model(provider: str, model: str | None) -> str | None:
    """Map an ACP agent model id to an aiuse pool_family for split vendors.

    Antigravity: ``claude-*`` / ``gpt-*`` → ``claude_gpt``; ``gemini-*`` →
    ``gemini``. Other providers return ``None`` (one residual pool).
    """
    if provider != "antigravity" or not model:
        return None
    low = model.casefold()
    if low.startswith(("claude-", "gpt-")):
        return "claude_gpt"
    if low.startswith("gemini-"):
        return "gemini"
    return None


def _family_for(provider: str, model: str | None) -> str | None:
    return pool_family_for_acp_model(provider, model)


def _extract_refusal(text: str, *, now: datetime) -> dict[str, Any] | None:
    low = text.casefold()
    if not any(marker.casefold() in low for marker in _QUOTA_MARKERS):
        return None
    reset_match = _RESET_STAMP.search(text)
    if reset_match is None:
        return None
    resets_at = parse_dt(reset_match.group("reset"))
    if resets_at is None or resets_at <= now:
        return None
    if resets_at - now < _MIN_RESET_HORIZON:
        return None
    model_match = _MODEL.search(text)
    model = model_match.group("model") if model_match else None
    return {"resets_at": resets_at, "model": model, "snippet": text[:240]}


def scan_acp_quota_refusals(
    *,
    log_dir: str | Path | None = None,
    lookback: timedelta = timedelta(hours=DEFAULT_LOOKBACK_HOURS),
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Recent multi-day quota refusals from acp-run logs, newest first per pool."""
    now = now or utcnow()
    root = Path(log_dir).expanduser() if log_dir else default_acp_log_dir()
    if not root.is_dir() or lookback.total_seconds() <= 0:
        return []
    since = now - lookback
    try:
        entries = [entry for entry in os.scandir(root) if entry.is_file()]
    except OSError:
        return []
    recent: list[tuple[float, Path]] = []
    for entry in entries:
        if not entry.name.endswith((".jsonl", ".stderr")):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime >= since.timestamp():
            recent.append((mtime, Path(entry.path)))
    recent.sort(reverse=True)

    found: dict[tuple[str, str | None], dict[str, Any]] = {}
    for _mtime, path in recent[:_MAX_FILES]:
        agent = _agent_of(path.name)
        if agent is None:
            continue
        provider = ACP_AGENT_PROVIDERS.get(agent)
        if provider is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # JSONL can be large; only scan when a marker appears.
        if not any(marker in text for marker in _QUOTA_MARKERS if marker.isupper()) and not any(
            marker.casefold() in text.casefold() for marker in _QUOTA_MARKERS
        ):
            continue
        refusal = _extract_refusal(text, now=now)
        if refusal is None:
            continue
        family = _family_for(provider, refusal.get("model"))
        key = (provider, family)
        if key in found:
            continue
        found[key] = {
            "schema_version": "1.0",
            "provider": provider,
            "pool_family": family,
            "resets_at": refusal["resets_at"].astimezone(timezone.utc).isoformat(),
            "reason": (f"acp-run log {path.name}: Individual/QUOTA_EXHAUSTED (model={refusal.get('model') or '?'})"),
            "created_at": now.isoformat(),
            "source": "agent-reported",
            "evidence": str(path),
        }
    return list(found.values())


def persist_acp_quota_refusals(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Discover ACP refusals into agent notes; return the active note list."""
    return load_active_notes(now=now, prune=True, discover_acp=True)
