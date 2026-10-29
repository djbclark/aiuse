"""Collect live subscription/API quotas from CodexBar."""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar, copy_context
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from aiuse.models import (
    PROVIDER_DISPLAY_NAMES,
    WINDOW_NOMINAL_MINUTES,
    AccountUsage,
    BillingKind,
    QuotaWindow,
    UsageCredits,
    canonical_provider,
    classify_window_minutes,
    keep_copilot_report_window,
    parse_dt,
    utcnow,
)
from aiuse.models import coerce_float as _f
from aiuse.models import coerce_int as _int_or_none
from aiuse.timefmt import format_as_of, format_clock

from .base import CollectorError, CollectorNotInstalled, CollectorTimeout, run_json, which
from .bedrock_cost import native_bedrock_payload
from .bedrock_gate import measured_bedrock_spend
from .throttle import QueryGate, TimeoutBackoff

# Providers that are typically pure prepaid / API balance (not use-or-lose monthly)
PREPAID_HINTS = {
    "openrouter",
    "openai",
    "deepseek",
    "deepinfra",
    "groqcloud",
    "together",
    "fireworks",
}

# CodexBar itself queries providers serially within a single invocation, so a
# bundled "enabled providers" call costs the sum of every provider's latency.
# Querying one provider per subprocess and running those concurrently instead
# costs only the slowest provider's latency. Capped to avoid spawning an
# unreasonable number of processes for a very long explicit --providers list.
_MAX_CONCURRENT_PROVIDER_QUERIES = 16

# After a provider's query hangs until its timeout, skip it for this long
# (doubling per further consecutive timeout, capped at 12x). One hanging
# provider otherwise holds every aiuse run, including each scheduled sample,
# at the full CodexBar timeout. [collectors.codexbar] timeout_backoff overrides;
# 0 disables. See aiuse-e9d and docs/collector-concurrency.md.
DEFAULT_TIMEOUT_BACKOFF_SECONDS = 1800.0

# Providers queried with a specific CodexBar `--source` first. Antigravity
# falls back to `auto` when that source returns nothing usable. OpenCode Go
# does not: see `_NO_AUTO_FALLBACK`.
#
# - opencodego -> web: `auto` rescans ~/.local/share/opencode/opencode.db
#   (json over every message; no usage-result cache — `--refresh` is a
#   `codexbar cost` flag) and sums those costs against hardcoded $12/$30/$60
#   caps. That scan is what blew the 45s kill, and the estimate can show
#   monthly headroom when the console is already empty. Web is the real
#   limit; a miss does not fall through to the rescan.
# - antigravity -> oauth: `auto` reads the agy CLI, which spawns agy three times
#   per probe and boots its language server each time (issue #34). `oauth`
#   reads stored Google auth and spawns nothing; when it has no credentials
#   the CLI path still runs, gated by query_min_interval.
_PREFERRED_SOURCES: dict[str, str] = {"opencodego": "web", "antigravity": "oauth"}

# Preferred source is the only CodexBar call. A miss returns nothing (a
# timeout still propagates so hang backoff applies) instead of a second scan.
_NO_AUTO_FALLBACK: frozenset[str] = frozenset({"opencodego"})

_SLOT_LABELS: dict[str, tuple[str, str, str]] = {
    # CodexBar maps Copilot primary→premium, secondary→chat (no tertiary /
    # completions slot). Completions are intentionally not surfaced — see
    # `_keep_copilot_window`.
    "copilot": (
        "GitHub Copilot premium requests",
        "GitHub Copilot chat messages",
        "GitHub Copilot quota 3",
    ),
    # Cursor pools (see docs/cursor-quota.md): total (monthly umbrella), auto,
    # third-party (breakdown rows), on-demand (spend cap), grok-bot (weekly).
    "cursor": (
        "total",
        "auto",
        "third-party",
    ),
    # ClinePass: official 5h / weekly / monthly subscription limits (nested).
    "clinepass": (
        "ClinePass 5-hour",
        "ClinePass weekly",
        "ClinePass monthly",
    ),
    # z.ai coding plan: TOKENS_LIMIT (5h) then TIME_LIMIT (weekly); a second
    # shorter token window is the session slot when the API sends two.
    "zai": (
        "z.ai 5-hour",
        "z.ai weekly",
        "z.ai session",
    ),
    # Devin self-serve: included daily + weekly quota (Free/Pro/Max).
    "devin": (
        "Devin daily",
        "Devin weekly",
        "Devin quota 3",
    ),
    "grok": ("Grok usage limit", "Grok quota 2", "Grok quota 3"),
    "warp": ("Warp credits", "Warp quota 2", "Warp quota 3"),
    "elevenlabs": (
        "ElevenLabs character credits",
        "ElevenLabs quota 2",
        "ElevenLabs quota 3",
    ),
}


DEFAULT_WIDGET_SNAPSHOT_PATH = (
    Path.home() / "Library" / "Group Containers" / "Y5PE65HELJ.com.steipete.codexbar" / "widget-snapshot.json"
)


def _codexbar_bin() -> str:
    """Resolve symlinks for the executable to work around CodexBar issue #2756/#2889."""
    cmd = which("codexbar")
    return os.path.realpath(cmd) if cmd else "codexbar"


_KIND_MINUTES = {"5h": 300, "weekly": 10080, "monthly": 43200}


def _row_window_kind(rid: str, title: str) -> str | None:
    """Duration bucket a widget-snapshot ``usageRows`` entry names in its id or title."""
    if "5h" in rid or "5-hour" in title:
        return "5h"
    if "weekly" in rid or "weekly" in title:
        return "weekly"
    if "monthly" in rid or "monthly" in title:
        return "monthly"
    return None


def _row_slot_candidates(provider: str, title: str, slots: tuple[Any, ...]) -> list[dict[str, Any]]:
    """The slot blocks a ``usageRows`` entry may borrow its clock from.

    A provider whose slots are named pools (``_SLOT_POOL_PREFIXES``) lends a
    row only its own pool's slot: "Claude/GPT weekly" never takes the Gemini
    slot's reset just because both pools happen to sit at the same percent.
    """
    pools = _SLOT_POOL_PREFIXES.get(provider)
    if pools:
        for index, prefix in enumerate(pools):
            if title.startswith(prefix.lower()):
                block = slots[index] if index < len(slots) else None
                return [block] if isinstance(block, dict) else []
    return [block for block in slots if isinstance(block, dict)]


def _parse_widget_snapshot_entry(entry: dict[str, Any], *, age_seconds: float) -> list[AccountUsage]:
    provider = str(entry.get("provider") or "").lower()
    primary = entry.get("primary")
    secondary = entry.get("secondary")
    tertiary = entry.get("tertiary")

    extra_windows: list[dict[str, Any]] = []
    for r in entry.get("usageRows") or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "").lower()
        if rid in ("primary", "secondary", "tertiary", "session", "weekly"):
            continue
        w_dict: dict[str, Any]
        row_window = r.get("window")
        if isinstance(row_window, dict):
            w_dict = dict(row_window)
        else:
            # The snapshot gives these rows a percent and a title only; the
            # clock (length, reset) has to come from the slot block that is the
            # same measurement. Matching on percent alone picked the first slot
            # for every row once all pools sat at 0%, so Antigravity's weekly
            # rows inherited the 5-hour slot's 300 minutes and reset and the
            # matrix stacked all five in the 5H column (2026-10-17). The row's
            # own duration kind and pool now gate the match; a row no slot
            # describes keeps its title's nominal length and no reset.
            used = (100.0 - float(r["percentLeft"])) if r.get("percentLeft") is not None else 0.0
            w_dict = {"usedPercent": used}
            rtitle = str(r.get("title") or "").lower()
            kind = _row_window_kind(rid, rtitle)
            for cand in _row_slot_candidates(provider, rtitle, (primary, secondary, tertiary)):
                if cand.get("usedPercent") is None or abs(float(cand["usedPercent"]) - used) >= 0.05:
                    continue
                cand_kind = _block_window_kind(cand)
                if kind and cand_kind and cand_kind != kind:
                    continue
                w_dict.update(
                    {
                        "windowMinutes": cand.get("windowMinutes"),
                        "resetsAt": cand.get("resetsAt"),
                        "resetDescription": cand.get("resetDescription"),
                    }
                )
                break
            if not w_dict.get("windowMinutes") and kind:
                w_dict["windowMinutes"] = _KIND_MINUTES[kind]
        extra_windows.append({"title": r.get("title"), "id": r.get("id"), "window": w_dict})

    row = {
        "provider": entry.get("provider"),
        "source": "widget-snapshot",
        "account": entry.get("quotaOwnerKey"),
        "usage": {
            "primary": primary,
            "secondary": secondary,
            "tertiary": tertiary,
            "extraRateWindows": extra_windows,
            "updatedAt": entry.get("updatedAt"),
            "providerCost": entry.get("providerCost"),
        },
        "credits": {
            "remaining": entry.get("creditsRemaining"),
        },
    }
    account = _from_row(row)
    account.notes.append(f"Cached widget snapshot age: {int(age_seconds)}s.")
    result = [account]
    for sibling in (_opencode_zen_from_row(row), _omp_credits_from_row(row)):
        if sibling is not None:
            result.append(sibling)
    return result


def _read_widget_snapshot(
    cache_path: Path | str | None,
    max_age: float,
    provider_list: list[str | None],
    skip_providers: frozenset[str] | None,
) -> list[AccountUsage] | None:
    path = Path(cache_path).expanduser() if cache_path else DEFAULT_WIDGET_SNAPSHOT_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    gen_dt = parse_dt(data.get("generatedAt"))
    if not gen_dt:
        return None
    age = (utcnow() - gen_dt).total_seconds()
    if age < 0:
        age = 0.0
    if age > max_age:
        return None

    entries = data.get("entries")
    if not isinstance(entries, list):
        return None

    if provider_list != [None]:
        needed_providers = {canonical_provider(p) for p in provider_list if p}
        available_providers = {
            canonical_provider(e.get("provider")) for e in entries if isinstance(e, dict) and e.get("provider")
        }
        if not needed_providers.issubset(available_providers):
            return None

    accounts: list[AccountUsage] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("provider"):
            continue
        p = canonical_provider(entry.get("provider"))
        if skip_providers and p in skip_providers:
            continue
        if provider_list != [None] and p not in {canonical_provider(x) for x in provider_list if x}:
            continue
        accounts.extend(_parse_widget_snapshot_entry(entry, age_seconds=age))

    return accounts


class _NoRedirect(HTTPRedirectHandler):
    """Refuse redirects so the bearer token stays on the configured host."""

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class _CodexbarHttp:
    """A CodexBar ``serve`` base URL and the bearer token for its data routes."""

    def __init__(self, base_url: str, token: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token


# Set for the duration of one ``collect_codexbar`` call. Worker threads copy
# the context at submit time, so concurrent provider queries see the same server.
_CODEXBAR_HTTP: ContextVar[_CodexbarHttp | None] = ContextVar("aiuse_codexbar_http", default=None)
_HTTP_MISS = object()


def collect_codexbar(
    *,
    providers: str | list[str] | None = "enabled",
    timeout: float = 45.0,
    discovery_timeout: float | None = None,
    min_intervals: dict[str, float] | None = None,
    skip_providers: frozenset[str] | None = None,
    provider_timeouts: dict[str, float] | None = None,
    timeout_backoff: float | None = DEFAULT_TIMEOUT_BACKOFF_SECONDS,
    use_cache: bool = False,
    max_cache_age: float = 300.0,
    cache_path: Path | str | None = None,
    all_accounts_providers: frozenset[str] = frozenset(),
    include_disabled: bool = False,
    http_url: str | None = None,
    http_token: str | None = None,
) -> list[AccountUsage]:
    """Collect CodexBar usage rows.

    ``all_accounts_providers`` (``[collectors.codexbar] all_accounts``) names
    providers queried with ``--all-accounts`` so every CodexBar token account
    becomes its own row. The widget snapshot holds only the active account, so
    with ``use_cache`` those providers skip the snapshot and are queried live.

    ``include_disabled`` (the watch all-providers sweep) also live-queries
    CodexBar providers that are turned off in CodexBar itself, when aiuse
    knows that provider and this run did not already collect it. The widget
    cache only holds CodexBar's enabled set, so a provider disabled in both
    places (OpenCode Go on this machine) would otherwise never appear.

    ``http_url`` is CodexBar's HTTP server (Settings → Advanced). When
    ``/health`` answers, usage queries use that server instead of the widget
    snapshot and the CLI. A server that is down falls back to those. The
    token is sent only as an Authorization bearer header.
    """
    reset = _CODEXBAR_HTTP.set(_codexbar_http_if_up(http_url, http_token))
    try:
        return _collect_codexbar_impl(
            providers=providers,
            timeout=timeout,
            discovery_timeout=discovery_timeout,
            min_intervals=min_intervals,
            skip_providers=skip_providers,
            provider_timeouts=provider_timeouts,
            timeout_backoff=timeout_backoff,
            use_cache=use_cache,
            max_cache_age=max_cache_age,
            cache_path=cache_path,
            all_accounts_providers=all_accounts_providers,
            include_disabled=include_disabled,
        )
    finally:
        _CODEXBAR_HTTP.reset(reset)


def _collect_codexbar_impl(
    *,
    providers: str | list[str] | None = "enabled",
    timeout: float = 45.0,
    discovery_timeout: float | None = None,
    min_intervals: dict[str, float] | None = None,
    skip_providers: frozenset[str] | None = None,
    provider_timeouts: dict[str, float] | None = None,
    timeout_backoff: float | None = DEFAULT_TIMEOUT_BACKOFF_SECONDS,
    use_cache: bool = False,
    max_cache_age: float = 300.0,
    cache_path: Path | str | None = None,
    all_accounts_providers: frozenset[str] = frozenset(),
    include_disabled: bool = False,
) -> list[AccountUsage]:
    provider_list = _normalize_providers(providers)
    # CodexBar has no `omp` provider: its credits ride on the Claude row.
    provider_list = list(dict.fromkeys("claude" if p == "omp" else p for p in provider_list))
    # "enabled" / "all" discovers the catalog. An explicit provider list does not,
    # and must not gain a Bedrock Cost Explorer call it did not ask for.
    was_discovery = provider_list == [None]
    all_accounts = frozenset(canonical_provider(p) for p in all_accounts_providers if p) - (
        skip_providers or frozenset()
    )
    catalog_box: dict[str, Any] = {"done": False, "payload": None}

    def catalog() -> Any:
        if not catalog_box["done"]:
            catalog_box["payload"] = _load_provider_catalog(
                timeout=discovery_timeout if discovery_timeout is not None else timeout
            )
            catalog_box["done"] = True
        return catalog_box["payload"]

    def finish(found: list[AccountUsage], *, queried: bool) -> list[AccountUsage]:
        return _with_disabled(
            _attach_bedrock(
                found,
                queried=queried,
                skip_providers=skip_providers,
                enabled_collect=was_discovery or include_disabled,
                timeout=timeout,
                catalog=catalog,
            )
        )

    def _with_disabled(found: list[AccountUsage]) -> list[AccountUsage]:
        if not include_disabled:
            return found
        extra, extra_errors = _query_disabled_known_providers(
            already={canonical_provider(account.provider) for account in found},
            skip_providers=skip_providers or frozenset(),
            timeout=timeout,
            discovery_timeout=discovery_timeout if discovery_timeout is not None else timeout,
            min_intervals=min_intervals,
            provider_timeouts=provider_timeouts,
            timeout_backoff=timeout_backoff,
        )
        return found + extra + _query_error_rows(extra_errors)

    # A live HTTP server is the menu bar's own cache. Skip the widget snapshot
    # so a provider the snapshot omits is still read from that server.
    if use_cache and _CODEXBAR_HTTP.get() is None:
        cache_list = [p for p in provider_list if p is None or canonical_provider(p) not in all_accounts]
        cached = (
            _read_widget_snapshot(
                cache_path=cache_path,
                max_age=max_cache_age,
                provider_list=cache_list,
                skip_providers=(skip_providers or frozenset()) | all_accounts,
            )
            if cache_list
            else None
        )
        if cached is not None:
            cached_bedrock = any(a.provider == "bedrock" for a in cached)
            cached = [a for a in cached if a.provider != "bedrock"]
            backoff = TimeoutBackoff("codexbar", base=timeout_backoff) if timeout_backoff else None
            omp = _live_omp_supplement(
                cached,
                skip_providers=skip_providers,
                timeout=provider_timeouts.get("claude", timeout) if provider_timeouts else timeout,
                min_intervals=min_intervals,
                backoff=backoff,
            )
            if provider_list == [None]:
                live_list: list[str | None] = sorted(all_accounts)
            else:
                live_list = [p for p in provider_list if p is not None and canonical_provider(p) in all_accounts]
            if (cached_bedrock or "bedrock" in provider_list) and "bedrock" not in (skip_providers or ()):
                live_list.append("bedrock")
            live_list = list(dict.fromkeys(live_list))
            if not live_list:
                return finish(cached + omp, queried=False)
            live, errors = _collect_live(
                live_list,
                timeout=timeout,
                min_intervals=min_intervals,
                provider_timeouts=provider_timeouts,
                backoff=backoff,
                all_accounts_providers=all_accounts,
            )
            live_queried = any(p is not None and canonical_provider(p) == "bedrock" for p in live_list)
            return finish(cached + omp + live + _query_error_rows(errors), queried=live_queried)

    if not which("codexbar"):
        raise CollectorNotInstalled("codexbar not found on PATH")
    if provider_list == [None]:
        # Discover the actual enabled-provider list ourselves so each can be
        # queried as its own concurrent subprocess, instead of asking CodexBar
        # for "enabled providers" in one call that it resolves serially.
        discovered = _parse_enabled_providers(catalog())
        if discovered:
            provider_list = discovered

    if skip_providers:
        # [disabled_services]: never spawn a query (or surface its timeout) for a
        # provider the operator turned off, even if CodexBar still has it enabled.
        kept = [p for p in provider_list if p is None or canonical_provider(p) not in skip_providers]
        if not kept:
            if "bedrock" in (skip_providers or ()):
                return []
            return finish([], queried=False)
        provider_list = kept

    accounts, errors = _collect_live(
        provider_list,
        timeout=timeout,
        min_intervals=min_intervals,
        provider_timeouts=provider_timeouts,
        backoff=TimeoutBackoff("codexbar", base=timeout_backoff) if timeout_backoff else None,
        all_accounts_providers=all_accounts,
    )
    if not accounts and errors and not include_disabled:
        raise CollectorError("; ".join(errors))
    bedrock_queried = any(p is not None and canonical_provider(p) == "bedrock" for p in provider_list)
    return finish(accounts + _query_error_rows(errors), queried=bedrock_queried)


def _query_error_rows(errors: list[str]) -> list[AccountUsage]:
    if not errors:
        return []
    return [
        AccountUsage(
            source="codexbar",
            provider="codexbar-query-errors",
            error="; ".join(errors),
            billing_kind=BillingKind.UNKNOWN,
        )
    ]


def _collect_live(
    provider_list: list[str | None],
    *,
    timeout: float,
    min_intervals: dict[str, float] | None,
    provider_timeouts: dict[str, float] | None,
    backoff: TimeoutBackoff | None,
    all_accounts_providers: frozenset[str] = frozenset(),
) -> tuple[list[AccountUsage], list[str]]:
    """Query ``provider_list`` live; return (account rows, per-provider errors)."""
    accounts: list[AccountUsage] = []
    errors: list[str] = []
    reuse_notes: dict[str, str] = {}

    for provider_arg, outcome in _query_providers(
        provider_list,
        timeout=timeout,
        min_intervals=min_intervals,
        reuse_notes=reuse_notes,
        provider_timeouts=provider_timeouts,
        backoff=backoff,
        all_accounts_providers=all_accounts_providers,
    ):
        if isinstance(outcome, CollectorError):
            name = provider_arg or "enabled providers"
            errors.append(f"{name}: {outcome}")
            continue
        rows = outcome if isinstance(outcome, list) else [outcome]
        for row in rows:
            if isinstance(row, dict):
                if provider_arg is not None:
                    row_provider = str(row.get("provider", "")).strip().lower()
                    if row_provider and row_provider != provider_arg.lower():
                        continue
                account = _from_row(row)
                if provider_arg is not None and provider_arg in reuse_notes:
                    account.notes = list(account.notes) + [reuse_notes[provider_arg]]
                accounts.append(account)
                for sibling in (_opencode_zen_from_row(row), _omp_credits_from_row(row)):
                    if sibling is not None:
                        accounts.append(sibling)
    return accounts, errors


def _live_omp_supplement(
    cached: list[AccountUsage],
    *,
    skip_providers: frozenset[str] | None,
    timeout: float,
    min_intervals: dict[str, float] | None,
    backoff: TimeoutBackoff | None,
) -> list[AccountUsage]:
    """The ``omp`` row for a cache-served run.

    The widget snapshot carries no cloud-session credits, so when it serves a
    Claude row the credits need one live ``codexbar usage --provider claude``
    (through the usual query gate and hang backoff). Best-effort: a failure
    leaves the run without an ``omp`` row rather than failing the cached rows.
    """
    if "omp" in (skip_providers or ()) or not any(a.provider == "claude" for a in cached):
        return []
    outcome = _query_providers(
        ["claude"], timeout=timeout, min_intervals=min_intervals, reuse_notes={}, backoff=backoff
    )[0][1]
    if isinstance(outcome, CollectorError):
        return []
    rows = outcome if isinstance(outcome, list) else [outcome]
    found = (_omp_credits_from_row(row) for row in rows if isinstance(row, dict))
    return [account for account in found if account is not None]


def _normalize_providers(providers: str | list[str] | None) -> list[str | None]:
    """Return None for enabled-provider discovery, or explicit provider queries."""
    if providers is None:
        return [None]
    if isinstance(providers, list):
        items: list[str] = [str(provider).strip().lower() for provider in providers if str(provider).strip()]
        if len(items) == 1 and items[0] in ("", "enabled", "configured", "default", "all"):
            return [None]
        if len(items) == 1 and items[0] == "both":
            return ["codex", "claude"]
    else:
        text = str(providers).strip().lower()
        if text in ("", "enabled", "configured", "default", "all"):
            return [None]
        if text in ("both",):
            return ["codex", "claude"]
        items = [provider.strip().lower() for provider in text.split(",") if provider.strip()]
    return list(items) if items else [None]


def _load_provider_catalog(*, timeout: float = 45.0) -> Any | None:
    """CodexBar's provider catalog. A local read; None when it cannot be parsed.

    Discovery is best-effort. A failure falls back to the bundled usage call
    rather than failing the collect.
    """
    try:
        return run_json([_codexbar_bin(), "config", "providers", "--format", "json"], timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def _discover_enabled_providers(*, timeout: float = 45.0) -> list[str | None] | None:
    """Ask CodexBar which providers are enabled, so each can be queried alone.

    Returns None if unavailable or unparsable, so the caller falls back to the
    bundled call.
    """
    return _parse_enabled_providers(_load_provider_catalog(timeout=timeout))


def _catalog_has_bedrock(payload: Any) -> bool:
    if not isinstance(payload, list):
        return False
    return any(isinstance(row, dict) and "bedrock" in str(row.get("provider") or "").lower() for row in payload)


def _bedrock_accounts(*, timeout: float) -> list[AccountUsage]:
    """One gated Bedrock read. An error is a row, not a failed collect."""
    outcome = _query_provider("bedrock", timeout=timeout)
    if isinstance(outcome, CollectorError):
        return [
            AccountUsage(
                source="codexbar",
                provider="bedrock",
                error=str(outcome),
                billing_kind=BillingKind.PAYG_API,
            )
        ]
    rows = outcome if isinstance(outcome, list) else [outcome]
    accounts: list[AccountUsage] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "").strip().lower()
        if provider and provider != "bedrock":
            continue
        accounts.append(_from_row(row))
    return accounts


def _attach_bedrock(
    found: list[AccountUsage],
    *,
    queried: bool,
    skip_providers: frozenset[str] | None,
    enabled_collect: bool,
    timeout: float,
    catalog: Any,
) -> list[AccountUsage]:
    """Add Bedrock when CodexBar lists it and this run did not already ask.

    ``catalog`` is the provider-config payload, or a zero-arg loader. The
    loader runs only when a query is still possible, so an explicit
    single-provider collect does not touch Cost Explorer.
    """
    if queried or not enabled_collect or "bedrock" in (skip_providers or ()):
        return found
    if any(canonical_provider(account.provider) == "bedrock" for account in found):
        return found
    payload = catalog() if callable(catalog) else catalog
    if not _catalog_has_bedrock(payload):
        return found
    return found + _bedrock_accounts(timeout=timeout)


def _parse_enabled_providers(payload: Any) -> list[str | None] | None:
    if not isinstance(payload, list):
        return None
    enabled: list[str | None] = [
        str(row["provider"]).lower()
        for row in payload
        if isinstance(row, dict) and row.get("enabled") and row.get("provider")
    ]
    return enabled or None


def _disabled_known_query_ids(payload: Any, *, already: set[str], skip: frozenset[str]) -> list[str]:
    """CodexBar CLI ids to live-query: disabled there, known to aiuse, not already collected.

    One id per canonical provider. Prefer the canonical spelling, then the
    same id with hyphens removed (``opencode-go`` → ``opencodego``). A looser
    alias (``opencode``, ``gemini``, ``alibabatokenplan``) is not a second query.
    """
    if not isinstance(payload, list):
        return []
    grouped: dict[str, list[str]] = {}
    for row in payload:
        if not isinstance(row, dict) or row.get("enabled") or not row.get("provider"):
            continue
        provider_id = str(row["provider"]).strip().lower()
        # Bedrock has its own Cost Explorer gate. The disabled sweep must not
        # start a second billed read beside that gate.
        if "bedrock" in provider_id:
            continue
        canonical = canonical_provider(provider_id)
        if canonical not in PROVIDER_DISPLAY_NAMES or canonical in already or canonical in skip:
            continue
        grouped.setdefault(canonical, []).append(provider_id)
    chosen: list[str] = []
    for canonical in sorted(grouped):
        ids = grouped[canonical]
        dehyphen = canonical.replace("-", "")
        if canonical in ids:
            chosen.append(canonical)
        elif dehyphen in ids:
            chosen.append(dehyphen)
    return chosen


def _query_disabled_known_providers(
    *,
    already: set[str],
    skip_providers: frozenset[str],
    timeout: float,
    discovery_timeout: float,
    min_intervals: dict[str, float] | None,
    provider_timeouts: dict[str, float] | None,
    timeout_backoff: float | None,
) -> tuple[list[AccountUsage], list[str]]:
    """Live-query CodexBar-disabled providers the enabled/cache pass did not return."""
    try:
        payload = run_json(
            [_codexbar_bin(), "config", "providers", "--format", "json"],
            timeout=discovery_timeout,
        )
    except Exception:  # noqa: BLE001 — discovery is best-effort; the enabled rows still stand
        return [], []
    provider_ids = _disabled_known_query_ids(payload, already=already, skip=skip_providers)
    if not provider_ids:
        return [], []
    query_ids: list[str | None] = list(provider_ids)
    return _collect_live(
        query_ids,
        timeout=timeout,
        min_intervals=min_intervals,
        provider_timeouts=provider_timeouts,
        backoff=TimeoutBackoff("codexbar", base=timeout_backoff) if timeout_backoff else None,
        all_accounts_providers=frozenset(),
    )


def _query_providers(
    provider_list: list[str | None],
    *,
    timeout: float = 45.0,
    min_intervals: dict[str, float] | None = None,
    reuse_notes: dict[str, str] | None = None,
    provider_timeouts: dict[str, float] | None = None,
    backoff: TimeoutBackoff | None = None,
    all_accounts_providers: frozenset[str] = frozenset(),
) -> list[tuple[str | None, Any]]:
    """Run one `codexbar usage` call per entry in provider_list, concurrently.

    Returns (provider_arg, payload_or_error) pairs in provider_list order,
    regardless of which subprocess finishes first, so downstream account and
    error ordering stays deterministic. Duplicate entries are queried once —
    querying the same provider twice concurrently is never useful and would
    otherwise race on which duplicate's outcome survives.

    Providers listed in ``min_intervals`` go through a cross-process
    :class:`QueryGate`; when one is throttled its last stored payload is
    returned instead and ``reuse_notes[provider]`` explains why.

    ``provider_timeouts`` overrides ``timeout`` per provider. With a
    ``backoff``, a provider whose last query hung is skipped (its error says
    until when) and a fresh hang is recorded for the next run.

    Providers in ``all_accounts_providers`` are asked for every token account
    (``--all-accounts``); see :func:`_query_provider`.
    """
    deduped: list[str | None] = list(dict.fromkeys(provider_list))
    intervals = min_intervals or {}
    per_provider = {str(k).strip().lower(): float(v) for k, v in (provider_timeouts or {}).items()}
    notes = reuse_notes if reuse_notes is not None else {}

    def run(provider_arg: str | None) -> Any:
        if provider_arg is None:
            return _query_provider(None, timeout=timeout)
        provider = provider_arg.lower()
        provider_timeout = per_provider.get(provider, timeout)
        # Only passed when set, so stand-ins with the plain signature still work.
        extra: dict[str, bool] = {"all_accounts": True} if provider in all_accounts_providers else {}
        if provider == "bedrock":
            # The $0.01 gate lives inside _query_provider. QueryGate's reused
            # payload would freeze updatedAt. The gate restamps that clock and
            # keeps the measurement time for the as-of stamp.
            if backoff is not None:
                reason = backoff.blocked(provider)
                if reason is not None:
                    return CollectorError(reason)
            outcome = _query_provider(provider_arg, timeout=provider_timeout, **extra)
            if backoff is not None:
                if isinstance(outcome, CollectorTimeout):
                    backoff.record_timeout(provider, provider_timeout)
                else:
                    backoff.clear(provider)
            return outcome
        if backoff is not None:
            reason = backoff.blocked(provider)
            if reason is not None:
                return CollectorError(reason)
        if intervals.get(provider, 0) > 0:
            outcome = _query_provider_gated(
                provider_arg, intervals[provider], timeout=provider_timeout, notes=notes, **extra
            )
        else:
            outcome = _query_provider(provider_arg, timeout=provider_timeout, **extra)
        if backoff is not None:
            if isinstance(outcome, CollectorTimeout):
                backoff.record_timeout(provider, provider_timeout)
            elif provider_arg not in notes:  # a live answer, not a throttled reuse
                backoff.clear(provider)
        return outcome

    if len(deduped) <= 1:
        return [(provider_arg, run(provider_arg)) for provider_arg in deduped]

    workers = min(len(deduped), _MAX_CONCURRENT_PROVIDER_QUERIES)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(copy_context().run, run, provider_arg): provider_arg for provider_arg in deduped}
        outcomes: dict[str | None, Any] = {}
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()
    return [(provider_arg, outcomes[provider_arg]) for provider_arg in deduped]


def _query_provider_gated(
    provider_arg: str,
    min_interval: float,
    *,
    timeout: float,
    notes: dict[str, str],
    all_accounts: bool = False,
) -> Any:
    provider = provider_arg.lower()
    with QueryGate(provider, min_interval=min_interval, wait=timeout + 15.0) as gate:
        if gate.allowed:
            extra: dict[str, bool] = {"all_accounts": True} if all_accounts else {}
            outcome = _query_provider(provider_arg, timeout=timeout, **extra)
            gate.record("codexbar", outcome, usable=_usable_usage_payload(outcome))
            return outcome
        reused_at = gate.reused_from("codexbar")
        if reused_at is not None:
            notes[provider_arg] = (
                f"{gate.describe()}; reused CodexBar's result from {format_clock(reused_at, date=True)}."
            )
        return gate.reuse("codexbar")


def _query_provider(
    provider_arg: str | None,
    *,
    timeout: float = 45.0,
    all_accounts: bool = False,
) -> Any:
    provider = provider_arg.lower() if provider_arg is not None else None
    if provider == "bedrock":
        return native_bedrock_payload(timeout=timeout)
    # Omitting --provider asks CodexBar for its enabled-provider list and data
    # (only reached as a fallback if _discover_enabled_providers() fails).
    argv = [_codexbar_bin(), "usage", "--format", "json"]
    if provider_arg is not None:
        argv.extend(["--provider", provider_arg])

    preferred = _PREFERRED_SOURCES.get(provider) if provider is not None else None
    if all_accounts and provider_arg is not None:
        # One row per CodexBar token account. A provider with no token
        # accounts answers "No token accounts configured" (or nothing usable):
        # fall through to the plain query of the active account. A timeout
        # stays a timeout so hang backoff records it without a second wait.
        source_args = ["--source", preferred] if preferred is not None else []
        every_account = _run_codexbar_usage([*argv, *source_args, "--all-accounts"], timeout=timeout)
        if _usable_usage_payload(every_account) or isinstance(every_account, CollectorTimeout):
            return every_account
    if preferred is not None:
        preferred_outcome = _run_codexbar_usage([*argv, "--source", preferred], timeout=timeout)
        if _usable_usage_payload(preferred_outcome):
            return preferred_outcome
        if provider in _NO_AUTO_FALLBACK:
            # A timeout must stay a timeout so hang backoff records it. Any
            # other miss (no cookie, parse error) is empty: the auto fallback
            # would rescan local history and publish the wrong estimate.
            if isinstance(preferred_outcome, CollectorTimeout):
                return preferred_outcome
            return []
        # The preferred source failed or returned only errors — fall through to
        # CodexBar auto so the provider still appears in the report.

    return _run_codexbar_usage(argv, timeout=timeout)


def _usage_query_params(argv: list[str]) -> list[tuple[str, str]]:
    """Map a ``codexbar usage`` argv onto the HTTP server's query string."""
    params: list[tuple[str, str]] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--provider", "--source") and index + 1 < len(argv):
            params.append((arg[2:], argv[index + 1]))
            index += 2
            continue
        if arg == "--all-accounts":
            params.append(("allAccounts", "true"))
        index += 1
    return params


def _codexbar_http_if_up(url: str | None, token: str | None) -> _CodexbarHttp | None:
    """The configured server when ``/health`` answers, otherwise the CLI path."""
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return None
    bearer = token.strip() if isinstance(token, str) and token.strip() else None
    client = _CodexbarHttp(url.strip(), bearer)
    try:
        status, body = _http_get(client, "/health", timeout=3.0)
    except (URLError, TimeoutError, OSError):
        return None
    if status != 200:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return None
    return client


def _http_get(
    client: _CodexbarHttp, path: str, *, timeout: float, params: list[tuple[str, str]] | None = None
) -> tuple[int, bytes]:
    """GET one CodexBar HTTP route. Redirects are not followed."""
    query = f"?{urlencode(params)}" if params else ""
    req = Request(f"{client.base_url}{path}{query}", headers={"Accept": "application/json"})
    if client.token:
        req.add_header("Authorization", f"Bearer {client.token}")
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()


def _fetch_codexbar_http_usage(client: _CodexbarHttp, argv: list[str], *, timeout: float) -> Any:
    """One ``/usage`` read. ``_HTTP_MISS`` means the CLI should run instead."""
    try:
        status, body = _http_get(client, "/usage", timeout=timeout, params=_usage_query_params(argv))
    except TimeoutError:
        return CollectorTimeout(f"codexbar HTTP usage exceeded {timeout:g}s")
    except URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            return CollectorTimeout(f"codexbar HTTP usage exceeded {timeout:g}s")
        return _HTTP_MISS
    except OSError:
        return _HTTP_MISS
    if status != 200:
        return _HTTP_MISS
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return _HTTP_MISS


def _run_codexbar_usage(argv: list[str], *, timeout: float) -> Any:
    http = _CODEXBAR_HTTP.get()
    if http is not None:
        fetched = _fetch_codexbar_http_usage(http, argv, timeout=timeout)
        if fetched is not _HTTP_MISS:
            return fetched
    try:
        return run_json(argv, timeout=timeout)
    except CollectorError as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 — isolate this provider's failure from the rest of the batch
        return CollectorError(str(exc))


def _usable_usage_payload(outcome: Any) -> bool:
    """True when CodexBar returned at least one non-error usage row."""
    if isinstance(outcome, CollectorError):
        return False
    rows = outcome if isinstance(outcome, list) else [outcome]
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("error"):
            continue
        usage = row.get("usage")
        if isinstance(usage, dict) and usage:
            if any(isinstance(usage.get(k), dict) for k in ("primary", "secondary", "tertiary")):
                return True
    return False


def _display_account(value: Any) -> str | None:
    """Drop CodexBar's synthetic API-key account labels (not a real identity)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("-codexbar-api-key"):
        return None
    return text


def _from_row(row: dict[str, Any]) -> AccountUsage:
    provider = str(row.get("provider") or "unknown").lower()
    source_tag = str(row.get("source") or "unknown mechanism")
    err = row.get("error")
    if isinstance(err, dict):
        err = err.get("message") or str(err)
    if isinstance(err, str):
        return AccountUsage(
            source="codexbar",
            provider=provider,
            account=_display_account(row.get("account")),
            error=err,
            # Bedrock is pay-as-you-go even when the row is an error (UNKNOWN
            # stands in for a $0 Cost Explorer total).
            billing_kind=BillingKind.PAYG_API if provider == "bedrock" else _billing_kind(provider, None),
            raw=row,
        )

    usage_value = row.get("usage")
    usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
    identity_value = usage.get("identity")
    identity: dict[str, Any] = identity_value if isinstance(identity_value, dict) else {}
    account = _display_account(row.get("account") or usage.get("accountEmail") or identity.get("accountEmail"))
    plan = usage.get("loginMethod") or identity.get("loginMethod")

    rate_labels = row.get("rateWindowLabels")
    rate_labels = rate_labels if isinstance(rate_labels, dict) else None

    windows: list[QuotaWindow] = []
    extra_windows: list[QuotaWindow] = []
    for extra in usage.get("extraRateWindows") or []:
        if not isinstance(extra, dict) or not isinstance(extra.get("window"), dict):
            continue
        extra_id = str(extra.get("id") or "").strip()
        title = str(extra.get("title") or extra_id or "Unnamed CodexBar quota")
        window = _window(title, extra["window"], window_id=extra_id or None)
        if window:
            if extra_id == "cursor-grok-bot":
                window = QuotaWindow(
                    label="grok-bot",
                    used_percent=window.used_percent,
                    remaining_percent=window.remaining_percent,
                    resets_at=window.resets_at,
                    window_minutes=window.window_minutes,
                    window_id=extra_id,
                    reset_description=window.reset_description,
                    raw=window.raw,
                )
            extra_windows.append(window)
    windows.extend(extra_windows)

    for index, key in enumerate(("primary", "secondary", "tertiary"), start=1):
        block = usage.get(key)
        if not isinstance(block, dict):
            continue
        label = _slot_label(provider, index, block, rate_labels=rate_labels)
        window = _window(label, block, window_id=key)
        # Skip only when this slot is the same measurement as an already-parsed
        # extra/named window — do not drop real subscription slots just because
        # a prepaid balance blob also exists.
        if window and not any(window.same_measurement(extra) for extra in extra_windows):
            windows.append(window)

    if provider == "copilot":
        windows = [window for window in windows if keep_copilot_report_window(window.label)]

    # Provider-specific balance/usage blobs that are not subscription windows.
    for nested_key, label in (
        ("openRouterUsage", "OpenRouter prepaid balance usage"),
        ("openAIAPIUsage", "OpenAI pay-as-you-go API usage"),
    ):
        nested = usage.get(nested_key)
        if isinstance(nested, dict) and nested.get("usedPercent") is not None:
            used = _f(nested.get("usedPercent"))
            windows.append(
                QuotaWindow(
                    label=label,
                    used_percent=used,
                    remaining_percent=max(0.0, 100.0 - used) if used is not None else None,
                    raw=nested,
                )
            )

    balance_usd = None
    credits_remaining = None
    notes: list[str] = [f"Live data fetched by CodexBar via {source_tag}."]
    if provider == "opencodego" and "local" in source_tag.casefold():
        notes.append(
            "OpenCode Go local estimate (SQLite costs vs fixed caps) — may diverge "
            "from the official Go limit; prefer CodexBar web when available."
        )

    credits = row.get("credits")
    if isinstance(credits, dict):
        credits_remaining = _f(credits.get("remaining"))

    openrouter = usage.get("openRouterUsage")
    if isinstance(openrouter, dict):
        balance_usd = _f(openrouter.get("balance"))
        total = _f(openrouter.get("totalCredits"))
        used = _f(openrouter.get("totalUsage"))
        # Some CodexBar builds omit `balance` but still ship funded/spent totals.
        if balance_usd is None and total is not None and used is not None:
            balance_usd = max(0.0, total - used)
        if total is not None and used is not None:
            notes.append(f"OpenRouter prepaid credits: ${total:.2f} funded, ${used:.2f} spent.")

    # CodexBar stores OpenCode Zen (a separate prepaid service) in
    # providerCost.used with period "Zen balance" and limit 0.  It is emitted
    # as its own AccountUsage by `_opencode_zen_from_row`, not attached to Go.
    # Cursor (and similar) put on-demand $used/$limit in providerCost with a
    # real limit (period e.g. "Monthly").
    usage_credits: UsageCredits | None = None
    provider_cost = usage.get("providerCost")
    if provider == "bedrock":
        # Native billing separates usage from credits and proves a measured
        # zero. Legacy CodexBar zeros lack that provenance. limit 0 is no budget.
        cost_used = measured_bedrock_spend(row)
        cost_value = provider_cost if isinstance(provider_cost, dict) else {}
        cost_limit = _f(cost_value.get("limit")) if isinstance(cost_value, dict) else None
        limit = cost_limit if cost_limit is not None and cost_limit > 0 else None
        if cost_used is not None and (cost_used > 0 or isinstance(usage.get("bedrockBilling"), dict)):
            usage_credits = UsageCredits(
                used=cost_used,
                limit=limit,
                remaining=max(0.0, limit - cost_used) if limit is not None else None,
                currency=str(cost_value.get("currencyCode") or "USD") if isinstance(cost_value, dict) else "USD",
                used_percent=(cost_used / limit) * 100.0 if limit else None,
                resets_at=parse_dt(cost_value.get("resetsAt")) if isinstance(cost_value, dict) and limit else None,
            )
            read_at = parse_dt(usage.get("bedrockReadAt"))
            updated = parse_dt(usage.get("updatedAt"))
            if read_at is not None and updated is not None and (updated - read_at).total_seconds() > 90:
                notes.append(
                    f"Cost Explorer ${cost_used:.2f} {format_as_of(read_at)}; "
                    "held until other usage moves and 6h have passed."
                )
            else:
                notes.append(f"Bedrock month-to-date spend: ${cost_used:.2f} PAYG.")
        else:
            notes.append("Bedrock month-to-date spend: UNKNOWN.")
    elif isinstance(provider_cost, dict):
        period = str(provider_cost.get("period") or "")
        zen = _f(provider_cost.get("used"))
        if zen is not None and "zen" in period.lower():
            pass
        else:
            cost_used = _f(provider_cost.get("used"))
            cost_limit = _f(provider_cost.get("limit"))
            if cost_used is not None and cost_limit is not None and cost_limit > 0:
                remaining = max(0.0, cost_limit - cost_used)
                usage_credits = UsageCredits(
                    used=cost_used,
                    limit=cost_limit,
                    remaining=remaining,
                    currency=str(provider_cost.get("currencyCode") or "USD"),
                    used_percent=(cost_used / cost_limit) * 100.0,
                    resets_at=parse_dt(provider_cost.get("resetsAt")),
                )
                notes.append(f"On-demand: ↑ ${cost_used:.2f} of ${cost_limit:.2f} (↓ ${remaining:.2f} prepaid).")
                if provider == "cursor":
                    used_pct = (cost_used / cost_limit) * 100.0 if cost_limit else None
                    rem_pct = max(0.0, 100.0 - used_pct) if used_pct is not None else None
                    windows.append(
                        QuotaWindow(
                            label="on-demand",
                            window_id="on-demand",
                            used_percent=used_pct,
                            remaining_percent=rem_pct,
                            resets_at=parse_dt(provider_cost.get("resetsAt")),
                            window_minutes=43200,
                            reset_description=f"On-demand ${cost_used:.2f} / ${cost_limit:.2f}",
                            refill_capacity=cost_limit,
                            refill_capacity_unit="usd",
                            raw=provider_cost,
                        )
                    )
    reset_credits = usage.get("codexResetCredits")
    if isinstance(reset_credits, dict) and reset_credits.get("availableCount") is not None:
        notes.append(f"Codex limit-reset credits available: {reset_credits['availableCount']}.")
    if usage.get("dataConfidence"):
        notes.append(f"CodexBar data confidence: {usage['dataConfidence']}.")

    billing = _billing_kind(provider, usage, windows)
    if provider == "bedrock":
        billing = BillingKind.PAYG_API
        # "Spend: $16.89" is the meter, not a prepaid balance left.
        if isinstance(plan, str) and plan.lower().startswith("spend:"):
            plan = "PAYG"
    if billing == BillingKind.PREPAID_BALANCE and balance_usd is None and credits_remaining is not None:
        balance_usd = credits_remaining

    # DeepSeek-style prepaid balance embedded in a quota description. A "$" figure
    # immediately followed by "/" (e.g. "$0.002/1K tokens") is a per-unit rate, not
    # a balance, so it's skipped.
    for window in windows:
        description = (window.reset_description or "") + " " + str(window.raw.get("resetDescription") or "")
        if "$" in description and balance_usd is None:
            match = re.search(r"\$(-?[0-9]+(?:\.[0-9]+)?)", description)
            if match and re.match(r"\s*/", description[match.end() :]):
                match = None
            if match:
                balance_usd = float(match.group(1))
                # Never flip subscription windows (with resets) to prepaid just
                # because a "$" figure appears in a description string.
                if billing == BillingKind.UNKNOWN and not any(window.resets_at is not None for window in windows):
                    billing = BillingKind.PREPAID_BALANCE

    # OpenRouter often puts the wallet only in loginMethod (`Balance: $2.51`) when
    # `openRouterUsage` is absent — same display path DeepSeek gets from
    # reset_description. Without this, the ladder falls back to "prepaid API".
    if balance_usd is None and isinstance(plan, str) and "$" in plan and not plan.lower().startswith("spend:"):
        match = re.search(r"\$(-?[0-9]+(?:\.[0-9]+)?)", plan)
        if match and re.match(r"\s*/", plan[match.end() :]):
            match = None
        if match:
            balance_usd = float(match.group(1))
            if billing == BillingKind.UNKNOWN and not any(window.resets_at is not None for window in windows):
                billing = BillingKind.PREPAID_BALANCE

    bedrock_billing = None
    if provider == "bedrock" and isinstance(usage.get("bedrockBilling"), dict):
        bedrock_billing = dict(usage["bedrockBilling"])
        bedrock_billing["read_at"] = usage.get("bedrockReadAt") or usage.get("updatedAt")
    account_error = None
    if provider == "bedrock":
        # Nothing above may turn this meter into a prepaid balance. The only
        # dollars are month-to-date PAYG spend. Native billing can prove zero.
        billing = BillingKind.PAYG_API
        balance_usd = None
        credits_remaining = None
        if isinstance(plan, str) and plan.lower().startswith("spend:"):
            plan = "PAYG"
        if usage_credits is None:
            account_error = "UNKNOWN"

    return AccountUsage(
        source="codexbar",
        provider=provider,
        account=str(account) if account else None,
        plan=str(plan) if plan else None,
        billing_kind=billing,
        windows=windows,
        balance_usd=balance_usd,
        credits_remaining=credits_remaining,
        usage_credits=usage_credits,
        bedrock_billing=bedrock_billing,
        error=account_error,
        notes=notes,
        raw=row,
        # CodexBar's own measurement time for this provider row — the honest
        # freshness signal, since `codexbar usage` may serve its last menu-bar
        # refresh rather than a live API read.
        collected_at=parse_dt(usage.get("updatedAt")),
    )


def _opencode_zen_from_row(row: dict[str, Any]) -> AccountUsage | None:
    """Extract OpenCode Zen as its own prepaid service, separate from Go."""
    if str(row.get("provider") or "").lower() != "opencodego":
        return None
    usage_value = row.get("usage")
    usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
    cost_value = usage.get("providerCost")
    cost: dict[str, Any] = cost_value if isinstance(cost_value, dict) else {}
    period = str(cost.get("period") or "")
    balance = _f(cost.get("used"))
    if balance is None or "zen" not in period.lower():
        return None
    source_tag = str(row.get("source") or "auto")
    return AccountUsage(
        source="codexbar",
        provider="opencode-zen",
        billing_kind=BillingKind.PREPAID_BALANCE,
        balance_usd=balance,
        notes=[f"Live data fetched by CodexBar via {source_tag}.", f"OpenCode Zen balance: ${balance:.2f}."],
        raw={"provider": "opencode-zen", "source_row": row},
    )


_CLOUD_CREDITS_ROW_ID = "claude-cloud-credits"


def _omp_credits_from_row(row: dict[str, Any]) -> AccountUsage | None:
    """Extract Claude's promotional cloud-session credits as the ``omp`` service.

    The Max plan deposits Claude Platform API credits each billing cycle
    ($200 on Max 20x, $100 on Max 5x). omp spends that balance. It is separate
    from the plan's 5-hour and weekly limits, so CodexBar's ``claude-cloud-credits``
    row becomes its own provider. Unused dollars expire at ``resets_at``, and
    the next cycle deposits a new grant. CodexBar lists that reset and the
    dollars, not ``windowMinutes``, so the length is one nominal month ending
    at the reset.
    """
    if str(row.get("provider") or "").lower() != "claude":
        return None
    usage = row.get("usage")
    details = usage.get("details") if isinstance(usage, dict) else None
    credit_row = next(
        (
            item
            for section in details or []
            if isinstance(section, dict)
            for item in section.get("rows") or []
            if isinstance(item, dict) and item.get("id") == _CLOUD_CREDITS_ROW_ID
        ),
        None,
    )
    if credit_row is None:
        return None
    progress = credit_row.get("progress")
    progress = progress if isinstance(progress, dict) else {}
    total = _f(progress.get("total"))
    remaining = _f(credit_row.get("usageValue"))
    used = _f(progress.get("used"))
    if remaining is None and total is not None and used is not None:
        remaining = max(0.0, total - used)
    if remaining is None:
        return None
    # Dollars spent so far.
    spent = used if used is not None else (max(0.0, total - remaining) if total is not None else None)
    spent = round(spent, 6) if spent is not None else None  # drop float noise from the subtraction
    expiry = re.search(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z", str(credit_row.get("secondaryValue") or ""))
    expires_at = parse_dt(expiry.group(0)) if expiry else None
    window: QuotaWindow | None = None
    if total is not None and total > 0:
        remaining_percent = max(0.0, min(100.0, remaining / total * 100.0))
        window = QuotaWindow(
            label="credits",
            used_percent=100.0 - remaining_percent,
            remaining_percent=remaining_percent,
            resets_at=expires_at,
            window_minutes=WINDOW_NOMINAL_MINUTES["monthly"],
            reset_description=f"${remaining:.2f} of ${total:.2f} left, expires",
            refill_capacity=total,
            refill_capacity_unit="usd",
            window_id=_CLOUD_CREDITS_ROW_ID,
            raw=credit_row,
        )
    usage_dict = usage if isinstance(usage, dict) else {}
    source_tag = str(row.get("source") or "auto")
    return AccountUsage(
        source="codexbar",
        provider="omp",
        account=_display_account(row.get("account") or usage_dict.get("accountEmail")),
        billing_kind=BillingKind.SUBSCRIPTION_WINDOW if window is not None else BillingKind.PREPAID_BALANCE,
        windows=[window] if window is not None else [],
        balance_usd=remaining,
        spent_usd=spent,
        expires_at=expires_at,
        # The Claude Max plan's monthly API credits (a promotional grant that expires): a normal
        # tier since 2026-10-19 (operator), api-only work first, then code/bulk via omp, never
        # left unspent. The per-run operator gate was lifted the same day; the row keeps the
        # provider name "omp" because other tools look it up by that name.
        requires_operator_approval=False,
        credits_label="Claude API credits",
        notes=[
            f"Live data fetched by CodexBar via {source_tag}.",
            "Claude API credits: the Max plan's monthly promotional grant (expiring); spent by omp "
            "or any ANTHROPIC_API_KEY path. Normal tier since 2026-10-19; use before expiry.",
        ],
        raw={"provider": "omp", "source_row": row},
        collected_at=parse_dt(usage_dict.get("updatedAt")),
    )


def _window(label: str, block: dict[str, Any], *, window_id: str | None = None) -> QuotaWindow | None:
    used = _f(block.get("usedPercent"))
    remaining = _f(block.get("remainingPercent"))
    description = block.get("resetDescription") or block.get("reset_description")

    # A provider reporting 0/0 has no usable entitlement; it is not 100% unused.
    quantity = re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
        str(description or ""),
    )
    if quantity and float(quantity.group(2)) == 0:
        used = None
        remaining = 0.0
    elif remaining is None and used is not None:
        remaining = max(0.0, 100.0 - used)

    resets = parse_dt(block.get("resetsAt") or block.get("resets_at"))
    if used is None and remaining is None and resets is None and not description:
        return None
    return QuotaWindow(
        label=label,
        used_percent=used,
        remaining_percent=remaining,
        resets_at=resets,
        window_minutes=_int_or_none(block.get("windowMinutes") or block.get("window_minutes")),
        reset_description=str(description) if description else None,
        raw=block,
        window_id=window_id,
    )


def _block_window_kind(block: dict[str, Any]) -> str | None:
    """Duration bucket for a CodexBar slot, from its length or its reset distance.

    CodexBar frequently omits ``windowMinutes`` (every antigravity slot does),
    so fall back to how far out the reset is. Only used to compose a label —
    getting the *pool* right matters far more than the suffix.
    """
    kind = classify_window_minutes(_int_or_none(block.get("windowMinutes") or block.get("window_minutes")))
    if kind is not None:
        return kind
    resets = parse_dt(block.get("resetsAt") or block.get("resets_at"))
    if resets is None:
        return None
    days = (resets - utcnow()).total_seconds() / 86400.0
    if days <= 0.5:
        return "5h"
    if days <= 8.0:
        return "weekly"
    if days <= 45.0:
        return "monthly"
    return None


# Providers whose numbered slots map to *named, hard-separated pools* rather
# than to one fixed label each. The pool name is the stable part; the duration
# suffix is composed per observation.
#
# Antigravity: CodexBar reports Gemini and Claude/GPT as two independent pools.
# When it populates `extraRateWindows` those windows arrive already titled
# ("Gemini 5-hour", "Claude/GPT weekly"); when it only fills primary/secondary
# they arrived here unnamed, which forked the same subscription into a "quota
# 1/2" identity that history and pool-splitting could not match to the titled
# one. Slot→pool order confirmed against snapshot history: every observed
# `quota 1` reset timestamp also appears as a `Gemini 5-hour` reset and none as
# a Claude/GPT one; slot 2 is Claude/GPT by elimination (there are only two).
_SLOT_POOL_PREFIXES: dict[str, tuple[str, ...]] = {
    "antigravity": ("Gemini", "Claude/GPT"),
}

_KIND_SUFFIX = {"5h": " 5-hour", "weekly": " weekly", "monthly": " monthly"}


def _cursor_rate_label(index: int, rate_labels: dict[str, Any]) -> str | None:
    slot = ("primary", "secondary", "tertiary")[index - 1]
    raw = rate_labels.get(slot)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    low = text.casefold()
    if low == "cursor":
        return "auto"
    if low == "total":
        return "total"
    if low == "third party":
        return "third-party"
    if low == "on demand":
        return "on-demand"
    return text.lower() if text.isascii() else text


def _slot_label(
    provider: str,
    index: int,
    block: dict[str, Any],
    *,
    rate_labels: dict[str, Any] | None = None,
) -> str:
    if provider == "cursor" and rate_labels:
        cursor_label = _cursor_rate_label(index, rate_labels)
        if cursor_label:
            return cursor_label
    slot_labels = _SLOT_LABELS.get(provider)
    if slot_labels:
        return slot_labels[index - 1]

    pools = _SLOT_POOL_PREFIXES.get(provider)
    if pools and index <= len(pools):
        return f"{pools[index - 1]}{_KIND_SUFFIX.get(_block_window_kind(block) or '', '')}"

    provider_name = {
        "codex": "Codex",
        "opencodego": "OpenCode Go",
        "zai": "z.ai",
        "clinepass": "ClinePass",
        "antigravity": "Google AI / Antigravity",
        "deepseek": "DeepSeek",
        "openrouter": "OpenRouter",
    }.get(provider, provider.replace("-", " ").title())
    # Reset distance, not just a declared duration, so a slot that reports only
    # `resetsAt` still gets named by its period instead of falling through.
    kind = _block_window_kind(block)
    # Always include index on the fallback path so two same-duration unnamed
    # slots never share a label and get collapsed later.
    if kind == "5h":
        return f"{provider_name} 5-hour quota ({index})"
    if kind == "weekly":
        return f"{provider_name} weekly quota ({index})"
    if kind == "monthly":
        return f"{provider_name} monthly quota ({index})"

    # A prepaid provider with neither a duration nor a reset is not reporting a
    # recurring window at all — it is reporting credit. "quota N (name not
    # supplied by CodexBar)" both misdescribed that and blamed CodexBar for
    # withholding a name that never existed. deepseek and openrouter arrive
    # here every collection, carrying a balance in `reset_description` and a
    # meaningless 0%-used. Gated on PREPAID_HINTS rather than on the missing
    # reset alone, because a *subscription* slot can also arrive empty (an
    # antigravity pool CodexBar knows about but has no data for this run), and
    # calling that "prepaid balance" would be a worse lie than the old label.
    if provider.lower() in PREPAID_HINTS and not parse_dt(block.get("resetsAt") or block.get("resets_at")):
        return f"{provider_name} prepaid balance"
    return f"{provider_name} quota {index}"


def _billing_kind(
    provider: str,
    usage: dict[str, Any] | None,
    windows: list[QuotaWindow] | None = None,
) -> BillingKind:
    # Usage-shape signals are checked before the generic provider-name hint list so a
    # provider that happens to be in PREPAID_HINTS but reports a real subscription
    # window (or an explicit pay-as-you-go usage blob) is not misclassified.
    if usage and usage.get("openAIAPIUsage"):
        return BillingKind.PAYG_API
    if usage and usage.get("openRouterUsage"):
        return BillingKind.PREPAID_BALANCE
    if windows and any(window.resets_at is not None for window in windows):
        return BillingKind.SUBSCRIPTION_WINDOW
    if provider.lower() in PREPAID_HINTS:
        return BillingKind.PREPAID_BALANCE
    if usage and any(usage.get(key) for key in ("primary", "secondary", "tertiary")):
        return BillingKind.UNKNOWN
    return BillingKind.UNKNOWN
