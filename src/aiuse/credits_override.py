"""Independent operator/API credit readings for OMP (Claude API) and Pi (OpenAI API).

CodexBar reports the Claude API credits (``omp``) from claude.ai's cloud-credits row, which
can say "100% unused" while the Anthropic console shows the grant half spent. The Anthropic
Admin API has no credit-balance endpoint (its cost report is cost-to-date only), so the
operator records the console figures with ``aiuse set-credits``. The override is applied at
read time, on the snapshot dict, so a cached snapshot is corrected too. Pi uses its own
verified grant/console reading; no Codex subscription quota or balance is imported.

Which reading wins, per account:

1. an override that is at least as new as the CodexBar reading wins;
2. a newer CodexBar reading that shows MORE credits than the override is contradicted (a
   grant never grows), so the override still wins and the CodexBar reading is kept under
   ``superseded_reading``;
   (A verified Pi console entry can create its row. A missing OMP row is created only
   from a recent Admin API reading, not a standalone operator override.)
3. a newer CodexBar reading that shows the same or less wins (credits were spent since);
   An active Admin API grant with a known start and expiry also wins against a differently
   sized or dated CodexBar promotion; observing that promotion does not establish a new grant.
   An override is ignored once its ``expires_at`` has passed (a lapsed grant) and once it is
   older than 7 days (``OVERRIDE_MAX_AGE``; refresh it with ``aiuse set-credits``). An Admin
   API reading (``source: admin-api``) gets the 24 h limit CodexBar readings get; the
   CodexBar rules then apply alone. An ``observed_at`` in the future is invalid. Anything
   malformed (non-finite or non-numeric figures, remaining > total, bad timestamps, wrong pool
   name) is ignored with one stderr line and never raises. A hidden or disabled credit pool is never
   rebuilt or rewritten.
4. a CodexBar reading older than 24 h with no override that supersedes it is expired:
   its balance and percentages are dropped, so the pool is ``unknown`` and not routable.
"""

from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiuse.analysis import history
from aiuse.analysis.pool_keys import parse_pool_rule_key
from aiuse.config import (
    credit_organization_name,
    omp_organization_label,
    organization_names_match,
    plan_monthly_spend_limit,
)
from aiuse.models import (
    WINDOW_NOMINAL_MINUTES,
    AccountUsage,
    Snapshot,
    canonical_provider,
    parse_dt,
    provider_cli_binary,
    utcnow,
)

OVERRIDE_PROVIDER = "omp"  # Backward-compatible default for existing callers.
CREDIT_PROVIDERS = ("omp", "pi")
CREDIT_LABELS = {"omp": "Claude API credits", "pi": "OpenAI API credits"}
CREDIT_WINDOW_IDS = {"omp": "claude-cloud-credits", "pi": "openai-api-credits"}
OVERRIDE_SOURCE = "operator-override"
OMP_OPENAI_SOURCE = "omp_models_yml"
CODEXBAR_MAX_AGE = timedelta(hours=24)
OVERRIDE_MAX_AGE = timedelta(days=7)
_CLOCK_SKEW = timedelta(minutes=5)
_EPSILON = 0.005
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_KEYS = ("remaining_usd", "total_usd", "expires_at", "note", "observed_at")


MAX_DOLLARS = 1e9
MAX_FILE_BYTES = 64 * 1024


def _is_omp_openai_route(account: Mapping[str, Any], provider: str) -> bool:
    return provider == "omp" and account.get("source") == OMP_OPENAI_SOURCE


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
    granted_raw = raw.get("granted_at")
    granted = _timestamp(granted_raw)
    if granted_raw is not None and granted is None:
        return None, "granted_at must be an ISO timestamp string or null"
    anchor_raw = raw.get("anchor_observed_at")
    anchor = _timestamp(anchor_raw)
    if anchor_raw is not None and (anchor is None or anchor > observed):
        return None, "anchor_observed_at must be a timestamp no later than observed_at"
    note = raw.get("note")
    if now is not None:
        if observed > now + _CLOCK_SKEW:
            return None, "observed_at is in the future"
        if raw.get("source") == "admin-api":
            if now - observed > CODEXBAR_MAX_AGE:
                return None, "Admin API reading older than 24h"
        elif now - observed > OVERRIDE_MAX_AGE:
            return None, "older than 7 days; refresh it with aiuse set-credits"
        if expires is not None and now >= expires:
            return None, "the grant's expires_at has passed"
    return {
        "remaining_usd": remaining,
        "total_usd": total,
        "expires_at": expires.isoformat() if expires else None,
        "note": note if isinstance(note, str) else None,
        "observed_at": observed.isoformat(),
        "granted_at": granted.isoformat() if granted else None,
        "source": "admin-api" if raw.get("source") == "admin-api" else OVERRIDE_SOURCE,
        **({"anchor_observed_at": anchor.isoformat()} if anchor else {}),
    }, None


def overrides_path() -> Path:
    return history.snapshot_dir().parent / "credit-overrides.json"


def _config_hides_omp(config: Mapping[str, Any] | None, provider: str = OVERRIDE_PROVIDER) -> bool:
    """The operator hid or disabled this credit provider: no override output."""
    from aiuse.provider_select import blocked_reason, reenabled_by_include

    if blocked_reason(config, provider):
        return True
    for section in ("hidden_pools", "disabled_services"):
        if section == "disabled_services" and reenabled_by_include(config, provider):
            continue
        mapping = (config or {}).get(section)
        if isinstance(mapping, Mapping) and any(parse_pool_rule_key(str(key))[0] == provider for key in mapping):
            return True
    return False


def _collector_scope_excludes_credit_provider(config: Mapping[str, Any] | None, provider: str) -> bool:
    """When ``[collectors.codexbar] providers`` is an explicit list, omit other pools.

    Credit overrides (omp/pi) are not CodexBar subscription rows, but applying them while
    the run intentionally excludes that collector's identity would inject a balance the
    operator did not ask this pass to surface (see
    ``test_apply_credit_overrides_to_snapshot_adds_admin_api_omp_and_respects_provider_filter``).
    ``load_overrides`` does not use this — only snapshot application paths.
    """
    if not isinstance(config, dict):
        return False
    from aiuse.config import requested_providers

    req = requested_providers(config)
    return req is not None and provider not in req


def readings_file() -> Path:
    return overrides_path().with_name("credit-readings.json")


def load_overrides(
    config: Mapping[str, Any] | None = None, *, now: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """The freshest validated figure per pool: operator override or Admin API reading.

    Empty when ``config`` hides or disables omp. Never raises.
    """
    now = now or utcnow()
    merged: dict[str, dict[str, Any]] = {}
    has_admin: set[str] = set()
    # Validate every candidate with the time rules first, then take the freshest valid one, so an
    # invalid (future-dated, expired, too old) reading can never hide a valid override.
    # Read API state first, then the anchor once: never validate against one
    # operator version while selecting another version of that same file.
    api_entries = _load_file(readings_file(), "admin-api")
    operator_entries = _load_file(overrides_path(), OVERRIDE_SOURCE)
    for entries, source in ((operator_entries, OVERRIDE_SOURCE), (api_entries, "admin-api")):
        for provider, entry in entries.items():
            if _config_hides_omp(config, provider):
                continue
            if provider == "pi" and source == "admin-api":
                anchor = operator_entries.get(provider)
                # A later API observation must not revive a superseded operator anchor.
                if anchor is not None and not _matches_anchor(entry, anchor):
                    continue
            valid, _why = _safe_clean(entry, now=now)
            if valid is None:
                continue
            if source == "admin-api":
                has_admin.add(provider)
            current = merged.get(provider)
            if (
                current is None
                or (parse_dt(valid["observed_at"]) or _EPOCH) > (parse_dt(current["observed_at"]) or _EPOCH)
                or (
                    provider == "pi"
                    and source == "admin-api"
                    and valid["observed_at"] == current["observed_at"]
                    and valid["remaining_usd"] < current["remaining_usd"]
                )
            ):
                merged[provider] = valid
    for provider in has_admin:
        if provider in merged:
            merged[provider]["has_admin_reading"] = True
    return merged


def load_file_entry(path: Path, source: str, provider: str = OVERRIDE_PROVIDER) -> dict[str, Any] | None:
    """One structurally valid credit entry (no time rules), or None."""
    return _load_file(path, source).get(provider)


def load_grant_params(
    provider: str = OVERRIDE_PROVIDER,
    config: Mapping[str, Any] | None = None,
) -> tuple[float, datetime, datetime | None] | None:
    """``(total_usd, granted_at, expires_at)`` from operator override, else config.

    The grant's parameters do not age, so the override's 7-day limit is not applied.
    """
    entry = _load_file(overrides_path(), OVERRIDE_SOURCE).get(provider)
    granted = parse_dt(entry.get("granted_at")) if entry else None
    if entry is not None and granted is not None:
        return float(entry["total_usd"]), granted, parse_dt(entry.get("expires_at"))
    from aiuse.config import credit_grant_from_config

    return credit_grant_from_config(config, provider)


def _load_file(path: Path, source: str) -> dict[str, dict[str, Any]]:
    try:
        with path.open("rb") as handle:
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
        if provider not in CREDIT_PROVIDERS:
            print(f"aiuse: ignoring credit override for unknown pool {str(provider)[:40]!r}", file=sys.stderr)
            continue
        if isinstance(raw, Mapping):
            raw = {**raw, "source": source}  # the file decides the source, never its content
        entry, why = _safe_clean(raw)
        if entry is None:
            print(f"aiuse: ignoring {provider} credit override: {why}", file=sys.stderr)
            continue
        entry["source"] = source
        out[provider] = entry
    return out


def _safe_clean(raw: Any, *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """``clean_override`` that can never raise: a malformed file is never fatal."""
    try:
        return clean_override(raw, now=now)
    except Exception as exc:  # noqa: BLE001 - hostile input must only ever mean "ignored"
        return None, f"unreadable ({exc.__class__.__name__})"


def _same_grant(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("total_usd", "granted_at", "expires_at"))


def _matches_anchor(reading: Mapping[str, Any], anchor: Mapping[str, Any]) -> bool:
    return _same_grant(reading, anchor) and reading.get("anchor_observed_at") == anchor.get("observed_at")


def save_credit_entry(
    path: Path,
    provider: str,
    entry: Mapping[str, Any],
    source: str,
    *,
    anchor: Mapping[str, Any] | None = None,
    allow_calibration: bool = False,
    inherit_granted: bool = False,
) -> dict[str, Any]:
    """Select and persist an effective reading inside one shared credit-state lock.

    Pi API readings carry the operator anchor's observation time. Only a newer
    operator anchor may increase credit; a newer cost observation may not. OMP
    retains its explicit purchased-credit calibration exception.
    """
    if provider not in CREDIT_PROVIDERS or source not in (OVERRIDE_SOURCE, "admin-api"):
        raise ValueError("unsupported credit provider or source")
    valid, why = _safe_clean({**entry, "source": source})
    if valid is None:
        raise ValueError(f"invalid credit reading: {why}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name("credit-state.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if anchor is not None:
            current_anchor = load_file_entry(overrides_path(), OVERRIDE_SOURCE, provider)
            if current_anchor != anchor or not _matches_anchor(valid, anchor):
                raise ValueError("operator anchor changed during refresh; retry")
        if path.exists() and path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("existing credit state exceeds 64 KiB; refusing to overwrite")
        data = _load_file(path, source)
        current = data.get(provider)
        if current is not None:
            if inherit_granted and source == OVERRIDE_SOURCE:
                valid["granted_at"] = current.get("granted_at")
            if (_timestamp(valid["observed_at"]) or _EPOCH) <= (_timestamp(current["observed_at"]) or _EPOCH):
                return current
            if source == "admin-api":
                new_anchor = _timestamp(valid.get("anchor_observed_at"))
                old_anchor = _timestamp(current.get("anchor_observed_at"))
                if old_anchor is not None and (new_anchor is None or new_anchor < old_anchor):
                    return current
                replacement = new_anchor is not None and new_anchor > (
                    old_anchor or _timestamp(current["observed_at"]) or _EPOCH
                )
                if not replacement and _same_grant(valid, current) and not (provider == "omp" and allow_calibration):
                    valid["remaining_usd"] = min(valid["remaining_usd"], current["remaining_usd"])
                elif provider == "pi" and not replacement and not _same_grant(valid, current):
                    raise ValueError("changed Pi grant requires a newer operator anchor")
        data[provider] = valid
        serialized = json.dumps(data, indent=2) + "\n"
        if len(serialized.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("credit state exceeds 64 KiB; shorten the note")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(serialized)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return valid


def save_override(
    provider: str,
    *,
    remaining: float,
    total: float,
    expires: datetime | None,
    note: str | None,
    now: datetime | None = None,
    granted: datetime | None = None,
) -> dict[str, Any]:
    path = overrides_path()
    granted_at = granted.isoformat() if granted else None
    entry = {
        "granted_at": granted_at,
        "remaining_usd": remaining,
        "total_usd": total,
        "expires_at": expires.isoformat() if expires else None,
        "note": note,
        "observed_at": (now or utcnow()).isoformat(),
    }
    # Inherit an omitted --granted only after acquiring the persistence lock.
    return save_credit_entry(path, provider, entry, OVERRIDE_SOURCE, inherit_granted=granted is None)


def parse_expiry(text: str) -> datetime:
    """``2026-11-08`` (midnight UTC) or a full ISO timestamp."""
    parsed = parse_dt(text)
    if parsed is None:
        raise ValueError(f"cannot parse {text!r} as an ISO date or datetime")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _credits_window(
    total: float, remaining: float, expires: str | None, provider: str = OVERRIDE_PROVIDER
) -> dict[str, Any]:
    pct = max(0.0, min(100.0, remaining / total * 100.0)) if total > 0 else None
    return {
        "label": "credits",
        "used_percent": None if pct is None else 100.0 - pct,
        "remaining_percent": pct,
        "resets_at": expires,
        "reset_description": f"${remaining:.2f} of ${total:.2f} left, expires",
        "refill_capacity": total,
        "refill_capacity_unit": "usd",
        "window_id": CREDIT_WINDOW_IDS[provider],
    }


def _normalize_credits_clock(account: dict[str, Any], override: Mapping[str, Any]) -> None:
    """Restore the grant clock even when watch reuses an already-overridden row.

    Only borrow a grant start from a reading with the same capacity and expiry.
    A new grant must never inherit the previous grant's elapsed time.
    """
    window_id = CREDIT_WINDOW_IDS.get(str(account.get("provider") or OVERRIDE_PROVIDER), "claude-cloud-credits")
    for window in account.get("windows") or []:
        if not isinstance(window, dict) or window.get("window_id") != window_id:
            continue
        expiry = _timestamp(window.get("resets_at") or account.get("expires_at"))
        capacity = _number(window.get("refill_capacity"))
        override_total = _number(override.get("total_usd"))
        granted = None
        if (
            capacity is not None
            and override_total is not None
            and abs(capacity - override_total) <= _EPSILON
            and expiry is not None
            and expiry == _timestamp(override.get("expires_at"))
        ):
            granted = _timestamp(override.get("granted_at"))
        if granted is not None and expiry is not None and granted < expiry:
            window["window_minutes"] = max(1, round((expiry - granted).total_seconds() / 60))
        elif not window.get("window_minutes"):
            window["window_minutes"] = WINDOW_NOMINAL_MINUTES["monthly"]


def _omp_hidden(snap: Mapping[str, Any], provider: str = OVERRIDE_PROVIDER) -> bool:
    """A hidden/disabled credit provider must never be rebuilt by its override."""
    for section in ("hidden_pools", "disabled_services"):
        mapping = snap.get(section)
        if isinstance(mapping, Mapping) and any(parse_pool_rule_key(str(key))[0] == provider for key in mapping):
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


def _attach_config_grant_clock(
    account: dict[str, Any],
    *,
    provider: str,
    config: Mapping[str, Any] | None,
) -> None:
    """Fill ``expires_at`` from ``[credits.omp]`` when CodexBar did not supply one."""
    if provider != OVERRIDE_PROVIDER:
        return
    grant = load_grant_params(provider, config)
    if grant is None:
        return
    total, granted_at, expires = grant
    if expires is None and account.get("expires_at") is None:
        return
    capacity = next(
        (
            w.get("refill_capacity")
            for w in account.get("windows") or []
            if isinstance(w, dict) and w.get("refill_capacity_unit") == "usd"
        ),
        None,
    )
    if capacity is not None and abs(float(capacity) - float(total)) > _EPSILON:
        return
    if capacity is None:
        balance = account.get("balance_usd")
        spent = account.get("spent_usd")
        if balance is not None and spent is not None:
            if abs(float(balance) + float(spent) - float(total)) > _EPSILON:
                return
        elif account.get("source") in (OVERRIDE_SOURCE, "admin-api", "none"):
            return
    expiry_iso = account.get("expires_at") or (expires.isoformat() if expires is not None else None)
    if account.get("expires_at") is None and expires is not None:
        account["expires_at"] = expires.isoformat()
        for window in account.get("windows") or []:
            if isinstance(window, dict) and window.get("refill_capacity_unit") == "usd":
                window["resets_at"] = expires.isoformat()
    if expiry_iso is not None:
        live_expiry = parse_dt(expiry_iso)
        config_expiry = expires
        grant_start_for_clock = (
            granted_at.isoformat()
            if granted_at is not None
            and config_expiry is not None
            and live_expiry is not None
            and live_expiry == config_expiry
            else None
        )
        _normalize_credits_clock(
            account,
            {
                "total_usd": total,
                "granted_at": grant_start_for_clock,
                "expires_at": expiry_iso,
            },
        )


def _expire_reading(account: dict[str, Any], why: str = "older than 24h") -> None:
    for window in account.get("windows") or []:
        if isinstance(window, dict):
            window["used_percent"] = None
            window["remaining_percent"] = None
    account["balance_usd"] = None
    account["spent_usd"] = None
    account["reading_expired"] = True
    note = (
        f"{account.get('source')} reading {why}: balance unknown; "
        f"record the console figure with `aiuse set-credits {account.get('provider', OVERRIDE_PROVIDER)}`."
    )
    if note not in (account.get("notes") or []):
        account["notes"] = [*(account.get("notes") or []), note]


def _apply_credit_override_for_provider(
    snap: dict[str, Any],
    overrides: Mapping[str, Mapping[str, Any]] | None,
    *,
    provider: str,
    now: datetime | None = None,
    config: Mapping[str, Any] | None = None,
) -> None:
    """Rewrite one independent API-credit provider's snapshot accounts."""
    now = now or utcnow()
    if _omp_hidden(snap, provider):
        return
    override: Mapping[str, Any] = {}
    ignored: str | None = None
    if (overrides or {}).get(provider) is not None:
        cleaned, ignored = _safe_clean(overrides[provider], now=now)  # type: ignore[index]
        override = cleaned or {}
    accounts = snap.get("accounts")
    pi_rows = [a for a in accounts or [] if isinstance(a, dict) and a.get("provider") == "pi"]
    if provider == "pi" and len(pi_rows) > 1:
        # Provider-scoped funding is not proof of ownership by any individual
        # account, even two credentials claiming the same organization.
        for account in pi_rows:
            if account.get("source") in (OVERRIDE_SOURCE, "admin-api"):
                _expire_reading(account, "ambiguous across multiple Pi rows (unbound funding)")
            elif override:
                note = "credit override ignored: unbound funding is ambiguous across multiple Pi rows"
                if note not in (account.get("notes") or []):
                    account["notes"] = [*(account.get("notes") or []), note]
        return
    want_org = credit_organization_name(config, provider) if provider == OVERRIDE_PROVIDER else None
    if (
        override
        and (
            provider == "pi"
            or override.get("source") == "admin-api"
            or override.get("has_admin_reading")
            or (provider == OVERRIDE_PROVIDER and override.get("source") == OVERRIDE_SOURCE)
        )
        and isinstance(accounts, list)
        and not any(
            isinstance(a, dict) and a.get("provider") == provider and not _is_omp_openai_route(a, provider)
            for a in accounts
        )
    ):
        # An Admin API reading is collector output, so it may make the row when the snapshot
        # has none. Operator ``set-credits`` may do the same for omp when collectors omitted it
        # (for example after a config organization filter dropped the CodexBar row).
        accounts.append(
            {
                "source": "none",
                "provider": provider,
                "cli_binary": provider_cli_binary(provider),
                "billing_kind": "subscription_window",
                "windows": [],
                "credits_label": CREDIT_LABELS[provider],
                "notes": [],
                "error": None,
            }
        )
    for account in snap.get("accounts") or []:
        if not isinstance(account, dict) or account.get("provider") != provider:
            continue
        if _is_omp_openai_route(account, provider):
            continue
        if (
            want_org
            and account.get("source") == "codexbar"
            and not organization_names_match(want_org, omp_organization_label(account))
        ):
            wrong = (
                f"organization {omp_organization_label(account)!r} does not match "
                f"[credits.omp] organization = {want_org!r}"
            )
            if wrong not in (account.get("notes") or []):
                account["notes"] = [*(account.get("notes") or []), wrong]
            _expire_reading(account, wrong)
            account["observed_at"] = None
        if account.get("source") not in (OVERRIDE_SOURCE, "admin-api"):
            _normalize_credits_clock(account, override)
        _attach_config_grant_clock(account, provider=provider, config=config)
        if ignored:
            ignored_note = f"credit override ignored: {ignored}"
            if ignored_note not in (account.get("notes") or []):
                account["notes"] = [*(account.get("notes") or []), ignored_note]
        reading_at = parse_dt(account.get("collected_at") or snap.get("collected_at"))
        balance = account.get("balance_usd")
        ov_at = parse_dt(override.get("observed_at"))
        # Reused effective rows keep their grant provenance on equal/older
        # readings. Newer readings still update them, and stale rows still age.
        if (
            account.get("source") in (OVERRIDE_SOURCE, "admin-api")
            and reading_at is not None
            and (ov_at is None or ov_at <= reading_at)
        ):
            # Never rewrite an established clock from equal/older metadata.
            # Legacy OMP can repair a missing clock; Pi additionally requires
            # the same observation/source/balance before borrowing metadata.
            if provider == "omp" or (
                ov_at == reading_at
                and account.get("source") == override.get("source")
                and balance == override.get("remaining_usd")
            ):
                missing = [
                    w for w in account.get("windows") or [] if isinstance(w, dict) and not w.get("window_minutes")
                ]
                if missing:
                    _normalize_credits_clock({**account, "windows": missing}, override)
            max_age = OVERRIDE_MAX_AGE if account.get("source") == OVERRIDE_SOURCE else CODEXBAR_MAX_AGE
            _attach_config_grant_clock(account, provider=provider, config=config)
            expiry = _grant_expiry(account)
            if expiry is not None and now >= expiry:
                _expire_reading(account, "grant expired")
            elif now - reading_at > max_age:
                _expire_reading(account, "older than 7 days" if max_age == OVERRIDE_MAX_AGE else "older than 24h")
            continue
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
        granted = _timestamp(override.get("granted_at"))
        override_expiry = _timestamp(override.get("expires_at"))
        # An active grant anchored by Admin API metadata is not replaced by a
        # differently sized or differently dated CodexBar promotion. A new
        # CodexBar observation alone does not establish that this grant changed.
        incompatible_active_admin_grant = (
            override.get("source") == "admin-api"
            and granted is not None
            and override_expiry is not None
            and granted <= now < override_expiry
            and (not same_grant or _grant_expiry(account) != override_expiry)
        )
        if (
            ov_at is not None
            and isinstance(ov_remaining, (int, float))
            and isinstance(ov_total, (int, float))
            and (
                reading_at is None
                or ov_at >= reading_at
                or balance is None
                or incompatible_active_admin_grant
                or (same_grant and float(balance) > float(ov_remaining) + _EPSILON)
            )
        ):
            if account.get("source") not in ("none", OVERRIDE_SOURCE, "admin-api"):
                account["superseded_reading"] = {
                    "source": account.get("source"),
                    "observed_at": reading_at.isoformat() if reading_at else None,
                    "balance_usd": balance,
                }
            expires = override.get("expires_at")
            if provider == "omp":
                expires = expires or account.get("expires_at")
            account.update(
                source=override.get("source") or OVERRIDE_SOURCE,
                collector_id=override.get("source") or OVERRIDE_SOURCE,
                balance_usd=float(ov_remaining),
                spent_usd=round(max(0.0, float(ov_total) - float(ov_remaining)), 6),
                windows=[_credits_window(float(ov_total), float(ov_remaining), expires, provider)],
                collected_at=ov_at.isoformat(),
                expires_at=expires,
                error=None,
            )
            account["billing_kind"] = "prepaid_balance" if provider == "pi" and not expires else "subscription_window"
            account["credits_label"] = CREDIT_LABELS[provider]
            account["usage_credits"] = None  # Costs are not an additional wallet beside the grant.
            account.pop("reading_expired", None)
            # The key-pool verdict described the replaced row; a measured balance now decides usability.
            account.pop("api_key_pool", None)
            account.pop("grant_expired", None)
            _normalize_credits_clock(account, override)
            note = f"{'Admin API reading' if override.get('source') == 'admin-api' else 'Operator override (aiuse set-credits)'} at {ov_at.isoformat()}"
            if override.get("note"):
                note += f": {override['note']}"
            if note not in (account.get("notes") or []):
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
        _attach_config_grant_clock(account, provider=provider, config=config)
        # The grant itself lapsing is independent of how fresh the reading is.
        expiry = _grant_expiry(account)
        if expiry is not None and now >= expiry:
            account["grant_expired"] = True
            if not account.get("reading_expired"):
                _expire_reading(account, f"is for a grant that expired {expiry.date().isoformat()}")
        account["observed_at"] = reading_at.isoformat() if reading_at else None

        plans = config.get("plans") if isinstance(config, Mapping) else None
        spend_limit = plan_monthly_spend_limit(plans, provider, account.get("account")) if plans else None
        if spend_limit is not None and spend_limit > 0:
            used = account.get("spent_usd")
            if used is None and isinstance(account.get("usage_credits"), dict):
                used = account["usage_credits"].get("used")
            if used is not None and math.isfinite(used):
                used = float(used)
                used_pct = round(used / spend_limit * 100.0, 4)
                rem_pct = round(max(0.0, 100.0 - used_pct), 4)
                rem_usd = round(max(0.0, spend_limit - used), 4)
                account["usage_credits"] = {
                    "used": used,
                    "limit": spend_limit,
                    "remaining": rem_usd,
                    "currency": "USD",
                    "used_percent": used_pct,
                }
                account["spent_usd"] = used
                windows = account.setdefault("windows", [])
                monthly_wid = f"{provider}-spend-limit"
                has_monthly = any(isinstance(w, dict) and w.get("window_id") == monthly_wid for w in windows)
                if not has_monthly:
                    windows.append(
                        {
                            "label": "monthly-spend",
                            "window_id": monthly_wid,
                            "used_percent": used_pct,
                            "remaining_percent": rem_pct,
                            "window_minutes": 43200,
                            "reset_description": f"Spend cap ${used:.2f} / ${spend_limit:.2f}",
                            "refill_capacity": spend_limit,
                            "refill_capacity_unit": "usd",
                        }
                    )


def apply_credit_overrides(
    snap: dict[str, Any],
    overrides: Mapping[str, Mapping[str, Any]] | None,
    *,
    now: datetime | None = None,
    config: Mapping[str, Any] | None = None,
) -> None:
    """Apply independent API-credit readings without mixing provider balances."""
    # Cached snapshots may retain CodexBar's OpenAI spelling. Normalize before
    # counting accounts, or an alias can evade the unbound-funding guard.
    for account in snap.get("accounts") or []:
        if isinstance(account, dict) and canonical_provider(str(account.get("provider") or "")) == "pi":
            account["provider"] = "pi"
    for provider in CREDIT_PROVIDERS:
        if _collector_scope_excludes_credit_provider(config, provider):
            continue
        _apply_credit_override_for_provider(snap, overrides, provider=provider, now=now, config=config)


def apply_credit_overrides_to_snapshot(
    snapshot: Snapshot,
    config: Mapping[str, Any] | None = None,
    *,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> bool:
    """Apply ``apply_credit_overrides`` to ``snapshot.accounts`` in place.

    Returns True when an API-credit account was added or updated so callers can
    re-derive ``UseOrLoseAlert`` rows against the effective balance.
    """
    visibility = {"hidden_pools": snapshot.hidden_pools, "disabled_services": snapshot.disabled_services}
    providers = {
        provider
        for provider in CREDIT_PROVIDERS
        if not _config_hides_omp(config, provider)
        and not _collector_scope_excludes_credit_provider(config, provider)
        and not _omp_hidden(visibility, provider)
    }
    if not providers:
        return False
    loaded = load_overrides(config, now=now) if overrides is None else overrides
    effective_overrides = {provider: entry for provider, entry in (loaded or {}).items() if provider in providers}
    has_accounts = any(canonical_provider(a.provider) in providers for a in snapshot.accounts)
    if not has_accounts and not effective_overrides:
        return False
    before = [a.to_dict() for a in snapshot.accounts if canonical_provider(a.provider) in providers]
    snap_dict: dict[str, Any] = {
        "collected_at": snapshot.collected_at.isoformat(),
        "accounts": [copy.deepcopy(d) for d in before],
        "hidden_pools": dict(snapshot.hidden_pools),
        "disabled_services": dict(snapshot.disabled_services),
    }
    apply_credit_overrides(snap_dict, effective_overrides, now=now, config=config)
    after = snap_dict.get("accounts") or []
    from aiuse.serve import _snapshot_from_accounts_dict

    rebuilt = _snapshot_from_accounts_dict(
        {"collected_at": snapshot.collected_at.isoformat(), "accounts": after}
    ).accounts
    if [account.to_dict() for account in rebuilt] == before:
        return False  # Compare normalized models, not sparse intermediate window dicts.
    if not has_accounts:
        snapshot.accounts.extend(rebuilt)
        return bool(rebuilt)
    rebuilt_iter = iter(rebuilt)
    new_accounts: list[AccountUsage] = []
    for account in snapshot.accounts:
        if canonical_provider(account.provider) in providers:
            replacement = next(rebuilt_iter, None)
            if replacement is not None:
                new_accounts.append(replacement)
        else:
            new_accounts.append(account)
    new_accounts.extend(rebuilt_iter)
    snapshot.accounts = new_accounts
    return True


def overrides_signature() -> tuple[tuple[int, int] | None, ...]:
    """(mtime_ns, size) of the figure files, to notice a ``set-credits`` or refresh."""
    out: list[tuple[int, int] | None] = []
    for path in (overrides_path(), readings_file()):
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
        description="Record a verified API credit balance (omp = Claude API; pi = OpenAI API).",
    )
    parser.add_argument("provider", choices=CREDIT_PROVIDERS)
    parser.add_argument("--remaining", type=_finite, required=True, metavar="USD", help="dollars left")
    parser.add_argument("--total", type=_finite, required=True, metavar="USD", help="dollars granted")
    parser.add_argument("--expires", metavar="DATE", help="grant expiry, YYYY-MM-DD or ISO datetime (UTC)")
    parser.add_argument(
        "--granted", metavar="DATE", help="grant date, YYYY-MM-DD or ISO datetime (UTC); needed by refresh-credits"
    )
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
    granted = None
    if args.granted:
        try:
            granted = parse_expiry(args.granted)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    now = utcnow()
    try:
        saved = save_override(
            args.provider,
            remaining=args.remaining,
            total=args.total,
            expires=expires,
            note=args.note,
            granted=granted,
            now=now,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if saved.get("observed_at") != now.isoformat():
        print(
            f"error: not recorded: an equal or newer {args.provider} reading is already accepted "
            f"(${saved['remaining_usd']:.2f} of ${saved['total_usd']:.2f} left, observed {saved['observed_at']})",
            file=sys.stderr,
        )
        return 1
    print(
        f"recorded: {args.provider} ${args.remaining:.2f} of ${args.total:.2f} left"
        f"{', expires ' + expires.date().isoformat() if expires else ''} ({overrides_path()})"
    )
    return 0
