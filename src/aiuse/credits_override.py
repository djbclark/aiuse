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
   (When the snapshot has no omp row at all, the override alone makes the row.)
3. a newer CodexBar reading that shows the same or less wins (credits were spent since);
   An override is ignored once its ``expires_at`` has passed (a lapsed grant) and once it is
   older than 7 days (``OVERRIDE_MAX_AGE``; refresh it with ``aiuse set-credits``); the
   CodexBar rules then apply alone. An ``observed_at`` in the future is invalid. Anything
   malformed (non-finite or non-numeric figures, remaining > total, bad timestamps, wrong pool
   name) is ignored with one stderr line and never raises. A hidden or disabled omp pool is never
   rebuilt or rewritten.
4. a CodexBar reading older than 24 h with no override that supersedes it is expired:
   its balance and percentages are dropped, so the pool is ``unknown`` and not routable.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiuse.analysis import history
from aiuse.analysis.pool_keys import parse_pool_rule_key
from aiuse.models import parse_dt, provider_cli_binary, utcnow

OVERRIDE_PROVIDER = "omp"
OVERRIDE_SOURCE = "operator-override"
CODEXBAR_MAX_AGE = timedelta(hours=24)
OVERRIDE_MAX_AGE = timedelta(days=7)
_CLOCK_SKEW = timedelta(minutes=5)
_EPSILON = 0.005
_KEYS = ("remaining_usd", "total_usd", "expires_at", "note", "observed_at")


MAX_DOLLARS = 1e9
MAX_FILE_BYTES = 64 * 1024


def _number(value: Any) -> float | None:
    """A finite int/float within +-MAX_DOLLARS (never bool, NaN, Infinity or a huge int), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    return number if math.isfinite(number) and abs(number) <= MAX_DOLLARS else None


def _timestamp(value: Any) -> datetime | None:
    return parse_dt(value) if isinstance(value, str) else None


def clean_override(raw: Any, *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one override entry: ``(entry, None)`` or ``(None, why it is ignored)``.

    Structure and numbers are always checked; with ``now`` the time rules are too (not yet
    observed, past ``expires_at``, older than ``OVERRIDE_MAX_AGE``).
    """
    if not isinstance(raw, Mapping):
        return None, "not an object"
    total = _number(raw.get("total_usd"))
    remaining = _number(raw.get("remaining_usd"))
    if total is None or remaining is None:
        return None, "remaining_usd/total_usd must be finite numbers"
    if total <= 0 or not 0 <= remaining <= total:
        return None, "need total_usd > 0 and 0 <= remaining_usd <= total_usd"
    observed = _timestamp(raw.get("observed_at"))
    if observed is None:
        return None, "observed_at must be an ISO timestamp string"
    expires_raw = raw.get("expires_at")
    expires = _timestamp(expires_raw)
    if expires_raw is not None and expires is None:
        return None, "expires_at must be an ISO timestamp string or null"
    note = raw.get("note")
    if now is not None:
        if observed > now + _CLOCK_SKEW:
            return None, "observed_at is in the future"
        if now - observed > OVERRIDE_MAX_AGE:
            return None, "older than 7 days; refresh it with aiuse set-credits"
        if expires is not None and now >= expires:
            return None, "the grant's expires_at has passed"
    return {
        "remaining_usd": remaining,
        "total_usd": total,
        "expires_at": expires.isoformat() if expires else None,
        "note": note if isinstance(note, str) else None,
        "observed_at": observed.isoformat(),
        "source": "admin-api" if raw.get("source") == "admin-api" else OVERRIDE_SOURCE,
    }, None


def overrides_path() -> Path:
    return history.snapshot_dir().parent / "credit-overrides.json"


def _config_hides_omp(config: Mapping[str, Any] | None) -> bool:
    """The operator hid or disabled omp in config: no override output at all."""
    for section in ("hidden_pools", "disabled_services"):
        mapping = (config or {}).get(section)
        if isinstance(mapping, Mapping) and any(
            parse_pool_rule_key(str(key))[0] == OVERRIDE_PROVIDER for key in mapping
        ):
            return True
    return False


def load_overrides(config: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """The validated overrides; empty when ``config`` hides or disables omp. Never raises."""
    if _config_hides_omp(config):
        return {}
    try:
        with overrides_path().open("rb") as handle:
            blob = handle.read(MAX_FILE_BYTES + 1)
        if len(blob) > MAX_FILE_BYTES:
            print("aiuse: ignoring credit overrides: file larger than 64 KiB", file=sys.stderr)
            return {}
        data = json.loads(blob.decode("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError) as exc:
        print(f"aiuse: ignoring unreadable credit overrides ({exc.__class__.__name__})", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print("aiuse: ignoring credit overrides: not a JSON object", file=sys.stderr)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for provider, raw in data.items():
        if provider != OVERRIDE_PROVIDER:
            print(f"aiuse: ignoring credit override for unknown pool {str(provider)[:40]!r}", file=sys.stderr)
            continue
        entry, why = _safe_clean(raw)
        if entry is None:
            print(f"aiuse: ignoring {provider} credit override: {why}", file=sys.stderr)
            continue
        out[provider] = entry
    return out


def _safe_clean(raw: Any, *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """``clean_override`` that can never raise: a malformed file is never fatal."""
    try:
        return clean_override(raw, now=now)
    except Exception as exc:  # noqa: BLE001 - hostile input must only ever mean "ignored"
        return None, f"unreadable ({exc.__class__.__name__})"


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


def _omp_hidden(snap: Mapping[str, Any]) -> bool:
    """omp is hidden or disabled by the operator: the override must not bring it back."""
    for section in ("hidden_pools", "disabled_services"):
        mapping = snap.get(section)
        if isinstance(mapping, Mapping) and any(
            parse_pool_rule_key(str(key))[0] == OVERRIDE_PROVIDER for key in mapping
        ):
            return True
    return False


def _grant_expiry(account: Mapping[str, Any]) -> datetime | None:
    """The effective grant expiry: ``expires_at``, else the usd credits window's reset."""
    expiry = parse_dt(account.get("expires_at"))
    if expiry is None:
        for window in account.get("windows") or []:
            if isinstance(window, dict) and window.get("refill_capacity_unit") == "usd":
                expiry = parse_dt(window.get("resets_at"))
                break
    return expiry


def _expire_reading(account: dict[str, Any], why: str = "older than 24h") -> None:
    for window in account.get("windows") or []:
        if isinstance(window, dict):
            window["used_percent"] = None
            window["remaining_percent"] = None
    account["balance_usd"] = None
    account["spent_usd"] = None
    account["reading_expired"] = True
    account["notes"] = [
        *(account.get("notes") or []),
        f"{account.get('source')} reading {why}: balance unknown; "
        "record the console figure with `aiuse set-credits omp`.",
    ]


def apply_credit_overrides(
    snap: dict[str, Any],
    overrides: Mapping[str, Mapping[str, Any]] | None,
    *,
    now: datetime | None = None,
) -> None:
    """Rewrite the omp account(s) of a snapshot dict in place (see the module doc)."""
    now = now or utcnow()
    if _omp_hidden(snap):
        return
    override: Mapping[str, Any] = {}
    ignored: str | None = None
    if (overrides or {}).get(OVERRIDE_PROVIDER) is not None:
        cleaned, ignored = _safe_clean(overrides[OVERRIDE_PROVIDER], now=now)  # type: ignore[index]
        override = cleaned or {}
    accounts = snap.get("accounts")
    if (
        override
        and override.get("source") == "admin-api"
        and isinstance(accounts, list)
        and not any(isinstance(a, dict) and a.get("provider") == OVERRIDE_PROVIDER for a in accounts)
    ):
        # An Admin API reading is collector output, so it may make the row when the snapshot
        # has none. An operator override only decorates a row the collectors produced.
        accounts.append(
            {
                "source": "none",
                "provider": OVERRIDE_PROVIDER,
                "cli_binary": provider_cli_binary(OVERRIDE_PROVIDER),
                "billing_kind": "subscription_window",
                "windows": [],
                "credits_label": "Claude API credits",
                "notes": [],
                "error": None,
            }
        )
    for account in snap.get("accounts") or []:
        if not isinstance(account, dict) or account.get("provider") != OVERRIDE_PROVIDER:
            continue
        if account.get("source") in (OVERRIDE_SOURCE, "admin-api"):
            continue
        if ignored:
            account["notes"] = [*(account.get("notes") or []), f"credit override ignored: {ignored}"]
        reading_at = parse_dt(account.get("collected_at") or snap.get("collected_at"))
        balance = account.get("balance_usd")
        ov_at = parse_dt(override.get("observed_at"))
        ov_remaining = override.get("remaining_usd")
        ov_total = override.get("total_usd")
        capacity = next(
            (
                w.get("refill_capacity")
                for w in account.get("windows") or []
                if isinstance(w, dict) and w.get("refill_capacity_unit") == "usd"
            ),
            None,
        )
        # "A grant never grows" only proves a fresher CodexBar reading wrong when both measure
        # the same grant: the override's total must match CodexBar's. Otherwise fresher wins.
        same_grant = isinstance(capacity, (int, float)) and abs(float(capacity) - float(ov_total or 0)) <= _EPSILON
        if (
            ov_at is not None
            and isinstance(ov_remaining, (int, float))
            and isinstance(ov_total, (int, float))
            and (
                reading_at is None
                or ov_at >= reading_at
                or balance is None
                or (same_grant and float(balance) > float(ov_remaining) + _EPSILON)
            )
        ):
            if account.get("source") != "none":
                account["superseded_reading"] = {
                    "source": account.get("source"),
                    "observed_at": reading_at.isoformat() if reading_at else None,
                    "balance_usd": balance,
                }
            expires = override.get("expires_at") or account.get("expires_at")
            account.update(
                source=override.get("source") or OVERRIDE_SOURCE,
                collector_id=override.get("source") or OVERRIDE_SOURCE,
                balance_usd=float(ov_remaining),
                spent_usd=round(max(0.0, float(ov_total) - float(ov_remaining)), 6),
                windows=[_credits_window(float(ov_total), float(ov_remaining), expires)],
                collected_at=ov_at.isoformat(),
                expires_at=expires,
                error=None,
            )
            account["billing_kind"] = "subscription_window"
            note = f"{'Admin API reading' if override.get('source') == 'admin-api' else 'Operator override (aiuse set-credits)'} at {ov_at.isoformat()}"
            if override.get("note"):
                note += f": {override['note']}"
            account["notes"] = [*(account.get("notes") or []), note]
            reading_at = ov_at
        elif override and ov_at is not None and reading_at is not None and reading_at > ov_at:
            # A fresher CodexBar reading wins: say so, and keep what the override claimed.
            account["superseded_override"] = {
                "source": override.get("source", OVERRIDE_SOURCE),
                "observed_at": ov_at.isoformat(),
                "remaining_usd": override.get("remaining_usd"),
            }
            if now - reading_at > CODEXBAR_MAX_AGE:
                _expire_reading(account)
        elif reading_at is not None and now - reading_at > CODEXBAR_MAX_AGE:
            _expire_reading(account)
        # The grant itself lapsing is independent of how fresh the reading is.
        expiry = _grant_expiry(account)
        if expiry is not None and now >= expiry:
            account["grant_expired"] = True
            if not account.get("reading_expired"):
                _expire_reading(account, f"is for a grant that expired {expiry.date().isoformat()}")
        account["observed_at"] = reading_at.isoformat() if reading_at else None


def overrides_signature() -> tuple[tuple[int, int] | None, ...]:
    """(mtime_ns, size) of the figure files, to notice a ``set-credits`` or refresh."""
    out: list[tuple[int, int] | None] = []
    for path in (overrides_path(),):
        try:
            stat = path.stat()
            out.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _finite(text: str) -> float:
    import argparse

    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"not a finite number: {text!r}")
    return value


def run_set_credits(argv: list[str]) -> int:
    """CLI entry: ``aiuse set-credits omp --remaining 95.57 --total 200 --expires 2026-11-08 --note ...``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="aiuse set-credits",
        description="Record the console's credit balance for a pool CodexBar misreads (omp = Claude API credits).",
    )
    parser.add_argument("provider", choices=[OVERRIDE_PROVIDER])
    parser.add_argument("--remaining", type=_finite, required=True, metavar="USD", help="dollars left")
    parser.add_argument("--total", type=_finite, required=True, metavar="USD", help="dollars granted")
    parser.add_argument("--expires", metavar="DATE", help="grant expiry, YYYY-MM-DD or ISO datetime (UTC)")
    parser.add_argument("--note", default=None, help='free text, e.g. "console 2026-10-19"')
    args = parser.parse_args(argv)
    if args.total <= 0 or not 0 <= args.remaining <= args.total:
        print("error: need 0 <= --remaining <= --total and --total > 0", file=sys.stderr)
        return 2
    if args.total > MAX_DOLLARS:
        print(f"error: --total above the supported cap of {MAX_DOLLARS:.0f} dollars", file=sys.stderr)
        return 2
    expires = None
    if args.expires:
        try:
            expires = parse_expiry(args.expires)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    save_override(args.provider, remaining=args.remaining, total=args.total, expires=expires, note=args.note)
    print(
        f"recorded: {args.provider} ${args.remaining:.2f} of ${args.total:.2f} left"
        f"{', expires ' + expires.date().isoformat() if expires else ''} ({overrides_path()})"
    )
    return 0
