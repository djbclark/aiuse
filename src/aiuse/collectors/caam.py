"""Collect vault-profile quotas via ``caam`` (Coding Agent Account Manager).

``caam`` is an account vault beside the live login, not a replacement for
``caut``. ``caam limits --format json`` (never with ``--source``, ``--profile``,
``--best``, ``--rank`` or ``--cached``) reads the **vault profiles** for the
providers that have a usage API — claude, codex, grok, cursor — and returns an
array of ``usage.ProfileUsage`` rows (caam v0.1.23). ``used_percent`` is the
share **consumed** (100 = exhausted) and maps straight through, never inverted.
An empty vault returns ``[]``, which is success. Gemini, opencode and agy have
no limits API in caam, so rows for them do not appear. See docs/caam.md.
"""

from __future__ import annotations

from typing import Any

from aiuse.models import (
    AccountUsage,
    QuotaWindow,
    canonical_provider,
    parse_dt,
)
from aiuse.models import (
    coerce_float as _f,
)

from .base import CollectorError, run_json, which

# caam tool ids -> aiuse canonical provider ids (docs/provider-identity.md;
# do not invent ids). "gemini" and "opencode" also canonicalize through
# models.PROVIDER_ID_ALIASES — spelled out here so every caam id resolves in
# one place; "agy" has no models.py alias, so this is the only place it maps.
_CAAM_PROVIDERS: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "antigravity",
    "grok": "grok",
    "opencode": "opencode-go",
    "cursor": "cursor",
    "agy": "antigravity",
}

# Key fragments / value prefixes that must never reach AccountUsage.raw.
_SECRET_KEY_FRAGMENTS = (
    "token",
    "secret",
    "authorization",
    "cookie",
    "refresh",
    "access_token",
    "id_token",
    "api_key",
)
_SECRET_VALUE_PREFIXES = ("eyJ", "sk-")

# `caam status` is a local, no-network vault read; a short budget is plenty.
_STATUS_TIMEOUT = 30.0


def collect_caam(*, timeout: float = 90.0) -> list[AccountUsage]:
    """Shell out to ``caam limits --format json``.

    caam's own deadline is 60s; aiuse gives the sweep the same 90s budget as
    openusage_sh. A profile row without a measured window is skipped entirely
    (never a fake 0% row): higher-priority collectors already cover vendors
    whose login is live but whose vault has no quota row.
    """
    if not which("caam"):
        raise CollectorError(
            "caam not found on PATH (install: upstream install script or "
            "release tarball from https://github.com/Dicklesworthstone/"
            "coding_agent_account_manager, and ensure ~/.local/bin is on PATH)"
        )

    payload = run_json(["caam", "limits", "--format", "json"], timeout=timeout)
    if not isinstance(payload, list):
        raise CollectorError("caam limits returned non-array JSON")

    status_tools = _status_tools(timeout=_STATUS_TIMEOUT)
    accounts: list[AccountUsage] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        tool = str(row.get("provider") or "").strip().lower()
        account = _from_row(row, status_tool=status_tools.get(tool))
        if account is not None:
            accounts.append(account)
    return accounts


def _status_tools(*, timeout: float) -> dict[str, dict[str, Any]]:
    """``caam status --json`` tool rows, keyed by caam tool id.

    Best effort only: status is a local read whose failure must never fail
    the collector — its health lines are notes, not data.
    """
    try:
        payload = run_json(["caam", "status", "--json"], timeout=timeout)
    except CollectorError:
        return {}
    if not isinstance(payload, dict):
        return {}
    tools: dict[str, dict[str, Any]] = {}
    for entry in payload.get("tools") or []:
        if isinstance(entry, dict) and entry.get("tool"):
            tools[str(entry["tool"]).strip().lower()] = entry
    return tools


def _from_row(row: dict[str, Any], *, status_tool: dict[str, Any] | None) -> AccountUsage | None:
    """Map one ProfileUsage row onto AccountUsage, or None when unmeasured."""
    caam_id = str(row.get("provider") or "").strip().lower()
    provider = _CAAM_PROVIDERS.get(caam_id, canonical_provider(caam_id))
    usage_value = row.get("usage")
    usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}

    windows = _windows(usage)
    if not windows:
        return None

    notes = [f"caam vault profile limits via {usage.get('source') or 'vault'}."]
    notes.extend(_usage_notes(usage))
    plan = usage.get("plan_type")
    if status_tool is not None:
        notes.extend(_status_notes(status_tool))
        if not plan:
            plan = _identity_plan(status_tool)

    return AccountUsage(
        source="caam",
        provider=provider,
        account=str(row["profile_name"]) if row.get("profile_name") else None,
        plan=str(plan) if plan else None,
        windows=windows,
        notes=notes,
        collected_at=parse_dt(usage.get("fetched_at")),
        raw=_redact(row),
    )


def _windows(usage: dict[str, Any]) -> list[QuotaWindow]:
    """Measured windows; empty when the row errored or quota is unavailable."""
    if str(usage.get("error") or "").strip():
        return []
    if str(usage.get("quota_status") or "").strip().lower() == "unavailable":
        return []
    windows: list[QuotaWindow] = []
    for key, fallback_label in (
        ("primary_window", "primary"),
        ("secondary_window", "secondary"),
        ("tertiary_window", "tertiary"),
    ):
        block = usage.get(key)
        if not isinstance(block, dict):
            continue
        window = _window(block, fallback_label)
        if window is not None:
            windows.append(window)
    return windows


def _window(block: dict[str, Any], fallback_label: str) -> QuotaWindow | None:
    """One UsageWindow; None when missing, unmeasured, or without a used share."""
    if block.get("unmeasured"):
        return None
    used = _f(block.get("used_percent"))
    if used is None:
        return None
    resets = parse_dt(block.get("resets_at"))
    if resets is not None and resets.year <= 1:
        resets = None  # Go zero time, not a real timestamp
    return QuotaWindow(
        label=str(block.get("label") or fallback_label),
        used_percent=used,
        remaining_percent=100.0 - used if 0.0 <= used <= 100.0 else None,
        resets_at=resets,
        reset_description="rolled over — fresh window" if block.get("rolled") else None,
        raw=_redact(block),
    )


def _usage_notes(usage: dict[str, Any]) -> list[str]:
    """Short health lines from the limits payload itself."""
    notes: list[str] = []
    quota_status = str(usage.get("quota_status") or "").strip()
    if quota_status and quota_status != "ok":
        notes.append(f"caam quota_status: {quota_status}")
    burn = usage.get("burn_rate")
    if isinstance(burn, dict):
        rate = _f(burn.get("percent_per_hour"))
        if rate is not None:
            notes.append(f"caam burn {rate:g}%/h")
    depletion = parse_dt(usage.get("estimated_depletion"))
    if depletion is not None and depletion.year > 1:
        notes.append(f"caam estimates depletion at {depletion.isoformat()}")
    return notes


def _status_notes(status_tool: dict[str, Any]) -> list[str]:
    """Non-secret ``caam status`` health lines for this tool."""
    health_value = status_tool.get("health")
    health = health_value if isinstance(health_value, dict) else {}
    status = str(health.get("status") or "").strip()
    reason = str(health.get("reason") or "").strip()
    notes: list[str] = []
    if status and status != "ok":
        notes.append(f"caam health: {status}" + (f" — {reason}" if reason else ""))
    elif reason:
        notes.append(f"caam health: {reason}")
    if health.get("refresh_due") is True:
        notes.append("caam: refresh_due")
    if health.get("login_required") is True:
        notes.append("caam: login_required")
    expires = str(health.get("expires_at") or "").strip()
    if expires:
        notes.append(f"caam credential expires_at {expires}")
    return [note for note in notes if not _looks_secret(note)]


def _identity_plan(status_tool: dict[str, Any]) -> Any:
    identity = status_tool.get("identity")
    if isinstance(identity, dict):
        return identity.get("plan_type")
    return None


def _looks_secret(value: str) -> bool:
    return value.startswith(_SECRET_VALUE_PREFIXES)


def _redact(value: Any) -> Any:
    """Deep copy with secret-named keys and token-shaped values dropped."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if any(fragment in name for fragment in _SECRET_KEY_FRAGMENTS):
                continue
            out[str(key)] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and value.startswith(_SECRET_VALUE_PREFIXES):
        return "[redacted]"
    return value
