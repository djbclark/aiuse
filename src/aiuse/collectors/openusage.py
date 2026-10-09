"""Collect live quotas via OpenUsage (CLI and/or loopback HTTP API).

Preferred machine interfaces (either is enough):

1. ``openusage`` CLI on PATH (Settings → Command Line → Install in the app)
2. HTTP ``GET http://127.0.0.1:6736/v1/limits`` while OpenUsage.app is running

Schema: ``openusage.limits.v1`` — providers keyed by id with ``resources``.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from contextlib import ExitStack
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

from .base import CollectorError, first_tool, probe_output, run_json
from .throttle import QueryGate

# Two unrelated products ship a binary called ``openusage``. OpenUsage.app's
# CLI (a symlink to the bundled helper below, installed from Settings →
# Command Line) prints JSON and exits; openusage.sh's Go binary opens a
# full-screen terminal dashboard on /dev/tty when run without a subcommand,
# hangs until the collector timeout, and when killed leaves the terminal in
# raw mode. So the collector never trusts the first ``openusage`` on PATH: it
# walks every candidate (PATH order, then the usual install locations) and
# runs the first one that proves to be the app CLI — by resolving into
# OpenUsage.app, or failing that by answering ``--help`` with the app CLI's
# usage text. Verdicts are cached per path for the life of the process.
_APP_HELPER_SUFFIX = os.path.join("OpenUsage.app", "Contents", "Helpers", "openusage")
_KNOWN_APP_CLI_PATHS: tuple[str, ...] = (
    "/usr/local/bin/openusage",
    "/Applications/OpenUsage.app/Contents/Helpers/openusage",
    "~/Applications/OpenUsage.app/Contents/Helpers/openusage",
)
# Fragments of OpenUsage.app's ``openusage --help`` banner ("Read limits
# through OpenUsage's shared five-minute cache and exit. Output is always
# JSON.") — any one of them identifies it; openusage.sh's help says
# "terminal dashboard" and lists subcommands instead.
_APP_HELP_MARKERS: tuple[str, ...] = ("Read limits through OpenUsage", "Output is always JSON")
_PROBE_TIMEOUT_S = 5.0
_cli_verdicts: dict[str, bool] = {}


def _is_app_bundle_path(path: str) -> bool:
    try:
        real = os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return False
    return real.endswith(_APP_HELPER_SUFFIX)


def is_app_cli(path: str, *, probe: bool = True) -> bool:
    """True when ``path`` is OpenUsage.app's CLI.

    Cheap structural check first (resolves into OpenUsage.app); otherwise, with
    ``probe``, a detached ``--help`` run that must print the app CLI's usage
    text. Verdicts are cached per path.
    """
    cached = _cli_verdicts.get(path)
    if cached is not None:
        return cached
    verdict = _is_app_bundle_path(path)
    if not verdict and probe:
        banner = probe_output([path, "--help"], timeout=_PROBE_TIMEOUT_S)
        verdict = any(marker in banner for marker in _APP_HELP_MARKERS)
    _cli_verdicts[path] = verdict
    return verdict


def clear_cli_verdicts() -> None:
    """Forget cached ``is_app_cli`` verdicts (tests; after an install)."""
    _cli_verdicts.clear()


def resolve_app_cli() -> tuple[str | None, list[str]]:
    """(path of OpenUsage.app's CLI or None, same-named binaries rejected on the way)."""
    return first_tool("openusage", is_app_cli, extra_paths=_KNOWN_APP_CLI_PATHS)


def app_cli_path() -> str | None:
    """Path of OpenUsage.app's CLI, or None when only loopback HTTP is available."""
    return resolve_app_cli()[0]


def foreign_openusage_binaries() -> list[str]:
    """``openusage`` binaries found that are not OpenUsage.app's CLI (doctor warning)."""
    return resolve_app_cli()[1]


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

# Resource ids whose label above already names the pool, so prefixing the
# provider's displayName would double-qualify it ("Antigravity Gemini 5-hour"
# where CodexBar says "Gemini 5-hour"). Two spellings of one window fork the
# history series that analysis/history.py keys on.
_SELF_QUALIFIED_LABELS = frozenset(
    {
        "geminiSession",
        "geminiWeekly",
        "nonGeminiSession",
        "nonGeminiWeekly",
    }
)


def collect_openusage_ai(
    *,
    timeout: float = 90.0,
    base_url: str = DEFAULT_OPENUSAGE_BASE,
    force_refresh: bool = True,
    try_launch_app: bool = True,
    min_intervals: dict[str, float] | None = None,
) -> list[AccountUsage]:
    """Fetch OpenUsage limits via CLI first, then loopback HTTP."""
    payload, via, throttle_note = _fetch_limits_gated(
        timeout=timeout,
        base_url=base_url,
        force_refresh=force_refresh,
        try_launch_app=try_launch_app,
        min_intervals=min_intervals or {},
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
        account = _from_provider(str(provider_id), body, via=via)
        if throttle_note and account.provider in _gated_providers(min_intervals):
            account.notes = list(account.notes) + [throttle_note]
        accounts.append(account)

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


def _gated_providers(min_intervals: dict[str, float] | None) -> set[str]:
    return {provider for provider, seconds in (min_intervals or {}).items() if seconds > 0}


def _fetch_limits_gated(
    *,
    timeout: float,
    base_url: str,
    force_refresh: bool,
    try_launch_app: bool,
    min_intervals: dict[str, float],
) -> tuple[dict[str, Any], str, str | None]:
    """``_fetch_limits`` with ``--force`` withheld while any gated provider is throttled.

    A forced CLI refresh makes OpenUsage re-query every provider it has enabled,
    so it counts as a query of each gated provider that comes back in the
    payload. Without ``--force`` OpenUsage serves its own cached reading.
    """
    gated = sorted(_gated_providers(min_intervals))
    if not force_refresh or not gated or not app_cli_path():
        payload, via = _fetch_limits(
            timeout=timeout, base_url=base_url, force_refresh=force_refresh, try_launch_app=try_launch_app
        )
        return payload, via, None

    with ExitStack() as stack:
        gates = [
            stack.enter_context(QueryGate(provider, min_interval=min_intervals[provider], wait=timeout + 15.0))
            for provider in gated
        ]
        blocked = [gate for gate in gates if not gate.allowed]
        payload, via = _fetch_limits(
            timeout=timeout, base_url=base_url, force_refresh=not blocked, try_launch_app=try_launch_app
        )
        if blocked:
            reasons = "; ".join(gate.describe() for gate in blocked)
            return payload, via, f"OpenUsage refresh not forced: {reasons}."
        if via == "cli":
            returned = payload.get("providers") if isinstance(payload, dict) else None
            for gate in gates:
                if isinstance(returned, dict) and gate.provider in {str(k).lower() for k in returned}:
                    gate.record("openusage_ai", store=False)
        return payload, via, None


def _fetch_limits(
    *,
    timeout: float,
    base_url: str,
    force_refresh: bool,
    try_launch_app: bool,
) -> tuple[dict[str, Any], str]:
    cli = app_cli_path()
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
    estimated_resources: list[str] = []

    if body.get("stale"):
        notes.append("OpenUsage snapshot marked stale (past five-minute freshness).")

    for res_id, res in resources.items():
        if not isinstance(res, dict):
            continue
        kind = str(res.get("kind") or "")
        unit = str(res.get("unit") or "")
        label_key = str(res_id)
        pretty = _RESOURCE_LABELS.get(label_key, label_key)
        if res.get("estimated") is True:
            estimated_resources.append(pretty)

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
        elif label_key in _SELF_QUALIFIED_LABELS:
            label = pretty
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

    if estimated_resources:
        # OpenCode Go is the known case: OpenUsage sums local spend against
        # fixed $12/$30/$60 caps (same heuristic as CodexBar --source local).
        labels = ", ".join(estimated_resources[:6])
        more = "" if len(estimated_resources) <= 6 else f" (+{len(estimated_resources) - 6} more)"
        notes.append(
            f"OpenUsage marked estimated (local cost vs fixed $ caps): {labels}{more}. "
            "May understate used quota versus official OpenCode web billing."
        )

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
