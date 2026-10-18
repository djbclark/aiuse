"""Detect quota percentages that stopped updating while the window is still open.

On 2026-10-18 Google's Antigravity Claude/GPT weekly reading stayed at the
identical CodexBar float ``59.054479002952576`` (OpenUsage ``59.0``) from
12:01Z through 13:45Z, then a 429 at 13:55Z and CodexBar ``100%`` at 14:00Z
with the same weekly ``resets_at``. A ``fresh`` cache of that frozen 41% left
routed work into an already-draining (or already-exhausted) pool.

aiuse cannot fix Google's endpoint. When the same ``used_percent`` for a
provider pool window appears across recent snapshots spanning at least
``min_frozen_seconds``, mark the live window ``measurement_frozen`` so
``--available`` fails closed until the upstream number moves again (or an
agent note / live 100% reading takes over).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from aiuse.analysis.pace import independent_pool_key
from aiuse.models import parse_dt, utcnow

DEFAULT_MIN_FROZEN_SECONDS = 2700.0  # 45 minutes
DEFAULT_HISTORY_HOURS = 6.0
DEFAULT_HISTORY_MAX = 24


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


def apply_frozen_measurements(
    snap: dict[str, Any],
    *,
    now: datetime | None = None,
    history: list[Mapping[str, Any]] | None = None,
    min_frozen_seconds: float = DEFAULT_MIN_FROZEN_SECONDS,
    history_hours: float = DEFAULT_HISTORY_HOURS,
    history_max: int = DEFAULT_HISTORY_MAX,
) -> dict[str, Any]:
    """Mark windows whose used% has not moved across recent history as frozen.

    Mutates ``snap`` in place. A frozen applicable window forces
    ``usable_now`` false so ``--available`` will not advertise it.
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

    frozen_fps = {
        fp for fp, (oldest, newest) in spans.items() if (newest - oldest).total_seconds() >= min_frozen_seconds
    }
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
                f"used_percent unchanged for {span_s // 60}m across collectors; "
                "upstream quota reading may be lagging — not trusted for routing"
            )
            touched = True
        if not touched:
            continue
        # Fail closed: a frozen applicable window must not stay routable.
        applicable = [
            w
            for w in (account.get("windows") or [])
            if isinstance(w, dict) and w.get("quota_scope") != "model_sublimit" and w.get("pool_family") != "on_demand"
        ]
        if any(w.get("measurement_frozen") for w in applicable):
            account["usable_now"] = False
            account["measurement_frozen"] = True
    return snap
