"""Ceiling (total) rows vs breakdown sub-rows — explicit registry, not guesswork."""

from __future__ import annotations

from aiuse.models import QuotaWindow

# provider -> ceiling family -> sub-row families under that ceiling.
CEILING_FAMILIES: dict[str, dict[str, frozenset[str]]] = {
    "cursor": {"total": frozenset({"auto", "third_party"})},
}


def split_ceiling(
    provider: str,
    windows: list[QuotaWindow],
) -> tuple[QuotaWindow | None, list[QuotaWindow]]:
    """Return (ceiling, sub_rows) when at least one sub-row is present."""
    families = CEILING_FAMILIES.get(provider)
    if not families:
        return None, list(windows)

    from aiuse.analysis.selfdescribe import pool_family_id

    by_family: dict[str, QuotaWindow] = {}
    for window in windows:
        family = pool_family_id(provider, window.label, window.window_id)
        if family:
            by_family[family] = window

    for ceiling_family, sub_families in families.items():
        ceiling = by_family.get(ceiling_family)
        if ceiling is None:
            continue
        sub_rows = [by_family[f] for f in sub_families if f in by_family]
        if sub_rows:
            return ceiling, sub_rows
    return None, list(windows)


def window_is_ceiling(provider: str, pool_family: str | None) -> bool:
    if not pool_family:
        return False
    families = CEILING_FAMILIES.get(provider)
    if not families:
        return False
    return pool_family in families


def window_is_breakdown(provider: str, pool_family: str | None) -> bool:
    if not pool_family:
        return False
    families = CEILING_FAMILIES.get(provider)
    if not families:
        return False
    for sub_families in families.values():
        if pool_family in sub_families:
            return True
    return False


def effective_remaining(
    window: QuotaWindow,
    provider: str,
    windows: list[QuotaWindow],
) -> tuple[float | None, bool]:
    """(headroom, ceiling_binding) for one window on a live account."""
    from aiuse.analysis.selfdescribe import pool_family_id

    family = pool_family_id(provider, window.label, window.window_id)
    own = window.remaining()
    if own is None:
        return None, False
    if not window_is_breakdown(provider, family):
        return own, False
    ceiling, _ = split_ceiling(provider, windows)
    if ceiling is None:
        return own, False
    cap = ceiling.remaining()
    if cap is None:
        return own, False
    effective = min(own, cap)
    return effective, effective < own


def ceiling_binding_note(window: QuotaWindow, provider: str, windows: list[QuotaWindow]) -> str | None:
    own = window.remaining()
    effective, binding = effective_remaining(window, provider, windows)
    if not binding or own is None or effective is None:
        return None
    return f"{window.label}: {own:.0f}% left, but total leaves {effective:.0f}% — total binds"
