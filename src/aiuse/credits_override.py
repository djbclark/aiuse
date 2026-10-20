"""Operator-supplied credit balances, and the rule for which reading of the omp row wins.

CodexBar reports the Claude API credits (``omp``) from claude.ai's cloud-credits row, which
can say "100% unused" while the Anthropic console shows the grant half spent. The Anthropic
Admin API has no credit-balance endpoint (its cost report is cost-to-date only), so the
operator records the console figures with ``aiuse set-credits``. The override is applied at
read time, on the snapshot dict, so a cached snapshot is corrected too.

Which reading wins, per account:

1. an override that is at least as new as the CodexBar reading wins;
2. a newer CodexBar reading that shows MORE credits than the override is contradicted (a
   grant never grows), so the override still wins and the CodexBar reading is kept under
   ``superseded_reading``;
3. a newer CodexBar reading that shows the same or less wins (credits were spent since);
4. a CodexBar reading older than 24 h with no override that supersedes it is expired:
   its balance and percentages are dropped, so the pool is ``unknown`` and not routable.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiuse.analysis import history
from aiuse.models import parse_dt, utcnow

OVERRIDE_PROVIDER = "omp"
OVERRIDE_SOURCE = "operator-override"
CODEXBAR_MAX_AGE = timedelta(hours=24)
_EPSILON = 0.005


def overrides_path() -> Path:
    return history.snapshot_dir().parent / "credit-overrides.json"


def load_overrides() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(overrides_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def save_override(
    provider: str,
    *,
    remaining: float,
    total: float,
    expires: datetime | None,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    entry = {
        "remaining_usd": remaining,
        "total_usd": total,
        "expires_at": expires.isoformat() if expires else None,
        "note": note,
        "observed_at": (now or utcnow()).isoformat(),
    }
    path = overrides_path()
    data = load_overrides()
    data[provider] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return entry


def parse_expiry(text: str) -> datetime:
    """``2026-11-08`` (midnight UTC) or a full ISO timestamp."""
    parsed = parse_dt(text)
    if parsed is None:
        raise ValueError(f"cannot parse {text!r} as an ISO date or datetime")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _credits_window(total: float, remaining: float, expires: str | None) -> dict[str, Any]:
    pct = max(0.0, min(100.0, remaining / total * 100.0)) if total > 0 else None
    return {
        "label": "credits",
        "used_percent": None if pct is None else 100.0 - pct,
        "remaining_percent": pct,
        "resets_at": expires,
        "reset_description": f"${remaining:.2f} of ${total:.2f} left, expires",
        "refill_capacity": total,
        "refill_capacity_unit": "usd",
        "window_id": "claude-cloud-credits",
    }


def apply_credit_overrides(
    snap: dict[str, Any],
    overrides: Mapping[str, Mapping[str, Any]] | None,
    *,
    now: datetime | None = None,
) -> None:
    """Rewrite the omp account(s) of a snapshot dict in place (see the module doc)."""
    now = now or utcnow()
    override: Mapping[str, Any] = (overrides or {}).get(OVERRIDE_PROVIDER) or {}
    for account in snap.get("accounts") or []:
        if not isinstance(account, dict) or account.get("provider") != OVERRIDE_PROVIDER:
            continue
        if account.get("source") == OVERRIDE_SOURCE:
            continue
        reading_at = parse_dt(account.get("collected_at") or snap.get("collected_at"))
        balance = account.get("balance_usd")
        ov_at = parse_dt(override.get("observed_at"))
        ov_remaining = override.get("remaining_usd")
        ov_total = override.get("total_usd")
        if (
            ov_at is not None
            and isinstance(ov_remaining, (int, float))
            and isinstance(ov_total, (int, float))
            and (
                reading_at is None
                or ov_at >= reading_at
                or balance is None
                or float(balance) > float(ov_remaining) + _EPSILON
            )
        ):
            account["superseded_reading"] = {
                "source": account.get("source"),
                "observed_at": reading_at.isoformat() if reading_at else None,
                "balance_usd": balance,
            }
            expires = override.get("expires_at") or account.get("expires_at")
            account.update(
                source=OVERRIDE_SOURCE,
                collector_id=OVERRIDE_SOURCE,
                balance_usd=float(ov_remaining),
                spent_usd=round(max(0.0, float(ov_total) - float(ov_remaining)), 6),
                windows=[_credits_window(float(ov_total), float(ov_remaining), expires)],
                collected_at=ov_at.isoformat(),
                expires_at=expires,
                error=None,
            )
            account["billing_kind"] = "subscription_window"
            note = f"Operator override (aiuse set-credits) set {ov_at.isoformat()}"
            if override.get("note"):
                note += f": {override['note']}"
            account["notes"] = [*(account.get("notes") or []), note]
            reading_at = ov_at
        elif reading_at is not None and now - reading_at > CODEXBAR_MAX_AGE:
            for window in account.get("windows") or []:
                if isinstance(window, dict):
                    window["used_percent"] = None
                    window["remaining_percent"] = None
            account["balance_usd"] = None
            account["spent_usd"] = None
            account["reading_expired"] = True
            account["notes"] = [
                *(account.get("notes") or []),
                f"{account.get('source')} reading is older than 24h: balance unknown; "
                "record the console figure with `aiuse set-credits omp`.",
            ]
        account["observed_at"] = reading_at.isoformat() if reading_at else None


def run_set_credits(argv: list[str]) -> int:
    """CLI entry: ``aiuse set-credits omp --remaining 95.57 --total 200 --expires 2026-11-08 --note ...``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="aiuse set-credits",
        description="Record the console's credit balance for a pool CodexBar misreads (omp = Claude API credits).",
    )
    parser.add_argument("provider", choices=[OVERRIDE_PROVIDER])
    parser.add_argument("--remaining", type=float, required=True, metavar="USD", help="dollars left")
    parser.add_argument("--total", type=float, required=True, metavar="USD", help="dollars granted")
    parser.add_argument("--expires", metavar="DATE", help="grant expiry, YYYY-MM-DD or ISO datetime (UTC)")
    parser.add_argument("--note", default=None, help='free text, e.g. "console 2026-10-19"')
    args = parser.parse_args(argv)
    if args.total <= 0 or not 0 <= args.remaining <= args.total:
        print("error: need 0 <= --remaining <= --total and --total > 0", file=sys.stderr)
        return 1
    expires = None
    if args.expires:
        try:
            expires = parse_expiry(args.expires)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    save_override(args.provider, remaining=args.remaining, total=args.total, expires=expires, note=args.note)
    print(
        f"recorded: {args.provider} ${args.remaining:.2f} of ${args.total:.2f} left"
        f"{', expires ' + expires.date().isoformat() if expires else ''} ({overrides_path()})"
    )
    return 0
