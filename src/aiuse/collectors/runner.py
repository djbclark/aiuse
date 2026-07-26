"""Orchestrate all collectors into a Snapshot with multi-source cross-checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from aiuse.config import timeout_for
from aiuse.models import AccountUsage, CrossCheck, QuotaWindow, Snapshot, provider_display_name, utcnow

from .base import which
from .caut import collect_caut
from .codexbar import collect_codexbar
from .cswap import collect_cswap
from .openusage import collect_openusage
from .tokscale import collect_tokscale

# Prefer earlier sources for *selection* (what drives the ladder). All live
# sources still participate in cross-checks. Correctness > minimising comparisons.
DEFAULT_SOURCE_PRIORITY: tuple[str, ...] = (
    "codexbar",
    "caut",
    "openusage",
    "tokscale",
)

PROVIDER_SOURCE_PRIORITY: dict[str, tuple[str, ...]] = {
    # cswap is the multi-account Claude authority when enabled.
    "claude": ("cswap", "codexbar", "caut", "openusage", "tokscale"),
    # tokscale keeps distinct Copilot premium vs chat/completions semantics.
    "copilot": ("tokscale", "codexbar", "caut", "openusage"),
}

SOURCE_LABELS: dict[str, str] = {
    "cswap": "cswap",
    "codexbar": "CodexBar",
    "caut": "caut",
    "openusage": "OpenUsage",
    "tokscale": "tokscale",
}

_PROVIDER_ALIASES = {
    "chatgpt": "codex",
    "openai-codex": "codex",
    "github-copilot": "copilot",
    "grok-build": "grok",
    "supergrok": "grok",
    "opencodego": "opencode-go",
    "opencode": "opencode-go",
}


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
    if _enabled(collectors_cfg, "openusage"):
        ou_cfg = collectors_cfg.get("openusage") if isinstance(collectors_cfg.get("openusage"), dict) else {}
        ou_timeout = timeout_for(config, "openusage")
        force = bool((ou_cfg or {}).get("force_refresh", True))
        launch = bool((ou_cfg or {}).get("try_launch_app", True))
        base = str((ou_cfg or {}).get("base_url") or "http://127.0.0.1:6736")
        jobs.append(
            (
                "openusage",
                partial(
                    collect_openusage,
                    timeout=ou_timeout,
                    force_refresh=force,
                    try_launch_app=launch,
                    base_url=base,
                ),
            )
        )
    if _enabled(collectors_cfg, "tokscale"):
        tokscale_timeout = timeout_for(config, "tokscale")
        jobs.append(("tokscale", partial(collect_tokscale, timeout=tokscale_timeout)))

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
    )
    return snapshot


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
    key = provider.lower().replace(" ", "-")
    return _PROVIDER_ALIASES.get(key, key)


def _source_priority(provider: str, *, cswap_authoritative: bool) -> tuple[str, ...]:
    if provider == "claude" and not cswap_authoritative:
        # cswap disabled: fall through to multi-source peers only.
        return tuple(s for s in PROVIDER_SOURCE_PRIORITY["claude"] if s != "cswap")
    return PROVIDER_SOURCE_PRIORITY.get(provider, DEFAULT_SOURCE_PRIORITY)


def _select_and_cross_check(
    accounts: list[AccountUsage],
    *,
    cswap_authoritative: bool,
) -> tuple[list[AccountUsage], list[CrossCheck]]:
    """Select report rows while cross-checking all live sources.

    Selection picks one primary source per provider (priority list). Cross-checks
    compare every pair of live sources so adding caut/OpenUsage later does not
    require new pairwise branches.
    """

    for account in accounts:
        account.provider = _canonical_provider(account.provider)

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

    for source in priority:
        rows = by_source.get(source) or []
        if any(_has_live_data(a) for a in rows):
            return source
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
        issues.append(f"account identifiers differ ({left.account} versus {right.account})")

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
            message=(
                "Tools disagree on some live quota figures: "
                + "; ".join(issues)
                + ". Small gaps are often expected (poll timing, last-good hydrate, "
                "label vocabulary, or single-session vs multi-account views) and do not "
                "mean both sources are wrong — cswap stays authoritative for Claude."
            ),
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
    if "week" in label:
        return "weekly"
    if "month" in label or "included" in label:
        return "monthly"
    return None


def _has_usable_capacity(window: QuotaWindow) -> bool:
    remaining = window.remaining()
    return remaining is not None and remaining > 0


def _source_name(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


# Re-export for docs / install scripts that want a single inventory.
ALL_DATA_SOURCES: tuple[str, ...] = ("cswap", "codexbar", "caut", "openusage", "tokscale")


def collector_tools_present() -> dict[str, bool]:
    """PATH presence for doctor / diagnostics."""
    return {
        "cswap": which("cswap") is not None,
        "codexbar": which("codexbar") is not None,
        "caut": which("caut") is not None,
        "openusage": which("openusage") is not None,
        "tokscale": which("tokscale") is not None,
    }
