"""Passive ACP-log scan that turns a fresh vendor 429 into an agent note.

Google's Antigravity quota endpoint can serve a frozen used% for an hour or
more while real spend continues (2026-10-18: Claude/GPT weekly stuck at
~59% until a 429, then CodexBar flipped to 100%). When an agent hits
``Individual quota reached`` / ``QUOTA_EXHAUSTED`` with a multi-day reset,
that refusal is stronger evidence than the cached percent — but only if
something records it. ``acp-dispatch`` calls ``aiuse note-exhausted`` on a
classified quota failure; bare ``acp-run`` (and some exit-0 end_turn paths)
do not. This module reads recent acp-run JSONL/stderr and persists the same
note shape so ``enrich_snapshot`` fails closed for ``--available``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiuse.agent_notes import load_active_notes
from aiuse.analysis.pace import GEMINI_FRONTIER_MODEL_PREFIXES, collapse_gemini_family
from aiuse.collectors.acp_usage import ACP_AGENT_PROVIDERS, default_acp_log_dir
from aiuse.models import parse_dt, utcnow

DEFAULT_LOOKBACK_HOURS = 6.0
_MAX_FILES = 80

# Google / cloudcode-pa: Individual quota + quotaResetTimeStamp (weekly).
_RESET_STAMP = re.compile(
    r"quotaResetTimeStamp:(?P<reset>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
    re.IGNORECASE,
)
_ISO_TIMESTAMP = re.compile(
    r"\b(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\b",
    re.IGNORECASE,
)
_RETRY_AFTER = re.compile(
    r"retry-after[:=]\s*(?P<seconds>\d+)",
    re.IGNORECASE,
)
_MODEL = re.compile(
    r"model:(?P<model>claude-[A-Za-z0-9._+-]+|gpt-[A-Za-z0-9._+-]+|gemini-[A-Za-z0-9._+-]+)",
    re.IGNORECASE,
)
_QUOTA_MARKERS = (
    "individual quota reached",
    "reason:quota_exhausted",
    "QUOTA_EXHAUSTED",
    "user_weekly_rate_limited",
    "weekly_rate_limited",
    "user_session_rate_limited",
    "session_rate_limited",
)
_COPILOT_QUOTA_MARKERS = (
    "user_weekly_rate_limited",
    "weekly_rate_limited",
    "user_session_rate_limited",
    "session_rate_limited",
)
_SPEND_LIMIT_MARKERS = (
    "enforced spend limit",
    "organization has reached its configured enforced spend limit",
)
_TRANSPORT_BURST_MARKERS = ("resource_exhausted",)
_LOG_NAME = re.compile(r"^(\d{8})-(\d{6})-(.+)-(\d+)\.(jsonl|stderr)$")
# Multi-day reset: shorter resets are often the 5h / burst window, which the
# existing agy CLI client_limits path already surfaces without flipping the pool.
_MIN_RESET_HORIZON = timedelta(hours=20)


def _agent_of(name: str) -> str | None:
    match = _LOG_NAME.match(name)
    return match.group(3) if match else None


def pool_family_for_acp_model(
    provider: str, model: str | None, reported_families: Iterable[str] | None = None
) -> str | None:
    """Map an ACP agent model id to an aiuse pool_family for split vendors.

    Antigravity: ``claude-*`` / ``gpt-*`` → ``claude_gpt``; every ``gemini-*`` →
    ``gemini``, except that a Gemini frontier model (``gemini-4-argon*``) maps to
    its own ``gemini_<name>`` family only when ``reported_families`` (the pool
    families of the account's windows) holds it, because only some plans report a
    per-model window (Pro did for Argon; Ultra shares one Gemini pool across
    Carbon, Neon, Hydrogen and Argon). The default ``None`` means "no account read
    yet" and returns the model's NOMINAL family (``gemini_argon``), never the shared
    ``gemini``: folding needs positive evidence about the account (a window
    missing from one reading is not that evidence; see
    ``aiuse.analysis.argon_evidence``), so only a caller that holds an account's
    reported families passes them here.
    Other providers return ``None`` (one residual pool).
    """
    return collapse_gemini_family(nominal_pool_family_for_acp_model(provider, model), reported_families)


def nominal_pool_family_for_acp_model(provider: str, model: str | None) -> str | None:
    """The family a model draws when its account reports an own window for it.

    Discovery sites (refusal logs, ACP activity) have no account in hand; they
    record this nominal family and the consumer folds it with
    ``aiuse.analysis.pace.collapse_gemini_family`` against the windows the
    account actually reports.
    """
    if provider != "antigravity" or not model:
        return None
    low = model.casefold()
    if low.startswith(("claude-", "gpt-")):
        return "claude_gpt"
    if low.startswith("gemini-"):
        for prefix, name in GEMINI_FRONTIER_MODEL_PREFIXES.items():
            if low.startswith(prefix):
                return f"gemini_{name}"
        return "gemini"
    return None


def _family_for(provider: str, model: str | None) -> str | None:
    return nominal_pool_family_for_acp_model(provider, model)


def _extract_refusal(text: str, *, now: datetime, provider: str | None = None) -> dict[str, Any] | None:
    low = text.casefold()
    if any(marker in low for marker in _SPEND_LIMIT_MARKERS):
        return {
            "resets_at": None,
            "model": None,
            "reason": "OpenAI organization enforced spend limit reached (operator action required)",
            "snippet": text[:240],
            "refusal_kind": "spend_limit",
            "state": "blocked",
            "operator_action_required": True,
            "usable_now": False,
        }
    if not any(marker.casefold() in low for marker in _QUOTA_MARKERS):
        return None

    if provider == "copilot" or any(m in low for m in _COPILOT_QUOTA_MARKERS):
        is_weekly = "weekly" in low or "user_weekly" in low
        reset_match = _RESET_STAMP.search(text) or _ISO_TIMESTAMP.search(text)
        retry_match = _RETRY_AFTER.search(text)
        resets_at = None
        if reset_match:
            resets_at = parse_dt(reset_match.group(1))
        elif retry_match:
            try:
                resets_at = now + timedelta(seconds=int(retry_match.group("seconds")))
            except (ValueError, OverflowError):
                resets_at = None
        if resets_at is None or resets_at <= now:
            resets_at = now + timedelta(days=7 if is_weekly else 1)
        reason_label = "weekly rate limit (7-day)" if is_weekly else "session rate limit"
        return {
            "resets_at": resets_at,
            "model": None,
            "reason": f"Copilot {reason_label} exceeded",
            "snippet": text[:240],
        }

    reset_match = _RESET_STAMP.search(text)
    if reset_match is None:
        return None
    resets_at = parse_dt(reset_match.group("reset"))
    if resets_at is None or resets_at <= now:
        return None
    if resets_at - now < _MIN_RESET_HORIZON:
        return None
    model_match = _MODEL.search(text)
    model = model_match.group("model") if model_match else None
    return {"resets_at": resets_at, "model": model, "reason": None, "snippet": text[:240]}


def scan_acp_quota_refusals(
    *,
    log_dir: str | Path | None = None,
    lookback: timedelta = timedelta(hours=DEFAULT_LOOKBACK_HOURS),
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Recent multi-day quota refusals from acp-run logs, newest first per pool."""
    now = now or utcnow()
    root = Path(log_dir).expanduser() if log_dir else default_acp_log_dir()
    if not root.is_dir() or lookback.total_seconds() <= 0:
        return []
    since = now - lookback
    try:
        entries = [entry for entry in os.scandir(root) if entry.is_file()]
    except OSError:
        return []
    recent: list[tuple[float, Path]] = []
    for entry in entries:
        if not entry.name.endswith((".jsonl", ".stderr")):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime >= since.timestamp():
            recent.append((mtime, Path(entry.path)))
    recent.sort(reverse=True)

    found: dict[tuple[str, str | None], dict[str, Any]] = {}
    for _mtime, path in recent[:_MAX_FILES]:
        agent = _agent_of(path.name)
        if agent is None:
            continue
        provider = ACP_AGENT_PROVIDERS.get(agent)
        if provider is None and agent in ("pi", "openai"):
            provider = "pi"
        if provider is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # JSONL can be large; only scan when a marker appears.
        low = text.casefold()
        has_quota_marker = any(marker in text for marker in _QUOTA_MARKERS if marker.isupper()) or any(
            marker.casefold() in low for marker in _QUOTA_MARKERS
        )
        has_spend_marker = any(marker in low for marker in _SPEND_LIMIT_MARKERS)
        if not has_quota_marker and not has_spend_marker:
            continue
        refusal = _extract_refusal(text, now=now, provider=provider)
        if refusal is None:
            continue
        if refusal.get("refusal_kind") == "spend_limit":
            # OpenAI org enforced spend limit belongs to the metered API pool (pi/openai_api), never codex.
            provider = "pi"
            family: str | None = "openai_api"
        else:
            family = _family_for(provider, refusal.get("model"))
        key = (provider, family)
        if key in found:
            continue
        reason = (
            f"acp-run log {path.name}: {refusal['reason']}"
            if refusal.get("reason")
            else f"acp-run log {path.name}: Individual/QUOTA_EXHAUSTED (model={refusal.get('model') or '?'})"
        )
        resets_dt = refusal.get("resets_at")
        record: dict[str, Any] = {
            "schema_version": "1.0",
            "provider": provider,
            "pool_family": family,
            "resets_at": resets_dt.astimezone(timezone.utc).isoformat() if resets_dt is not None else None,
            "reason": reason,
            "created_at": now.isoformat(),
            "source": "agent-reported",
            "evidence": str(path),
        }
        for extra in ("refusal_kind", "state", "operator_action_required", "usable_now"):
            if extra in refusal:
                record[extra] = refusal[extra]
        found[key] = record
    return list(found.values())


def scan_transport_burst_lockouts(
    *,
    agy_log_dir: str | Path | None = None,
    lookback: timedelta | None = None,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Per-transport burst lockouts (e.g. agy CLI 429 RESOURCE_EXHAUSTED).

    Unlike ``scan_acp_quota_refusals``, a CLI burst lockout is recorded only for
    its transport (``cli``) and never marks the underlying subscription pool
    exhausted so working transports (e.g. ``acp``) remain routable.
    """
    from aiuse.client_limits import DEFAULT_LOOKBACK_MINUTES, scan_agy_cli_lockout

    moment = now or utcnow()
    window = lookback if lookback is not None else timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)
    log_path = Path(agy_log_dir).expanduser() if agy_log_dir is not None else None
    agy = scan_agy_cli_lockout(lookback=window, now=moment, log_dir=log_path)
    if agy is None:
        return {}
    entry = dict(agy)
    entry.setdefault("provider", "antigravity")
    entry.setdefault("transport", str(entry.get("client") or "cli"))
    entry.setdefault("usable", False)
    entry.setdefault("pool_exhausted", False)
    return {"antigravity": [entry]}


def scan_pi_quota_refusals(
    *,
    sessions_dir: str | Path | None = None,
    lookback: timedelta = timedelta(hours=DEFAULT_LOOKBACK_HOURS),
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Recent quota/balance refusals from Pi session JSONL logs (``~/.pi/agent/sessions``)."""
    import json

    from aiuse.models import canonical_provider

    moment = now or utcnow()
    root = Path(sessions_dir).expanduser() if sessions_dir else Path.home() / ".pi" / "agent" / "sessions"
    if not root.is_dir() or lookback.total_seconds() <= 0:
        return []
    since_ts = (moment - lookback).timestamp()
    recent: list[tuple[float, Path]] = []
    for path in root.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= since_ts:
            recent.append((mtime, path))
    recent.sort(reverse=True)

    found: dict[tuple[str, str | None], dict[str, Any]] = {}
    for _mtime, path in recent[:_MAX_FILES]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if "error" not in line and "429" not in line and "402" not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "message":
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            err = msg.get("errorMessage")
            if not isinstance(err, str) or not err.strip():
                continue
            raw_prov = str(msg.get("provider") or "").strip()
            if not raw_prov or raw_prov == "xai-oauth":
                provider = "grok" if raw_prov == "xai-oauth" else ""
            else:
                provider = canonical_provider(raw_prov)
            if not provider:
                continue
            extracted = _extract_refusal(err, now=moment, provider=provider)
            if extracted is None:
                continue
            model = str(msg.get("model") or "") or extracted.get("model")
            family = _family_for(provider, model)
            key = (provider, family)
            if key not in found:
                found[key] = {
                    "provider": provider,
                    "pool_family": family,
                    "resets_at": extracted["resets_at"],
                    "reason": extracted.get("reason") or f"pi session refusal ({model or provider})",
                    "model": model,
                    "source": "pi-session",
                }
    return list(found.values())


def persist_acp_quota_refusals(
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Discover ACP refusals into agent notes; return the active note list."""
    return load_active_notes(now=now, prune=True, discover_acp=True)
