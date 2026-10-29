"""Orchestrate all collectors into a Snapshot with multi-source cross-checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from typing import Any

from aiuse.analysis.history import find_carried_resets
from aiuse.analysis.pace import independent_pool_key
from aiuse.analysis.selfdescribe import FRESH_THRESHOLD_SECONDS_DEFAULT
from aiuse.config import (
    SINGLE_PROVIDER_COLLECTORS,
    canonical_disabled_services,
    collection_policy_fingerprint,
    collector_serves_requested,
    requested_providers,
    timeout_for,
)
from aiuse.models import (
    PROVIDER_ID_ALIASES,
    AccountUsage,
    CrossCheck,
    QuotaWindow,
    Snapshot,
    canonical_provider,
    provider_display_name,
    utcnow,
)
from aiuse.tty import restore_stdin_tty, save_stdin_tty
from aiuse.usage_sources import ACP_SOURCE, pinned_usage_source

from .acp_usage import collect_acp, default_acp_log_dir
from .bailian import collect_bailian
from .base import CollectorNotInstalled, which
from .caam import collect_caam
from .caut import collect_caut
from .clinepass import collect_clinepass
from .codexbar import DEFAULT_TIMEOUT_BACKOFF_SECONDS as DEFAULT_CODEXBAR_TIMEOUT_BACKOFF
from .codexbar import collect_codexbar
from .cswap import collect_cswap
from .deepseek import collect_deepseek
from .grok import collect_grok
from .hermes import collect_hermes
from .muse import collect_muse
from .opencode_go import collect_opencode_go
from .opencode_zen import collect_opencode_zen
from .openrouter import collect_openrouter
from .openusage import app_cli_path as openusage_app_cli_path
from .openusage import collect_openusage_ai
from .openusage_sh import collect_openusage_sh
from .paseo import collect_paseo
from .qwencloud import collect_qwencloud
from .sipb import collect_sipb
from .throttle import min_intervals
from .tokscale import collect_tokscale

# Prefer earlier sources for *selection* (what drives the ladder). All live
# sources still participate in cross-checks. Correctness > minimising comparisons.
DEFAULT_SOURCE_PRIORITY: tuple[str, ...] = (
    "codexbar",
    "caut",
    "openusage_ai",
    "openusage_sh",
    "opencode_go",
    "opencode_zen",
    "openrouter",
    "deepseek",
    "tokscale",
    "paseo",
    "clinepass",
    "hermes",
    "muse",
    "qwencloud",
    "bailian",
    "acp",
)

PROVIDER_SOURCE_PRIORITY: dict[str, tuple[str, ...]] = {
    # cswap is the multi-account Claude authority when enabled.
    "claude": (
        "cswap",
        "codexbar",
        "caut",
        "openusage_ai",
        "tokscale",
        "openusage_sh",
        "paseo",
        "hermes",
        "caam",
        "acp",
    ),
    # caam reads vault profiles only, so it stays the last resort behind every
    # direct source (never ahead of grok_billing's own billing number).
    "codex": ("codexbar", "caut", "openusage_ai", "openusage_sh", "tokscale", "paseo", "hermes", "caam", "acp"),
    "grok": (
        "codexbar",
        "caut",
        "openusage_ai",
        "openusage_sh",
        "tokscale",
        "hermes",
        "grok_billing",
        "paseo",
        "caam",
        "acp",
    ),
    "cursor": ("codexbar", "caut", "openusage_ai", "openusage_sh", "tokscale", "hermes", "caam", "acp"),
    # tokscale keeps distinct Copilot premium vs chat/completions semantics.
    "copilot": ("tokscale", "codexbar", "caut", "openusage_ai", "openusage_sh", "paseo", "hermes", "acp"),
    # Native qwencloud CLI is the authority; CodexBar qwen-cloud (cookies) cross-checks.
    "qwencloud": ("qwencloud", "codexbar", "acp"),
    # Native bl CLI is the authority; CodexBar alibaba-*-plan (cookies) cross-checks.
    "alibaba": ("bailian", "codexbar", "acp"),
    # Native /user/balance is DeepSeek's own number; CodexBar cross-checks (#16).
    "deepseek": ("deepseek", "codexbar", "caut", "openusage_ai", "openusage_sh", "tokscale", "acp"),
    # Native /go page sees an expired plan; CodexBar local $caps cannot.
    "opencode-go": ("opencode_go", "codexbar", "caut", "openusage_ai", "openusage_sh", "tokscale", "acp"),
}

SOURCE_LABELS: dict[str, str] = {
    "cswap": "cswap",
    "codexbar": "CodexBar",
    "caam": "caam",
    "caut": "caut",
    "openusage_ai": "OpenUsage.ai",
    "openusage_sh": "OpenUsage.sh",
    "opencode_go": "OpenCode Go (native)",
    "opencode_zen": "OpenCode Zen (native)",
    "openrouter": "OpenRouter (native)",
    "deepseek": "DeepSeek (native)",
    "tokscale": "tokscale",
    "paseo": "Paseo (daemon)",
    "clinepass": "ClinePass (native)",
    "hermes": "Hermes (local)",
    "muse": "Muse (native)",
    "qwencloud": "QwenCloud (native)",
    "bailian": "Bailian (native)",
    "grok_billing": "Grok Billing (native)",
    "acp": "ACP logs",
}

# Collectors that serve exactly one provider live in config.py
# (SINGLE_PROVIDER_COLLECTORS) so validate_config can warn about redundant
# collector overrides without an import cycle.


def _disabled_services(config: dict[str, Any]) -> dict[str, str]:
    """Canonical provider -> reason from top-level ``[disabled_services]``."""
    return canonical_disabled_services(config)


def _provider_names(raw: Any) -> frozenset[str]:
    """Provider ids from a TOML list or a comma-separated string."""
    if isinstance(raw, str):
        items: list[Any] = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return frozenset()
    return frozenset(canonical_provider(str(item).strip()) for item in items if str(item).strip())


def _codexbar_timeout_settings(config: dict[str, Any], codexbar_cfg: dict[str, Any]) -> tuple[dict[str, float], float]:
    """Per-provider CodexBar timeouts and the hang backoff from ``[collectors.codexbar]``.

    ``provider_timeouts`` maps a CodexBar provider id to seconds; a CLI
    ``--timeout`` (``timeouts.force``) still wins over every one of them.
    ``timeout_backoff`` is the seconds a provider that hung is skipped (0 off).
    """
    provider_timeouts: dict[str, float] = {}
    timeouts_cfg = config.get("timeouts") if isinstance(config.get("timeouts"), dict) else {}
    raw = codexbar_cfg.get("provider_timeouts")
    if isinstance(raw, dict) and (timeouts_cfg or {}).get("force") is None:
        for provider, seconds in raw.items():
            try:
                value = float(seconds)
            except (TypeError, ValueError):
                continue
            if value > 0:
                provider_timeouts[str(provider).strip().lower()] = value
    try:
        backoff = float(codexbar_cfg.get("timeout_backoff", DEFAULT_CODEXBAR_TIMEOUT_BACKOFF))
    except (TypeError, ValueError):
        backoff = DEFAULT_CODEXBAR_TIMEOUT_BACKOFF
    return provider_timeouts, max(0.0, backoff)


# Canonical provider identity lives in models.PROVIDER_ID_ALIASES so collection
# and the history/analysis passes cannot drift onto different spellings.
_PROVIDER_ALIASES = PROVIDER_ID_ALIASES


def run_collectors(config: dict[str, Any] | None = None) -> Snapshot:
    """Collect from every enabled source, leaving the terminal as it was found.

    A collector child that grabs the terminal (a TUI opened on /dev/tty, a tool
    killed at its timeout in raw mode) must not corrupt the report printed right
    after collection, so termios is restored here, before any rendering, as well
    as at process exit (cli.main).
    """
    saved_tty = save_stdin_tty()
    try:
        return _run_collectors(config)
    finally:
        restore_stdin_tty(saved_tty)


def _run_collectors(config: dict[str, Any] | None = None) -> Snapshot:
    config = config or {}
    collectors_cfg = config.get("collectors") or {}
    snapshot = Snapshot(collected_at=utcnow())
    intervals = min_intervals(config)
    disabled_services = _disabled_services(config)
    disabled_providers = set(disabled_services)
    provider_filter = requested_providers(config)
    raw_analysis = config.get("analysis")
    analysis_cfg = raw_analysis if isinstance(raw_analysis, dict) else {}
    try:
        fresh_threshold = float(analysis_cfg.get("fresh_threshold_seconds") or FRESH_THRESHOLD_SECONDS_DEFAULT)
    except (TypeError, ValueError):
        fresh_threshold = FRESH_THRESHOLD_SECONDS_DEFAULT

    # Each collector shells out (or hits loopback) independently — run concurrently.
    # Correctness: long default timeouts; all enabled sources always queried.
    jobs: list[tuple[str, Callable[[], list[AccountUsage]]]] = []
    if _enabled(collectors_cfg, "cswap"):
        cswap_timeout = timeout_for(config, "cswap")
        jobs.append(("cswap", partial(collect_cswap, timeout=cswap_timeout)))
    if _enabled(collectors_cfg, "codexbar"):
        codexbar_cfg = collectors_cfg.get("codexbar") if isinstance(collectors_cfg.get("codexbar"), dict) else {}
        providers = (codexbar_cfg or {}).get("providers", "enabled")
        codexbar_timeout = timeout_for(config, "codexbar")
        discovery_timeout = timeout_for(config, "codexbar_discovery")
        provider_timeouts, timeout_backoff = _codexbar_timeout_settings(config, codexbar_cfg or {})
        use_cache = bool((codexbar_cfg or {}).get("use_cache", True))
        raw_max_age = (codexbar_cfg or {}).get("max_cache_age_seconds")
        if raw_max_age is None:
            raw_max_age = (codexbar_cfg or {}).get("max_cache_age", 300.0)
        try:
            max_cache_age = float(raw_max_age)
        except (TypeError, ValueError):
            max_cache_age = 300.0
        cache_path = (codexbar_cfg or {}).get("cache_path")
        all_accounts = _provider_names((codexbar_cfg or {}).get("all_accounts"))
        include_disabled = bool((codexbar_cfg or {}).get("include_disabled", False))
        http_url = (codexbar_cfg or {}).get("http_url")
        http_token = (codexbar_cfg or {}).get("http_token")
        jobs.append(
            (
                "codexbar",
                partial(
                    collect_codexbar,
                    providers=providers,
                    timeout=codexbar_timeout,
                    discovery_timeout=discovery_timeout,
                    min_intervals=intervals,
                    skip_providers=frozenset(disabled_providers),
                    provider_timeouts=provider_timeouts,
                    timeout_backoff=timeout_backoff,
                    use_cache=use_cache,
                    max_cache_age=max_cache_age,
                    cache_path=cache_path,
                    all_accounts_providers=all_accounts,
                    include_disabled=include_disabled,
                    http_url=http_url if isinstance(http_url, str) else None,
                    http_token=http_token if isinstance(http_token, str) else None,
                ),
            )
        )
    if _enabled(collectors_cfg, "grok_billing"):
        jobs.append(("grok_billing", partial(collect_grok, timeout=timeout_for(config, "grok_billing"))))
    if _enabled(collectors_cfg, "caut"):
        caut_cfg = collectors_cfg.get("caut") if isinstance(collectors_cfg.get("caut"), dict) else {}
        caut_providers = (caut_cfg or {}).get("providers", "all")
        caut_timeout = timeout_for(config, "caut")
        jobs.append(
            (
                "caut",
                partial(collect_caut, providers=str(caut_providers), timeout=caut_timeout),
            )
        )
    if _enabled(collectors_cfg, "caam"):
        jobs.append(("caam", partial(collect_caam, timeout=timeout_for(config, "caam"))))
    if _enabled(collectors_cfg, "openusage_ai"):
        ou_cfg = collectors_cfg.get("openusage_ai") if isinstance(collectors_cfg.get("openusage_ai"), dict) else {}
        ou_timeout = timeout_for(config, "openusage_ai")
        force = bool((ou_cfg or {}).get("force_refresh", True))
        launch = bool((ou_cfg or {}).get("try_launch_app", True))
        base = str((ou_cfg or {}).get("base_url") or "http://127.0.0.1:6736")
        jobs.append(
            (
                "openusage_ai",
                partial(
                    collect_openusage_ai,
                    timeout=ou_timeout,
                    force_refresh=force,
                    try_launch_app=launch,
                    base_url=base,
                    min_intervals=intervals,
                ),
            )
        )
    if _enabled(collectors_cfg, "openusage_sh"):
        jobs.append(("openusage_sh", partial(collect_openusage_sh, timeout=timeout_for(config, "openusage_sh"))))
    if _enabled(collectors_cfg, "opencode_go"):
        jobs.append(("opencode_go", partial(collect_opencode_go, timeout=timeout_for(config, "opencode_go"))))
    if _enabled(collectors_cfg, "opencode_zen"):
        jobs.append(("opencode_zen", partial(collect_opencode_zen, timeout=timeout_for(config, "opencode_zen"))))
    if _enabled(collectors_cfg, "openrouter"):
        jobs.append(("openrouter", partial(collect_openrouter, timeout=timeout_for(config, "openrouter"))))
    if _enabled(collectors_cfg, "deepseek"):
        jobs.append(("deepseek", partial(collect_deepseek, timeout=timeout_for(config, "deepseek"))))
    if _enabled(collectors_cfg, "sipb"):
        sipb_cfg = collectors_cfg.get("sipb") if isinstance(collectors_cfg.get("sipb"), dict) else {}
        ignore_expired = bool(
            (sipb_cfg or {}).get("ignore_expired_certificates", False) or not (sipb_cfg or {}).get("verify_ssl", True)
        )
        jobs.append(
            (
                "sipb",
                partial(
                    collect_sipb,
                    timeout=timeout_for(config, "sipb"),
                    ignore_expired_certificates=ignore_expired,
                ),
            )
        )
    if _enabled(collectors_cfg, "tokscale"):
        tokscale_timeout = timeout_for(config, "tokscale")
        jobs.append(("tokscale", partial(collect_tokscale, timeout=tokscale_timeout)))
    if _enabled(collectors_cfg, "paseo"):
        jobs.append(("paseo", partial(collect_paseo, timeout=timeout_for(config, "paseo"))))
    if _enabled(collectors_cfg, "clinepass"):
        clinepass_cfg = collectors_cfg.get("clinepass") if isinstance(collectors_cfg.get("clinepass"), dict) else {}
        jobs.append(
            (
                "clinepass",
                partial(
                    collect_clinepass,
                    timeout=timeout_for(config, "clinepass"),
                    accounts=(clinepass_cfg or {}).get("accounts"),
                ),
            )
        )
    if _enabled(collectors_cfg, "hermes"):
        jobs.append(("hermes", partial(collect_hermes, timeout=timeout_for(config, "hermes"))))
    if _enabled(collectors_cfg, "muse"):
        jobs.append(("muse", partial(collect_muse, timeout=timeout_for(config, "muse"))))
    if _enabled(collectors_cfg, "qwencloud"):
        jobs.append(("qwencloud", partial(collect_qwencloud, timeout=timeout_for(config, "qwencloud"))))
    if _enabled(collectors_cfg, "bailian"):
        jobs.append(("bailian", partial(collect_bailian, timeout=timeout_for(config, "bailian"))))
    if _enabled(collectors_cfg, "acp"):
        acp_cfg = collectors_cfg.get("acp") if isinstance(collectors_cfg.get("acp"), dict) else {}
        log_dir = (acp_cfg or {}).get("log_dir") or None
        try:
            max_age_hours = float((acp_cfg or {}).get("max_age_hours", 168))
        except (TypeError, ValueError):
            max_age_hours = 168.0
        jobs.append(("acp", partial(collect_acp, log_dir=log_dir, max_age_hours=max_age_hours)))

    # Provider-only collectors of a disabled service never run (see
    # SINGLE_PROVIDER_COLLECTORS) — the rows would be filtered out below anyway.
    if disabled_providers:
        jobs = [(name, fn) for name, fn in jobs if SINGLE_PROVIDER_COLLECTORS.get(name) not in disabled_providers]
    if provider_filter is not None:
        jobs = [(name, fn) for name, fn in jobs if collector_serves_requested(name, provider_filter)]

    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {name: pool.submit(fn) for name, fn in jobs}
        for name, _ in jobs:
            try:
                snapshot.accounts.extend(futures[name].result())
            except CollectorNotInstalled:
                snapshot.uninstalled_tools.append(name)
            except Exception as exc:  # noqa: BLE001
                snapshot.collector_errors.append(f"{name}: {exc}")

    snapshot.disabled_services = disabled_services
    if disabled_providers:
        # A disabled service is off-limits overall: drop every row for it so
        # no surface (report, ladder, matrix, JSON, history, cache) presents
        # its quota as usable. Canonical aliases (e.g. "grok-build") match too.
        snapshot.accounts = [
            account for account in snapshot.accounts if canonical_provider(account.provider) not in disabled_providers
        ]
    if provider_filter is not None:
        snapshot.accounts = [
            account for account in snapshot.accounts if canonical_provider(account.provider) in provider_filter
        ]

    snapshot.hidden_pools = _apply_hidden_pools(snapshot.accounts, config)

    _merge_grok_extra_credits(snapshot.accounts)
    usage_sources = config.get("usage_sources") if isinstance(config.get("usage_sources"), dict) else None
    snapshot.accounts, snapshot.cross_checks = _select_and_cross_check(
        snapshot.accounts,
        cswap_authoritative=_enabled(collectors_cfg, "cswap"),
        account_aliases=config.get("account_aliases"),
        usage_sources=usage_sources,
        fresh_threshold_seconds=fresh_threshold,
    )
    _carry_forward_resets(snapshot)
    _apply_lapsed_accounts(snapshot.accounts, config)
    _apply_client_limit_notes(snapshot.accounts, config)
    snapshot.config_fingerprint = collection_policy_fingerprint(config)
    return snapshot


def _apply_hidden_pools(accounts: list[AccountUsage], config: dict[str, Any]) -> dict[str, str]:
    """Drop windows matching [hidden_pools]; drop accounts left with no windows.

    A row that never had windows (SIPB up/down, a collector error) stays.
    Hiding Cursor's grok-bot pool must not delete that status row.
    """
    from aiuse.analysis.pool_keys import hidden_pool_rules, window_matches_rule
    from aiuse.analysis.selfdescribe import pool_family_id

    rules = hidden_pool_rules(config)
    if not rules:
        return {}
    hidden: dict[str, str] = {}
    kept: list[AccountUsage] = []
    for account in accounts:
        had_windows = bool(account.windows)
        kept_windows: list = []
        for window in account.windows:
            family = pool_family_id(account.provider, window.label, window.window_id)
            matched = next(
                (reason for p, f, reason in rules if window_matches_rule(account.provider, family, p, f)),
                None,
            )
            if matched:
                key = f"{account.provider}/{family}" if family else account.provider
                hidden[key] = matched
            else:
                kept_windows.append(window)
        has_balance = account.balance_usd is not None or account.credits_remaining is not None
        has_usage = account.usage_credits is not None and account.usage_credits.used is not None
        if kept_windows or has_balance or has_usage or not had_windows:
            account.windows = kept_windows
            kept.append(account)
    accounts[:] = kept
    return hidden


def _apply_client_limit_notes(accounts: list[AccountUsage], config: dict[str, Any] | None) -> None:
    """Note a per-client lockout the quota windows cannot show (issue #33).

    Passive: aiuse.client_limits reads the client's own logs and sends no
    request. Numbers and usability stay as collected; only a note is added,
    so the board and --full say why a route with headroom may still fail.
    """
    if not any(account.provider == "antigravity" for account in accounts):
        return
    from aiuse.client_limits import load_client_limits

    limits = load_client_limits(config)
    for account in accounts:
        for limit in limits.get(account.provider, []):
            message = str(limit.get("message") or "")
            if message and message not in account.notes:
                account.notes = [*account.notes, message]


def _merge_grok_extra_credits(accounts: list[AccountUsage]) -> None:
    """Fold Grok Extra Usage Credits into CodexBar/OpenUsage grok rows.

    The billing row is a supplement: when another collector already carries a
    ``grok`` row, the wallet folds in as ``usage_credits`` (and the billing
    plan window fills a host that has none) and the standalone row is dropped.
    When nothing else reports grok (e.g. CodexBar disabled), the row is kept —
    dropping it would hide a known prepaid wallet behind a missing host row.
    """
    extra = next(
        (
            account
            for account in accounts
            if account.provider == "grok"
            and account.source == "grok_billing"
            and (account.usage_credits is not None or account.windows)
        ),
        None,
    )
    has_host = any(account.provider == "grok" and account.source != "grok_billing" for account in accounts)
    keep_standalone = extra is not None and not has_host
    accounts[:] = [
        account
        for account in accounts
        if not (account.provider == "grok" and account.source == "grok_billing" and not keep_standalone)
    ]
    if extra is None or not has_host:
        return
    for account in accounts:
        if account.provider != "grok":
            continue
        if account.usage_credits is None and extra.usage_credits is not None:
            account.usage_credits = extra.usage_credits
        if not account.windows and extra.windows:
            account.windows = list(extra.windows)
        for note in extra.notes:
            if note not in account.notes:
                account.notes.append(note)


def _carry_forward_resets(snapshot: Snapshot) -> None:
    """Keep the countdown of an in-use window whose selected source dropped its reset.

    Selection keeps one source's rows, and not every source reports a reset on
    every window (CodexBar sends none for Antigravity's 5-hour windows while
    OpenUsage does), so an in-use window can lose a reset that an earlier
    reading measured. History supplies it back, marked as an estimate (see
    ``history.find_carried_resets`` for when that is safe). Running before the
    snapshot is returned means the saved snapshot, ``--json`` and the screen
    all carry it, and the next collection finds it in history in turn.
    """
    for window, carried in find_carried_resets(snapshot):
        window.carry_reset(carried.resets_at, source=carried.source, collected_at=carried.collected_at)


def _apply_lapsed_accounts(accounts: list[AccountUsage], config: dict[str, Any] | None) -> None:
    """Render operator-declared dead subscriptions as empty, not on-pace.

    A not-renewed plan keeps serving stale collector cache — cswap ``lastGood``
    windows whose resets are still ahead — that looks like usable quota, but no
    collector can see renewal state. ``analysis.lapsed_accounts`` maps
    ``"provider/account"`` to a reason (or ``true``); matching accounts get a
    single empty subscription window mirroring the expired-plan shape
    (collectors/opencode_go.py), so every surface (ladder, matrix, JSON,
    history) treats them as depleted instead of learning phantom quota.
    """
    analysis = (config or {}).get("analysis")
    lapsed = analysis.get("lapsed_accounts") if isinstance(analysis, dict) else None
    if not isinstance(lapsed, dict) or not lapsed:
        return
    rules: list[tuple[str, str, str]] = []
    for key, value in lapsed.items():
        provider, _, account_part = str(key).partition("/")
        provider = canonical_provider(provider.strip())
        reason = value.strip() if isinstance(value, str) and value.strip() else ""
        if provider:
            rules.append((provider, account_part.strip().casefold(), reason))
    for account in accounts:
        for provider, account_part, reason in rules:
            if canonical_provider(account.provider) != provider:
                continue
            if account_part and (account.account or "").strip().casefold() != account_part:
                continue
            description = reason or "subscription not renewed"
            if reason and not any(marker in reason.casefold() for marker in ("expired", "not renewed", "lapsed")):
                description = f"subscription not renewed ({reason})"
            account.plan = "expired"
            account.windows = [
                QuotaWindow(
                    label=f"{provider_display_name(provider)} subscription",
                    used_percent=None,
                    remaining_percent=0.0,
                    reset_description=description,
                )
            ]
            account.balance_usd = None
            account.usage_credits = None
            account.credits_remaining = None
            account.error = None
            account.notes.append(f"analysis.lapsed_accounts marks this account empty: {description}.")
            break


def _enabled(collectors_cfg: dict[str, Any], name: str) -> bool:
    section = collectors_cfg.get(name)
    if section is None:
        return True
    if isinstance(section, bool):
        return section
    if isinstance(section, dict):
        return bool(section.get("enabled", True))
    return True


def _canonical_provider(provider: str) -> str:
    return canonical_provider(provider)


def _source_priority(provider: str, *, cswap_authoritative: bool) -> tuple[str, ...]:
    if provider == "claude" and not cswap_authoritative:
        # cswap disabled: fall through to multi-source peers only.
        return tuple(s for s in PROVIDER_SOURCE_PRIORITY["claude"] if s != "cswap")
    return PROVIDER_SOURCE_PRIORITY.get(provider, DEFAULT_SOURCE_PRIORITY)


def _select_and_cross_check(
    accounts: list[AccountUsage],
    *,
    cswap_authoritative: bool,
    account_aliases: dict[str, Any] | None = None,
    usage_sources: dict[str, Any] | None = None,
    fresh_threshold_seconds: float = FRESH_THRESHOLD_SECONDS_DEFAULT,
) -> tuple[list[AccountUsage], list[CrossCheck]]:
    """Select report rows while cross-checking all live sources.

    Selection picks one primary source per provider (priority list). Cross-checks
    compare every pair of live sources so adding caut/OpenUsage later does not
    require new pairwise branches.

    ACP context rows are not a plan-percent peer. Blend mode keeps the quota
    primary and copies the context reading onto it. A ``[usage_sources]`` pin
    drops every other source for that provider.
    """

    for account in accounts:
        account.provider = _canonical_provider(account.provider)
    _drop_reset_passed_windows(accounts)
    _normalize_account_names(accounts, account_aliases)

    providers = sorted({account.provider for account in accounts}, key=str.casefold)
    selected: list[AccountUsage] = []
    checks: list[CrossCheck] = []

    for provider in providers:
        rows = [account for account in accounts if account.provider == provider]
        by_source: dict[str, list[AccountUsage]] = defaultdict(list)
        for account in rows:
            by_source[account.source].append(account)

        pin = pinned_usage_source(usage_sources, provider)
        acp_rows = list(by_source.get(ACP_SOURCE, []))
        if pin:
            pinned_rows = by_source.get(pin, [])
            if not pinned_rows:
                selected.append(
                    AccountUsage(
                        source=pin,
                        provider=provider,
                        error=(
                            f"[usage_sources] pins {pin} for {provider}, and that source returned nothing this run."
                        ),
                        notes=["Other usage sources were ignored because this provider is pinned."],
                    )
                )
                continue
            by_source = defaultdict(list, {pin: pinned_rows})
            if pin != ACP_SOURCE:
                acp_rows = []

        priority = _source_priority(provider, cswap_authoritative=cswap_authoritative)
        account_groups = _account_groups(provider, by_source)
        chosen: list[AccountUsage] = []
        if account_groups:
            # Multi-account provider: the best source per account, so an account
            # only one tool reports is kept instead of dropped with its source.
            primary = None
            for group in account_groups:
                group_primary = _pick_primary_source(
                    provider,
                    group,
                    priority,
                    cswap_authoritative=cswap_authoritative,
                    fresh_threshold_seconds=fresh_threshold_seconds,
                )
                chosen.extend(_rows_from_primary(provider, group_primary, group, rows))
        else:
            primary = _pick_primary_source(
                provider,
                by_source,
                priority,
                cswap_authoritative=cswap_authoritative,
                fresh_threshold_seconds=fresh_threshold_seconds,
            )
            chosen = _rows_from_primary(provider, primary, by_source, rows)

        if not chosen and acp_rows:
            chosen = list(acp_rows)
        elif chosen and acp_rows and primary != ACP_SOURCE:
            _attach_acp_context(chosen, acp_rows)
        if len(chosen) == 1 and not chosen[0].account:
            named = [a.account.strip() for a in rows if a.source != ACP_SOURCE and a.account and a.account.strip()]
            if named and len({name.casefold() for name in named}) == 1:
                chosen[0].account = named[0]
        selected.extend(chosen)

        quota_by_source = {source: source_rows for source, source_rows in by_source.items() if source != ACP_SOURCE}
        checks.extend(
            _provider_multi_source_cross_checks(
                provider,
                quota_by_source,
                primary=None if primary == ACP_SOURCE else primary,
                cswap_authoritative=cswap_authoritative,
            )
        )

    return selected, checks


def _rows_from_primary(
    provider: str,
    primary: str | None,
    by_source: dict[str, list[AccountUsage]],
    rows: list[AccountUsage],
) -> list[AccountUsage]:
    """The rows to surface once ``primary`` is picked (``rows`` when none was)."""
    if primary is None:
        # No priority source present — keep whatever we have.
        return rows
    primary_rows = by_source.get(primary, [])
    if primary == "cswap":
        # Keep every cswap slot (live + errored) for multi-account identity.
        return primary_rows
    live = [a for a in primary_rows if _has_live_data(a)]
    if live:
        return live
    # A Max-plan OpenUsage.ai row with zero windows is an identity
    # label, not a Claude reading. Keeping it is what showed Claude
    # with no usage when CodexBar and cswap returned nothing.
    if provider == "claude" and primary == "openusage_ai" and not any(row.error for row in primary_rows):
        return []
    return primary_rows


def _multi_account_labels(provider: str, by_source: dict[str, list[AccountUsage]]) -> list[str]:
    """Casefolded labels of a provider some single source reports several accounts for.

    Empty unless one non-ACP source names at least two distinct accounts (live
    or errored rows alike). Two tools naming one account differently (email
    versus a CLI label) is not multi-account evidence, so single-account users
    keep per-provider selection. Claude stays on its cswap authority path.
    """
    if provider == "claude":
        return []
    labels: list[str] = []
    for source, source_rows in by_source.items():
        if source == ACP_SOURCE:
            continue
        names = list(
            dict.fromkeys(a.account.strip().casefold() for a in source_rows if a.account and a.account.strip())
        )
        if len(names) > 1:
            labels.extend(name for name in names if name not in labels)
    return labels


def _account_groups(provider: str, by_source: dict[str, list[AccountUsage]]) -> list[dict[str, list[AccountUsage]]]:
    """Per-account ``by_source`` maps for a multi-account provider, else [].

    Rows labelled with another (or no) name cannot be tied to one of the
    accounts, so they stay out of selection (an ``[account_aliases]`` entry
    maps them); the cross-checks still see them.
    """
    labels = _multi_account_labels(provider, by_source)
    groups: dict[str, dict[str, list[AccountUsage]]] = {label: defaultdict(list) for label in labels}
    for source, source_rows in by_source.items():
        if source == ACP_SOURCE:
            continue
        for account in source_rows:
            key = (account.account or "").strip().casefold()
            if key in groups:
                groups[key][source].append(account)
    return [groups[label] for label in labels]


def _attach_acp_context(accounts: list[AccountUsage], acp_rows: list[AccountUsage]) -> None:
    """Copy the newest ACP context reading onto the quota rows that drive the ladder."""
    donor = max(acp_rows, key=lambda row: row.collected_at or datetime.min.replace(tzinfo=timezone.utc))
    if donor.context_usage is None:
        return
    for account in accounts:
        if account.context_usage is None:
            account.context_usage = donor.context_usage
        for note in donor.notes:
            if note not in account.notes:
                account.notes.append(note)


def _pick_primary_source(
    provider: str,
    by_source: dict[str, list[AccountUsage]],
    priority: Sequence[str],
    *,
    cswap_authoritative: bool,
    fresh_threshold_seconds: float = FRESH_THRESHOLD_SECONDS_DEFAULT,
) -> str | None:
    """Return the source id to surface for this provider, or None."""
    if provider == "claude" and cswap_authoritative and "cswap" in by_source:
        cswap_rows = by_source["cswap"]
        cswap_live = [a for a in cswap_rows if _has_live_data(a)]
        if cswap_live:
            if len(cswap_rows) > 1:
                return "cswap"
            # Single-slot cswap stays first in priority, but a known-stale
            # hydrated/cached reading yields to a known-fresh authoritative peer.
            live_sources = [
                source for source in priority if any(_has_live_data(account) for account in by_source.get(source, []))
            ]
            authoritative = [source for source in live_sources if not _source_is_local_estimate(by_source[source])]
            return _prefer_fresh_source(authoritative or live_sources, by_source, fresh_threshold_seconds)
        # Fall through to next live peer when cswap has no usable quota.
        for source in priority:
            if source == "cswap":
                continue
            if any(_has_live_data(a) for a in by_source.get(source, [])):
                return source
        return "cswap" if by_source.get("cswap") else None

    live_sources = [
        source for source in priority if any(_has_live_data(account) for account in by_source.get(source, []))
    ]
    if live_sources:
        authoritative = [source for source in live_sources if not _source_is_local_estimate(by_source[source])]
        candidates = authoritative or live_sources
        return _prefer_fresh_source(candidates, by_source, fresh_threshold_seconds)
    for source in priority:
        if by_source.get(source):
            return source
    # Unknown source present (future collectors) — prefer first with live data.
    for source, rows in by_source.items():
        if any(_has_live_data(a) for a in rows):
            return source
    return next(iter(by_source), None)


def _independent_pools(rows: list[AccountUsage]) -> set[str]:
    """Independent allotments a source actually reported (Gemini vs Claude/GPT)."""
    pools: set[str] = set()
    for account in rows:
        if not _has_live_data(account):
            continue
        for window in account.windows:
            key = independent_pool_key(window.label, window_id=window.window_id, provider=account.provider)
            if key:
                pools.add(key)
    return pools


def _prefer_fresh_source(
    candidates: list[str],
    by_source: dict[str, list[AccountUsage]],
    fresh_threshold_seconds: float,
) -> str:
    """Pick the first candidate, demoting a known-stale reading behind a fresh one.

    A stale reading can hide a fresh exhaustion (the agy case: CodexBar's hours-old
    row would keep winning over OpenUsage's fresh row purely on priority order).
    Only demote when a source is provably stale AND a provably fresh peer exists;
    unknown-age sources keep their priority position.

    A fresh source that omits an independent pool does not count. OpenUsage's
    Antigravity payload is only the Claude/GPT windows (2026-10-22); CodexBar
    also reports the Gemini weekly that the client enforces. Letting the fresh
    omission win hid that weekly at 100% used.
    """
    now = utcnow()
    ages = {source: _source_age(by_source[source], now) for source in candidates}
    known_fresh: list[str] = []
    any_stale = False
    for source in candidates:
        age = ages[source]
        if age is None:
            continue
        if age <= fresh_threshold_seconds:
            known_fresh.append(source)
        else:
            any_stale = True
    if known_fresh and any_stale:
        fresh = known_fresh[0]
        fresh_pools = _independent_pools(by_source[fresh])
        for source in candidates:
            if source == fresh:
                return fresh
            if _independent_pools(by_source[source]) - fresh_pools:
                return source
        return fresh
    return candidates[0]


def _consolidate_accounts(
    accounts: list[AccountUsage],
    *,
    cswap_authoritative: bool,
) -> list[AccountUsage]:
    """Compatibility wrapper for callers that only need selected rows."""
    selected, _ = _select_and_cross_check(accounts, cswap_authoritative=cswap_authoritative)
    return selected


def _drop_reset_passed_windows(accounts: list[AccountUsage], *, now: datetime | None = None) -> None:
    """Drop cached recurring windows whose reset timestamp has passed since collection.

    When a collector serves a cached reading (e.g. CodexBar reading claude-swap's
    cache while cswap needs re-login), any window measured before ``resets_at``
    whose ``resets_at <= now`` has already reset; keeping its pre-reset
    ``used_percent`` reports false usage and triggers spurious cross-check warnings.
    """
    now = now or utcnow()
    for account in accounts:
        if account.collected_at is None or not account.windows:
            continue
        kept: list[QuotaWindow] = []
        dropped: list[str] = []
        for w in account.windows:
            if (
                w.resets_at is not None
                and w.refill_capacity_unit != "usd"
                and account.collected_at < w.resets_at <= now
            ):
                dropped.append(w.label)
            else:
                kept.append(w)
        if dropped:
            account.windows = kept
            account.notes.append(
                f"Dropped pre-reset cached window(s) whose reset time has passed: {', '.join(dropped)}."
            )
            if (
                not account.windows
                and account.balance_usd is None
                and account.credits_remaining is None
                and account.usage_credits is None
                and not account.error
            ):
                account.error = (
                    f"Cached {provider_display_name(account.provider)} usage from "
                    f"{_source_name(account.source)} expired at window reset."
                )


def _normalize_account_names(accounts: list[AccountUsage], aliases: dict[str, Any] | None) -> None:
    """Apply explicit aliases, then only provably one-to-one automatic aliases.

    Local tools often expose stable labels such as ``codex-cli`` rather than an
    email.  One named account from each identifying source is safely one
    logical account; anonymous rows are neutral. Multi-account providers are
    never guessed and need TOML mapping.
    """
    aliases = aliases if isinstance(aliases, dict) else {}
    for account in accounts:
        provider_aliases = aliases.get(account.provider)
        source_aliases = provider_aliases.get(account.source) if isinstance(provider_aliases, dict) else None
        if account.account and isinstance(source_aliases, dict):
            mapped = source_aliases.get(account.account)
            if isinstance(mapped, str) and mapped.strip():
                account.account = mapped.strip()

    for provider in {account.provider for account in accounts}:
        live = [account for account in accounts if account.provider == provider and _has_live_data(account)]
        by_source: dict[str, set[str]] = defaultdict(set)
        for account in live:
            if account.account and account.account.strip():
                by_source[account.source].add(account.account.strip())
        # Anonymous sources contribute no identity evidence, so they neither
        # authorize nor block a one-to-one mapping.  A source with multiple
        # *named* accounts does block it: that must be configured explicitly.
        if len(by_source) < 2 or any(len(names) != 1 for names in by_source.values()):
            continue
        # A source naming several accounts (an errored one included) is
        # multi-account: one live row each does not make them one account.
        provider_rows: dict[str, list[AccountUsage]] = defaultdict(list)
        for account in accounts:
            if account.provider == provider:
                provider_rows[account.source].append(account)
        if _multi_account_labels(provider, provider_rows):
            continue
        # Prefer the selected source's display identity.  It is already the
        # source that drives the ladder, and every source has exactly one row.
        priority = _source_priority(provider, cswap_authoritative="cswap" in by_source)
        canonical = next((next(iter(by_source[source])) for source in priority if source in by_source), None)
        if canonical is None:
            canonical = next(iter(next(iter(by_source.values()))))
        for account in live:
            if account.account:
                account.account = canonical


def _has_live_data(account: AccountUsage) -> bool:
    return not account.error and (
        bool(account.windows)
        or account.balance_usd is not None
        or account.credits_remaining is not None
        or account.usage_credits is not None
    )


def _source_age(rows: list[AccountUsage], now: datetime) -> float | None:
    """Newest reading age across a source's live rows, or None when unknown.

    A source that never sets ``collected_at`` (several collectors don't) is
    unknown-age, distinct from a source whose reading is provably stale — the
    selector only demotes a source when it is known-stale AND a known-fresh
    peer exists.
    """
    ages = [
        max(0.0, (now - row.collected_at).total_seconds())
        for row in rows
        if _has_live_data(row) and row.collected_at is not None
    ]
    return min(ages) if ages else None


def _provider_multi_source_cross_checks(
    provider: str,
    by_source: dict[str, list[AccountUsage]],
    *,
    primary: str | None,
    cswap_authoritative: bool,
) -> list[CrossCheck]:
    """Cross-check all live sources for one provider (all pairs)."""
    checks: list[CrossCheck] = []

    live_by_source: dict[str, list[AccountUsage]] = {
        source: [a for a in rows if _has_live_data(a)] for source, rows in by_source.items()
    }
    live_by_source = {s: rows for s, rows in live_by_source.items() if rows}
    source_ids = sorted(live_by_source.keys())

    if provider == "claude" and cswap_authoritative:
        checks.extend(_claude_authority_checks(by_source, live_by_source, primary=primary))

    if len(source_ids) < 2:
        if len(source_ids) == 1:
            only = source_ids[0]
            present = sorted(by_source.keys())
            peers = [s for s in present if s != only]
            if peers:
                # Peer present but not live — prefer warning when it errored.
                peer_errors: list[str] = []
                for peer in peers:
                    peer_errors.extend(a.error for a in by_source.get(peer, []) if a.error)
                peer_labels = ", ".join(_source_name(s) for s in peers)
                if peer_errors:
                    checks.append(
                        CrossCheck(
                            provider=provider,
                            account=live_by_source[only][0].account,
                            status="warning",
                            sources=[_source_name(only)] + [_source_name(s) for s in peers],
                            message=(
                                f"{_source_name(only)} returned live data, but {peer_labels} failed: {peer_errors[0]}"
                            ),
                        )
                    )
                else:
                    checks.append(
                        CrossCheck(
                            provider=provider,
                            account=live_by_source[only][0].account,
                            status="unavailable",
                            sources=[_source_name(only)] + [_source_name(s) for s in peers],
                            message=(
                                f"Only {_source_name(only)} returned live "
                                f"{provider_display_name(provider)} data this run; "
                                f"no independent overlap with {peer_labels}."
                            ),
                        )
                    )
            else:
                # Single source only — one note for the provider (not per account).
                checks.append(
                    CrossCheck(
                        provider=provider,
                        account=live_by_source[only][0].account,
                        status="unavailable",
                        sources=[_source_name(only)],
                        message=(
                            f"A multi-tool cross-check is unavailable; live "
                            f"{provider_display_name(provider)} data was reported only by "
                            f"{_source_name(only)}."
                        ),
                    )
                )
        elif not source_ids and by_source:
            # No live data at all (e.g. only errored cswap rows).
            if provider == "claude" and cswap_authoritative and "cswap" in by_source:
                # Already covered by _claude_authority_checks when no peers.
                pass
        return checks

    # All pairs of live sources — O(n²) sources, small n.
    for i, left_src in enumerate(source_ids):
        for right_src in source_ids[i + 1 :]:
            checks.extend(
                _cross_check_source_pair(
                    provider,
                    left_src,
                    live_by_source[left_src],
                    right_src,
                    live_by_source[right_src],
                )
            )

    # Failed peer while another is live
    for source, rows in by_source.items():
        if live_by_source.get(source):
            continue
        errors = [a.error for a in rows if a.error]
        if not errors:
            continue
        for other, other_live in live_by_source.items():
            checks.append(
                CrossCheck(
                    provider=provider,
                    account=other_live[0].account if other_live else None,
                    status="warning",
                    sources=[_source_name(other), _source_name(source)],
                    message=(
                        f"{_source_name(other)} returned live data, but {_source_name(source)} failed: {errors[0]}"
                    ),
                )
            )
    return checks


def _claude_authority_checks(
    by_source: dict[str, list[AccountUsage]],
    live_by_source: dict[str, list[AccountUsage]],
    *,
    primary: str | None,
) -> list[CrossCheck]:
    """Claude-specific warnings around cswap multi-account authority."""
    checks: list[CrossCheck] = []
    cswap_rows = by_source.get("cswap") or []
    if not cswap_rows:
        peer_labels = [_source_name(s) for s in sorted(live_by_source) if s != "cswap"]
        checks.append(
            CrossCheck(
                provider="claude",
                account=None,
                status="warning",
                sources=["cswap"] + peer_labels,
                message=(
                    "cswap returned no Claude Code account rows, so Claude cannot "
                    "be reported from its canonical multi-account source. "
                    "Check `cswap list` / `aiuse doctor` (auth and PATH)."
                ),
            )
        )
        return checks

    cswap_live = live_by_source.get("cswap") or []
    peer_live = {s: rows for s, rows in live_by_source.items() if s != "cswap"}
    if not cswap_live:
        if primary and primary != "cswap":
            checks.append(
                CrossCheck(
                    provider="claude",
                    account=None,
                    status="warning",
                    sources=["cswap", _source_name(primary)],
                    message=(
                        "cswap (the canonical multi-account Claude source) produced no "
                        "usable data this run; falling back to a non-canonical source. "
                        "Multi-account Claude Code data may be incomplete or attributed "
                        "to the wrong account."
                    ),
                )
            )
        elif (primary == "cswap" or primary is None) and any(a.error for a in cswap_rows) and not peer_live:
            # Errored-only cswap, nothing to fall back to — still signal the failure mode.
            checks.append(
                CrossCheck(
                    provider="claude",
                    account=cswap_rows[0].account if cswap_rows else None,
                    status="warning",
                    sources=["cswap"],
                    message=(
                        "cswap (the canonical multi-account Claude source) produced no "
                        "usable data this run; falling back to a non-canonical source. "
                        "Multi-account Claude Code data may be incomplete or attributed "
                        "to the wrong account."
                    ),
                )
            )

    # Errored cswap slots vs peers
    for cswap_row in cswap_rows:
        if _has_live_data(cswap_row):
            continue
        if peer_live:
            other = next(iter(peer_live))
            checks.append(
                CrossCheck(
                    provider="claude",
                    account=cswap_row.account,
                    status="warning",
                    sources=["cswap", _source_name(other)],
                    message=(
                        f"cswap could not read canonical usage for Claude Code account "
                        f"{cswap_row.account}, while {_source_name(other)} reported Claude data. "
                        f"Often expected when cswap JSON is decision-stale or that slot "
                        f"is idle — do not replace this account with {_source_name(other)}'s "
                        f"single-session view."
                    ),
                )
            )
        else:
            checks.append(
                CrossCheck(
                    provider="claude",
                    account=cswap_row.account,
                    status="unavailable",
                    sources=["cswap"] + [_source_name(s) for s in sorted(by_source) if s != "cswap"],
                    message=(f"No independent Claude quota cross-check is available for {cswap_row.account}."),
                )
            )
    return checks


def _cross_check_source_pair(
    provider: str,
    left_src: str,
    left_rows: list[AccountUsage],
    right_src: str,
    right_rows: list[AccountUsage],
) -> list[CrossCheck]:
    """Match accounts between two sources and compare overlapping windows."""
    checks: list[CrossCheck] = []
    matched_right: set[int] = set()

    unmatched_left: list[AccountUsage] = []
    label_matched = False
    for left in left_rows:
        peer = _match_peer_account(left, right_rows, left_live_count=len(left_rows))
        if peer is None:
            # Single-sided live row — note when the other source has data under another identity
            if len(right_rows) == 1 and not right_rows[0].account and len(left_rows) == 1:
                peer = right_rows[0]
            else:
                unmatched_left.append(left)
                continue
        matched_right.add(id(peer))
        if left.account and peer.account and not _labels_differ(left, peer):
            label_matched = True
        checks.append(_compare_live_rows(left, peer))

    # Both sources name accounts the same way once any label matched, so an
    # unmatched labelled row on either side is an account the other source
    # lacks (``{A, B}`` vs ``{A, C}``), not an alias problem.
    every_right_matched = all(id(right) in matched_right for right in right_rows)
    if label_matched or every_right_matched:
        # The peer simply lacks these accounts: say so rather than comparing
        # them against another account's numbers.
        for left in unmatched_left:
            if left.account:
                checks.append(_single_source_account_check(provider, left, left_src, right_src))

    for right in right_rows:
        if id(right) in matched_right:
            continue
        # Orphan peer row (e.g. CodexBar single session vs multi-account cswap)
        if left_src == "cswap" or right_src == "cswap":
            checks.append(
                CrossCheck(
                    provider=provider,
                    account=right.account,
                    status="warning",
                    sources=[_source_name(left_src), _source_name(right_src)],
                    message=(
                        f"{_source_name(right_src)} reported Claude account "
                        f"{right.account or 'unknown'}, but it did not match a "
                        f"{_source_name(left_src)} account. Often expected for "
                        f"single-session measurements vs multi-account cswap."
                    ),
                )
            )
        elif label_matched and right.account:
            checks.append(_single_source_account_check(provider, right, right_src, left_src))
        elif len(left_rows) == 1 and len(right_rows) == 1:
            if _labels_differ(left_rows[0], right):
                # Two named accounts: not the same quota, so not a disagreement.
                checks.append(_single_source_account_check(provider, left_rows[0], left_src, right_src))
                checks.append(_single_source_account_check(provider, right, right_src, left_src))
            else:
                # Already compared above if match worked; if not, force compare
                checks.append(_compare_live_rows(left_rows[0], right))
        elif right.account and left_rows:
            checks.append(
                CrossCheck(
                    provider=provider,
                    account=right.account,
                    status="warning",
                    sources=[_source_name(left_src), _source_name(right_src)],
                    message=(
                        f"Cannot safely map {_source_name(right_src)} account {right.account} "
                        f"to one of {_source_name(left_src)}'s multiple accounts. Add "
                        f"[account_aliases.{provider}.{right_src}] "
                        f'"{right.account}" = "<canonical account>"'
                    ),
                )
            )
    return checks


def _match_peer_account(
    anchor: AccountUsage,
    peers: list[AccountUsage],
    *,
    left_live_count: int,
) -> AccountUsage | None:
    """Match a peer row by case-insensitive account email when possible."""
    if anchor.account:
        email_match = next(
            (row for row in peers if row.account and row.account.lower() == anchor.account.lower()),
            None,
        )
        if email_match is not None:
            return email_match
    if left_live_count == 1 and len(peers) == 1 and not _labels_differ(anchor, peers[0]):
        # Allow anonymous single-peer binding (CodexBar often omits email).
        return peers[0]
    return None


def _labels_differ(left: AccountUsage, right: AccountUsage) -> bool:
    """Both rows are named, and not the same name (case-insensitive)."""
    return bool(left.account and right.account and left.account.strip().casefold() != right.account.strip().casefold())


def _single_source_account_check(provider: str, row: AccountUsage, source: str, peer: str) -> CrossCheck:
    return CrossCheck(
        provider=provider,
        account=row.account,
        status="unavailable",
        sources=[_source_name(source), _source_name(peer)],
        message=(
            f"Only {_source_name(source)} reported {provider_display_name(provider)} account "
            f"{row.account} this run; {_source_name(peer)} has no matching account to cross-check. "
            f"If both name one account, add [account_aliases.{provider}.{peer}]."
        ),
    )


def _compare_live_rows(left: AccountUsage, right: AccountUsage) -> CrossCheck:
    issues: list[str] = []
    matched_right: set[int] = set()
    matched_count = 0

    if left.account and right.account and left.account.lower() != right.account.lower():
        issues.append(
            f"account identifiers differ ({left.account} versus {right.account}); "
            f"if this is a multi-account provider, add "
            f"[account_aliases.{left.provider}.{left.source}] "
            f'"{left.account}" = "{right.account}"'
        )

    for left_window in left.windows:
        right_window = _matching_window(left_window, right.windows, matched_right)
        if right_window is None:
            if _has_usable_capacity(left_window):
                issues.append(f"{left.source} alone reported {left_window.label}")
            continue
        matched_right.add(id(right_window))
        matched_count += 1
        left_remaining = left_window.remaining()
        right_remaining = right_window.remaining()
        if left_remaining is not None and right_remaining is not None and abs(left_remaining - right_remaining) > 3.0:
            issues.append(
                f"{left_window.label} differs by {abs(left_remaining - right_remaining):.1f} percentage points"
            )
        if left_window.resets_at and right_window.resets_at:
            seconds = abs((left_window.resets_at - right_window.resets_at).total_seconds())
            if seconds > 900:
                issues.append(f"{left_window.label} reset times differ by {seconds / 60:.0f} minutes")

    for right_window in right.windows:
        if id(right_window) not in matched_right and _has_usable_capacity(right_window):
            issues.append(f"{right.source} alone reported {right_window.label}")

    # Balance cross-check when both report prepaid balances
    if left.balance_usd is not None and right.balance_usd is not None:
        if abs(left.balance_usd - right.balance_usd) > 0.5:
            issues.append(
                f"balance differs by ${abs(left.balance_usd - right.balance_usd):.2f} "
                f"({left.source} ${left.balance_usd:.2f} vs {right.source} ${right.balance_usd:.2f})"
            )
        else:
            matched_count += 1

    sources = [_source_name(left.source), _source_name(right.source)]
    account = left.account or right.account
    if issues:
        return CrossCheck(
            provider=left.provider,
            account=account,
            status="warning",
            sources=sources,
            message=_disagreement_message(left, right, issues),
        )
    return CrossCheck(
        provider=left.provider,
        account=account,
        status="consistent",
        sources=sources,
        message=(
            f"{_source_name(left.source)} and {_source_name(right.source)} agree on "
            f"{matched_count} overlapping live quota "
            f"measurement{'s' if matched_count != 1 else ''} within tolerance."
        ),
    )


def _source_is_local_estimate(rows: Sequence[AccountUsage]) -> bool:
    """True when every live row from this source is a local cost/$cap heuristic."""
    live = [account for account in rows if _has_live_data(account)]
    return bool(live) and all(_looks_like_local_quota_estimate(account) for account in live)


def _looks_like_local_quota_estimate(account: AccountUsage) -> bool:
    """True when the source is a local cost/$cap heuristic, not server billing.

    Known cases: CodexBar OpenCode Go ``--source local``, OpenUsage.ai resources
    with ``estimated: true`` (same $12/$30/$60 style caps).
    """
    notes = " ".join(account.notes or []).casefold()
    if "local estimate" in notes or "estimated (local cost" in notes:
        return True
    if "marked estimated" in notes and "fixed $ caps" in notes:
        return True
    for window in account.windows:
        raw = window.raw
        if isinstance(raw, dict) and raw.get("estimated") is True:
            return True
    return False


def _disagreement_message(left: AccountUsage, right: AccountUsage, issues: list[str]) -> str:
    """Explain a multi-source disagreement; call out known local-estimate traps."""
    detail = "; ".join(issues)
    left_est = _looks_like_local_quota_estimate(left)
    right_est = _looks_like_local_quota_estimate(right)
    if left_est != right_est:
        estimated = left if left_est else right
        authoritative = right if left_est else left
        return (
            f"{_source_name(estimated.source)} is a local cost estimate "
            f"(SQLite / fixed $ caps) that can understate used quota; "
            f"{_source_name(authoritative.source)} tracks OpenCode web billing and "
            f"should be trusted when they disagree. Details: {detail}."
        )
    if left.provider.casefold() in {"opencode-go", "opencode"}:
        return (
            "Tools disagree on OpenCode Go quota figures: "
            f"{detail}. Prefer CodexBar --source web (or the OpenCode usage page); "
            "local estimates and short-window bars can look open while monthly is spent."
        )
    return (
        "Tools disagree on some live quota figures: "
        f"{detail}. Small gaps are often expected (poll timing, last-good hydrate, "
        "label vocabulary, or single-session vs multi-account views) and do not "
        "mean both sources are wrong — cswap stays authoritative for Claude."
    )


def _matching_window(
    target: QuotaWindow,
    candidates: list[QuotaWindow],
    already_matched: set[int],
) -> QuotaWindow | None:
    unmatched = [candidate for candidate in candidates if id(candidate) not in already_matched]
    exact_label = next(
        (candidate for candidate in unmatched if candidate.label.lower() == target.label.lower()),
        None,
    )
    if exact_label is not None:
        return exact_label

    # Fuzzy: same duration bucket, and the same independent pool. Duration
    # alone pairs Antigravity's Gemini weekly with its Claude/GPT weekly
    # (both 10080 minutes) and then reports a fake percent gap.
    target_kind = _window_kind_hint(target)
    if target_kind:
        kind_match = next(
            (
                candidate
                for candidate in unmatched
                if _window_kind_hint(candidate) == target_kind and _same_independent_pool(target, candidate)
            ),
            None,
        )
        if kind_match is not None:
            return kind_match

    if target.resets_at is None:
        return None
    return next(
        (
            candidate
            for candidate in unmatched
            if candidate.resets_at is not None
            and abs((candidate.resets_at - target.resets_at).total_seconds()) <= 900
            and _same_independent_pool(target, candidate)
        ),
        None,
    )


def _same_independent_pool(left: QuotaWindow, right: QuotaWindow) -> bool:
    """True when two windows are not in different hard-separated allotments.

    A window with no pool marker (ordinary Claude 5h/weekly) may match another
    unmarked window. Gemini must not match Claude/GPT.
    """
    left_pool = independent_pool_key(left.label, window_id=left.window_id)
    right_pool = independent_pool_key(right.label, window_id=right.window_id)
    if left_pool is None or right_pool is None:
        return left_pool is None and right_pool is None
    return left_pool == right_pool


def _window_kind_hint(window: QuotaWindow) -> str | None:
    label = (window.label or "").lower()
    minutes = window.window_minutes
    if minutes is not None:
        if minutes <= 360:
            return "5h"
        if minutes <= 12000:
            return "weekly"
        if minutes >= 20000:
            return "monthly"
    if "5-hour" in label or "5h" in label or "session" in label:
        return "5h"
    if "week" in label or label in {"7d", "7-day", "7 day"}:
        return "weekly"
    if "month" in label or "included" in label or "billing-cycle" in label:
        return "monthly"
    return None


def _has_usable_capacity(window: QuotaWindow) -> bool:
    remaining = window.remaining()
    return remaining is not None and remaining > 0


def _source_name(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


# Re-export for docs / install scripts that want a single inventory.
ALL_DATA_SOURCES: tuple[str, ...] = (
    "cswap",
    "codexbar",
    "caam",
    "caut",
    "openusage_ai",
    "openusage_sh",
    "tokscale",
    "paseo",
    "clinepass",
    "hermes",
    "acp",
)


def collector_tools_present() -> dict[str, bool]:
    """PATH presence for doctor / diagnostics."""
    return {
        "cswap": which("cswap") is not None,
        "codexbar": which("codexbar") is not None,
        "caam": which("caam") is not None,
        "caut": which("caut") is not None,
        "openusage_ai": openusage_app_cli_path() is not None,
        "openusage_sh": which("openusage-sh") is not None,
        "tokscale": which("tokscale") is not None,
        "paseo": which("paseo") is not None,
        "hermes": True,
        "acp": default_acp_log_dir().is_dir(),
    }
