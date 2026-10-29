"""Hold AWS Bedrock's Cost Explorer read.

CodexBar's Bedrock provider calls ``ce:GetCostAndUsage``, which AWS bills at
$0.01 per request. The operator uses other models whenever Bedrock is in use,
so a new reading is worth paying for only when some other quota has moved,
and at most once every six hours. The first reading is always taken, so a
month-to-date figure appears. A failed call is stamped too: a Cost Explorer
error must not retry on every collect.

The store keeps the dollars and the time, never the CodexBar payload (that
payload can carry an account email).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiuse.analysis.history import snapshot_dir
from aiuse.models import canonical_provider, parse_dt

from .base import CollectorError

MIN_INTERVAL_SECONDS = 6 * 60 * 60
_DEFAULT_STORE = "~/.local/state/aiuse/bedrock-spend.json"
_SCHEMA = 1


def store_path() -> Path:
    override = os.environ.get("AIUSE_BEDROCK_STORE")
    if override:
        return Path(override)
    return Path(os.path.expanduser(_DEFAULT_STORE))


def snapshot_path() -> Path:
    override = os.environ.get("AIUSE_BEDROCK_SNAPSHOT")
    if override:
        return Path(override)
    return snapshot_dir() / "latest.json"


def _empty_store() -> dict[str, Any]:
    return {"schema_version": _SCHEMA}


def load_store(path: str | Path | None = None) -> dict[str, Any]:
    """The spend store. A missing or corrupt file reads as empty."""
    target = Path(path) if path is not None else store_path()
    try:
        data = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError):
        return _empty_store()
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA:
        return _empty_store()
    return data


def _write_store(store: dict[str, Any], path: str | Path | None) -> None:
    target = Path(path) if path is not None else store_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(store, indent=1, sort_keys=True) + "\n", "utf-8")
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except OSError:
        return


def _float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def activity_fingerprint(payload: Any) -> str:
    """Hash of every non-Bedrock meter on the previous board.

    A missing or unreadable snapshot is ``""``. Bedrock's own row is ignored,
    so a spend change cannot justify another Cost Explorer call. An empty
    board hashes to ``""`` as well: nothing else moved.
    """
    if not isinstance(payload, dict):
        return ""
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return ""
    rows: list[list[Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        provider = canonical_provider(str(account.get("provider") or ""))
        if provider == "bedrock":
            continue
        windows_value = account.get("windows")
        windows = windows_value if isinstance(windows_value, list) else []
        used = [window.get("used_percent") for window in windows if isinstance(window, dict)]
        rows.append(
            [
                provider,
                account.get("account"),
                account.get("balance_usd"),
                account.get("spent_usd"),
                used,
            ]
        )
    if not rows:
        return ""
    rows.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    raw = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_activity_fingerprint(path: str | Path | None = None) -> str:
    target = Path(path) if path is not None else snapshot_path()
    try:
        payload = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    return activity_fingerprint(payload)


def refresh_due(store: dict[str, Any], fingerprint: str, now: datetime) -> bool:
    """True when a new $0.01 read is allowed.

    No stored read yet: take one. Younger than six hours: hold. A stored
    read that failed (no ``spent_usd``): try again once the six hours have
    passed. A stored success: try again only when some other meter differs
    from the fingerprint saved with that success.
    """
    fetched = parse_dt(store.get("fetched_at") if isinstance(store.get("fetched_at"), str) else None)
    if fetched is None:
        return True
    if (now - fetched).total_seconds() < MIN_INTERVAL_SECONDS:
        return False
    if _float(store.get("spent_usd")) is None:
        return True
    return store.get("fingerprint") != fingerprint


def _row(spent: float, limit: float | None, *, read_at: datetime, updated_at: datetime) -> dict[str, Any]:
    cost: dict[str, Any] = {
        "used": spent,
        "limit": 0 if limit is None else limit,
        "currencyCode": "USD",
        "period": "Monthly",
    }
    return {
        "provider": "bedrock",
        "source": "codexbar",
        "usage": {
            "loginMethod": "Spend: ${:.2f}".format(spent),
            "providerCost": cost,
            "updatedAt": updated_at.isoformat(),
            "bedrockReadAt": read_at.isoformat(),
        },
    }


def _spend_from_payload(outcome: Any) -> tuple[float, float | None] | None:
    rows = outcome if isinstance(outcome, list) else [outcome]
    for row in rows:
        if not isinstance(row, dict) or row.get("error"):
            continue
        usage_value = row.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        cost_value = usage.get("providerCost")
        cost = cost_value if isinstance(cost_value, dict) else {}
        used = _float(cost.get("used"))
        if used is None:
            continue
        limit = _float(cost.get("limit"))
        return used, limit if limit is not None and limit > 0 else None
    return None


def _stamp_rows(outcome: Any, *, read_at: datetime, updated_at: datetime) -> Any:
    rows = outcome if isinstance(outcome, list) else [outcome]
    for row in rows:
        if not isinstance(row, dict):
            continue
        usage = row.get("usage")
        if not isinstance(usage, dict):
            usage = {}
            row["usage"] = usage
        # The board's freshness clock is this hold decision. The Cost Explorer
        # time stays on bedrockReadAt so a held number can still say when it
        # was measured.
        usage["updatedAt"] = updated_at.isoformat()
        usage.setdefault("bedrockReadAt", read_at.isoformat())
    return outcome


def _stored_read_at(store: dict[str, Any]) -> datetime | None:
    """When Cost Explorer last measured the spend. Independent of the last attempt."""
    for key in ("read_at", "fetched_at"):
        stamp = store.get(key)
        parsed = parse_dt(stamp if isinstance(stamp, str) else None)
        if parsed is not None and _float(store.get("spent_usd")) is not None:
            return parsed
    return None


def cached_payload(now: datetime | None = None, path: str | Path | None = None) -> list[dict[str, Any]] | None:
    """The last good spend, restamped to ``now``, or None when we have none."""
    store = load_store(path)
    spent = _float(store.get("spent_usd"))
    read_at = _stored_read_at(store)
    if spent is None or read_at is None:
        return None
    limit = _float(store.get("limit_usd"))
    moment = now or datetime.now(timezone.utc)
    return [_row(spent, limit if limit is not None and limit > 0 else None, read_at=read_at, updated_at=moment)]


def _remember_failure(store: dict[str, Any], *, now: datetime, message: str, path: str | Path | None) -> None:
    store["schema_version"] = _SCHEMA
    store["fetched_at"] = now.isoformat()
    store["error"] = message[:300]
    _write_store(store, path)


def gated_bedrock_payload(
    live: Callable[[], Any],
    *,
    now: datetime | None = None,
    store: str | Path | None = None,
    snapshot: str | Path | None = None,
) -> Any:
    """Return Bedrock rows, calling ``live`` only when :func:`refresh_due` says so."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    saved = load_store(store)
    fingerprint = load_activity_fingerprint(snapshot)
    if not refresh_due(saved, fingerprint, moment):
        held = cached_payload(moment, store)
        if held is not None:
            return held
        if saved.get("error"):
            return CollectorError(str(saved["error"]))
        return CollectorError("Bedrock spend unavailable")

    try:
        outcome = live()
    except CollectorError as exc:
        outcome = exc
    spend = None if isinstance(outcome, CollectorError) else _spend_from_payload(outcome)
    if spend is None:
        message = str(outcome) if isinstance(outcome, CollectorError) else "CodexBar returned no Bedrock spend"
        _remember_failure(saved, now=moment, message=message, path=store)
        held = cached_payload(moment, store)
        if held is not None:
            return held
        return outcome if isinstance(outcome, CollectorError) else CollectorError(message)

    spent, limit = spend
    read_at = moment
    rows = outcome if isinstance(outcome, list) else [outcome]
    for row in rows:
        if not isinstance(row, dict):
            continue
        usage_value = row.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        updated = usage.get("updatedAt")
        stamped = parse_dt(updated if isinstance(updated, str) else None)
        if stamped is not None:
            read_at = stamped
            break
    _write_store(
        {
            "schema_version": _SCHEMA,
            "fetched_at": moment.isoformat(),
            "read_at": read_at.isoformat(),
            "fingerprint": fingerprint,
            "spent_usd": spent,
            "limit_usd": limit,
        },
        store,
    )
    return _stamp_rows(outcome, read_at=read_at, updated_at=moment)
