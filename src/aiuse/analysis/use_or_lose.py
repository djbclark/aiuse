"""Detect monthly/weekly subscription allotments that will expire unused."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aiuse.analysis.history import (
    chronic_waste_summary,
    compute_learned_burn_rates,
    compute_learned_flexibility,
    merge_learned_flexibility,
    should_learn_from_history,
)
from aiuse.analysis.pace import (
    classify_pace,
    compute_pace,
    governing_partition,
    partition_independent_pools,
)
from aiuse.models import (
    WINDOW_5H_MAX_MINUTES,
    AccountUsage,
    BillingKind,
    FlexibilityClass,
    FlexibilityProfile,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    classify_window_minutes,
    provider_config_key,
    provider_display_name,
    utcnow,
)

# Windows shorter than this are rate-limits, not "monthly plan waste"
SHORT_WINDOW_LABELS = {"5-hour", "5h", "session", "hourly"}

DAYS_PER_MONTH = 30.44
MINUTES_PER_MONTH = DAYS_PER_MONTH * 24 * 60

# Default burn-rate assumptions
_DEFAULT_MAX_TOKENS_PER_MINUTE = 200000
_DEFAULT_MAX_REQUESTS_PER_MINUTE = 0.5
_DEFAULT_MAX_USD_PER_MINUTE = 0.05
_DEFAULT_WAKING_HOURS = 16


def _compute_value_at_risk(
    *,
    remaining: float,
    window_minutes: int,
    monthly_price: float,
    waking_hours_per_day: float,
    value_multiplier: float = 1.0,
) -> float:
    active_minutes_per_month = waking_hours_per_day * DAYS_PER_MONTH * 60
    if active_minutes_per_month <= 0 or window_minutes <= 0:
        return 0.0
    active_cycles = active_minutes_per_month / window_minutes
    # Monthly-or-longer windows can yield active_cycles < 1, which would make
    # value_per_refill exceed the whole plan price. A single refill cannot be
    # worth more than the plan itself.
    value_per_refill = monthly_price / max(active_cycles, 1.0)
    return value_per_refill * (remaining / 100.0) * value_multiplier


def _classify_flexibility(
    *,
    window_minutes: int | None,
    provider: str,
    config: dict[str, Any],
) -> tuple[FlexibilityClass, float]:
    duration_kind = classify_window_minutes(window_minutes)
    defaults_cfg = config.get("consumption_flexibility_defaults") or {}
    defaults = defaults_cfg if isinstance(defaults_cfg, dict) else {}
    overrides_cfg = config.get("provider_overrides") or {}
    overrides = overrides_cfg if isinstance(overrides_cfg, dict) else {}

    flex = None
    provider_key = provider_config_key(provider)

    if provider_key in overrides and duration_kind:
        prov_overrides = overrides[provider_key]
        if isinstance(prov_overrides, dict) and duration_kind in prov_overrides:
            per_window = prov_overrides[duration_kind]
            if isinstance(per_window, dict) and "flexibility" in per_window:
                flex = float(per_window["flexibility"])
    if flex is None and duration_kind:
        raw = defaults.get(duration_kind)
        flex = float(raw) if raw is not None else None
    if flex is None:
        flex = 0.5

    flex = max(0.0, min(1.0, flex))
    if flex >= 0.9:
        return FlexibilityClass.BURSTABLE, flex
    if flex >= 0.4:
        return FlexibilityClass.SEMI_THROTTLED, flex
    return FlexibilityClass.THROTTLED, flex


def _compute_flexibility_profile(
    *,
    window: QuotaWindow,
    provider: str,
    config: dict[str, Any],
    monthly_price: float | None,
    value_multiplier: float = 1.0,
    now: Any | None = None,
    learned: dict[str, float] | None = None,
) -> FlexibilityProfile | None:
    remaining = window.remaining()
    if remaining is None:
        return None

    cfg = config or {}
    waking = float(cfg.get("waking_hours_per_day", _DEFAULT_WAKING_HOURS))
    flex_class, flex_score = _classify_flexibility(window_minutes=window.window_minutes, provider=provider, config=cfg)

    duration_kind = classify_window_minutes(window.window_minutes)
    if learned and duration_kind:
        flex_score = merge_learned_flexibility(flex_score, provider, duration_kind, learned)
        if flex_score >= 0.9:
            flex_class = FlexibilityClass.BURSTABLE
        elif flex_score >= 0.4:
            flex_class = FlexibilityClass.SEMI_THROTTLED
        else:
            flex_class = FlexibilityClass.THROTTLED

    value_usd: float | None = None
    if monthly_price is not None and window.window_minutes:
        value_usd = round(
            _compute_value_at_risk(
                remaining=remaining,
                window_minutes=window.window_minutes,
                monthly_price=monthly_price,
                waking_hours_per_day=waking,
                value_multiplier=value_multiplier,
            ),
            2,
        )

    cycles_needed: int | None = None
    earliest: Any | None = None
    burn_minutes: float | None = None

    capacity = window.refill_capacity
    capacity_unit = window.refill_capacity_unit

    if capacity is None and duration_kind:
        overrides_cfg = cfg.get("provider_overrides") or {}
        overrides = overrides_cfg if isinstance(overrides_cfg, dict) else {}
        provider_key = provider_config_key(provider)
        prov_overrides = overrides.get(provider_key)
        if isinstance(prov_overrides, dict):
            window_overrides = prov_overrides.get(duration_kind)
            if isinstance(window_overrides, dict):
                capacity = window_overrides.get("refill_capacity")
                if capacity_unit is None:
                    capacity_unit = window_overrides.get("refill_capacity_unit")

    if capacity is not None and capacity > 0 and window.window_minutes:
        if capacity_unit == "tokens":
            rate = float(cfg.get("max_sustained_tokens_per_minute", _DEFAULT_MAX_TOKENS_PER_MINUTE))
        elif capacity_unit == "requests":
            rate = float(cfg.get("max_requests_per_minute", _DEFAULT_MAX_REQUESTS_PER_MINUTE))
        elif capacity_unit == "usd":
            rate = float(cfg.get("max_usd_per_minute", _DEFAULT_MAX_USD_PER_MINUTE))
        else:
            rate = 1.0
        # Full-window burn time at configured rate; remaining fraction scales it.
        # (Previously cycles_needed canceled capacity/capacity → always ~1.)
        burn_minutes = capacity / max(rate, 0.001)
        burn_minutes = round(burn_minutes, 1)

        burn_minutes_for_remaining = round(burn_minutes * (remaining / 100.0), 1)
        cycles_needed = max(1, int(-(-burn_minutes_for_remaining // window.window_minutes)))

        now_dt = now or utcnow()
        if window.resets_at and isinstance(window.resets_at, type(now_dt)):
            earliest = window.resets_at - timedelta(minutes=burn_minutes_for_remaining)
        burn_minutes = burn_minutes_for_remaining

    burn_text = _burn_estimate_text(
        burn_minutes=burn_minutes,
        capacity_unit=capacity_unit,
        cycles_needed=cycles_needed,
    )

    return FlexibilityProfile(
        flexibility_class=flex_class,
        consumption_flexibility=flex_score,
        value_at_risk_usd=value_usd,
        cycles_needed=cycles_needed,
        earliest_start_calendar=earliest if isinstance(earliest, type(utcnow())) else None,
        effective_burn_minutes=burn_minutes,
        burn_estimate=burn_text,
    )


def _burn_estimate_text(
    *,
    burn_minutes: float | None,
    capacity_unit: str | None,
    cycles_needed: int | None,
) -> str | None:
    if burn_minutes is None:
        return None
    if capacity_unit == "requests":
        if burn_minutes < 60:
            return f"~{burn_minutes:.0f} min of focused use"
        return f"~{burn_minutes / 60:.1f}h of focused use"
    if burn_minutes < 60:
        return f"~{burn_minutes:.0f} min at typical pace"
    if burn_minutes < 180:
        return f"~{burn_minutes / 60:.1f}h at typical pace"
    sessions = max(1, (cycles_needed or 1))
    return f"~{burn_minutes / 60:.1f}h across {sessions} session(s)"


def analyze_use_or_lose(
    snapshot: Snapshot,
    config: dict[str, Any] | None = None,
) -> list[UseOrLoseAlert]:
    cfg = (config or {}).get("analysis") or {}
    min_remaining = float(cfg.get("min_remaining_percent", 40))
    max_days = float(cfg.get("max_days_until_reset", 14))
    urgent_remaining = float(cfg.get("urgent_remaining_percent", 70))
    urgent_days = float(cfg.get("urgent_days_until_reset", 7))
    plans = (config or {}).get("plans") or {}

    now = utcnow()
    alerts: list[UseOrLoseAlert] = []
    seen: set[tuple[str, str, str]] = set()
    analysis_cfg = cfg
    mode = analysis_cfg.get("scoring_mode")
    if mode is None:
        mode = "legacy" if analysis_cfg.get("use_multi_dim_scoring", True) is False else "pace"
    multi_dim = mode == "multi_dim"
    min_value_usd = float(analysis_cfg.get("min_value_at_risk_usd", 0.50))
    min_value_fraction = float(analysis_cfg.get("min_value_fraction", 0.05))
    waking = float(analysis_cfg.get("waking_hours_per_day", _DEFAULT_WAKING_HOURS))

    learned_flex: dict[str, float] = {}
    learned_burn_rates: dict[str, tuple[float, int]] = {}
    if should_learn_from_history(analysis_cfg):
        retention = int(analysis_cfg.get("snapshot_retention_days", 90))
        learned_flex = compute_learned_flexibility(current=snapshot, retention_days=retention)
        if mode == "pace":
            learned_burn_rates = compute_learned_burn_rates(current=snapshot, retention_days=retention)

    for account in snapshot.accounts:
        if account.error and not account.windows:
            continue
        if account.billing_kind == BillingKind.PAYG_API:
            continue
        if account.billing_kind == BillingKind.PREPAID_BALANCE:
            # Purchased API tokens / prepaid credits (Deepseek, OpenRouter, …)
            # do not expire on a subscription cycle — never attach burn urgency.
            # Large idle balances may still appear as inventory INFO (kind=prepaid).
            if account.balance_usd is not None and account.balance_usd >= 10:
                alerts.append(
                    UseOrLoseAlert(
                        urgency=Urgency.INFO,
                        provider=account.provider,
                        account=account.account,
                        window_label=f"balance ${account.balance_usd:.2f}",
                        remaining_percent=0.0,
                        days_until_reset=None,
                        plan=account.plan,
                        message=(
                            f"{account.provider}: ${account.balance_usd:.2f} prepaid "
                            "balance (usually does not expire — spend when useful, "
                            "not urgent like subscription windows)."
                        ),
                        source=account.source,
                        score=0.0,
                        kind="prepaid",
                    )
                )
            continue

        plan_meta = _plan_meta(account.provider, plans)
        monthly_price = plan_meta.get("monthly_price")
        value_multipliers = plan_meta.get("value_multiplier")
        provider_key = provider_config_key(account.provider)

        # Shared-allotment (pace mode): score only the longest-duration window per
        # *independent* pool; shorter siblings (e.g. Claude 5h under weekly) are
        # suppressed children. Hard-separated families (Antigravity Gemini vs
        # Claude/GPT) are different pools and each get their own governing window.
        shared_allotment = mode == "pace" and _shared_allotment_enabled(provider_key, analysis_cfg)
        # Windows that may produce an alert under shared allotment (governings).
        scorable_ids: set[int] = set()
        # Governing window id → suppressed children of that same pool.
        children_by_gov_id: dict[int, list[QuotaWindow]] = {}
        if shared_allotment:
            any_governing = False
            for pool in partition_independent_pools(account.windows):
                governing_window, child_windows = governing_partition(pool)
                if governing_window is None:
                    # No usable remaining() in this pool — score its windows
                    # independently if we later fall through (leave them scorable).
                    for window in pool:
                        scorable_ids.add(id(window))
                        children_by_gov_id[id(window)] = []
                    continue
                any_governing = True
                scorable_ids.add(id(governing_window))
                children_by_gov_id[id(governing_window)] = child_windows
            if not any_governing and not scorable_ids:
                shared_allotment = False

        for window in account.windows:
            if mode == "legacy" and _is_short_window(window):
                continue

            remaining = window.remaining()
            if remaining is None:
                continue
            days = window.days_until_reset(now)

            # Deadlines in the past are not actionable
            if days is not None and days <= 0:
                continue

            key = (
                account.provider.lower(),
                (account.account or "").lower(),
                f"{window.label.lower()}|{window.resets_at.isoformat() if window.resets_at else ''}",
            )
            if key in seen:
                continue
            seen.add(key)

            duration_kind = classify_window_minutes(window.window_minutes)
            window_value_mult = 1.0
            if isinstance(value_multipliers, dict) and duration_kind:
                window_value_mult = float(value_multipliers.get(duration_kind, 1.0))

            flex_profile = _compute_flexibility_profile(
                window=window,
                provider=account.provider,
                config=analysis_cfg,
                monthly_price=monthly_price,
                value_multiplier=window_value_mult,
                now=now,
                learned=learned_flex if learned_flex else None,
            )

            if mode == "pace":
                if shared_allotment and id(window) not in scorable_ids:
                    continue  # child of a shared allotment — never its own alert

                pace_cfg = analysis_cfg.get("pace") or {}
                learned_rate: float | None = None
                learned_n = 0
                if learned_burn_rates and duration_kind:
                    rate_key = f"{provider_key}:{duration_kind}"
                    if rate_key in learned_burn_rates:
                        learned_rate, learned_n = learned_burn_rates[rate_key]
                pace = compute_pace(
                    window,
                    now=now,
                    learned_rate_per_day=learned_rate,
                    learned_sample_count=learned_n,
                )
                if pace is None:
                    # Governing window unusable: promote this pool's children so
                    # later iterations can score them independently.
                    if shared_allotment and id(window) in scorable_ids:
                        for child in children_by_gov_id.get(id(window), []):
                            scorable_ids.add(id(child))
                            children_by_gov_id[id(child)] = []
                        scorable_ids.discard(id(window))
                    continue
                if shared_allotment and id(window) in children_by_gov_id:
                    pace.governing = True
                verdict = classify_pace(
                    pace,
                    resets_at=window.resets_at,
                    waste_alert_fraction=float(pace_cfg.get("waste_alert_fraction", 0.30)),
                    min_elapsed_fraction=float(pace_cfg.get("min_elapsed_fraction", 0.15)),
                    conserve_min_lead_hours=float(pace_cfg.get("conserve_min_lead_hours", 4.0)),
                    has_learned_rate=learned_n > 0,
                )
                if verdict in ("on_pace", "unknown"):
                    continue
                if days is not None and days > max_days and verdict != "conserve":
                    continue

                v_cycle = 0.0
                if monthly_price and window.window_minutes:
                    v_cycle = _compute_value_at_risk(
                        remaining=100.0,
                        window_minutes=window.window_minutes,
                        monthly_price=float(monthly_price),
                        waking_hours_per_day=waking,
                        value_multiplier=window_value_mult,
                    )
                if pace.projected_waste_fraction is not None:
                    pace.projected_waste_usd = round((pace.projected_waste_fraction or 0.0) * v_cycle, 2)

                if verdict == "burn":
                    if pace.projected_waste_usd is not None and pace.projected_waste_usd < min_value_usd:
                        plan_price = float(monthly_price or 0)
                        if plan_price <= 0 or (pace.projected_waste_usd / plan_price) < min_value_fraction:
                            continue
                    score = min(100.0, 30.0 + 70.0 * (pace.projected_waste_fraction or 0.0))
                    if score >= 90:
                        urgency = Urgency.CRITICAL
                    elif score >= 75:
                        urgency = Urgency.HIGH
                    elif score >= 50:
                        urgency = Urgency.MEDIUM
                    else:
                        urgency = Urgency.LOW
                    kind = "burn"
                else:  # conserve
                    t_left = window.days_until_reset(now) or 0.0
                    t_ex = (
                        (pace.projected_exhaust_at - now).total_seconds() / 86400.0
                        if pace.projected_exhaust_at
                        else t_left
                    )
                    if t_left > 0:
                        score = 60.0 + 40.0 * max(0.0, min(1.0, (t_left - t_ex) / t_left))
                    else:
                        score = 60.0
                    urgency = Urgency.HIGH if (t_left - t_ex) >= 1.0 else Urgency.MEDIUM
                    kind = "conserve"

                suppressed = (
                    children_by_gov_id.get(id(window))
                    if shared_allotment and id(window) in children_by_gov_id
                    else None
                )
                if suppressed is not None and not suppressed:
                    suppressed = None
                message = _pace_message(
                    account=account,
                    window=window,
                    verdict=verdict,
                    pace=pace,
                    days=days,
                    deadline_is_estimated=not window.reset_time_is_precise(),
                    suppressed_children=suppressed,
                )
                alerts.append(
                    UseOrLoseAlert(
                        urgency=urgency,
                        provider=account.provider,
                        account=account.account,
                        window_label=window.label,
                        remaining_percent=remaining,
                        days_until_reset=days,
                        plan=account.plan or plan_meta.get("name"),
                        message=message,
                        source=account.source,
                        score=score,
                        flexibility_profile=flex_profile,
                        window_minutes=window.window_minutes,
                        kind=kind,
                        pace=pace,
                        deadline_is_estimated=not window.reset_time_is_precise(),
                    )
                )
                continue

            if multi_dim:
                if flex_profile is None:
                    continue

                if days is not None and days > max_days:
                    continue
                # min_remaining is intentionally NOT applied on the multi-dim path
                # (interim; Phase 2 pace/conserve logic will handle low-remaining
                # windows). Legacy branch below still gates on min_remaining.

                urgency, score = _score_multi_dimension(
                    profile=flex_profile,
                    remaining=remaining,
                    days=days,
                    config=config,
                    monthly_price=float(monthly_price) if monthly_price is not None else None,
                )

                value_usd = flex_profile.value_at_risk_usd
                plan_price = float(monthly_price or 0)

                if urgency in (Urgency.NONE,):
                    continue
                if value_usd is not None:
                    if value_usd < min_value_usd:
                        if plan_price > 0 and (value_usd / plan_price) < min_value_fraction:
                            continue
                        elif plan_price <= 0:
                            continue
                    elif plan_price > 0 and (value_usd / plan_price) < min_value_fraction:
                        if urgency in (Urgency.INFO, Urgency.LOW):
                            continue
                if urgency == Urgency.INFO and value_usd is not None and value_usd < min_value_usd:
                    continue
            else:
                if days is None:
                    continue
                if remaining < min_remaining:
                    continue
                if days > max_days:
                    continue

                urgency, score = _score(
                    remaining=remaining,
                    days=days,
                    urgent_remaining=urgent_remaining,
                    urgent_days=urgent_days,
                    min_remaining=min_remaining,
                    label=window.label,
                    max_days=max_days,
                )
                if urgency == Urgency.NONE:
                    continue

            message = _message(
                account=account,
                window_label=window.label,
                remaining=remaining,
                days=days,
                deadline_is_estimated=not window.reset_time_is_precise(),
                plan_notes=plan_meta.get("notes"),
            )

            alerts.append(
                UseOrLoseAlert(
                    urgency=urgency,
                    provider=account.provider,
                    account=account.account,
                    window_label=window.label,
                    remaining_percent=remaining,
                    days_until_reset=days,
                    plan=account.plan or plan_meta.get("name"),
                    message=message,
                    source=account.source,
                    score=score,
                    flexibility_profile=flex_profile,
                    window_minutes=window.window_minutes,
                    deadline_is_estimated=not window.reset_time_is_precise(),
                )
            )
    alerts.sort(key=lambda a: (-a.score, a.provider.casefold(), a.window_label.casefold()))

    if should_learn_from_history(analysis_cfg):
        retention = int(analysis_cfg.get("snapshot_retention_days", 90))
        # History summaries are computed independently from the live pace pass.
        # Apply the same shared-allotment child suppression here so an old
        # "5-hour 100% left" average cannot contradict an exhausted governing
        # weekly/monthly window in the current snapshot.
        shared_child_keys = _shared_allotment_child_keys(snapshot, analysis_cfg)
        # Chronic-waste stats are averaged across past cycles and carry no reset
        # time of their own — but the matching *live* window in this snapshot
        # usually has one, so borrow it instead of reporting "time unknown".
        live_resets_by_key: dict[str, datetime | None] = {}
        for live_account in snapshot.accounts:
            live_provider = provider_config_key(live_account.provider)
            for live_window in live_account.windows:
                live_key = f"{live_provider}:{live_window.label}"
                if live_window.resets_at is not None or live_key not in live_resets_by_key:
                    live_resets_by_key[live_key] = live_window.resets_at
        for wasted in chronic_waste_summary(current=snapshot, retention_days=retention):
            provider = wasted["provider"]
            label = wasted["label"]
            if (provider_config_key(str(provider)), str(label).casefold()) in shared_child_keys:
                continue
            avg_remaining = wasted["avg_remaining_pct"]
            samples = wasted["sample_count"]
            live_resets_at = live_resets_by_key.get(f"{provider}:{label}")
            days = (live_resets_at - utcnow()).total_seconds() / 86400.0 if live_resets_at is not None else None
            alerts.append(
                UseOrLoseAlert(
                    urgency=Urgency.INFO,
                    provider=provider,
                    account=None,
                    window_label=label,
                    remaining_percent=avg_remaining,
                    days_until_reset=days,
                    plan=None,
                    message=(
                        f"{provider_display_name(provider)} {label}: {avg_remaining:.0f}% left on average "
                        f"(over {samples} snapshots). Throttled window — consistently underused."
                    ),
                    source="history",
                    score=4.0,
                )
            )

    return alerts


def _shared_allotment_child_keys(
    snapshot: Snapshot,
    analysis_cfg: dict[str, Any],
) -> set[tuple[str, str]]:
    """Return current shared-pool children as ``(config provider, label)`` keys."""
    keys: set[tuple[str, str]] = set()
    for account in snapshot.accounts:
        provider_key = provider_config_key(account.provider)
        if not _shared_allotment_enabled(provider_key, analysis_cfg):
            continue
        for pool in partition_independent_pools(account.windows):
            governing, children = governing_partition(pool)
            if governing is None:
                continue
            keys.update((provider_key, child.label.casefold()) for child in children)
    return keys


def _is_short_window(window: QuotaWindow) -> bool:
    if window.window_minutes is not None and window.window_minutes <= WINDOW_5H_MAX_MINUTES:
        return True
    low = window.label.lower().strip()
    if low in SHORT_WINDOW_LABELS:
        return True
    if "5-hour" in low or "5 hour" in low or "5h" in low:
        return True
    return False


def _looks_monthly(label: str) -> bool:
    low = label.lower()
    return "month" in low or "billing" in low


def _plan_meta(provider: str, plans: dict[str, Any]) -> dict[str, Any]:
    # Canonical collector key → plans / config key (e.g. antigravity → gemini).
    lookup = provider_config_key(provider)
    meta = plans.get(lookup) or plans.get(provider) or {}
    return meta if isinstance(meta, dict) else {}


def _score(
    *,
    remaining: float,
    days: float | None,
    urgent_remaining: float,
    urgent_days: float,
    min_remaining: float,
    label: str,
    max_days: float,
) -> tuple[Urgency, float]:
    # Base score from remaining fraction that would be wasted
    score = remaining

    # Time pressure
    if days is not None:
        if days <= 1:
            score += 40
        elif days <= 3:
            score += 30
        elif days <= 7:
            score += 20
        elif days <= 14:
            score += 10
        else:
            score += 2
    else:
        score += 5  # unknown reset — still interesting if high remaining

    # Longer-lived quotas are generally more important than short refill windows.
    if _looks_monthly(label):
        score += 15
    elif "week" in label.lower() or "7" in label.lower():
        score += 8

    if remaining >= urgent_remaining and days is not None and days <= urgent_days:
        urgency = Urgency.CRITICAL if days <= 3 or remaining >= 90 else Urgency.HIGH
    elif remaining >= min_remaining and days is not None and days <= max_days:
        urgency = Urgency.MEDIUM if remaining >= 50 else Urgency.LOW
    else:
        urgency = Urgency.NONE

    return urgency, score


def _redistribute_weights(flexibility: float) -> tuple[float, float, float]:
    base_value = 0.35
    base_flex = 0.30
    base_deadline = 0.35

    redistributed = base_flex * flexibility
    w_flex = base_flex - redistributed
    w_value = base_value + redistributed * 0.50
    w_deadline = base_deadline + redistributed * 0.50

    return (w_value, w_flex, w_deadline)


def _score_multi_dimension(
    *,
    profile: FlexibilityProfile,
    remaining: float,
    days: float | None,
    config: dict[str, Any] | None = None,
    monthly_price: float | None = None,
) -> tuple[Urgency, float]:
    if remaining < 1.0:
        return Urgency.NONE, 0.0

    # Normalize value urgency against this window's own plan price — never the
    # most expensive plan in the whole config (that diluted cheaper plans).
    plan_price_for_norm = float(monthly_price) if monthly_price else 20.0

    flex = profile.consumption_flexibility

    # --- value_urgency (0-100) ---
    value_urgency = 0.0
    if profile.value_at_risk_usd is not None and plan_price_for_norm > 0:
        value_urgency = max(0.0, min(100.0, (profile.value_at_risk_usd / plan_price_for_norm) * 100))

    # --- flexibility_urgency (0-100) ---
    flexibility_urgency = 0.0
    if flex >= 0.9:
        flexibility_urgency = 0.0
    elif flex >= 0.4:
        flexibility_urgency = 30.0 * (1.0 - flex) + 10.0
    else:
        if profile.earliest_start_calendar is not None:
            now = utcnow()
            remaining_sec = profile.earliest_start_calendar.timestamp() - now.timestamp()
            total_sec = 3600.0
            if days is not None and days > 0:
                total_sec = days * 86400.0
            if remaining_sec <= 0:
                flexibility_urgency = 100.0
            else:
                ratio = max(0.0, min(1.0, remaining_sec / total_sec))
                flexibility_urgency = 60.0 + 40.0 * (1.0 - ratio)
        elif flex < 0.1:
            flexibility_urgency = 30.0
        else:
            flexibility_urgency = 15.0

    # --- deadline_urgency (0-100) ---
    deadline_urgency = 0.0
    if days is not None:
        if days <= 0.5:
            raw = 100.0
        elif days <= 1:
            raw = 80.0
        elif days <= 3:
            raw = 60.0
        elif days <= 7:
            raw = 40.0
        elif days <= 14:
            raw = 20.0
        else:
            raw = 5.0
        deadline_urgency = max(0.0, min(100.0, raw * (0.5 + flex * 0.5)))

    # --- composite ---
    w_value, w_flex, w_deadline = _redistribute_weights(flex)
    score = (w_value * value_urgency + w_flex * flexibility_urgency + w_deadline * deadline_urgency) * 1.5

    if score >= 100:
        urgency = Urgency.CRITICAL
    elif score >= 75:
        urgency = Urgency.HIGH
    elif score >= 60:
        urgency = Urgency.MEDIUM
    elif score >= 30:
        urgency = Urgency.LOW
    elif score >= 10:
        urgency = Urgency.INFO
    else:
        urgency = Urgency.NONE

    return urgency, score


def _shared_allotment_enabled(provider_key: str, analysis_cfg: dict[str, Any]) -> bool:
    overrides = analysis_cfg.get("provider_overrides") or {}
    if not isinstance(overrides, dict):
        return False
    prov = overrides.get(provider_key)
    if not isinstance(prov, dict):
        return False
    return bool(prov.get("shared_allotment"))


def _pace_message(
    *,
    account: AccountUsage,
    window: QuotaWindow,
    verdict: str,
    pace: Any,
    days: float | None,
    deadline_is_estimated: bool,
    suppressed_children: list[QuotaWindow] | None = None,
) -> str:
    who = account.account or "default"
    when = _human_when(days, estimated=deadline_is_estimated)
    remaining = window.remaining()
    rem_s = f"{remaining:.0f}%" if remaining is not None else "some"
    child_note = ""
    if suppressed_children:
        labels = ", ".join(c.label for c in suppressed_children)
        if verdict == "conserve":
            child_note = (
                f" Avoid burning {labels} sessions — they draw the same budget you're already close to exhausting."
            )
        else:
            child_note = f" (this also covers your {labels} — no need to burn it separately)"
    if verdict == "conserve":
        return (
            f"{provider_display_name(account.provider)} · {who} · {window.label}: "
            f"pace yourself — projected to run out before reset ({rem_s} left, resets {when})."
            f"{child_note}"
        )
    return (
        f"{provider_display_name(account.provider)} · {who} · {window.label}: "
        f"{rem_s} left may go unused if you stay at this pace (resets {when})."
        f"{child_note}"
    )


def _human_when(days: float | None, *, estimated: bool = False) -> str:
    if days is None:
        return "on an unknown schedule"
    if estimated:
        whole_days = max(1, int(days + 0.5))
        unit = "day" if whole_days == 1 else "days"
        return f"in ~{whole_days} {unit}"
    if days < 1:
        return f"in {days * 24:.0f}h"
    return f"in {days:.1f} days"


def _message(
    *,
    account: AccountUsage,
    window_label: str,
    remaining: float,
    days: float | None,
    deadline_is_estimated: bool,
    plan_notes: Any,
) -> str:
    who = account.account or "default"
    plan = account.plan or "subscription"
    time_part = (
        f"resets {_human_when(days, estimated=deadline_is_estimated)}" if days is not None else "reset time unknown"
    )
    note = f" {plan_notes}" if plan_notes else ""
    return (
        f"Use {provider_display_name(account.provider)} ({who}, {plan}) soon: "
        f"{remaining:.0f}% of the {window_label} remains and {time_part}."
        f"{note}"
    )
