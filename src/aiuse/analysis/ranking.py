"""Queue ordering: which alert the board puts last, and which one ``suggest`` names.

One ordering answers every "what do I use next?" question.  The board
(``report.py``) sorts its rows by :func:`_alert_queue_priority`, and
``suggest.pick_suggestion`` picks its winner by :func:`alert_sort_key`, which is
that same priority behind the same band, so the board's top row and the
suggestion cannot disagree.  This module owns the formula; ``report.py`` owns how
a priority is displayed (the 0-99 ``##`` score).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiuse.analysis.pace import compute_pace, independent_pool_key
from aiuse.analysis.use_or_lose import (
    _DEFAULT_WAKING_HOURS,
    _compute_value_at_risk,
    _plan_meta,
    window_value_multiplier,
)
from aiuse.config import DEFAULT_CONFIG, plan_monthly_price
from aiuse.models import (
    PaceProfile,
    QuotaWindow,
    Urgency,
    UseOrLoseAlert,
    effective_window_minutes,
    utcnow,
)

# Ladder display tags.  The numeric order is the visual order; each band has
# its own queue semantics so comparisons never pretend that an error, prepaid
# wallet, conserve warning, and burn recommendation share one numeric scale.
_BAND_ERROR = 0  # could not fetch usage
_BAND_EMPTY = 1  # totally depleted
_BAND_NA = 2  # non-expiring prepaid / payg — no use-or-lose urgency
_BAND_CONSERVE = 3  # pace yourself
_BAND_MID = 4  # on pace / advisory / low urgency
_BAND_USE = 5  # important to use soon


# Which band wins when several alerts describe one pool: attention-grabbing
# states outrank quiet ones, so a pool with an exhausted weekly and a healthy
# 5h still reads as exhausted.  Shared with ``report._build_matrix_rows``,
# which collapses a pool's alerts onto this one band, and with ``suggest``,
# which must not offer a burn a stronger sibling hides on the board.
_BAND_ATTENTION = {
    _BAND_ERROR: 5,
    _BAND_EMPTY: 4,
    _BAND_CONSERVE: 3,
    _BAND_USE: 2,
    _BAND_MID: 1,
    _BAND_NA: 0,
}


def _ladder_coverage_key(provider: str, account: str | None, pool_id: str = "") -> tuple[str, str, str]:
    """Provider + account + independent pool (Gemini vs Claude/GPT, etc.)."""
    return (provider.casefold(), (account or "").casefold(), pool_id)


def _pool_id_for_label(label: str | None) -> str:
    return independent_pool_key(label) or ""


def pool_masked(alert: UseOrLoseAlert, alerts: list[UseOrLoseAlert]) -> bool:
    """Whether a stronger alert in ``alert``'s own pool hides it on the board.

    The board collapses each pool onto its most attention-grabbing alert (see
    :data:`_BAND_ATTENTION`); a burn sitting behind a conserve or empty sibling
    is not the board's advice, so ``pick_suggestion`` must not name it
    (aiuse-57n).  An account-level window state the board also weighs (its
    unalerted band) is not visible here, so this covers alert-versus-alert
    masking only.
    """
    if alert.urgency == Urgency.NONE:
        return False
    key = _ladder_coverage_key(alert.provider, alert.account, _pool_id_for_label(alert.window_label))
    mine = _BAND_ATTENTION[alert_priority_band(alert)]
    for other in alerts:
        if other is alert or other.urgency == Urgency.NONE:
            continue
        other_key = _ladder_coverage_key(other.provider, other.account, _pool_id_for_label(other.window_label))
        if other_key == key and _BAND_ATTENTION[alert_priority_band(other)] > mine:
            return True
    return False


def alert_priority_band(alert: UseOrLoseAlert) -> int:
    """Return the display band used as the primary queue key."""
    rem = alert.remaining_percent
    # Prepaid API balances never expire — inventory only, never empty/use tags.
    if alert.kind == "prepaid":
        return _BAND_NA
    # No capacity is always empty, regardless of an analysis urgency that may
    # be absent or stale. Prepaid was handled above because it is inventory.
    if rem <= 0.0:
        return _BAND_EMPTY
    if alert.urgency == Urgency.NONE:
        return _BAND_MID
    if alert.kind == "conserve" and rem <= 1.0:
        return _BAND_EMPTY
    if alert.kind == "conserve":
        return _BAND_CONSERVE
    if alert.urgency == Urgency.INFO:
        return _BAND_MID
    if alert.kind == "burn":
        if alert.urgency == Urgency.LOW:
            return _BAND_MID
        days = alert.days_until_reset
        if days is not None and days > 7.0:
            return _BAND_MID
        return _BAND_USE
    return _BAND_MID


def alert_use_urgency(alert: UseOrLoseAlert) -> float:
    """Primary within-band priority; higher appears lower in the queue.

    For slow, mid and use rows it is the dollars at risk in the alert's window at
    the built-in plan prices (the formula is in :class:`_Pricing`).  Kept as a
    scalar public helper for callers/tests.  Rendering uses the full
    :func:`_alert_queue_priority` tuple, so ties preserve the analyzer's
    recommendation order.
    """
    return _alert_queue_priority(alert)[0]


def _known_reset_priority(days_until_reset: float | None) -> tuple[float, float]:
    """Known refills sort below unknown ones; sooner refills sort lowest."""
    if days_until_reset is None:
        return (0.0, 0.0)
    return (1.0, -float(days_until_reset))


def _conserve_use_readiness(alert: UseOrLoseAlert) -> float:
    """Higher means closer to safe use; severe early-lockout rows stay high.

    A conserve row is the one active band where the analysis score points in
    the opposite direction from "use next".  Prefer the actual gap between
    projected exhaustion and reset; fall back to the inverse alert score.
    """
    pace = alert.pace
    if pace is not None and pace.projected_exhaust_at is not None and alert.days_until_reset is not None:
        days_to_exhaust = (pace.projected_exhaust_at - utcnow()).total_seconds() / 86400.0
        lockout_lead = max(0.0, float(alert.days_until_reset) - days_to_exhaust)
        return max(0.0, 100.0 - lockout_lead * (100.0 / 7.0))
    return max(0.0, 100.0 - float(alert.score))


# Last-resort price for a provider with no configured monthly_price and no
# priced plan anywhere in the config.
_FALLBACK_PRICE_USD = 10.0
# A window under this share elapsed is too early to project: `classify_pace`
# refuses to call it burn or conserve (`analysis.pace.min_elapsed_fraction`),
# and ranking must not lean on a projection the analysis layer declines to act
# on.  Default mirrors that config key; `_Pricing.from_config` reads it live.
_DEFAULT_MIN_ELAPSED_FRACTION = 0.15
# A queue priority: the band's ordering key first, tie-breakers after it.
_Priority = tuple[float, ...]


def _plans_or_default(config_plans: Any) -> dict[str, Any]:
    if isinstance(config_plans, dict):
        return config_plans
    default = DEFAULT_CONFIG.get("plans")
    return default if isinstance(default, dict) else {}


def _lowest_plan_price(plans: dict[str, Any]) -> float:
    """Cheapest provider-level price configured; the unpriced-provider fallback."""
    prices = []
    for meta in plans.values():
        price = meta.get("monthly_price") if isinstance(meta, dict) else None
        if not isinstance(price, bool) and isinstance(price, (int, float)) and price > 0:
            prices.append(float(price))
    return min(prices) if prices else _FALLBACK_PRICE_USD


def _gated_waste_fraction(pace: PaceProfile | None, min_elapsed_fraction: float) -> float | None:
    """A pace's projected waste share, or ``None`` when the window is too early to trust.

    ``classify_pace`` refuses to call a window under ``min_elapsed_fraction``
    elapsed burn or conserve (``analysis.pace.min_elapsed_fraction``); a row
    that carries no approving alert must not rank on the very projection the
    analyzer declined, so it falls back to capacity instead of a waste share.
    """
    if pace is None:
        return None
    if pace.elapsed_fraction is not None and pace.elapsed_fraction < min_elapsed_fraction:
        return None
    return pace.projected_waste_fraction


@dataclass(frozen=True)
class _Pricing:
    """What turns a window into dollars at risk: plan prices and waking hours.

    Built once per render.  ``plans`` falls back to the built-in defaults so the
    board still ranks by real prices when no config was passed.

    Every active band orders its rows by dollars.  Three figures, one window:

    * **capacity** -- what is still unspent in the window now:
      ``price / max(refills per waking month, 1) x remaining% x value_multiplier``
      (``analysis.use_or_lose._compute_value_at_risk``).  A 5-hour window is worth
      far less than a weekly one, and a percent of a $200 plan ten of a $20 plan's.
    * **cycle value** -- the same figure at 100% remaining: one full refill.
    * **waste** -- ``projected_waste_fraction x cycle value``: what the window's
      pace says will still be unspent when it resets.  A window too early to
      project (under ``min_elapsed_fraction`` elapsed, the analyzer's own gate)
      has no such figure and ranks by capacity, like a row with no pace at all.

    ``slow`` rows rank by capacity: they are on course to run dry, so what counts
    is how much they hold.  ``mid`` and ``use`` rows rank by waste, so an imminent
    reset with real waste outranks a big window with days left; a row whose pace
    cannot project a waste share (unclocked window, an alert that carries no pace)
    ranks by its capacity, the most it could lose.
    """

    plans: dict[str, Any]
    waking_hours: float
    fallback_price: float
    min_elapsed_fraction: float = _DEFAULT_MIN_ELAPSED_FRACTION

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> _Pricing:
        cfg = config or {}
        plans = _plans_or_default(cfg.get("plans"))
        analysis = cfg.get("analysis")
        analysis = analysis if isinstance(analysis, dict) else {}
        waking = float(analysis.get("waking_hours_per_day", _DEFAULT_WAKING_HOURS))
        pace = analysis.get("pace")
        pace = pace if isinstance(pace, dict) else {}
        min_elapsed = float(pace.get("min_elapsed_fraction", _DEFAULT_MIN_ELAPSED_FRACTION))
        return cls(
            plans=plans,
            waking_hours=waking,
            fallback_price=_lowest_plan_price(plans),
            min_elapsed_fraction=min_elapsed,
        )

    def capacity_usd(
        self,
        provider: str,
        account: str | None,
        remaining: float,
        window_minutes: int | None,
        label: str | None = None,
    ) -> float:
        """Dollars at risk in the unspent part of one window.

        The analyzer's own ``_compute_value_at_risk``, priced per account: the
        account's ``[plans.<provider>."<account>"]`` price, else the provider's,
        else the cheapest configured plan (conservative: an unpriced provider
        never outranks a priced one).  A window whose length neither the source
        nor its label states has no refill to price and is worth 0.0, the floor
        of its band, where the band's own tie-break orders it.
        """
        minutes = effective_window_minutes(label, window_minutes)
        if not minutes:
            return 0.0
        price = plan_monthly_price(self.plans, provider, account)
        if price is None:
            price = self.fallback_price
        return _compute_value_at_risk(
            remaining=max(0.0, min(100.0, float(remaining))),
            window_minutes=minutes,
            monthly_price=price,
            waking_hours_per_day=self.waking_hours,
            value_multiplier=window_value_multiplier(_plan_meta(provider, self.plans), minutes),
        )

    def at_risk_usd(
        self,
        provider: str,
        account: str | None,
        remaining: float,
        window_minutes: int | None,
        label: str | None,
        waste_fraction: float | None,
    ) -> float:
        """Dollars a ``mid`` or ``use`` row is ranked by.

        ``waste_fraction`` is the pace's ``projected_waste_fraction``: the share of
        the window that will still be unspent at its reset.  With one, the row is
        worth that share of a full refill (:meth:`capacity_usd` at 100%), priced
        like capacity: the account's price, the plan's multiplier, the cheapest-plan
        fallback.  Without one there is nothing to project, so the row keeps the
        dollars it holds now (capacity), the most it could lose.
        """
        if waste_fraction is None:
            return self.capacity_usd(provider, account, remaining, window_minutes, label)
        cycle = self.capacity_usd(provider, account, 100.0, window_minutes, label)
        return max(0.0, min(1.0, float(waste_fraction))) * cycle

    def window_at_risk_usd(
        self,
        provider: str,
        account: str | None,
        window: QuotaWindow,
        now: datetime | None = None,
    ) -> float:
        """:meth:`at_risk_usd` for a live window with no alert, paced as the analyzer would.

        0.0 when the window reports no remaining.  ``now`` defaults to the wall
        clock; the board passes its snapshot's collection time.  A window under
        ``min_elapsed_fraction`` elapsed keeps its capacity: the analyzer would
        not trust its projection (``classify_pace``), so neither does the board.
        An early window the analyzer *did* approve -- a short or prioritized
        burn -- arrives as an alert, and ``_alert_queue_priority`` ranks it by
        the pace that alert carries.
        """
        remaining = window.remaining()
        if remaining is None:
            return 0.0
        pace = compute_pace(window, now=now or utcnow())
        waste = _gated_waste_fraction(pace, self.min_elapsed_fraction)
        return self.at_risk_usd(provider, account, remaining, window.window_minutes, window.label, waste)


def _alert_queue_priority(
    alert: UseOrLoseAlert,
    band: int | None = None,
    pricing: _Pricing | None = None,
) -> _Priority:
    """Category-specific queue priority; larger tuples appear lower.

    Slow, mid and use rows lead with dollars (see :class:`_Pricing`): a slow row's
    capacity, a mid or use row's projected waste; each band's own signal breaks
    ties.
    """
    band = alert_priority_band(alert) if band is None else band
    rem = max(0.0, float(alert.remaining_percent))
    days = float(alert.days_until_reset) if alert.days_until_reset is not None else None

    if band == _BAND_ERROR:
        return (0.0, 0.0, 0.0)
    if band == _BAND_EMPTY:
        known, sooner = _known_reset_priority(days)
        return (known, sooner, 0.0)
    if band == _BAND_NA:
        value = alert.flexibility_profile.value_at_risk_usd if alert.flexibility_profile is not None else None
        return (1.0 if value is not None else 0.0, float(value or 0.0), rem)

    pricing = pricing or _Pricing.from_config(None)
    if band == _BAND_CONSERVE:
        # Least capacity first, most capacity last; equal capacity falls back to
        # recovery speed: most dangerous lockout first, closest-to-safe last.
        capacity = pricing.capacity_usd(alert.provider, alert.account, rem, alert.window_minutes, alert.window_label)
        return (capacity, _conserve_use_readiness(alert), -float(alert.score))

    # Burn alerts, including low-urgency ``mid`` ones, lead with the dollars their
    # pace projects to expire unspent and break ties by analyzer score, then
    # remaining capacity, then sooner reset.
    waste = alert.pace.projected_waste_fraction if alert.pace is not None else None
    at_risk = pricing.at_risk_usd(alert.provider, alert.account, rem, alert.window_minutes, alert.window_label, waste)
    return (at_risk, float(alert.score), rem, -(days if days is not None else 99.0))


def _ladder_sort_key(
    band: int,
    priority: _Priority,
    provider: str,
    account: str | None,
) -> tuple:
    """Band first, then its own queue priority; stable by identity."""
    return (band, *priority, provider.casefold(), (account or "").casefold())


def alert_sort_key(alert: UseOrLoseAlert, pricing: _Pricing | None = None) -> tuple:
    """Where the board sorts ``alert``'s own row: the greater key sits lower, more actionable.

    ``max`` over alerts is therefore the one the board ranks highest; this is how
    ``pick_suggestion`` follows the board.
    """
    band = alert_priority_band(alert)
    return _ladder_sort_key(band, _alert_queue_priority(alert, band, pricing), alert.provider, alert.account)
