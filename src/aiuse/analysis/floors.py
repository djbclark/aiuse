"""Per-service headroom floors (schema 1.8): an advisory reserve a consumer keeps.

A floor says how much headroom a consumer should leave unspent on a pool,
scaled by what the plan costs: a flat 10 USD reserve is 5% of a 199.99 USD plan
but half of a 20 USD one, so floors vary per service by plan value:

    floor_percent = clamp(reserve_usd / plan_monthly_price * 100, min_percent, max_percent)

rounded to one decimal. With no known plan price the floor is ``default_percent``.
A per-provider override (``[floors.providers.<provider>]``) gives either a fixed
``percent`` or its own ``reserve_usd``. A per-pool-family override
(``[floors.pool_families.<family>]``, same two keys) beats the provider override:
one provider can run pools of very different cost (codex: the subscription
windows against the prepaid ``openai_api`` credits).

Prepaid pools (``PREPAID_POOL_FAMILIES``, today ``openai_api``) are not priced by
the plan: an account's plan price buys its subscription windows, so the prepaid
credits of the same account get ``default_percent`` with ``floor_basis`` ``default``
unless a pool-family override is configured.

Precedence, first that applies: pool-family override, the built-in prepaid
default, provider override, the plan-price formula, ``default_percent``.

Floors are advisory: ``headroom_percent``, ``usable_now`` and the 5% near-empty
rule (``NEAR_EMPTY_BELOW``) do not change. A consumer compares
``headroom_above_floor_percent`` (headroom minus floor, negative below the floor)
with its own policy.

``floor_basis`` is one of ``FLOOR_BASES``: ``reserve_usd`` (the formula, with the
default or a per-provider reserve), ``config`` (a per-provider fixed ``percent``),
``default`` (no positive numeric plan price, or a prepaid pool: ``default_percent``).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from aiuse.models import canonical_provider

FLOOR_BASES = ("reserve_usd", "config", "default")

# Pool families billed from a prepaid balance: the account's plan price does not
# price them, so they take default_percent unless a pool-family override is set.
PREPAID_POOL_FAMILIES = frozenset({"openai_api"})

# Providers whose accounts report a prepaid pool family, for the validate_config hint
# that a provider override does not reach that pool.
PREPAID_FAMILY_PROVIDERS: dict[str, frozenset[str]] = {"openai_api": frozenset({"codex", "omp"})}

DEFAULT_RESERVE_USD = 10.0
DEFAULT_MIN_PERCENT = 2.0
DEFAULT_MAX_PERCENT = 20.0
DEFAULT_DEFAULT_PERCENT = 10.0


def _number(value: Any) -> float | None:
    """A finite number (bool and NaN/inf rejected), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


finite_number = _number


def _positive(value: Any, default: float) -> float:
    number = _number(value)
    return number if number is not None and number > 0 else default


def _percent(value: Any, default: float) -> float:
    number = _number(value)
    return number if number is not None and 0.0 <= number <= 100.0 else default


def _provider_override(floors: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    providers = floors.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    want = canonical_provider(provider)
    for key, value in providers.items():
        if isinstance(value, Mapping) and canonical_provider(str(key)) == want:
            return value
    return {}


def _family_key(pool_family: Any) -> str:
    return str(pool_family or "").strip().casefold()


def _pool_family_override(floors: Mapping[str, Any], pool_family: Any) -> Mapping[str, Any]:
    families = floors.get("pool_families")
    want = _family_key(pool_family)
    if not want or not isinstance(families, Mapping):
        return {}
    for key, value in families.items():
        if isinstance(value, Mapping) and _family_key(key) == want:
            return value
    return {}


def has_pool_family_rule(floors: Mapping[str, Any] | None, pool_family: Any) -> bool:
    """True when this family has its own floor rule (a configured override or the prepaid default)."""
    cfg: Mapping[str, Any] = floors if isinstance(floors, Mapping) else {}
    return bool(_pool_family_override(cfg, pool_family)) or _family_key(pool_family) in PREPAID_POOL_FAMILIES


def resolve_floor(
    floors: Mapping[str, Any] | None, provider: str, monthly_price: Any, pool_family: str | None = None
) -> tuple[float, str]:
    """(floor_percent, floor_basis) for one provider's pool.

    ``floors`` is the ``[floors]`` config table (None: all defaults);
    ``monthly_price`` the account's configured plan price in USD (None when no
    price is known); ``pool_family`` the routing entry's family (None: the
    provider's unsplit pool). A malformed value falls back to its default here;
    ``validate_config`` reports it.
    """
    cfg: Mapping[str, Any] = floors if isinstance(floors, Mapping) else {}
    default_percent = _percent(cfg.get("default_percent"), DEFAULT_DEFAULT_PERCENT)
    low = _percent(cfg.get("min_percent"), DEFAULT_MIN_PERCENT)
    high = _percent(cfg.get("max_percent"), DEFAULT_MAX_PERCENT)
    low, high = min(low, high), max(low, high)
    price = _number(monthly_price)

    def formula(reserve: float) -> tuple[float, str]:
        if price is None or price <= 0:
            return round(default_percent, 1), "default"
        return round(min(max(reserve / price * 100.0, low), high), 1), "reserve_usd"

    base_reserve = _positive(cfg.get("reserve_usd"), DEFAULT_RESERVE_USD)
    family_override = _pool_family_override(cfg, pool_family)
    fixed = _number(family_override.get("percent"))
    if fixed is not None and 0.0 <= fixed <= 100.0:
        return round(fixed, 1), "config"
    family_reserve = _number(family_override.get("reserve_usd"))
    if family_reserve is not None and family_reserve > 0:
        return formula(family_reserve)
    if _family_key(pool_family) in PREPAID_POOL_FAMILIES:
        return round(default_percent, 1), "default"
    override = _provider_override(cfg, provider)
    fixed = _number(override.get("percent"))
    if fixed is not None and 0.0 <= fixed <= 100.0:
        return round(fixed, 1), "config"
    return formula(_positive(override.get("reserve_usd"), base_reserve))
