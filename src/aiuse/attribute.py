"""``aiuse attribute`` — what spent the quota.

Sets three local records side by side for a time range:

1. the quota samples (how many points each window burned),
2. tokscale's session-file ledger (tokens per client and model), and
3. optionally a LiteLLM proxy's spend log (tokens per virtual key).

The meters and the ledgers measure different things — a vendor weights models
and cached tokens its own way — so this reports them next to each other and
derives a tokens-per-point rate, rather than pretending to convert one into
the other.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from aiuse import ledger
from aiuse.analysis import history
from aiuse.collectors.base import CollectorError
from aiuse.config import timeout_for
from aiuse.models import canonical_provider, effective_window_minutes, provider_display_name, utcnow

SCHEMA_VERSION = "1.0"
# How far before ``since`` to look for the reading each series starts from.
_BASELINE_LOOKBACK = timedelta(hours=2)
# A fall larger than this is a window reset, not meter noise.
_RESET_DROP_POINTS = 0.5
_RESET_SLACK = timedelta(minutes=5)
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)([smhdw])$", re.I)
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class AttributeArgError(ValueError):
    """User-facing argument error (exit 2)."""


def parse_when(text: str, now: datetime) -> datetime:
    """``24h`` / ``90m`` / ``7d`` ago, or an ISO date or datetime (local if naive)."""
    value = text.strip()
    match = _DURATION_RE.fullmatch(value)
    if match:
        return now - timedelta(seconds=float(match.group(1)) * _DURATION_SECONDS[match.group(2).lower()])
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AttributeArgError(f"invalid time {text!r} (want 24h / 90m / 7d, or an ISO date or datetime)") from exc
    return parsed.astimezone() if parsed.tzinfo is None else parsed


def _time(row: dict[str, Any], *keys: str) -> datetime | None:
    for key in (*keys, "_file_time"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


Point = tuple[datetime, float, datetime | None]


def _quota_series(samples: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """One timeline per provider window, whichever collector reported it.

    The winning source for a provider changes from one collection to the next
    (CodexBar on one, OpenUsage on the next) and each labels the same window
    its own way, so identity is the source-independent series key, not the
    label. Within one sample a row that names the account wins over a
    provider-scoped one.
    """
    accounts: dict[str, set[str]] = {}
    for sample in samples:
        for account in sample.get("accounts") or []:
            if isinstance(account, dict) and account.get("account"):
                provider = canonical_provider(str(account.get("provider") or ""))
                accounts.setdefault(provider, set()).add(history.account_key(account["account"]))

    series: dict[tuple[str, str, str], dict[str, Any]] = {}
    for sample in samples:
        when = _time(sample, "collected_at")
        if when is None:
            continue
        seen: dict[tuple[str, str, str], bool] = {}
        for account in sample.get("accounts") or []:
            if not isinstance(account, dict):
                continue
            provider = canonical_provider(str(account.get("provider") or ""))
            named = history.account_key(account.get("account"))
            # A provider-scoped row belongs to the provider's only account.
            known = accounts.get(provider, set())
            owner = named or (next(iter(known)) if len(known) == 1 else "")
            for window in account.get("windows") or []:
                used = window.get("used_percent") if isinstance(window, dict) else None
                if used is None:
                    continue
                label = str(window.get("label") or "")
                minutes = effective_window_minutes(label, window.get("window_minutes"))
                key = (provider, owner, history.window_series_key(provider, label, minutes))
                if key in seen and (seen[key] or not named):
                    continue
                entry = series.setdefault(key, {"label": label, "account": None, "points": []})
                if key in seen:
                    entry["points"].pop()  # replace the provider-scoped reading
                seen[key] = bool(named)
                if named or not entry["account"]:
                    entry["label"] = label if named or not entry["points"] else entry["label"]
                    entry["account"] = account.get("account") or entry["account"]
                entry["points"].append((when, float(used), _time(window, "resets_at")))
    for entry in series.values():
        entry["points"].sort(key=lambda p: p[0])
    return series


def _burn(points: list[Point], since: datetime) -> dict[str, Any] | None:
    """Points burned from the last reading at or before ``since`` to the end.

    A fall is a window reset only when the reset time says so: the previous
    reading's reset moment has passed, or the reset moved later. A fall without
    that is a bad reading (a stale source, a collector that answered 0) and is
    skipped, so it neither erases nor double-counts what was burned.
    """
    before = [p for p in points if p[0] <= since]
    inside = [p for p in points if p[0] > since]
    track = ([before[-1]] if before else []) + inside
    if len(track) < 2:
        return None
    burned, resets = 0.0, 0
    steps: list[tuple[datetime, datetime, float]] = []
    ref = track[0]
    for cur in track[1:]:
        (t0, a, reset0), (t1, b, reset1) = ref, cur
        if b < a - _RESET_DROP_POINTS:
            if reset0 is not None and reset1 is not None:
                real = reset0 <= t1 + _RESET_SLACK or reset1 > reset0 + _RESET_SLACK
            else:
                real = True
            if not real:
                continue
            resets += 1
            step = b  # the window restarted; what it shows now was spent since
        else:
            step = max(0.0, b - a)
        burned += step
        if step > 0:
            steps.append((t0, t1, step))
        ref = cur
    return {
        "start_used": track[0][1],
        "end_used": ref[1],
        "burned_points": round(burned, 2),
        "resets": resets,
        "samples": len(track),
        "_steps": steps,
    }


def _burned_within(window: dict[str, Any], start: datetime, end: datetime) -> float:
    slack = timedelta(seconds=90)
    return sum(step for t0, t1, step in window.get("_steps") or [] if t0 >= start - slack and t1 <= end + slack)


def _sum_rows(rows: list[dict[str, Any]], keys: tuple[str, ...], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    totals: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        ident = tuple(row.get(k) for k in keys)
        bucket = totals.setdefault(ident, {**{k: row.get(k) for k in keys}, **dict.fromkeys(fields, 0)})
        for field in fields:
            bucket[field] += row.get(field) or 0
    return list(totals.values())


_TOKSCALE_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning", "messages", "cost")
_LITELLM_FIELDS = ("requests", "failures", "input", "output", "reasoning")


def _tokscale_weight(row: dict[str, Any]) -> float:
    return float(row.get("input") or 0) + float(row.get("output") or 0) + float(row.get("cache_write") or 0)


def _with_share(rows: list[dict[str, Any]], weight: Callable[[dict[str, Any]], float]) -> list[dict[str, Any]]:
    total = sum(weight(r) for r in rows)
    for row in rows:
        row["share"] = round(weight(row) / total, 4) if total > 0 else 0.0
    return sorted(rows, key=weight, reverse=True)


def build_report(
    config: dict[str, Any],
    since: datetime,
    until: datetime | None = None,
    *,
    provider: str | None = None,
    litellm_query: Callable[..., list[dict[str, Any]]] = ledger.query_litellm_ledger,
) -> dict[str, Any]:
    until = until or utcnow()
    if since >= until:
        raise AttributeArgError("--since must be earlier than --until")
    load_from = since - _BASELINE_LOOKBACK
    attribution_cfg = config.get("attribution") if isinstance(config.get("attribution"), dict) else {}

    full = ledger.load_json_range(history.snapshot_dir(), load_from, until)
    partial = ledger.load_json_range(history.partial_sample_dir(), load_from, until)
    series = _quota_series(full + partial)

    # tokscale: differences between consecutive ledger samples that end in range.
    samples = ledger.load_json_range(ledger.ledger_dir(), load_from, until)
    tok_deltas: list[tuple[datetime, list[dict[str, Any]]]] = []
    for prev, cur in zip(samples, samples[1:]):
        end = _time(cur, "captured_at")
        if end is None or end <= since:
            continue
        tok_deltas.append((end, ledger.ledger_delta(prev, cur)))
    ledger_times = [t for t in (_time(x, "captured_at") for x in samples) if t is not None]
    tok_span = (max(min(ledger_times), since), max(ledger_times)) if len(ledger_times) >= 2 else None
    tok_map = attribution_cfg.get("provider_map") if isinstance(attribution_cfg, dict) else None
    for _, rows in tok_deltas:
        for row in rows:
            row["quota_provider"] = ledger.tokscale_row_provider(row, tok_map)

    # LiteLLM: exact per-minute rows, no sampling needed.
    litellm_rows: list[dict[str, Any]] = []
    litellm_status = "off"
    settings = ledger.litellm_settings(config)
    if settings is not None:
        try:
            litellm_rows = litellm_query(settings, since, until, timeout=timeout_for(config, "default"))
            litellm_status = "on"
        except CollectorError as exc:
            litellm_status = f"error: {exc}"
        for row in litellm_rows:
            row["quota_provider"] = ledger.litellm_row_provider(row, settings.get("provider_map"))

    wanted = canonical_provider(provider) if provider else None
    names: set[str] = {key[0] for key in series}
    names |= {r["quota_provider"] for _, rows in tok_deltas for r in rows if r.get("quota_provider")}
    names |= {r["quota_provider"] for r in litellm_rows if r.get("quota_provider")}

    providers: list[dict[str, Any]] = []
    for name in sorted(names):
        if wanted and name != wanted:
            continue
        windows = []
        for (prov, _owner, _key), timeline in sorted(series.items()):
            if prov != name:
                continue
            burn = _burn(timeline["points"], since)
            if burn is not None:
                windows.append({"account": timeline["account"], "label": timeline["label"], **burn})
        windows.sort(key=lambda w: (str(w["account"] or ""), -_window_rank(w), w["label"]))
        tok_rows = [r for _, rows in tok_deltas for r in rows if r.get("quota_provider") == name]
        tok = _with_share(
            _sum_rows(tok_rows, ("client", "model"), _TOKSCALE_FIELDS),
            lambda r: float(r.get("cost") or 0) or _tokscale_weight(r) / 1e9,
        )
        lit = _with_share(
            _sum_rows(
                [r for r in litellm_rows if r.get("quota_provider") == name], ("client", "model"), _LITELLM_FIELDS
            ),
            lambda r: float(r.get("input") or 0) + float(r.get("output") or 0),
        )
        if not any(w["burned_points"] > 0 for w in windows) and not tok and not lit:
            continue

        intervals = []
        for start, end, points_by_label in _merge_steps(windows):
            t_rows = [
                r for when, rows in tok_deltas if start < when <= end for r in rows if r.get("quota_provider") == name
            ]
            l_rows = [
                r
                for r in litellm_rows
                if r.get("quota_provider") == name and (m := _time(r, "minute")) is not None and start <= m < end
            ]
            intervals.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "burned": points_by_label,
                    "tokscale": _sum_rows(t_rows, ("client",), _TOKSCALE_FIELDS),
                    "litellm": _sum_rows(l_rows, ("client",), _LITELLM_FIELDS),
                }
            )

        entry: dict[str, Any] = {
            "provider": name,
            "display": provider_display_name(name),
            "windows": [{k: v for k, v in w.items() if not k.startswith("_")} for w in windows],
            "tokscale": tok,
            "litellm": lit,
            "intervals": intervals,
        }
        # Rate against the longest window that moved: it is the one that binds.
        # Each ledger is rated only over the time it actually covers, so a
        # ledger that started an hour ago is not divided into a week's burn.
        rate: dict[str, Any] = {}
        for window in sorted(windows, key=_window_rank, reverse=True):
            tok_points = _burned_within(window, tok_span[0], tok_span[1]) if tok and tok_span else 0.0
            lit_points = window["burned_points"] if lit else 0.0
            if tok_points >= 1.0 and "tokscale_tokens" not in rate:
                rate.update(
                    tokscale_window=window["label"],
                    tokscale_points=round(tok_points, 2),
                    tokscale_tokens=round(sum(_tokscale_weight(r) for r in tok) / tok_points),
                    tokscale_cost=round(sum(float(r.get("cost") or 0) for r in tok) / tok_points, 4),
                )
            if lit_points >= 1.0 and "litellm_tokens" not in rate:
                rate.update(
                    litellm_window=window["label"],
                    litellm_points=round(lit_points, 2),
                    litellm_tokens=round(sum(r["input"] + r["output"] for r in lit) / lit_points),
                )
        if rate:
            entry["per_point"] = rate
        providers.append(entry)
    providers.sort(key=_governing_burn, reverse=True)

    unmapped_tok = _sum_rows(
        [r for _, rows in tok_deltas for r in rows if not r.get("quota_provider")],
        ("client", "provider", "model"),
        _TOKSCALE_FIELDS,
    )
    unmapped_lit = _sum_rows(
        [r for r in litellm_rows if not r.get("quota_provider")], ("client", "model"), _LITELLM_FIELDS
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "coverage": {
            "quota_samples": len(full),
            "burst_samples": len(partial),
            "ledger_samples": len(samples),
            "ledger_span": [t.isoformat() for t in tok_span] if tok_span else None,
            "litellm": litellm_status,
        },
        "providers": providers,
        "unmapped": {
            "tokscale": sorted(unmapped_tok, key=_tokscale_weight, reverse=True),
            "litellm": sorted(unmapped_lit, key=lambda r: r["input"] + r["output"], reverse=True),
        },
    }


def _governing_burn(provider: dict[str, Any]) -> float:
    """Burn of the longest window that moved — a 5-hour window refilling nine
    times is not a bigger story than a monthly one losing half."""
    moved = [w for w in provider["windows"] if w["burned_points"] > 0]
    if not moved:
        return 0.0
    longest = max(moved, key=_window_rank)
    return float(longest["burned_points"]) + 1000.0 * (4 + _window_rank(longest))


def _window_rank(window: dict[str, Any]) -> int:
    label = str(window.get("label") or "").lower()
    for rank, marker in enumerate(("month", "week", "dai", "hour")):
        if marker in label:
            return -rank
    return -9


def _merge_steps(windows: list[dict[str, Any]]) -> list[tuple[datetime, datetime, dict[str, float]]]:
    """Group each window's rising steps by the sample interval they happened in."""
    merged: dict[tuple[datetime, datetime], dict[str, float]] = {}
    for window in windows:
        for start, end, step in window.get("_steps") or []:
            merged.setdefault((start, end), {})[window["label"]] = round(step, 2)
    return [(start, end, labels) for (start, end), labels in sorted(merged.items())]


def _n(value: Any) -> str:
    number = float(value or 0)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(number) >= limit:
            return f"{number / limit:.1f}{suffix}"
    return f"{number:.0f}"


def _local(iso: str, fmt: str = "%m-%d %H:%M") -> str:
    return datetime.fromisoformat(iso).astimezone().strftime(fmt)


def render_report(report: dict[str, Any], *, intervals: bool = False) -> str:
    cov = report["coverage"]
    lines = [
        f"Attribution {_local(report['since'], '%Y-%m-%d %H:%M')} → {_local(report['until'], '%Y-%m-%d %H:%M')}"
        f" · {cov['quota_samples']} quota samples (+{cov['burst_samples']} burst)"
        f" · {cov['ledger_samples']} ledger samples · LiteLLM {cov['litellm']}",
    ]
    if not cov.get("ledger_span"):
        lines.append(
            "  (fewer than 2 ledger samples in range: no session-file tokens yet; `aiuse sample` records them)"
        )
    elif datetime.fromisoformat(cov["ledger_span"][0]) > datetime.fromisoformat(report["since"]) + timedelta(minutes=5):
        lines.append(
            f"  (session-file tokens cover only {_local(cov['ledger_span'][0])} → {_local(cov['ledger_span'][1], '%H:%M')})"
        )
    for prov in report["providers"]:
        lines += [
            "",
            prov["display"] if prov["display"] == prov["provider"] else f"{prov['display']} ({prov['provider']})",
        ]
        for w in prov["windows"]:
            if w["burned_points"] <= 0:
                continue
            reset = f" ({w['resets']} reset{'s' if w['resets'] != 1 else ''})" if w["resets"] else ""
            account = f" · {w['account']}" if w["account"] else ""
            lines.append(
                f"  {w['label'] + account:<44} {w['start_used']:5.1f}% → {w['end_used']:5.1f}% used"
                f"   burned {w['burned_points']:5.1f} pts{reset}"
            )
        if prov["tokscale"]:
            lines.append(
                f"  {'session files (tokscale)':<44} {'input':>8} {'output':>8} {'cache-rd':>9} {'msgs':>6} {'est.$':>8} {'share':>6}"
            )
            for r in prov["tokscale"][:8]:
                lines.append(
                    f"    {(str(r['client']) + '  ' + str(r['model']))[:42]:<42} {_n(r['input']):>8} {_n(r['output']):>8}"
                    f" {_n(r['cache_read']):>9} {_n(r['messages']):>6} {float(r['cost']):>8.2f} {r['share']:>6.0%}"
                )
        if prov["litellm"]:
            lines.append(
                f"  {'proxy (LiteLLM)':<44} {'input':>8} {'output':>8} {'reasoning':>9} {'reqs':>6} {'failed':>8} {'share':>6}"
            )
            for r in prov["litellm"][:8]:
                lines.append(
                    f"    {(str(r['client']) + '  ' + str(r['model']))[:42]:<42} {_n(r['input']):>8} {_n(r['output']):>8}"
                    f" {_n(r['reasoning']):>9} {_n(r['requests']):>6} {_n(r['failures']):>8} {r['share']:>6.0%}"
                )
        rate = prov.get("per_point") or {}
        if rate.get("tokscale_tokens"):
            lines.append(
                f"  ≈ {_n(rate['tokscale_tokens'])} session tokens (est. ${rate['tokscale_cost']:.2f}) per point of"
                f" {rate['tokscale_window']}, over {rate['tokscale_points']:g} pts"
            )
        if rate.get("litellm_tokens"):
            lines.append(
                f"  ≈ {_n(rate['litellm_tokens'])} proxy tokens per point of {rate['litellm_window']},"
                f" over {rate['litellm_points']:g} pts"
            )
        if intervals:
            for item in prov["intervals"]:
                burned = ", ".join(f"{label} +{pts:g}" for label, pts in item["burned"].items())
                who = [f"{r['client']} {_n(_tokscale_weight(r))}" for r in item["tokscale"]]
                who += [f"{r['client']} {_n(r['input'] + r['output'])} via proxy" for r in item["litellm"]]
                lines.append(
                    f"    {_local(item['start'])} → {_local(item['end'], '%H:%M')}  {burned}"
                    + (f"  ←  {'; '.join(who[:4])}" if who else "")
                )
    unmapped = report["unmapped"]
    if unmapped["tokscale"] or unmapped["litellm"]:
        lines += ["", "Token activity with no quota provider mapped (set [attribution] provider_map):"]
        for r in unmapped["tokscale"][:8]:
            lines.append(
                f"    {r['client']}/{r['provider']}  {r['model']}: {_n(_tokscale_weight(r))} tokens, {_n(r['messages'])} msgs"
            )
        for r in unmapped["litellm"][:8]:
            lines.append(
                f"    proxy {r['client']}  {r['model']}: {_n(r['input'] + r['output'])} tokens, {_n(r['requests'])} reqs"
            )
    if not report["providers"]:
        lines += ["", "Nothing moved and no tokens were recorded in this range."]
    return "\n".join(lines)
