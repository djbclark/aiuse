"""Adaptive sampling: the scheduled entry point (``aiuse sample``).

A scheduler fires this every few minutes. Most firings return at once; how
often it actually collects depends on what the quota meters did last time:

* **idle** — nothing moved: collect once per ``idle_interval`` (hourly).
* **active** — some window moved: collect every ``active_interval`` (15 min).
* **burst** — a window is burning fast: sample every ``burst_interval``
  (3 min), and only the providers that are burning, so a sprint on one
  subscription does not mean polling every vendor's API twenty times an hour.
  A full collection still happens every ``active_interval``.

Hourly samples were enough to see *that* a month of quota went in an
afternoon, and too coarse to say what did it. Dense samples during exactly
those hours are what lets ``aiuse attribute`` line the meter up against the
token ledgers.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from aiuse import ledger
from aiuse.analysis import history
from aiuse.config import DEFAULT_CONFIG, KNOWN_COLLECTOR_KEYS, timeout_for
from aiuse.models import PROVIDER_ID_ALIASES, Snapshot, canonical_provider, effective_window_minutes, utcnow

TIERS = ("idle", "active", "burst")
# A firing that lands a few seconds early (launchd jitter) still counts as due.
_SLACK_SECONDS = 15.0
# Two samples closer than this say nothing reliable about a rate.
_MIN_RATE_SECONDS = 60.0


def state_path() -> Path:
    return history.snapshot_dir().parent / "sampler-state.json"


def sampling_settings(config: dict[str, Any] | None) -> dict[str, float]:
    merged = dict(DEFAULT_CONFIG["sampling"])
    section = (config or {}).get("sampling")
    if isinstance(section, dict):
        for key, value in section.items():
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                continue
    return {k: float(v) for k, v in merged.items()}


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(state, indent=2, default=str) + "\n")
    os.rename(tmp, path)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Decision:
    action: str  # skip | full | partial
    reason: str
    wait_seconds: float = 0.0
    hot: list[dict[str, str]] = field(default_factory=list)


def decide(state: dict[str, Any], settings: dict[str, float], now: datetime, *, force: bool = False) -> Decision:
    tier = state.get("tier") if state.get("tier") in TIERS else "idle"
    last_sample = _parse(state.get("last_sample_at"))
    last_full = _parse(state.get("last_full_at"))
    if force or last_sample is None or last_full is None:
        return Decision("full", "forced" if force else "first sample")
    interval = settings[f"{tier}_interval"]
    waited = (now - last_sample).total_seconds()
    if waited < interval - _SLACK_SECONDS:
        return Decision("skip", f"{tier} tier", wait_seconds=interval - waited)
    hot = [h for h in state.get("hot") or [] if isinstance(h, dict) and h.get("provider")]
    full_due = (now - last_full).total_seconds() >= settings["active_interval"] - _SLACK_SECONDS
    if tier == "burst" and hot and not full_due:
        return Decision("partial", "burst tier", hot=hot)
    return Decision("full", f"{tier} tier")


def schedule(state: dict[str, Any], settings: dict[str, float]) -> tuple[datetime, datetime, str] | None:
    """``(previous sample, next sample due, tier)``, or None before the first sample."""
    last = _parse(state.get("last_sample_at"))
    if last is None:
        return None
    tier = state.get("tier") if state.get("tier") in TIERS else "idle"
    return last, last + timedelta(seconds=settings[f"{tier}_interval"]), str(tier)


def _series(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for account in snapshot.accounts:
        for window in account.windows:
            if window.used_percent is None:
                continue
            provider = canonical_provider(account.provider)
            out[f"{provider}|{account.account or ''}|{window.label}"] = {
                "used": float(window.used_percent),
                "at": snapshot.collected_at.isoformat(),
                "provider": provider,
                "source": account.source,
                "label": window.label,
                "window_minutes": effective_window_minutes(window.label, window.window_minutes),
            }
    return out


def advance_state(
    state: dict[str, Any], snapshot: Snapshot, settings: dict[str, float], *, partial: bool
) -> dict[str, Any]:
    """Fold one sample into the tier state. Returns the new state.

    A window counts as *moved* when it rose by ``min_move_percent``, and as a
    *burst* when it is rising at ``burst_percent_per_hour`` **and** at
    ``burst_pace_ratio`` times the pace that would exactly exhaust it — the
    second test keeps a 5-hour window's ordinary 20 points/hour from reading
    as an emergency while still catching a weekly window at 8.
    Rising is fast (one sample); falling takes ``cooldown_samples`` quiet ones.
    """
    raw_previous = state.get("last_values")
    previous: dict[str, Any] = raw_previous if isinstance(raw_previous, dict) else {}
    current = _series(snapshot)
    moved: list[str] = []
    hot: dict[str, dict[str, str]] = {}
    for key, cur in current.items():
        prev = previous.get(key)
        prev_at = _parse(prev.get("at")) if isinstance(prev, dict) else None
        if prev_at is None or not isinstance(prev, dict):
            continue
        seconds = (snapshot.collected_at - prev_at).total_seconds()
        delta = cur["used"] - float(prev.get("used") or 0.0)
        if seconds < _MIN_RATE_SECONDS or delta < settings["min_move_percent"]:
            continue  # too close to judge, flat, or a reset (usage fell)
        per_hour = delta / (seconds / 3600.0)
        minutes = cur.get("window_minutes")
        pace = (delta / 100.0) / ((seconds / 60.0) / float(minutes)) if minutes else float("inf")
        moved.append(f"{cur['provider']} {cur['label']} +{delta:.1f} ({per_hour:.1f}/h)")
        if per_hour >= settings["burst_percent_per_hour"] and pace >= settings["burst_pace_ratio"]:
            hot[cur["provider"]] = {"provider": cur["provider"], "source": cur["source"]}

    tier = state.get("tier") if state.get("tier") in TIERS else "idle"
    target = "burst" if hot else "active" if moved else "idle"
    quiet = int(state.get("quiet_samples") or 0)
    if TIERS.index(target) >= TIERS.index(tier):
        tier, quiet = target, 0
    else:
        quiet += 1
        if quiet >= int(settings["cooldown_samples"]):
            tier, quiet = TIERS[TIERS.index(tier) - 1], 0

    now_iso = snapshot.collected_at.isoformat()
    new_state = dict(state)
    new_state.update(
        {
            "tier": tier,
            "quiet_samples": quiet,
            "last_sample_at": now_iso,
            # Stay on the providers that were burning until the tier steps down.
            "hot": list(hot.values()) or (state.get("hot") or [] if tier == "burst" else []),
            "moved": moved,
            # A partial sample only refreshes the windows it collected.
            "last_values": {**previous, **current} if partial else current,
        }
    )
    if not partial:
        new_state["last_full_at"] = now_iso
    return new_state


def partial_config(
    config: dict[str, Any],
    hot: list[dict[str, str]],
    *,
    codexbar_enabled: Callable[[], list[str | None] | None] | None = None,
) -> dict[str, Any]:
    """A copy of ``config`` that collects only the sources behind ``hot`` providers."""
    sources = {h["source"] for h in hot if h.get("source")}
    providers = {canonical_provider(h["provider"]) for h in hot}
    cfg = copy.deepcopy(config)
    collectors = cfg.setdefault("collectors", {})
    for name in KNOWN_COLLECTOR_KEYS:
        entry = collectors.get(name)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["enabled"] = name in sources and bool(entry.get("enabled", True) if name in collectors else True)
        collectors[name] = entry
    if collectors.get("codexbar", {}).get("enabled"):
        # CodexBar spells some providers its own way (opencodego); ask it which
        # of its enabled providers are the hot ones rather than guessing.
        if codexbar_enabled is None:
            from aiuse.collectors.codexbar import _discover_enabled_providers

            codexbar_enabled = _discover_enabled_providers
        spellings = providers | {raw for raw, canon in PROVIDER_ID_ALIASES.items() if canon in providers}
        enabled = codexbar_enabled() or []
        wanted = sorted(str(p) for p in enabled if p and (p in spellings or canonical_provider(str(p)) in providers))
        if wanted:
            collectors["codexbar"]["providers"] = ",".join(wanted)
        else:
            collectors["codexbar"]["providers"] = ",".join(sorted(providers))
    return cfg


def collect_with_ledger(
    config: dict[str, Any],
    collect: Callable[[dict[str, Any]], Snapshot],
    *,
    capture: Callable[..., dict[str, Any]] | None = None,
) -> tuple[Snapshot, dict[str, Any] | None, str | None]:
    """Run a collection and, beside it, one tokscale ledger reading.

    Returns ``(snapshot, ledger_sample, ledger_error)``. The ledger is taken
    concurrently so it adds no wall time, and its failure never costs the
    snapshot.
    """
    pending: Future[dict[str, Any]] | None = None
    pool: ThreadPoolExecutor | None = None
    if ledger.ledger_enabled(config):
        pool = ThreadPoolExecutor(max_workers=1)
        pending = pool.submit(
            capture or ledger.capture_tokscale_ledger,
            previous=ledger.load_latest_ledger(),
            timeout=timeout_for(config, "tokscale"),
        )
    try:
        snapshot = collect(config)
        sample: dict[str, Any] | None = None
        error: str | None = None
        if pending is not None:
            try:
                sample = pending.result()
            except Exception as exc:  # noqa: BLE001 — the ledger is best-effort
                error = str(exc)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
    return snapshot, sample, error


def run_sample(
    config: dict[str, Any],
    *,
    force: bool = False,
    quiet: bool = False,
    now: datetime | None = None,
    collect: Callable[[dict[str, Any]], Snapshot] | None = None,
    stdout: Any = None,
) -> int:
    """One scheduler firing. Prints a single status line; exit 0 unless collection failed outright."""
    from aiuse.analysis.use_or_lose import analyze_use_or_lose
    from aiuse.collectors.runner import run_collectors

    out = stdout or sys.stdout
    collect = collect or run_collectors
    settings = sampling_settings(config)
    state = load_state()
    decision = decide(state, settings, now or utcnow(), force=force)
    if decision.action == "skip":
        # Said only to a person: a scheduler firing every few minutes would
        # otherwise fill its log with hundreds of skip lines a day.
        isatty = getattr(out, "isatty", None)
        if not quiet and (isatty is None or isatty()):
            print(f"skip: {decision.reason}, next sample in {decision.wait_seconds:.0f}s", file=out)
        return 0

    partial = decision.action == "partial"
    run_config = partial_config(config, decision.hot) if partial else config
    snapshot, sample, ledger_error = collect_with_ledger(run_config, collect)
    if not snapshot.accounts and snapshot.collector_errors and not partial:
        print(f"sample failed: {'; '.join(snapshot.collector_errors)[:300]}", file=sys.stderr)
        return 1

    analysis_cfg = config.get("analysis") if isinstance(config.get("analysis"), dict) else {}
    retention = int((analysis_cfg or {}).get("snapshot_retention_days") or 90)
    hot_providers = sorted({h["provider"] for h in decision.hot})
    alerts = [] if partial else analyze_use_or_lose(snapshot, config)
    history.save_snapshot(
        snapshot, alerts, retention_days=retention, partial_providers=hot_providers if partial else None
    )
    if sample is not None:
        ledger.save_ledger(sample, collected_at=snapshot.collected_at, retention_days=retention)

    new_state = advance_state(state, snapshot, settings, partial=partial)
    save_state(new_state)
    if not quiet:
        tier = new_state["tier"]
        parts = [
            snapshot.collected_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            f"partial({','.join(hot_providers)})" if partial else "full",
            f"tier={tier}",
            f"next={settings[f'{tier}_interval']:.0f}s",
        ]
        if new_state.get("moved"):
            parts.append("moved: " + "; ".join(new_state["moved"][:4]))
        if ledger_error:
            parts.append(f"ledger error: {ledger_error[:120]}")
        print(" ".join(parts), file=out)
    return 0
