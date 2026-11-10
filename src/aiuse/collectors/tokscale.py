"""Collect subscription usage from tokscale (complementary to codexbar)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aiuse.analysis import history
from aiuse.models import (
    WINDOW_NOMINAL_MINUTES,
    AccountUsage,
    BillingKind,
    QuotaWindow,
    coerce_float,
    keep_copilot_report_window,
    parse_dt,
    utcnow,
)

from .base import CollectorError, CollectorNotInstalled, run_json, which
from .throttle import QueryGate


def default_tokscale_cache_path() -> Path:
    return history.snapshot_dir().parent / "tokscale-usage-cache.json"


def _read_tokscale_cache(
    cache_path: Path | str | None,
    max_age: float,
) -> tuple[Any, datetime] | None:
    path = Path(cache_path).expanduser() if cache_path else default_tokscale_cache_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    fetched_at = parse_dt(data.get("fetched_at"))
    if not fetched_at:
        return None
    age = (utcnow() - fetched_at).total_seconds()
    if age < 0 or age > max_age:
        return None
    payload = data.get("payload")
    if payload is None:
        return None
    return payload, fetched_at


def _write_tokscale_cache(
    cache_path: Path | str | None,
    payload: Any,
    fetched_at: datetime,
) -> None:
    path = Path(cache_path).expanduser() if cache_path else default_tokscale_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        data = {
            "schema_version": "1.0",
            "fetched_at": fetched_at.isoformat(),
            "payload": payload,
        }
        tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _parse_payload(payload: Any, *, cached_at: datetime | None = None) -> list[AccountUsage]:
    rows = payload if isinstance(payload, list) else [payload]
    accounts: list[AccountUsage] = []
    for row in rows:
        if isinstance(row, dict):
            accounts.append(_from_row(row, cached_at=cached_at))
    return accounts


def collect_tokscale(
    *,
    timeout: float = 45.0,
    use_cache: bool = True,
    max_cache_age: float = 600.0,
    cache_path: Path | str | None = None,
    min_interval: float = 600.0,
    run: Callable[..., Any] = run_json,
) -> list[AccountUsage]:
    if not which("tokscale"):
        raise CollectorNotInstalled("tokscale not found on PATH")

    now = utcnow()
    if use_cache and max_cache_age > 0:
        cached = _read_tokscale_cache(cache_path, max_cache_age)
        if cached is not None:
            payload, fetched_at = cached
            return _parse_payload(payload, cached_at=fetched_at)

    gate_interval = max(0.0, min_interval)
    with QueryGate("tokscale", min_interval=gate_interval, wait=min(timeout, 15.0)) as gate:
        if gate.allowed:
            payload = run(["tokscale", "usage", "--json"], timeout=timeout)
            gate.record("tokscale", payload)
            _write_tokscale_cache(cache_path, payload, now)
            return _parse_payload(payload, cached_at=None)

        reused = gate.reuse("tokscale")
        if not isinstance(reused, BaseException) and reused is not None:
            return _parse_payload(reused, cached_at=gate.reused_from("tokscale") or now)
        if isinstance(reused, BaseException):
            raise reused
        raise CollectorError(gate.describe())


def _from_row(row: dict[str, Any], *, cached_at: datetime | None = None) -> AccountUsage:
    provider = str(row.get("provider") or "unknown")
    # Normalize for merge/dedupe with codexbar
    provider_key = provider.lower().replace(" ", "-")

    windows: list[QuotaWindow] = []
    for m in row.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        used = coerce_float(m.get("used_percent"))
        remaining = coerce_float(m.get("remaining_percent"))
        if remaining is None and used is not None:
            remaining = max(0.0, 100.0 - used)
        label = _metric_label(provider_key, str(m.get("label") or "unnamed quota"))
        if provider_key == "copilot" and not keep_copilot_report_window(label):
            continue
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used,
                remaining_percent=remaining,
                window_minutes=_infer_window_minutes(provider_key, m.get("label", ""), label),
                resets_at=parse_dt(m.get("resets_at")),
                reset_description=m.get("remaining_label"),
                raw=m,
            )
        )

    notes: list[str] = [
        f"Data cached from tokscale ({cached_at.isoformat()})."
        if cached_at is not None
        else "Live data fetched by tokscale for selection and cross-checking."
    ]
    credit_status = row.get("credit_status")
    if isinstance(credit_status, dict):
        if credit_status.get("balance") is not None:
            notes.append(f"credit_balance={credit_status.get('balance')}")
        if credit_status.get("has_credits"):
            notes.append("has_credits=true")
        if credit_status.get("overage_limit_reached"):
            notes.append("overage_limit_reached")

    reset_credits = row.get("reset_credits")
    if isinstance(reset_credits, dict) and reset_credits.get("available_count") is not None:
        notes.append(f"reset_credits={reset_credits['available_count']}")

    return AccountUsage(
        source="tokscale",
        provider=provider_key,
        account=row.get("email"),
        plan=row.get("plan"),
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW if windows else BillingKind.UNKNOWN,
        windows=windows,
        notes=notes,
        raw=row,
    )


def _metric_label(provider: str, label: str) -> str:
    key = label.lower()
    if provider == "codex" and key == "weekly":
        return "Codex weekly quota"
    if provider == "copilot":
        return {
            "chat": "GitHub Copilot chat messages",
            "completions": "GitHub Copilot completions",
            "premium": "GitHub Copilot premium requests",
            "weekly": "GitHub Copilot weekly quota",
            "7-day": "GitHub Copilot weekly quota",
            "session": "GitHub Copilot session quota",
        }.get(key, f"GitHub Copilot {label}")
    if provider == "grok-build" and key == "weekly":
        return "Grok weekly quota"
    return f"{provider.replace('-', ' ').title()} {label}"


def _infer_window_minutes(provider: str, raw_label: str, display_label: str) -> int | None:
    key = (raw_label or "").lower().strip()
    provider_key = (provider or "").lower().replace(" ", "-")
    # Copilot label vocabulary only — do not apply to other providers.
    if provider_key == "copilot":
        if key in ("session", "5h", "5-hour", "5 hour") or "session" in key or "5-hour" in key:
            return WINDOW_NOMINAL_MINUTES["5h"]
        if key in ("weekly", "7-day", "7d") or "week" in key or "7-day" in key:
            return WINDOW_NOMINAL_MINUTES["weekly"]
        if key in ("chat", "completions", "premium"):
            return WINDOW_NOMINAL_MINUTES["monthly"]
    if key in ("session", "5h", "5-hour", "5 hour") or "5-hour" in key or "5 hour" in key:
        return WINDOW_NOMINAL_MINUTES["5h"]
    # Avoid matching "15h" / "45h" via bare "5h" substring.
    if re.search(r"(?<![0-9])5h(?![0-9a-z])", key):
        return WINDOW_NOMINAL_MINUTES["5h"]
    if key in ("weekly",) or "week" in key:
        return WINDOW_NOMINAL_MINUTES["weekly"]
    if provider_key == "copilot":
        return WINDOW_NOMINAL_MINUTES["monthly"]
    return None
