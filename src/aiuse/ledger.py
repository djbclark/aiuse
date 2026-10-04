"""Token ledgers: who spent tokens, to set beside what the quota meters say.

A quota meter says a window moved from 4% to 79%. It does not say which client
moved it. Two local records do:

* **tokscale** reads every agent CLI's session files and reports cumulative
  tokens per client and model for a day. Sampling that total alongside each
  snapshot turns it into tokens per client *per interval*: the difference
  between two samples is what was spent between them.
* **LiteLLM's spend log** (optional) records every request through a local
  proxy with an exact timestamp and the virtual key that made it, which covers
  callers that leave no session file at all (background services, cron jobs).

``aiuse attribute`` joins both with the quota samples; see ``attribute.py``.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from aiuse.analysis import history
from aiuse.collectors.base import CollectorError, run_json, which
from aiuse.models import canonical_provider, utcnow

LEDGER_SCHEMA_VERSION = "1.0"
# A sample taken after midnight also re-reads the day the previous sample was
# taken on, so that day's final total is known. Bounded: a machine that slept
# for a week should not replay seven tokscale scans.
_MAX_BACKFILL_DAYS = 3
_TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning", "messages", "cost")

# tokscale "provider" -> aiuse provider id, for the common case where the
# upstream names its own subscription. Checked after attribution.provider_map.
_TOKSCALE_PROVIDER_MAP: dict[str, str] = {
    "anthropic": "claude",
    "openai": "codex",
    "zhipu": "zai",
    "github-copilot": "copilot",
    "xai": "grok",
    "xai_oauth": "grok",
    "cline": "clinepass",
    "opencode_go": "opencode-go",
    "opencode": "opencode-zen",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "qwen": "qwencloud",
    "cursor": "cursor",
}
# Clients whose every request draws one subscription whatever model they
# route to (Antigravity sells Claude and Gemini out of Google's pools).
_TOKSCALE_CLIENT_MAP: dict[str, str] = {
    "antigravity-cli": "antigravity",
    "antigravity": "antigravity",
    "zcode": "zai",
    "cursor": "cursor",
    "copilot": "copilot",
    "devin-cli": "devin",
    "devin-desktop": "devin",
}

# api_base host -> aiuse provider id for LiteLLM rows.
_API_HOST_MAP: dict[str, str] = {
    "api.cline.bot": "clinepass",
    "openrouter.ai": "openrouter",
    "api.deepseek.com": "deepseek",
    "api.x.ai": "grok",
    "api.z.ai": "zai",
    "api.anthropic.com": "claude",
}


def ledger_dir() -> Path:
    return history.snapshot_dir().parent / "ledger"


def ledger_enabled(config: dict[str, Any] | None) -> bool:
    """``attribution.ledger``: true | false | "auto" (auto = tokscale on PATH)."""
    section = (config or {}).get("attribution")
    raw = section.get("ledger", "auto") if isinstance(section, dict) else "auto"
    if isinstance(raw, bool):
        return raw
    key = str(raw).strip().lower()
    if key in {"true", "yes", "on", "1"}:
        return True
    if key in {"false", "no", "off", "0"}:
        return False
    return which("tokscale") is not None


def _row(entry: dict[str, Any]) -> dict[str, Any]:
    def num(key: str) -> float:
        try:
            return float(entry.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "client": str(entry.get("client") or "unknown"),
        "model": str(entry.get("model") or "unknown"),
        "provider": str(entry.get("provider") or "unknown"),
        "input": int(num("input")),
        "output": int(num("output")),
        "cache_read": int(num("cacheRead")),
        "cache_write": int(num("cacheWrite")),
        "reasoning": int(num("reasoning")),
        "messages": int(num("messageCount")),
        "cost": round(num("cost"), 6),
    }


def _latest_day(sample: dict[str, Any] | None) -> date | None:
    days = (sample or {}).get("days")
    if not isinstance(days, dict) or not days:
        return None
    try:
        return max(date.fromisoformat(d) for d in days)
    except ValueError:
        return None


def capture_tokscale_ledger(
    *,
    now: datetime | None = None,
    previous: dict[str, Any] | None = None,
    timeout: float = 45.0,
    run: Callable[..., Any] = run_json,
) -> dict[str, Any]:
    """One cumulative per-client reading from tokscale, keyed by local day.

    tokscale buckets by *local* calendar day, so totals restart at local
    midnight. Keeping each day's total separately is what lets
    :func:`ledger_delta` subtract across midnight: the first sample of a new
    day also carries the finished day's final total.
    """
    now = now or utcnow()
    today = now.astimezone().date()
    days = [today]
    prev_day = _latest_day(previous)
    if prev_day is not None and prev_day < today:
        start = max(prev_day, today - timedelta(days=_MAX_BACKFILL_DAYS))
        days = [start + timedelta(days=i) for i in range((today - start).days + 1)]

    by_day: dict[str, list[dict[str, Any]]] = {}
    for day in days:
        iso = day.isoformat()
        payload = run(
            ["tokscale", "models", "--json", "--no-spinner", "--since", iso, "--until", iso],
            timeout=timeout,
        )
        entries = payload.get("entries") if isinstance(payload, dict) else None
        by_day[iso] = [_row(e) for e in (entries or []) if isinstance(e, dict)]
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "source": "tokscale",
        "captured_at": now.isoformat(),
        "days": by_day,
    }


def save_ledger(sample: dict[str, Any], *, collected_at: datetime, retention_days: int = 90) -> Path:
    """File the sample under the same timestamp as the snapshot it accompanies."""
    directory = ledger_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    ts = collected_at.strftime("%Y-%m-%dT%H%M%S.%fZ")
    path = directory / f"{ts}.json"
    n = 1
    while path.exists():
        path = directory / f"{ts}-{n}.json"
        n += 1
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(sample, default=str) + "\n")
    os.rename(tmp, path)
    try:
        history.prune_snapshots(retention_days, keep=path, directory=directory)
    except OSError:
        pass
    return path


def load_json_range(directory: Path, start: datetime | None, end: datetime | None) -> list[dict[str, Any]]:
    """Files in ``directory`` whose *filename* time falls in ``[start, end]``, oldest first.

    Selecting on the name keeps a 24-hour question from parsing 90 days of JSON.
    """
    if not directory.is_dir():
        return []
    picked: list[tuple[datetime, Path]] = []
    for entry in directory.iterdir():
        if entry.suffix.lower() != ".json" or entry.name == "latest.json":
            continue
        when = history._snapshot_file_time(entry.name)
        if when is None or (start and when < start) or (end and when > end):
            continue
        picked.append((when, entry))
    rows: list[dict[str, Any]] = []
    for when, entry in sorted(picked):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            data.setdefault("_file_time", when.isoformat())
            rows.append(data)
    return rows


def load_latest_ledger() -> dict[str, Any] | None:
    directory = ledger_dir()
    if not directory.is_dir():
        return None
    names = sorted(
        (e for e in directory.iterdir() if e.suffix == ".json" and history._snapshot_file_time(e.name)),
        reverse=True,
    )
    for entry in names[:3]:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            return data
    return None


def ledger_delta(prev: dict[str, Any], cur: dict[str, Any]) -> list[dict[str, Any]]:
    """Tokens spent between two samples, per (client, model, provider).

    Per day, ``cur - prev``; a day ``prev`` never saw started from zero. A
    negative difference (a session file was deleted between samples) is
    clamped to zero rather than reported as tokens un-spent.
    """
    prev_days: dict[str, Any] = prev.get("days") if isinstance(prev.get("days"), dict) else {}  # type: ignore[assignment]
    cur_days: dict[str, Any] = cur.get("days") if isinstance(cur.get("days"), dict) else {}  # type: ignore[assignment]
    totals: dict[tuple[str, str, str], dict[str, float]] = {}
    for day, rows in cur_days.items():
        before = {(r["client"], r["model"], r["provider"]): r for r in prev_days.get(day, []) if isinstance(r, dict)}
        for row in rows:
            key = (row["client"], row["model"], row["provider"])
            base = before.get(key, {})
            bucket = totals.setdefault(key, dict.fromkeys(_TOKEN_FIELDS, 0.0))
            for field in _TOKEN_FIELDS:
                bucket[field] += max(0.0, float(row.get(field) or 0) - float(base.get(field) or 0))
    out: list[dict[str, Any]] = []
    for (client, model, provider), bucket in totals.items():
        if not any(bucket.values()):
            continue
        row = {"client": client, "model": model, "provider": provider}
        row.update({f: (round(v, 6) if f == "cost" else int(v)) for f, v in bucket.items()})
        out.append(row)
    return out


def _mapped(key: str, overrides: dict[str, Any] | None) -> str | None:
    for pattern, target in (overrides or {}).items():
        if fnmatch.fnmatchcase(key, str(pattern)):
            return canonical_provider(str(target))
    return None


def tokscale_row_provider(row: dict[str, Any], overrides: dict[str, Any] | None = None) -> str | None:
    """The aiuse provider whose quota a tokscale row most plausibly drew on.

    ``overrides`` (``attribution.provider_map``) match ``client/provider``.
    None means "cannot tell": a router client such as Hermes reaches a vendor
    through whichever credential it was configured with, so only its
    unambiguous upstreams are mapped by default.
    """
    client, provider = str(row.get("client") or ""), str(row.get("provider") or "")
    hit = _mapped(f"{client}/{provider}", overrides)
    if hit:
        return hit
    if client in _TOKSCALE_CLIENT_MAP:
        return _TOKSCALE_CLIENT_MAP[client]
    # tokscale joins several upstreams with ", " when a model moved between them.
    for part in (p.strip() for p in provider.split(",")):
        if part in _TOKSCALE_PROVIDER_MAP:
            return _TOKSCALE_PROVIDER_MAP[part]
    return None


def litellm_settings(config: dict[str, Any] | None) -> dict[str, Any] | None:
    section = (config or {}).get("attribution")
    litellm = section.get("litellm") if isinstance(section, dict) else None
    if not isinstance(litellm, dict) or not str(litellm.get("database_url") or "").strip():
        return None
    return litellm


def litellm_row_provider(row: dict[str, Any], overrides: dict[str, Any] | None = None) -> str | None:
    """aiuse provider for a LiteLLM spend row; ``overrides`` match the model group."""
    hit = _mapped(str(row.get("model") or ""), overrides)
    if hit:
        return hit
    upstream = str(row.get("upstream") or "")
    if upstream.startswith("clinepass/"):
        return "clinepass"
    base = str(row.get("api_base") or "")
    host = urlsplit(base).hostname or ""
    if host == "opencode.ai":
        return "opencode-go" if "/zen/go" in base else "opencode-zen"
    return _API_HOST_MAP.get(host)


# One row per minute, client and route. psql substitutes :'start' / :'stop'
# as quoted literals, so the range never reaches the server as string-built SQL.
_LITELLM_SQL = """
select coalesce(json_agg(t), '[]'::json) from (
  select to_char(date_trunc('minute', "startTime"), 'YYYY-MM-DD"T"HH24:MI:00"+00:00"') as minute,
         coalesce(nullif(metadata->>'user_api_key_alias', ''),
                  'unkeyed:' || coalesce(nullif(regexp_replace(request_tags->>0, '^User-Agent: ', ''), ''), 'unknown')
         ) as client,
         coalesce(nullif(model_group, ''), nullif(model, ''), 'unknown') as model,
         max(model) as upstream,
         max(api_base) as api_base,
         count(*) as requests,
         count(*) filter (where status is distinct from 'success') as failures,
         coalesce(sum(prompt_tokens), 0) as input,
         coalesce(sum(completion_tokens), 0) as output,
         coalesce(sum(nullif(metadata->'usage_object'->'completion_tokens_details'->>'reasoning_tokens', '')::bigint), 0)
           as reasoning
  from "LiteLLM_SpendLogs"
  where "startTime" >= :'start'::timestamp and "startTime" < :'stop'::timestamp
  group by 1, 2, 3
) t;
"""


def query_litellm_ledger(
    settings: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    timeout: float = 45.0,
    run: Callable[..., Any] = subprocess.run,
) -> list[dict[str, Any]]:
    """Per-minute request and token totals from a LiteLLM proxy's spend log."""
    psql = str(settings.get("psql") or "psql")

    def naive_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    argv = [
        psql,
        "-X",
        "-At",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        f"start={naive_utc(start)}",
        "-v",
        f"stop={naive_utc(end)}",
        "-d",
        str(settings["database_url"]),
        "-f",
        "-",
    ]
    try:
        proc = run(argv, input=_LITELLM_SQL, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise CollectorError(f"command not found: {psql}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorError(f"timed out after {timeout}s querying the LiteLLM spend log") from exc
    if proc.returncode != 0:
        raise CollectorError(f"LiteLLM spend log query failed: {(proc.stderr or '').strip()[:300]}")
    try:
        rows = json.loads((proc.stdout or "").strip() or "[]")
    except json.JSONDecodeError as exc:
        raise CollectorError("LiteLLM spend log query returned invalid JSON") from exc
    return [r for r in rows if isinstance(r, dict)]
