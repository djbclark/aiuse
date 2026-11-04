"""Per-run provider selection: ``--include`` / ``--exclude`` override ``config.toml``.

The command line can narrow or widen the set of providers one run collects and
shows, without editing ``[disabled_services]``:

- ``--include LIST`` is an allow-list. Only matching providers are collected and
  shown, and a match that ``[disabled_services]`` disables is re-enabled for this
  run.
- ``--exclude LIST`` adds providers to the disabled set for this run. When a
  provider matches both flags, exclusion wins.

Each ``LIST`` is comma-separated and may repeat; every entry is a provider id or
alias, a display name, or a glob (``*``, ``?``, ``[...]``). Matching is
case-insensitive. A glob is tried against the canonical id and the display name
only (``oc-*``, ``claude*``), never against aliases: ``open*`` would otherwise
catch ``codex`` and ``pi`` through their ``openai`` aliases.

The selection lives under ``config["provider_selection"]`` for the run only; it
is never read from or written to ``config.toml``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from typing import Any

from aiuse.models import PROVIDER_DISPLAY_NAMES, canonical_provider

SELECTION_KEY = "provider_selection"
EXCLUDE_REASON = "excluded by --exclude (this run only)"
INCLUDE_REASON = "not in --include (this run only)"
_GLOB_CHARS = frozenset("*?[")


def split_patterns(values: Iterable[str] | None) -> list[str]:
    """Flatten repeated, comma-separated flag values into lowercase patterns, in order."""
    patterns: list[str] = []
    for value in values or ():
        for item in str(value).split(","):
            item = item.strip().lower().replace(" ", "-")
            if item and item not in patterns:
                patterns.append(item)
    return patterns


def _names(provider: str) -> set[str]:
    """The canonical id and display name of a provider: what a glob is matched against."""
    return {provider, PROVIDER_DISPLAY_NAMES.get(provider, provider).lower()}


def matches(provider: str, pattern: str) -> bool:
    """Whether ``pattern`` (id, alias, display name or glob) names ``provider``."""
    provider = canonical_provider(provider)
    pattern = pattern.strip().lower().replace(" ", "-")
    names = _names(provider)
    if _GLOB_CHARS & set(pattern):
        return any(fnmatchcase(name, pattern) for name in names)
    return pattern in names or canonical_provider(pattern) == provider


def matches_any(provider: str, patterns: Iterable[str]) -> bool:
    return any(matches(provider, pattern) for pattern in patterns)


def get_selection(config: Mapping[str, Any] | None) -> tuple[list[str], list[str]]:
    """The run's ``(include, exclude)`` patterns; both empty when no flag was given."""
    raw = (config or {}).get(SELECTION_KEY)
    if not isinstance(raw, Mapping):
        return [], []
    include: Any = raw.get("include") or []
    exclude: Any = raw.get("exclude") or []
    return [str(p) for p in include], [str(p) for p in exclude]


def set_selection(config: dict[str, Any], include: list[str], exclude: list[str]) -> None:
    """Record the run's patterns on ``config`` (removing the key when both are empty)."""
    if include or exclude:
        config[SELECTION_KEY] = {"include": list(include), "exclude": list(exclude)}
    else:
        config.pop(SELECTION_KEY, None)


def selection_active(config: Mapping[str, Any] | None) -> bool:
    return any(get_selection(config))


def blocked_reason(config: Mapping[str, Any] | None, provider: str) -> str | None:
    """Why this run's flags drop ``provider``, or ``None`` when they leave it alone."""
    include, exclude = get_selection(config)
    if exclude and matches_any(provider, exclude):
        return EXCLUDE_REASON
    if include and not matches_any(provider, include):
        return INCLUDE_REASON
    return None


def reenabled_by_include(config: Mapping[str, Any] | None, provider: str) -> bool:
    """``--include`` names this provider, so ``[disabled_services]`` no longer applies to it."""
    include, _exclude = get_selection(config)
    return bool(include) and matches_any(provider, include)


def reenabled_providers(config: Mapping[str, Any] | None) -> list[str]:
    """Config-disabled providers this run's ``--include`` brings back (a cache cannot have their rows)."""
    raw = (config or {}).get("disabled_services")
    if not isinstance(raw, Mapping):
        return []
    names = {canonical_provider(str(key).strip()) for key in raw if str(key).strip()}
    return sorted(p for p in names if reenabled_by_include(config, p) and blocked_reason(config, p) is None)


def known_providers(config: Mapping[str, Any] | None = None) -> list[str]:
    """Canonical ids globs expand against: the registry plus anything the config disables."""
    names = set(PROVIDER_DISPLAY_NAMES)
    raw = (config or {}).get("disabled_services")
    if isinstance(raw, Mapping):
        names.update(canonical_provider(str(key).strip()) for key in raw if str(key).strip())
    return sorted(names)


def blocked_providers(config: Mapping[str, Any] | None) -> set[str]:
    """Known providers this run's flags drop (used to skip their collectors)."""
    return {p for p in known_providers(config) if blocked_reason(config, p)}


def split_known(config: Mapping[str, Any] | None) -> tuple[list[str], list[str]]:
    """``(included, excluded)`` over the known providers, after config and flags."""
    from aiuse.config import canonical_disabled_services

    disabled = canonical_disabled_services(dict(config or {}))
    included: list[str] = []
    excluded: list[str] = []
    for provider in known_providers(config):
        if provider in disabled or blocked_reason(config, provider):
            excluded.append(provider)
        else:
            included.append(provider)
    return included, excluded


def with_include(config: Mapping[str, Any] | None, include: list[str]) -> dict[str, Any]:
    """A copy of ``config`` whose ``--include`` is ``include`` (``--exclude`` kept)."""
    preview = dict(config or {})
    set_selection(preview, include, get_selection(config)[1])
    return preview


def unmatched_patterns(config: Mapping[str, Any] | None, patterns: Iterable[str]) -> list[str]:
    """Patterns naming no known provider: usually a typo, so callers warn."""
    known = known_providers(config)
    return [pattern for pattern in patterns if not any(matches(provider, pattern) for provider in known)]


def selection_warnings(config: Mapping[str, Any] | None) -> list[str]:
    """One warning per ``--include`` / ``--exclude`` pattern that matches no known provider."""
    include, exclude = get_selection(config)
    return [
        f"warning: --{flag} {pattern!r} matches no known provider"
        for flag, group in (("include", include), ("exclude", exclude))
        for pattern in unmatched_patterns(config, group)
    ]
