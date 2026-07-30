"""Collect live quotas via OpenUsage (CLI and/or loopback HTTP API).

Preferred machine interfaces (either is enough):

1. ``openusage`` CLI on PATH (Settings → Command Line → Install in the app)
2. HTTP ``GET http://127.0.0.1:6736/v1/limits`` while OpenUsage.app is running

Schema: ``openusage.limits.v1`` — providers keyed by id with ``resources``.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    UsageCredits,
    keep_copilot_report_window,
    parse_dt,
)
from aiuse.models import (
    coerce_float as _f,
)

from .base import CollectorError, run_json, which

DEFAULT_OPENUSAGE_BASE = "http://127.0.0.1:6736"
DEFAULT_HTTP_TIMEOUT = 30.0

# Resource keys that are prepaid balances / credits, not use-or-lose windows.
_BALANCE_KEYS = frozenset(
    {
        "credits",
        "creditValue",
        "balance",
        "extraUsage",
        "extraUsageBalance",
        "rateLimitResets",
        "orgCredits",
        "orgSpend",
        "keyLimit",
    }
)

_PREPAID_PROVIDERS = frozenset({"openrouter", "deepseek", "openai"})

# Human-readable window labels for known resource ids.
_RESOURCE_LABELS: dict[str, str] = {
    "session": "session",
    "weekly": "weekly",
    "monthly": "monthly",
    "sonnet": "Sonnet",
    "fable": "Fable",
    "spark": "Spark",
    "sparkWeekly": "Spark weekly",
    "totalUsage": "included",
    "autoUsage": "Auto",
    "apiUsage": "API",
    "onDemand": "on-demand",
    "requests": "requests",
    "premiumCredits": "premium",
    "chat": "chat",
    "completions": "completions",
    "geminiSession": "Gemini 5-hour",
    "geminiWeekly": "Gemini weekly",
    "nonGeminiSession": "Claude/GPT 5-hour",
    "nonGeminiWeekly": "Claude/GPT weekly",
    "daily": "daily",
    "webSearches": "web searches",
}


def collect_openusage_ai(
    *,
    timeout: float = 90.0,
    base_url: str = DEFAULT_OPENUSAGE_BASE,
    force_refresh: bool = True,
    try_launch_app: bool = True,
) -> list[AccountUsage]:
    """Fetch OpenUsage limits via CLI first, then loopback HTTP."""
    payload, via = _fetch_limits(
        timeout=timeout,
        base_url=base_url,
        force_refresh=force_refresh,
        try_launch_app=try_launch_app,
    )
    if not isinstance(payload, dict):
        raise CollectorError("OpenUsage returned non-object JSON")

    schema = payload.get("schema") or ""
    if schema and "limits" not in schema and schema != "openusage.limits.v1":
        # Still try to parse if shape matches
        pass

    providers = payload.get("providers")
    if not isinstance(providers, dict):
        raise CollectorError("OpenUsage JSON missing providers{} map")

    accounts: list[AccountUsage] = []
    for provider_id, body in providers.items():
        if not isinstance(body, dict):
            continue
        accounts.append(_from_provider(str(provider_id), body, via=via))

    errors = payload.get("errors") or []
    if errors and accounts:
        err_note = "OpenUsage errors: " + "; ".join(
            f"{e.get('providerId', '?')}: {e.get('message', e)}" if isinstance(e, dict) else str(e) for e in errors[:8]
        )
        accounts[0].notes = list(accounts[0].notes) + [err_note]

    if not accounts:
        err_bits = []
        for e in errors[:5]:
            if isinstance(e, dict):
                err_bits.append(f"{e.get('providerId', '?')}: {e.get('message', e)}")
            else:
                err_bits.append(str(e))
        detail = "; ".join(err_bits) if err_bits else "no providers in response"
        raise CollectorError(f"OpenUsage returned no provider data ({detail})")

    return accounts


def _fetch_limits(
    *,
    timeout: float,
    base_url: str,
    force_refresh: bool,
    try_launch_app: bool,
) -> tuple[dict[str, Any], str]:
    cli = which("openusage")
    if cli:
        argv = [cli]
        if force_refresh:
            argv.append("--force")
        try:
            payload = run_json(argv, timeout=timeout)
            if isinstance(payload, dict) and payload.get("providers") is not None:
                return payload, "cli"
        except CollectorError:
            # Fall through to HTTP
            pass

    try:
        return _http_limits(base_url=base_url, timeout=min(timeout, DEFAULT_HTTP_TIMEOUT)), "http"
    except CollectorError as http_err:
        if try_launch_app:
            _try_open_app()
            try:
                return (
                    _http_limits(base_url=base_url, timeout=min(timeout, DEFAULT_HTTP_TIMEOUT)),
                    "http-after-launch",
                )
            except CollectorError:
                pass
        cli_hint = (
            "Install CLI: OpenUsage → Settings → Command Line → Install… "
            "(or leave OpenUsage.app running for http://127.0.0.1:6736/v1/limits)."
        )
        if cli:
            raise CollectorError(f"OpenUsage CLI and HTTP both failed; last HTTP: {http_err}") from http_err
        raise CollectorError(f"OpenUsage unavailable ({http_err}). {cli_hint}") from http_err


def _http_limits(*, base_url: str, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/v1/limits"
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CollectorError(f"OpenUsage HTTP URL must use http(s): {url}")
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        # The scheme and host are validated immediately above.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # nosemgrep
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise CollectorError(f"OpenUsage HTTP {url}: {exc}") from exc
    except TimeoutError as exc:
        raise CollectorError(f"OpenUsage HTTP timed out: {url}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CollectorError(f"OpenUsage HTTP returned non-JSON from {url}") from exc
    if not isinstance(payload, dict):
        raise CollectorError("OpenUsage HTTP JSON is not an object")
    return payload


def _try_open_app() -> None:
    """Best-effort launch of OpenUsage.app so the loopback API comes up."""
    try:
        subprocess.run(
            ["open", "-ga", "OpenUsage"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return
    # Brief wait for the server to bind
    import time

    for _ in range(8):
        time.sleep(0.5)
        try:
            _http_limits(base_url=DEFAULT_OPENUSAGE_BASE, timeout=2.0)
            return
        except CollectorError:
            continue


def _from_provider(provider_id: str, body: dict[str, Any], *, via: str) -> AccountUsage:
    provider = provider_id.lower().replace(" ", "-")
    # Normalize family ids used by OpenUsage
    if provider == "opencodego":
        provider = "opencode"
    display = str(body.get("displayName") or provider)
    plan = body.get("plan")
    resources_value = body.get("resources")
    resources = resources_value if isinstance(resources_value, dict) else {}

    windows: list[QuotaWindow] = []
    balance_usd: float | None = None
    credits_remaining: float | None = None
    usage_credits: UsageCredits | None = None
    notes: list[str] = [f"Live data fetched by OpenUsage via {via}."]

    if body.get("stale"):
        notes.append("OpenUsage snapshot marked stale (past five-minute freshness).")

    for res_id, res in resources.items():
        if not isinstance(res, dict):
            continue
        kind = str(res.get("kind") or "")
        unit = str(res.get("unit") or "")
        label_key = str(res_id)
        pretty = _RESOURCE_LABELS.get(label_key, label_key)

        if kind == "balance" or label_key in _BALANCE_KEYS:
            available = _f(res.get("available"))
            if available is None:
                continue
            if unit in ("usd", "dollars") or label_key in ("creditValue", "balance", "credits") and unit == "usd":
                if balance_usd is None:
                    balance_usd = available
                notes.append(f"{pretty}: ${available:g} available")
            elif unit == "credits" or label_key == "credits":
                credits_remaining = available
                notes.append(f"{pretty}: {available:g} credits")
            else:
                notes.append(f"{pretty}: {available:g} {unit or 'units'}")
            continue

        if kind != "consumption":
            continue

        used = _f(res.get("used"))
        limit = _f(res.get("limit"))
        remaining = _f(res.get("remaining"))
        used_pct: float | None = None
        rem_pct: float | None = None

        if unit == "percent":
            used_pct = used
            rem_pct = remaining if remaining is not None else (max(0.0, 100.0 - used) if used is not None else None)
        elif limit is not None and limit > 0 and used is not None:
            used_pct = (used / limit) * 100.0
            rem_pct = (remaining / limit) * 100.0 if remaining is not None else max(0.0, 100.0 - used_pct)
        elif remaining is not None and limit is not None and limit > 0:
            rem_pct = (remaining / limit) * 100.0
            used_pct = max(0.0, 100.0 - rem_pct)

        window_seconds = res.get("windowSeconds")
        window_minutes = None
        try:
            if window_seconds is not None:
                window_minutes = int(float(window_seconds) // 60)
        except (TypeError, ValueError):
            window_minutes = None

        if provider == "copilot":
            if label_key in ("chat", "completions"):
                continue
            if label_key == "premiumCredits":
                label = "GitHub Copilot premium requests"
            else:
                label = f"{display} {pretty}"
                if not keep_copilot_report_window(label):
                    continue
        else:
            label = f"{display} {pretty}"

        # Dollar pools (OpenCode Go, Cursor on-demand): keep as windows with % of $ cap
        if unit == "usd" and usage_credits is None and limit is not None and limit > 0:
            usage_credits = UsageCredits(
                used=used or 0.0,
                limit=limit,
                remaining=remaining if remaining is not None else max(0.0, limit - (used or 0.0)),
                currency="USD",
                used_percent=used_pct,
                resets_at=parse_dt(res.get("resetsAt")),
            )

        if used_pct is None and rem_pct is None:
            continue

        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used_pct,
                remaining_percent=rem_pct,
                resets_at=parse_dt(res.get("resetsAt")),
                window_minutes=window_minutes,
                reset_description=f"OpenUsage resource {label_key}",
                raw=res,
            )
        )

    # Billing classification
    if provider in _PREPAID_PROVIDERS and balance_usd is not None and not any(w.resets_at is not None for w in windows):
        billing = BillingKind.PREPAID_BALANCE
    elif windows and any(w.resets_at is not None for w in windows):
        billing = BillingKind.SUBSCRIPTION_WINDOW
    elif balance_usd is not None or credits_remaining is not None:
        billing = BillingKind.PREPAID_BALANCE
    else:
        billing = BillingKind.UNKNOWN

    return AccountUsage(
        source="openusage_ai",
        provider=provider,
        account=None,  # OpenUsage limits envelope is provider-card scoped
        plan=str(plan) if plan else None,
        billing_kind=billing,
        windows=windows,
        balance_usd=balance_usd,
        credits_remaining=credits_remaining,
        usage_credits=usage_credits,
        notes=notes,
        raw=body,
    )


# Import compatibility for callers that used the former ambiguous function.
collect_openusage = collect_openusage_ai
