"""Orchestrate all collectors into a Snapshot with multi-source cross-checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from aiuse.config import timeout_for
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

from .base import which
from .caut import collect_caut
from .clinepass import collect_clinepass
from .codexbar import collect_codexbar
from .cswap import collect_cswap
from .hermes import collect_hermes
from .muse import collect_muse
from .opencode_go import collect_opencode_go
from .opencode_zen import collect_opencode_zen
from .openrouter import collect_openrouter
from .openusage import collect_openusage_ai
from .openusage_sh import collect_openusage_sh
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
    "tokscale",
    "clinepass",
    "hermes",
    "muse",
)

PROVIDER_SOURCE_PRIORITY: dict[str, tuple[str, ...]] = {
    # cswap is the multi-account Claude authority when enabled.
    "claude": ("cswap", "codexbar", "caut", "openusage_ai", "tokscale", "openusage_sh", "hermes"),
    # tokscale keeps distinct Copilot premium vs chat/completions semantics.
    "copilot": ("tokscale", "codexbar", "caut", "openusage_ai", "openusage_sh", "hermes"),
    # Native /go page sees an expired plan; CodexBar local $caps cannot.
    "opencode-go": ("opencode_go", "codexbar", "caut", "openusage_ai", "openusage_sh", "tokscale"),
}

SOURCE_LABELS: dict[str, str] = {
    "cswap": "cswap",
    "codexbar": "CodexBar",
    "caut": "caut",
    "openusage_ai": "OpenUsage.ai",
    "openusage_sh": "OpenUsage.sh",
    "opencode_go": "OpenCode Go (native)",
    "opencode_zen": "OpenCode Zen (native)",
    "openrouter": "OpenRouter (native)",
    "tokscale": "tokscale",
    "clinepass": "ClinePass (native)",
    "hermes": "Hermes (local)",
    "muse": "Muse (native)",
}

# Canonical provider identity lives in models.PROVIDER_ID_ALIASES so collection
# and the history/analysis passes cannot drift onto different spellings.
_PROVIDER_ALIASES = PROVIDER_ID_ALIASES


def run_collectors(config: dict[str, Any] | None = None) -> Snapshot:
    config = config or {}
    collectors_cfg = config.get("collectors") or {}
    snapshot = Snapshot(collected_at=utcnow())

    # Each collector shells out (or hits loopback) independently — run concurrently.
    # Correctness: long default timeouts; all enabled sources always queried.
    jobs: list[tuple[str, Callable[[], list[AccountUsage]]]] = []
    if _enabled(collectors_cfg, "cswap"):
        cswap_timeout = timeout_for(config, "cswap")
        jobs.append(("cswap", partial(collect_cswap, timeout=cswap_timeout)))
    if _enabled(collectors_cfg, "codexbar"):
        providers = (collectors_cfg.get("codexbar") or {}).get("providers", "enabled")
        codexbar_timeout = timeout_for(config, "codexbar")
        discovery_timeout = timeout_for(config, "codexbar_discovery")
        jobs.append(
            (
                "codexbar",
                partial(
                    collect_codexbar,
                    providers=providers,
                    timeout=codexbar_timeout,
                    discovery_timeout=discovery_timeout,
                ),
            )
        )
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
    if _enabled(collectors_cfg, "tokscale"):
        tokscale_timeout = timeout_for(config, "tokscale")
        jobs.append(("tokscale", partial(collect_tokscale, timeout=tokscale_timeout)))
    if _enabled(collectors_cfg, "clinepass"):
        jobs.append(("clinepass", partial(collect_clinepass, timeout=timeout_for(config, "clinepass"))))
    if _enabled(collectors_cfg, "hermes"):
        jobs.append(("hermes", partial(collect_hermes, timeout=timeout_for(config, "hermes"))))
    if _enabled(collectors_cfg, "muse"):
        jobs.append(("muse", partial(collect_muse, timeout=timeout_for(config, "muse"))))

    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {name: pool.submit(fn) for name, fn in jobs}
        for name, _ in jobs:
            try:
                snapshot.accounts.extend(futures[name].result())
            except Exception as exc:  # noqa: BLE001
                snapshot.collector_errors.append(f"{name}: {exc}")

    snapshot.accounts, snapshot.cross_checks = _select_and_cross_check(
        snapshot.accounts,
        cswap_authoritative=_enabled(collectors_cfg, "cswap"),
        account_aliases=config.get("account_aliases"),
    )
    _apply_lapsed_accounts(snapshot.accounts, config)
    return snapshot


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
) -> tuple[list[AccountUsage], list[CrossCheck]]:
    """Select report rows while cross-checking all live sources.

    Selection picks one primary source per provider (priority list). Cross-checks
    compare every pair of live sources so adding caut/OpenUsage later does not
    require new pairwise branches.
    """

    for account in accounts:
        account.provider = _canonical_provider(account.provider)
    _normalize_account_names(accounts, account_aliases)

    providers = sorted({account.provider for account in accounts}, key=str.casefold)
    selected: list[AccountUsage] = []
    checks: list[CrossCheck] = []

    for provider in providers:
        rows = [account for account in accounts if account.provider == provider]
        by_source: dict[str, list[AccountUsage]] = defaultdict(list)
        for account in rows:
            by_source[account.source].append(account)

        priority = _source_priority(provider, cswap_authoritative=cswap_authoritative)
        primary = _pick_primary_source(provider, by_source, priority, cswap_authoritative=cswap_authoritative)

        if primary is not None:
            primary_rows = by_source.get(primary, [])
            if primary == "cswap":
                # Keep every cswap slot (live + errored) for multi-account identity.
                selected.extend(primary_rows)
            else:
                live = [a for a in primary_rows if _has_live_data(a)]
                selected.extend(live if live else primary_rows)
        else:
            # No priority source present — keep whatever we have.
            selected.extend(rows)

        checks.extend(
            _provider_multi_source_cross_checks(
                provider,
                by_source,
                primary=primary,
                cswap_authoritative=cswap_authoritative,
            )
        )

    return selected, checks


def _pick_primary_source(
    provider: str,
    by_source: dict[str, list[AccountUsage]],
    priority: Sequence[str],
    *,
    cswap_authoritative: bool,
) -> str | None:
    """Return the source id to surface for this provider, or None."""
    if provider == "claude" and cswap_authoritative and "cswap" in by_source:
        cswap_live = [a for a in by_source["cswap"] if _has_live_data(a)]
        if cswap_live:
            return "cswap"
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
        return (authoritative or live_sources)[0]
    for source in priority:
        if by_source.get(source):
            return source
    # Unknown source present (future collectors) — prefer first with live data.
    for source, rows in by_source.items():
        if any(_has_live_data(a) for a in rows):
            return source
    return next(iter(by_source), None)


def _consolidate_accounts(
    accounts: list[AccountUsage],
    *,
    cswap_authoritative: bool,
) -> list[AccountUsage]:
    """Compatibility wrapper for callers that only need selected rows."""
    selected, _ = _select_and_cross_check(accounts, cswap_authoritative=cswap_authoritative)
    return selected


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

    for left in left_rows:
        peer = _match_peer_account(left, right_rows, left_live_count=len(left_rows))
        if peer is None:
            # Single-sided live row — note when the other source has data under another identity
            if len(right_rows) == 1 and not right_rows[0].account and len(left_rows) == 1:
                peer = right_rows[0]
            else:
                continue
        matched_right.add(id(peer))
        checks.append(_compare_live_rows(left, peer))

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
        elif len(left_rows) == 1 and len(right_rows) == 1:
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
    if left_live_count == 1 and len(peers) == 1:
        # Allow anonymous single-peer binding (CodexBar often omits email).
        return peers[0]
    return None


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

    # Fuzzy: same duration bucket keywords
    target_kind = _window_kind_hint(target)
    if target_kind:
        kind_match = next(
            (c for c in unmatched if _window_kind_hint(c) == target_kind),
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
            if candidate.resets_at is not None and abs((candidate.resets_at - target.resets_at).total_seconds()) <= 900
        ),
        None,
    )


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
    "caut",
    "openusage_ai",
    "openusage_sh",
    "tokscale",
    "clinepass",
    "hermes",
)


def collector_tools_present() -> dict[str, bool]:
    """PATH presence for doctor / diagnostics."""
    return {
        "cswap": which("cswap") is not None,
        "codexbar": which("codexbar") is not None,
        "caut": which("caut") is not None,
        "openusage_ai": which("openusage") is not None,
        "openusage_sh": which("openusage-sh") is not None,
        "tokscale": which("tokscale") is not None,
        "hermes": True,
    }
