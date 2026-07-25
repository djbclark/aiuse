"""Snapshot persistence and consumption-rate learning (Phase 4 — experimental)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiuse.models import Snapshot, provider_config_key, utcnow

_DEFAULT_SNAPSHOT_DIR = "~/.cache/aiuse/snapshots"
_DEFAULT_RETENTION_DAYS = 90
_DEFAULT_MIN_SNAPSHOTS = 2
_DEFAULT_LOOKBACK_DAYS = 7


def snapshot_dir() -> Path:
    return Path(os.path.expanduser(_DEFAULT_SNAPSHOT_DIR))


def save_snapshot(snapshot: Snapshot, alerts: list[Any]) -> Path:
    path = snapshot_dir()
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    # Microseconds keep same-second runs unique and still sort lexicographically.
    ts = snapshot.collected_at.strftime("%Y-%m-%dT%H%M%S.%fZ")
    filepath = path / f"{ts}.json"
    n = 1
    while filepath.exists():
        filepath = path / f"{ts}-{n}.json"
        n += 1
    payload = {
        "collected_at": snapshot.collected_at.isoformat(),
        "accounts": [a.to_dict() for a in snapshot.accounts],
        "alerts": [a.to_dict() for a in alerts],
    }
    text = json.dumps(payload, indent=2, default=str) + "\n"
    fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return filepath


def load_recent_snapshots(
    *, retention_days: int = _DEFAULT_RETENTION_DAYS, max_count: int = 30
) -> list[dict[str, Any]]:
    directory = snapshot_dir()
    if not directory.is_dir():
        return []
    cutoff = utcnow() - timedelta(days=retention_days)
    snapshots: list[dict[str, Any]] = []
    for entry in sorted(directory.iterdir(), reverse=True):
        if not entry.is_file() or not entry.suffix.lower() == ".json":
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ts_str = data.get("collected_at")
        if ts_str:
            try:
                collected = datetime.fromisoformat(ts_str)
                if collected.tzinfo is None:
                    collected = collected.replace(tzinfo=timezone.utc)
                if collected < cutoff:
                    continue
            except ValueError:
                continue
        snapshots.append(data)
        if len(snapshots) >= max_count:
            break
    return snapshots


def _account_window_key(account: dict[str, Any], window: dict[str, Any]) -> str:
    provider = str(account.get("provider", "")).lower()
    acct = str(account.get("account", "")).lower()
    label = str(window.get("label", "")).lower()
    resets = window.get("resets_at") or ""
    return f"{provider}|{acct}|{label}|{resets}"


def compute_learned_burn_rates(
    *,
    current: Snapshot,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    min_snapshots: int = _DEFAULT_MIN_SNAPSHOTS,
) -> dict[str, tuple[float, int]]:
    """Return ``{'provider:duration_kind': (avg_fraction_per_day, sample_count)}``.

    ``avg_fraction_per_day`` is remaining-percent consumed per day / 100
    (e.g. 0.30 means ~30% of the window per day).

    Pairs are weighted by elapsed time so multi-minute noise does not dominate
    day-scale samples. Snapshot pairs that straddle a window reset contribute a
    reconstructed tail-of-cycle point instead of being dropped.
    """
    history = load_recent_snapshots(retention_days=retention_days)
    if len(history) < min_snapshots:
        return {}

    provider_window_burns: dict[str, list[tuple[float, float]]] = {}

    now = utcnow()
    for prev_data in history:
        ts_str = prev_data.get("collected_at", "")
        try:
            prev_time = datetime.fromisoformat(ts_str)
            if prev_time.tzinfo is None:
                prev_time = prev_time.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        time_delta_days = max(0.01, (now - prev_time).total_seconds() / 86400.0)
        if time_delta_days > retention_days:
            continue
        # Weight by elapsed days (capped at 1) so 3-minute pairs barely matter.
        weight = min(time_delta_days, 1.0)

        for prev_account in prev_data.get("accounts") or []:
            provider = provider_config_key(str(prev_account.get("provider", "")))
            for prev_window in prev_account.get("windows") or []:
                prev_remaining = prev_window.get("remaining_percent")
                if prev_remaining is None:
                    prev_remaining = _remaining_from_used(prev_window.get("used_percent"))
                if prev_remaining is None:
                    continue
                try:
                    prev_remaining_f = float(prev_remaining)
                except (TypeError, ValueError):
                    continue

                current_remaining = _find_current_remaining(current, prev_account, prev_window, match_resets=True)
                burn_rate: float | None = None
                pair_weight = weight

                if current_remaining is not None:
                    consumed = prev_remaining_f - current_remaining
                    if consumed > 0:
                        burn_rate = consumed / time_delta_days
                    elif current_remaining > prev_remaining_f:
                        # Same resets_at match but remaining rose — ignore.
                        continue
                    else:
                        continue
                else:
                    # No same-cycle match: try same label (possible reset).
                    loose = _find_current_remaining(current, prev_account, prev_window, match_resets=False)
                    if loose is None or loose <= prev_remaining_f:
                        continue
                    # Reset between snapshots: attribute remaining at prev as
                    # consumption closed out over prev → previous resets_at.
                    resets_raw = prev_window.get("resets_at")
                    days_to_reset = time_delta_days
                    if resets_raw:
                        try:
                            resets_at = datetime.fromisoformat(str(resets_raw).replace("Z", "+00:00"))
                            if resets_at.tzinfo is None:
                                resets_at = resets_at.replace(tzinfo=timezone.utc)
                            if prev_time < resets_at <= now:
                                days_to_reset = max(0.01, (resets_at - prev_time).total_seconds() / 86400.0)
                        except ValueError:
                            pass
                    burn_rate = prev_remaining_f / days_to_reset
                    pair_weight = min(days_to_reset, 1.0)

                if burn_rate is None:
                    continue
                window_minutes = prev_window.get("window_minutes")
                duration_key = _duration_key(window_minutes)
                if duration_key:
                    pk = f"{provider}:{duration_key}"
                    provider_window_burns.setdefault(pk, []).append((burn_rate, pair_weight))

    rates: dict[str, tuple[float, int]] = {}
    for pk, burns in provider_window_burns.items():
        if len(burns) < 2:
            continue
        total_w = sum(w for _, w in burns)
        if total_w <= 0:
            continue
        avg_burn_pct = sum(b * w for b, w in burns) / total_w
        rates[pk] = (avg_burn_pct / 100.0, len(burns))
    return rates


def compute_learned_flexibility(
    *,
    current: Snapshot,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    min_snapshots: int = _DEFAULT_MIN_SNAPSHOTS,
) -> dict[str, float]:
    rates = compute_learned_burn_rates(
        current=current,
        retention_days=retention_days,
        min_snapshots=min_snapshots,
    )
    return {k: _burn_rate_to_flexibility(rate * 100.0) for k, (rate, _n) in rates.items()}


def _burn_rate_to_flexibility(burn_rate_pct_per_day: float) -> float:
    if burn_rate_pct_per_day >= 80:
        return 1.0
    if burn_rate_pct_per_day >= 40:
        return 0.7 + 0.3 * (burn_rate_pct_per_day - 40) / 40
    if burn_rate_pct_per_day >= 10:
        return 0.25 + 0.45 * (burn_rate_pct_per_day - 10) / 30
    if burn_rate_pct_per_day >= 2:
        return 0.05 + 0.20 * (burn_rate_pct_per_day - 2) / 8
    return 0.0


def merge_learned_flexibility(
    base_flex: float,
    provider: str,
    duration_kind: str | None,
    learned: dict[str, float],
) -> float:
    if not duration_kind or not learned:
        return base_flex
    key = f"{provider.lower().replace(' ', '-')}:{duration_kind}"
    learned_flex = learned.get(key)
    # Exact provider match only — never blend another provider's rate for the
    # same duration bucket (Grok weekly ≠ Codex weekly).
    if learned_flex is None:
        return base_flex
    return 0.3 * learned_flex + 0.7 * base_flex


def chronic_waste_summary(
    *,
    current: Snapshot,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
) -> list[dict[str, Any]]:
    history = load_recent_snapshots(retention_days=retention_days)
    if len(history) < _DEFAULT_MIN_SNAPSHOTS:
        return []

    # samples: list of (resets_at_key, remaining) — at most one per cycle
    wasted: dict[str, dict[str, Any]] = {}

    for prev_data in history[:7]:
        ts_str = prev_data.get("collected_at", "")
        try:
            prev_time = datetime.fromisoformat(ts_str)
            if prev_time.tzinfo is None:
                prev_time = prev_time.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        for prev_account in prev_data.get("accounts") or []:
            provider = provider_config_key(str(prev_account.get("provider", "")))
            for prev_window in prev_account.get("windows") or []:
                window_minutes = prev_window.get("window_minutes")
                if not window_minutes or window_minutes > 360:
                    continue
                prev_remaining = prev_window.get("remaining_percent")
                if prev_remaining is None:
                    prev_remaining = _remaining_from_used(prev_window.get("used_percent"))
                if prev_remaining is None:
                    continue

                key = f"{provider}:{prev_window.get('label', '')}"
                resets_key = str(prev_window.get("resets_at") or "") or f"unknown:{ts_str}"
                bucket = wasted.setdefault(
                    key,
                    {
                        "provider": provider,
                        "label": prev_window.get("label", ""),
                        "by_reset": {},  # resets_at -> remaining (most recent wins)
                    },
                )
                # history is newest-first; keep first sample per resets_at
                if resets_key not in bucket["by_reset"]:
                    bucket["by_reset"][resets_key] = float(prev_remaining)

    result: list[dict[str, Any]] = []
    for key, data in wasted.items():
        by_reset: dict[str, float] = data["by_reset"]
        if len(by_reset) < 2:
            continue
        samples = list(by_reset.values())
        avg = sum(samples) / len(samples)
        result.append(
            {
                "provider": data["provider"],
                "label": data["label"],
                "avg_remaining_pct": round(avg, 1),
                "sample_count": len(samples),
            }
        )
    result.sort(key=lambda x: x["avg_remaining_pct"], reverse=True)
    return result


def _remaining_from_used(used: Any) -> float | None:
    if used is None:
        return None
    try:
        return max(0.0, 100.0 - float(used))
    except (TypeError, ValueError):
        return None


def _find_current_remaining(
    snapshot: Snapshot,
    prev_account: dict[str, Any],
    prev_window: dict[str, Any],
    *,
    match_resets: bool = True,
) -> float | None:
    prev_provider = provider_config_key(str(prev_account.get("provider", "")))
    prev_account_id = str(prev_account.get("account", "")).lower()
    prev_label = str(prev_window.get("label", "")).lower()
    prev_resets = prev_window.get("resets_at") or ""

    for acc in snapshot.accounts:
        if provider_config_key(acc.provider) != prev_provider:
            continue
        if (acc.account or "").lower() != prev_account_id:
            continue
        for w in acc.windows:
            if w.label.lower() != prev_label:
                continue
            if match_resets and prev_resets:
                w_resets = w.resets_at.isoformat() if w.resets_at else ""
                if w_resets != prev_resets:
                    continue
            val = w.remaining()
            if val is not None:
                return val
            return _remaining_from_used(w.used_percent)
    return None


def _duration_key(window_minutes: Any) -> str | None:
    if window_minutes is None:
        return None
    try:
        m = int(window_minutes)
    except (TypeError, ValueError):
        return None
    if m <= 360:
        return "5h"
    if m <= 10080:
        return "weekly"
    if m <= 44640:
        return "monthly"
    return None


def count_snapshots(*, retention_days: int = _DEFAULT_RETENTION_DAYS, max_count: int = 10_000) -> int:
    """How many retained snapshot files are on disk (newest-first load)."""
    return len(load_recent_snapshots(retention_days=retention_days, max_count=max_count))


def should_persist_snapshots(analysis_cfg: dict[str, Any] | None = None) -> bool:
    """Whether this run should write a snapshot file.

    Explicit ``persist_snapshots: true`` always saves. ``learn_from_history``
    ``true`` or ``auto`` also saves (so auto can accumulate until learning
    activates). Explicit ``learn_from_history: false`` does not force persist.
    """
    cfg = analysis_cfg or {}
    if cfg.get("persist_snapshots"):
        return True
    raw = cfg.get("learn_from_history", "auto")
    if raw is False:
        return False
    if isinstance(raw, str) and raw.strip().lower() in {"false", "no", "off", "0"}:
        return False
    # true / auto / unrecognized → persist so history can become useful
    return True


def should_learn_from_history(analysis_cfg: dict[str, Any] | None = None) -> bool:
    """Whether history should influence scoring/alerts this run.

    ``learn_from_history`` values:
    - ``true`` / ``"true"`` — always attempt learning
    - ``false`` / ``"false"`` — never
    - ``"auto"`` (default) — learn once retained snapshot count >= min (2)
    """
    cfg = analysis_cfg or {}
    raw = cfg.get("learn_from_history", "auto")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in {"true", "yes", "on", "1"}:
            return True
        if key in {"false", "no", "off", "0"}:
            return False
        # "auto" and any other unrecognized string → auto
    try:
        retention = int(cfg.get("snapshot_retention_days") or _DEFAULT_RETENTION_DAYS)
    except (TypeError, ValueError):
        retention = _DEFAULT_RETENTION_DAYS
    return count_snapshots(retention_days=retention) >= _DEFAULT_MIN_SNAPSHOTS


def history_status_line(*, analysis_cfg: dict[str, Any] | None = None) -> str:
    """One-line status for --full / docs: snapshot count and learning flag."""
    cfg = analysis_cfg or {}
    try:
        retention = int(cfg.get("snapshot_retention_days") or _DEFAULT_RETENTION_DAYS)
    except (TypeError, ValueError):
        retention = _DEFAULT_RETENTION_DAYS
    n = count_snapshots(retention_days=retention)
    raw = cfg.get("learn_from_history", "auto")
    if isinstance(raw, bool):
        mode = "on" if raw else "off"
    elif isinstance(raw, str) and raw.strip().lower() in {"false", "no", "off", "0"}:
        mode = "off"
    elif isinstance(raw, str) and raw.strip().lower() in {"true", "yes", "on", "1"}:
        mode = "on"
    else:
        # auto
        active = n >= _DEFAULT_MIN_SNAPSHOTS
        mode = f"auto/{'on' if active else 'waiting'}"
    noun = "snapshot" if n == 1 else "snapshots"
    return f"History: {n} {noun} in {snapshot_dir()} (learning {mode})"


def late_cycle_remaining_summary(
    *,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    late_elapsed_min: float = 0.70,
    min_samples: int = 2,
) -> list[dict[str, Any]]:
    """Average remaining% when observed late in a cycle (proxy for waste at reset).

    Uses historical windows with known ``resets_at`` + ``window_minutes`` where
    elapsed fraction ≥ ``late_elapsed_min``. Groups by provider + duration kind.
    """
    history = load_recent_snapshots(retention_days=retention_days, max_count=10_000)
    if len(history) < _DEFAULT_MIN_SNAPSHOTS:
        return []

    buckets: dict[str, list[float]] = {}
    meta: dict[str, dict[str, str]] = {}

    for prev_data in history:
        ts_str = prev_data.get("collected_at", "")
        try:
            obs_time = datetime.fromisoformat(str(ts_str))
            if obs_time.tzinfo is None:
                obs_time = obs_time.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        for prev_account in prev_data.get("accounts") or []:
            provider = provider_config_key(str(prev_account.get("provider", "")))
            for prev_window in prev_account.get("windows") or []:
                remaining = prev_window.get("remaining_percent")
                if remaining is None:
                    remaining = _remaining_from_used(prev_window.get("used_percent"))
                if remaining is None:
                    continue
                try:
                    rem_f = float(remaining)
                except (TypeError, ValueError):
                    continue
                duration_key = _duration_key(prev_window.get("window_minutes"))
                if not duration_key:
                    continue
                resets_raw = prev_window.get("resets_at")
                minutes = prev_window.get("window_minutes")
                if not resets_raw or not minutes:
                    continue
                try:
                    resets_at = datetime.fromisoformat(str(resets_raw).replace("Z", "+00:00"))
                    if resets_at.tzinfo is None:
                        resets_at = resets_at.replace(tzinfo=timezone.utc)
                    m = int(minutes)
                except (TypeError, ValueError):
                    continue
                if m <= 0:
                    continue
                t_left_days = (resets_at - obs_time).total_seconds() / 86400.0
                d_days = m / 1440.0
                if d_days <= 0:
                    continue
                # Past reset → treat as cycle end sample if remaining was still high.
                if t_left_days < 0:
                    elapsed = 1.0
                else:
                    elapsed = min(1.0, max(0.0, 1.0 - t_left_days / d_days))
                if elapsed < late_elapsed_min:
                    continue
                pk = f"{provider}:{duration_key}"
                buckets.setdefault(pk, []).append(rem_f)
                meta[pk] = {"provider": provider, "duration_kind": duration_key}

    result: list[dict[str, Any]] = []
    for pk, samples in buckets.items():
        if len(samples) < min_samples:
            continue
        avg = sum(samples) / len(samples)
        result.append(
            {
                "provider": meta[pk]["provider"],
                "duration_kind": meta[pk]["duration_kind"],
                "avg_remaining_pct": round(avg, 1),
                "sample_count": len(samples),
            }
        )
    result.sort(key=lambda x: x["avg_remaining_pct"], reverse=True)
    return result


def history_insights(
    snapshot: Snapshot,
    *,
    analysis_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured history insights for JSON / docs (additive contract).

    Empty-ish when learning is off or history is thin.
    """
    cfg = analysis_cfg or {}
    try:
        retention = int(cfg.get("snapshot_retention_days") or _DEFAULT_RETENTION_DAYS)
    except (TypeError, ValueError):
        retention = _DEFAULT_RETENTION_DAYS

    n = count_snapshots(retention_days=retention)
    learning = should_learn_from_history(cfg)
    out: dict[str, Any] = {
        "snapshot_count": n,
        "learning_active": learning,
        "retention_days": retention,
        "learned_burn_rates": {},
        "chronic_underuse": [],
        "usually_left_late_cycle": [],
        "burn_candidates_from_history": [],
    }
    if not learning:
        return out

    rates = compute_learned_burn_rates(current=snapshot, retention_days=retention)
    out["learned_burn_rates"] = {
        key: {"fraction_per_day": round(rate, 4), "sample_count": n_samples}
        for key, (rate, n_samples) in sorted(rates.items())
    }
    out["chronic_underuse"] = chronic_waste_summary(current=snapshot, retention_days=retention)
    late = late_cycle_remaining_summary(retention_days=retention)
    out["usually_left_late_cycle"] = late
    # History-taught "burn soon" candidates: high remaining late in prior cycles.
    candidates = [
        item
        for item in late
        if float(item.get("avg_remaining_pct") or 0) >= 40.0 and int(item.get("sample_count") or 0) >= 2
    ]
    out["burn_candidates_from_history"] = candidates[:8]
    return out


def history_section_lines(
    snapshot: Snapshot,
    *,
    analysis_cfg: dict[str, Any] | None = None,
) -> list[str]:
    """Plain-text body lines for the ``## History`` block on ``--full``."""
    from aiuse.models import provider_display_name

    cfg = analysis_cfg or {}
    try:
        retention = int(cfg.get("snapshot_retention_days") or _DEFAULT_RETENTION_DAYS)
    except (TypeError, ValueError):
        retention = _DEFAULT_RETENTION_DAYS

    lines: list[str] = [history_status_line(analysis_cfg=cfg)]
    recent = load_recent_snapshots(retention_days=retention, max_count=10_000)
    if recent:
        times: list[datetime] = []
        for item in recent:
            ts = item.get("collected_at")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(str(ts))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                times.append(dt)
            except ValueError:
                continue
        if times:
            oldest, newest = min(times), max(times)
            span_h = max(0.0, (newest - oldest).total_seconds() / 3600.0)
            if span_h >= 48:
                span_s = f"{span_h / 24.0:.1f}d"
            else:
                span_s = f"{span_h:.0f}h"
            lines.append(
                f"  span: {oldest.strftime('%Y-%m-%d %H:%M')} → "
                f"{newest.strftime('%Y-%m-%d %H:%M')} UTC ({span_s}, {len(times)} files)"
            )
            lines.append(f"  retention: {retention}d (config snapshot_retention_days)")

    if not should_learn_from_history(cfg):
        raw = cfg.get("learn_from_history", "auto")
        explicitly_off = raw is False or (isinstance(raw, str) and raw.strip().lower() in {"false", "no", "off", "0"})
        if explicitly_off:
            lines.append("  Learning disabled (learn_from_history: false).")
        else:
            n = len(recent)
            need = max(0, _DEFAULT_MIN_SNAPSHOTS - n)
            lines.append(
                f"  Learning waits for {need} more snapshot{'s' if need != 1 else ''} (need {_DEFAULT_MIN_SNAPSHOTS}+)."
            )
        return lines

    rates = compute_learned_burn_rates(current=snapshot, retention_days=retention)
    if rates:
        lines.append("  Learned burn rates (blended into pace when present):")
        for key, (rate, n) in sorted(rates.items()):
            provider, _, duration = key.partition(":")
            name = provider_display_name(provider)
            lines.append(f"    · {name} {duration}: ~{rate * 100:.0f}%/day ({n} sample{'s' if n != 1 else ''})")
    else:
        lines.append("  No learned burn rates yet (need enough same-window samples across time).")

    late = late_cycle_remaining_summary(retention_days=retention)
    if late:
        lines.append("  Usually left late in cycle (≥70% elapsed; proxy for waste at reset):")
        for item in late[:8]:
            name = provider_display_name(str(item["provider"]))
            lines.append(
                f"    · {name} {item['duration_kind']}: ~{item['avg_remaining_pct']:.0f}% left "
                f"avg ({item['sample_count']} late sample{'s' if item['sample_count'] != 1 else ''})"
            )
        burnish = [i for i in late if float(i["avg_remaining_pct"]) >= 40.0][:5]
        if burnish:
            lines.append("  History suggests burning these (often leftover late in window):")
            for item in burnish:
                name = provider_display_name(str(item["provider"]))
                lines.append(
                    f"    · {name} {item['duration_kind']} — usually ~{item['avg_remaining_pct']:.0f}% left late"
                )

    chronic = chronic_waste_summary(current=snapshot, retention_days=retention)
    if chronic:
        lines.append("  Chronic underuse (short windows, multiple reset cycles):")
        for item in chronic[:8]:
            name = provider_display_name(str(item["provider"]))
            lines.append(
                f"    · {name} {item['label']}: {item['avg_remaining_pct']:.0f}% left avg "
                f"over {item['sample_count']} cycles"
            )
    elif rates or late:
        lines.append("  No chronic short-window underuse detected yet.")
    return lines
