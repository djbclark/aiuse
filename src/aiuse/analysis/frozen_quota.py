"""Detect quota percentages that froze while the pool was actively used.

On 2026-10-18 Google's Antigravity Claude/GPT weekly reading stayed at the
identical CodexBar float ``59.054479002952576`` (OpenUsage ``59.0``) from
12:01Z through 13:45Z while agy Claude/Opus turns kept spending, then a 429
at 13:55Z and CodexBar ``100%`` at 14:00Z. A ``fresh`` cache of that frozen
41% left routed work into an already-draining pool.

aiuse cannot fix Google's endpoint. Fail closed only when **both** hold:

1. The same ``used_percent`` for a provider pool window spans at least
   ``min_frozen_seconds`` of recent snapshot history, and
2. An acp-run log under ``~/.local/state/acp-run/`` shows a turn for that
   provider whose applied model belongs to that pool family **during** the
   flat span (claude-*/gpt-* → ``claude_gpt``, gemini-* → ``gemini``).

Idle flat pools (overnight Gemini, unused grok/copilot reserves, quiet
weekly windows) stay routable — those are exactly what ``--available``
should burn. No usage evidence → no flag, whatever the span.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from aiuse.analysis.pace import independent_pool_key
from aiuse.collectors.acp_usage import ACP_AGENT_PROVIDERS, default_acp_log_dir
from aiuse.models import parse_dt, utcnow
from aiuse.quota_refusals import pool_family_for_acp_model

DEFAULT_MIN_FROZEN_SECONDS = 2700.0  # 45 minutes
DEFAULT_HISTORY_HOURS = 6.0
DEFAULT_HISTORY_MAX = 24
_MAX_ACP_FILES = 120

_LOG_NAME = re.compile(r"^(\d{8})-(\d{6})-(.+)-(\d+)\.(jsonl|stderr)$")
_APPLIED_MODEL = re.compile(r"applied:\s*model=([A-Za-z0-9._+-]+)", re.IGNORECASE)
_SET_MODEL = re.compile(
    r'"id"\s*:\s*"model"[^}]*"value"\s*:\s*"([^"]+)"|'
    r'"value"\s*:\s*"([^"]+)"[^}]*"id"\s*:\s*"model"',
)


def _window_fingerprint(provider: str, window: Mapping[str, Any]) -> tuple[Any, ...] | None:
    used = window.get("used_percent")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    label = str(window.get("label") or "")
    wid = str(window.get("window_id") or "").strip().casefold() or None
    family = independent_pool_key(label, window_id=wid, provider=provider)
    minutes = window.get("window_minutes")
    resets = window.get("resets_at")
    # Whole percents so OpenUsage's 59.0 matches CodexBar's 59.054… float.
    return (provider, family, minutes, round(float(used)), str(resets or ""))


def _iter_account_windows(snap: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for account in snap.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        provider = str(account.get("provider") or "")
        if not provider:
            continue
        for window in account.get("windows") or []:
            if isinstance(window, dict):
                rows.append((provider, window))
    return rows


def _history_rows(
    history: list[Mapping[str, Any]] | None,
    *,
    now: datetime,
    history_hours: float,
    history_max: int,
) -> list[tuple[datetime, Mapping[str, Any]]]:
    if history is None:
        from aiuse.analysis.history import load_recent_snapshots

        history = load_recent_snapshots(
            retention_days=max(1, int(history_hours / 24) + 1),
            max_count=history_max,
        )
    cutoff = now - timedelta(hours=history_hours)
    rows: list[tuple[datetime, Mapping[str, Any]]] = []
    for snap in history:
        collected = parse_dt(snap.get("collected_at"))
        if collected is None or collected < cutoff:
            continue
        rows.append((collected.astimezone(timezone.utc), snap))
    rows.sort(key=lambda item: item[0], reverse=True)
    return rows[:history_max]


def _filename_start(name: str) -> datetime | None:
    match = _LOG_NAME.match(name)
    if match is None:
        return None
    try:
        naive = datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S")
    except ValueError:
        return None
    # acp-run names files in local wall time.
    return naive.astimezone().astimezone(timezone.utc)


def _models_from_text(text: str) -> list[str]:
    found: list[str] = []
    for match in _APPLIED_MODEL.finditer(text):
        found.append(match.group(1))
    for match in _SET_MODEL.finditer(text):
        value = match.group(1) or match.group(2)
        if value:
            found.append(value)
    # set_config JSON lines: {"kind":"set_config","data":{"id":"model","value":"…"}}
    for line in text.splitlines():
        if '"id"' not in line or "model" not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "set_config":
            continue
        data = event.get("data")
        if isinstance(data, dict) and data.get("id") == "model":
            value = data.get("value")
            if isinstance(value, str) and value:
                found.append(value)
    return found


def _event_times(text: str) -> list[datetime]:
    times: list[datetime] = []
    for line in text.splitlines():
        if '"t"' not in line[:40] and not line.lstrip().startswith('{"t"'):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw = event.get("t") if isinstance(event, dict) else None
        if isinstance(raw, (int, float)):
            times.append(datetime.fromtimestamp(float(raw), tz=timezone.utc))
    return times


def scan_acp_pool_activity(
    *,
    since: datetime,
    until: datetime,
    log_dir: str | Path | None = None,
) -> set[tuple[str, str | None]]:
    """``{(provider, pool_family)}`` with an acp-run turn overlapping ``[since, until]``.

    ``pool_family`` is ``None`` when the provider has no split (or the model
    could not be mapped); that still counts as activity for every window of
    that provider whose fingerprint family is also ``None``.
    """
    if until < since:
        return set()
    root = Path(log_dir).expanduser() if log_dir is not None else default_acp_log_dir()
    if not root.is_dir():
        return set()
    # Widen the mtime window slightly so a long turn that started just before
    # ``since`` is still opened.
    mtime_floor = (since - timedelta(hours=2)).timestamp()
    try:
        entries = [entry for entry in os.scandir(root) if entry.is_file()]
    except OSError:
        return set()
    recent: list[tuple[float, Path]] = []
    for entry in entries:
        if not entry.name.endswith((".jsonl", ".stderr")):
            continue
        match = _LOG_NAME.match(entry.name)
        if match is None:
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < mtime_floor:
            continue
        recent.append((mtime, Path(entry.path)))
    recent.sort(reverse=True)

    active: set[tuple[str, str | None]] = set()
    for _mtime, path in recent[:_MAX_ACP_FILES]:
        match = _LOG_NAME.match(path.name)
        if match is None:
            continue
        agent = match.group(3)
        provider = ACP_AGENT_PROVIDERS.get(agent)
        if provider is None:
            continue
        start = _filename_start(path.name)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        times = _event_times(text)
        if start is not None:
            times.append(start)
        if not times:
            times.append(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
        if not any(since <= moment <= until for moment in times):
            continue
        models = _models_from_text(text)
        if not models:
            # A turn for this provider with no model id still counts for
            # unsplit providers (family None).
            active.add((provider, None))
            continue
        for model in models:
            family = pool_family_for_acp_model(provider, model)
            active.add((provider, family))
    return active


def _usage_covers(
    active: set[tuple[str, str | None]],
    *,
    provider: str,
    family: str | None,
) -> bool:
    if (provider, family) in active:
        return True
    # Unmapped-model activity on a split vendor does not cover a specific family.
    if family is None and any(prov == provider for prov, _fam in active):
        return True
    return False


def apply_frozen_measurements(
    snap: dict[str, Any],
    *,
    now: datetime | None = None,
    history: list[Mapping[str, Any]] | None = None,
    min_frozen_seconds: float = DEFAULT_MIN_FROZEN_SECONDS,
    history_hours: float = DEFAULT_HISTORY_HOURS,
    history_max: int = DEFAULT_HISTORY_MAX,
    log_dir: str | Path | None = None,
    activity: set[tuple[str, str | None]] | None = None,
) -> dict[str, Any]:
    """Mark flat-and-spent windows as frozen; idle flats stay routable.

    Mutates ``snap`` in place. A frozen applicable window forces
    ``usable_now`` false so ``--available`` will not advertise it.

    ``activity`` is an optional precomputed ``scan_acp_pool_activity`` result
    (tests inject it); when omitted the acp-run log dir is scanned.
    """
    now = now or utcnow()
    if min_frozen_seconds <= 0:
        return snap

    current_fps: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for provider, window in _iter_account_windows(snap):
        fp = _window_fingerprint(provider, window)
        if fp is None:
            continue
        current_fps.setdefault(fp, []).append(window)

    if not current_fps:
        return snap

    rows = _history_rows(history, now=now, history_hours=history_hours, history_max=history_max)
    # fingerprint -> (oldest_seen, newest_seen)
    spans: dict[tuple[Any, ...], tuple[datetime, datetime]] = {}
    for collected, past in rows:
        for provider, window in _iter_account_windows(past):
            fp = _window_fingerprint(provider, window)
            if fp is None or fp not in current_fps:
                continue
            oldest, newest = spans.get(fp, (collected, collected))
            if collected < oldest:
                oldest = collected
            if collected > newest:
                newest = collected
            spans[fp] = (oldest, newest)

    candidate_fps = {
        fp for fp, (oldest, newest) in spans.items() if (newest - oldest).total_seconds() >= min_frozen_seconds
    }
    if not candidate_fps:
        return snap

    frozen_fps: set[tuple[Any, ...]] = set()
    for fp in candidate_fps:
        provider = str(fp[0])
        family = fp[1] if isinstance(fp[1], str) or fp[1] is None else None
        if activity is None:
            # Scan each candidate's own flat span, not the union of every
            # candidate's span. A single union window let a turn in a
            # *different* candidate's span leak onto this one: a codex turn
            # during an earlier 20%→21% span marked codex's later flat 25%
            # window "spent" even though no codex activity fell inside it.
            oldest, newest = spans[fp]
            fp_activity = scan_acp_pool_activity(since=oldest, until=newest, log_dir=log_dir)
            covered = _usage_covers(fp_activity, provider=provider, family=family)
        else:
            covered = _usage_covers(activity, provider=provider, family=family)
        if covered:
            frozen_fps.add(fp)
    if not frozen_fps:
        return snap

    for account in snap.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        provider = str(account.get("provider") or "")
        touched = False
        for window in account.get("windows") or []:
            if not isinstance(window, dict):
                continue
            fp = _window_fingerprint(provider, window)
            if fp is None or fp not in frozen_fps:
                continue
            oldest, newest = spans[fp]
            span_s = int((newest - oldest).total_seconds())
            window["measurement_frozen"] = True
            window["fresh"] = False
            window["stale"] = True
            window["frozen_span_seconds"] = span_s
            window["frozen_note"] = (
                f"used_percent unchanged for {span_s // 60}m while acp-run "
                "spent this pool; upstream quota reading may be lagging — "
                "not trusted for routing"
            )
            touched = True
        if not touched:
            continue
        applicable = [
            w
            for w in (account.get("windows") or [])
            if isinstance(w, dict) and w.get("quota_scope") != "model_sublimit" and w.get("pool_family") != "on_demand"
        ]
        if any(w.get("measurement_frozen") for w in applicable):
            account["usable_now"] = False
            account["measurement_frozen"] = True
    return snap
