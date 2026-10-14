"""Shared pool-family normalization for config keys and matching."""

from __future__ import annotations

from aiuse.models import canonical_provider

# Legacy config spellings -> canonical pool_family (snake_case).
POOL_FAMILY_ALIASES: dict[str, str] = {
    "cursor_grok": "grok_bot",
    "included": "total",
    "other": "third_party",
    "cursor_other_models": "third_party",
}


def normalize_pool_family(raw: str | None) -> str | None:
    if not raw:
        return None
    key = str(raw).strip().casefold().replace("-", "_")
    return POOL_FAMILY_ALIASES.get(key, key)


def parse_pool_rule_key(key: str) -> tuple[str, str | None]:
    """Parse ``provider`` or ``provider/pool_family`` with alias normalization."""
    provider, _, family_raw = str(key).partition("/")
    provider = canonical_provider(provider.strip())
    family = normalize_pool_family(family_raw.strip()) if family_raw.strip() else None
    return provider, family


def pool_rules(config: dict, section: str) -> list[tuple[str, str | None, str]]:
    """Return [(provider, pool_family|None, reason)] from a config mapping."""
    raw = config.get(section)
    if not isinstance(raw, dict):
        if section == "excluded_pools":
            analysis = config.get("analysis")
            raw = analysis.get("excluded_pools") if isinstance(analysis, dict) else None
        if not isinstance(raw, dict):
            return []
    rules: list[tuple[str, str | None, str]] = []
    for key, value in raw.items():
        provider, family = parse_pool_rule_key(str(key))
        if not provider:
            continue
        reason = value.strip() if isinstance(value, str) and value.strip() else "excluded by operator"
        if section == "hidden_pools":
            reason = value.strip() if isinstance(value, str) and value.strip() else "hidden by operator"
        rules.append((provider, family, reason))
    return rules


def hidden_pool_rules(config: dict) -> list[tuple[str, str | None, str]]:
    return pool_rules(config, "hidden_pools")


def excluded_pool_rules(config: dict) -> list[tuple[str, str | None, str]]:
    return pool_rules(config, "excluded_pools")


def window_matches_rule(
    provider: str,
    pool_family: str | None,
    rule_provider: str,
    rule_family: str | None,
) -> bool:
    if canonical_provider(provider) != rule_provider:
        return False
    if rule_family is None:
        return True
    normalized = normalize_pool_family(pool_family)
    return normalized == rule_family
