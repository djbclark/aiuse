"""Which usage sources can speak for each vendor, and which one is pinned.

Quota sources (cswap, CodexBar, caut, OpenUsage, tokscale, and the native
collectors) report plan windows and balances. They do not need the vendor
TUI. The ACP log source reports context-window fill and per-turn tokens
from ``~/.local/state/acp-run`` and is never blended into those percents.

``[usage_sources]`` maps a provider to ``blend`` (the default) or to one
collector id. A pin drops every other source for that provider.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from aiuse.collectors.acp_usage import (
    ACP_AGENT_PROVIDERS,
    ACP_AGENTS_WITHOUT_USAGE,
    AcpReading,
    scan_acp_logs,
)
from aiuse.collectors.base import which
from aiuse.models import canonical_provider, provider_display_name

ACP_SOURCE = "acp"

# Collectors that can report plan or balance data for a provider. Multi-provider
# tools are listed only where that tool is known to track the service.
QUOTA_COVERAGE: dict[str, tuple[str, ...]] = {
    "claude": ("cswap", "codexbar", "caut", "openusage_ai", "openusage_sh", "tokscale", "hermes"),
    "codex": ("codexbar", "caut", "openusage_ai", "openusage_sh", "tokscale", "hermes"),
    "copilot": ("tokscale", "codexbar", "openusage_ai", "openusage_sh"),
    "cursor": ("codexbar", "openusage_ai", "openusage_sh", "tokscale"),
    "grok": ("codexbar", "openusage_ai", "openusage_sh", "tokscale", "grok_billing", "hermes"),
    "antigravity": ("codexbar", "openusage_ai", "openusage_sh", "tokscale", "hermes"),
    "opencode-go": ("opencode_go", "codexbar", "openusage_ai", "openusage_sh", "tokscale"),
    "opencode-zen": ("opencode_zen",),
    "deepseek": ("deepseek", "codexbar", "openusage_ai", "openusage_sh", "tokscale", "hermes"),
    "openrouter": ("openrouter", "codexbar", "openusage_ai", "hermes"),
    "qwencloud": ("qwencloud", "codexbar"),
    "alibaba": ("bailian", "codexbar"),
    "muse": ("muse",),
    "clinepass": ("clinepass",),
    "zai": ("codexbar", "openusage_ai", "openusage_sh", "tokscale"),
    "devin": ("codexbar", "openusage_ai", "tokscale"),
}

_MULTI_PROVIDER = frozenset({"codexbar", "openusage_ai", "openusage_sh", "tokscale", "caut", "hermes"})

_TOOL: dict[str, str] = {
    "cswap": "cswap",
    "codexbar": "codexbar",
    "caut": "caut",
    "openusage_sh": "openusage-sh",
    "tokscale": "tokscale",
    "clinepass": "cline",
    "muse": "muse",
    "qwencloud": "qwencloud",
    "bailian": "bl",
    "grok_billing": "grok",
}

_CREDENTIAL_ENV: dict[str, str] = {
    "opencode_go": "AIUSE_OPENCODE_ZEN_COOKIE",
    "opencode_zen": "AIUSE_OPENCODE_ZEN_COOKIE",
    "openrouter": "AIUSE_OPENROUTER_MANAGEMENT_KEY",
    "deepseek": "AIUSE_DEEPSEEK_API_KEY",
}

_OPENUSAGE_APP = Path("/Applications/OpenUsage.app/Contents/Helpers/openusage")


def pinned_usage_source(usage_sources: dict[str, Any] | None, provider: str) -> str | None:
    """Collector id pinned as the only reading for ``provider``, or None to blend."""
    if not isinstance(usage_sources, dict):
        return None
    provider_id = canonical_provider(provider)
    for key, value in usage_sources.items():
        if canonical_provider(str(key)) != provider_id:
            continue
        source = value.strip() if isinstance(value, str) else ""
        if not source or source == "blend":
            return None
        return source
    return None


def usage_source_report(
    config: dict[str, Any] | None = None,
    *,
    log_dir: str | Path | None = None,
    max_age_hours: float | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Active and inactive usage sources for every known vendor service.

    Does not run a quota collect and does not start an ACP turn. ``active``
    means the tool is on PATH, a credential env var is set, or a recent ACP
    log contains usage fields. A vendor TUI can be missing and the quota
    sources still show up.
    """
    cfg = config or {}
    raw_collectors = cfg.get("collectors")
    collectors: dict[str, Any] = raw_collectors if isinstance(raw_collectors, dict) else {}
    raw_pins = cfg.get("usage_sources")
    pins: dict[str, Any] = raw_pins if isinstance(raw_pins, dict) else {}
    lookup = which_fn if which_fn is not None else which
    env = environ if environ is not None else dict(os.environ)
    acp_section = collectors.get(ACP_SOURCE)
    acp_cfg: dict[str, Any] = acp_section if isinstance(acp_section, dict) else {}
    if max_age_hours is None:
        try:
            max_age_hours = float(acp_cfg.get("max_age_hours", 168))
        except (TypeError, ValueError):
            max_age_hours = 168.0
    configured_dir = log_dir if log_dir is not None else acp_cfg.get("log_dir")
    scan = scan_acp_logs(log_dir=configured_dir, max_age_hours=max_age_hours)
    fresh, stale = _acp_by_provider(scan.readings)

    providers = set(QUOTA_COVERAGE)
    providers.update(ACP_AGENT_PROVIDERS.values())
    providers.update(provider for provider in ACP_AGENTS_WITHOUT_USAGE.values() if provider != "goose")

    rows = []
    for provider in sorted(providers, key=str.casefold):
        pin = pinned_usage_source(pins, provider)
        sources = []
        for source in QUOTA_COVERAGE.get(provider, ()):
            sources.append(_quota_source_row(source, collectors, lookup, env, pin))
        sources.append(_acp_source_row(provider, collectors, fresh.get(provider), stale.get(provider), pin))
        rows.append(
            {
                "provider": provider,
                "display_name": provider_display_name(provider),
                "mode": "pinned" if pin else "blend",
                "pinned_source": pin,
                "sources": sources,
            }
        )

    return {
        "note": (
            "Quota sources report plan windows and balances and do not need the vendor TUI. "
            "ACP reports context-window fill and per-turn tokens from existing acp-run logs. "
            "It is not 5h, weekly, or subscription quota. "
            "mode blend keeps today's source priority and attaches ACP context beside it. "
            "mode pinned uses only pinned_source. "
            "status active means the source can be read right now, not that the last snapshot had a number."
        ),
        "acp_agents_without_usage": scan.agents_without_usage,
        "acp_log_dir": str(scan.log_dir) if scan.log_dir is not None else None,
        "acp_log_dir_present": scan.log_dir_present,
        "providers": rows,
    }


def render_usage_sources(report: dict[str, Any]) -> str:
    """Plain-text form of :func:`usage_source_report`."""
    lines = [
        "usage sources",
        report["note"],
        "",
    ]
    for index, provider in enumerate(report.get("providers") or [], start=1):
        mode = provider["mode"]
        if provider.get("pinned_source"):
            mode = f"pinned:{provider['pinned_source']}"
        lines.append(f"{index}. {provider['display_name']} ({provider['provider']})  {mode}")
        for source_index, source in enumerate(provider.get("sources") or [], start=1):
            lines.append(
                f"   {index}.{source_index} {source['id']:<14} {source['status']:<10} {source['kind']:<8} {source['detail']}"
            )
        lines.append("")
    missing = report.get("acp_agents_without_usage") or []
    if missing:
        lines.append(
            "ACP servers with no usage fields (checked 2026-10-09, and any log in this directory "
            f"that still has none): {', '.join(missing)}."
        )
        lines.append("Plan quota for those services still comes from the quota sources above.")
    if not report.get("acp_log_dir_present"):
        lines.append(f"ACP log directory is absent ({report.get('acp_log_dir')}). No context readings.")
    return "\n".join(lines).rstrip() + "\n"


def _acp_by_provider(readings: list[AcpReading]) -> tuple[dict[str, AcpReading], dict[str, AcpReading]]:
    fresh: dict[str, AcpReading] = {}
    stale: dict[str, AcpReading] = {}
    for reading in readings:
        bucket = stale if reading.stale else fresh
        current = bucket.get(reading.provider)
        if current is None or reading.measured_at > current.measured_at:
            bucket[reading.provider] = reading
    return fresh, stale


def _enabled(collectors: dict[str, Any], name: str) -> bool:
    section = collectors.get(name)
    if section is None:
        return True
    if isinstance(section, bool):
        return section
    if isinstance(section, dict):
        return bool(section.get("enabled", True))
    return True


def _quota_source_row(
    source: str,
    collectors: dict[str, Any],
    lookup: Callable[[str], str | None],
    env: dict[str, str],
    pin: str | None,
) -> dict[str, Any]:
    if not _enabled(collectors, source):
        status, detail = "disabled", "collectors." + source + " enabled = false"
    else:
        status, detail = _quota_availability(source, lookup, env)
    if pin and pin != source and status != "disabled":
        detail = f"pinned to {pin}; {detail}"
        status = "ignored"
    return {"id": source, "kind": "quota", "status": status, "detail": detail}


def _quota_availability(
    source: str,
    lookup: Callable[[str], str | None],
    env: dict[str, str],
) -> tuple[str, str]:
    extra = (
        " Multi-provider: a row appears only when this tool tracks the service." if source in _MULTI_PROVIDER else ""
    )
    if source == "hermes":
        state = Path.home() / ".local" / "state" / "hermes"
        if state.is_dir():
            return "active", "Hermes session logs under ~/.local/state/hermes." + extra
        return "inactive", "no ~/.local/state/hermes" + extra
    if source == "openusage_ai":
        if _OPENUSAGE_APP.is_file():
            return "active", "OpenUsage.app CLI." + extra
        if lookup("openusage"):
            return "active", "openusage on PATH (confirm it is OpenUsage.app, not openusage.sh)." + extra
        return "inactive", "OpenUsage.app CLI not found; loopback HTTP may still answer." + extra
    env_name = _CREDENTIAL_ENV.get(source)
    if env_name:
        if env.get(env_name, "").strip():
            return "active", f"{env_name} is set."
        return "enabled", f"collector on; quiet until {env_name} or its SecretSpec key is set."
    tool = _TOOL.get(source)
    if tool is None:
        return "enabled", "collector on."
    if lookup(tool):
        return "active", f"{tool} on PATH." + extra
    return "inactive", f"{tool} not on PATH." + extra


def _acp_source_row(
    provider: str,
    collectors: dict[str, Any],
    fresh: AcpReading | None,
    stale: AcpReading | None,
    pin: str | None,
) -> dict[str, Any]:
    if not _enabled(collectors, ACP_SOURCE):
        status, detail = "disabled", "collectors.acp enabled = false"
    elif fresh is not None:
        status, detail = "active", _acp_detail(fresh)
    elif stale is not None:
        status, detail = "inactive", _acp_detail(stale) + " Older than collectors.acp.max_age_hours."
    else:
        agents = [
            agent for agent, mapped in {**ACP_AGENT_PROVIDERS, **ACP_AGENTS_WITHOUT_USAGE}.items() if mapped == provider
        ]
        if agents:
            names = ", ".join(sorted(set(agents)))
            known_gap = any(agent in ACP_AGENTS_WITHOUT_USAGE for agent in agents)
            if known_gap and not any(agent in ACP_AGENT_PROVIDERS for agent in agents):
                detail = f"{names} ACP server emits no usage fields. Plan quota is unchanged."
            else:
                detail = f"no recent {names} log with usage fields."
            status = "inactive"
        else:
            status, detail = "inactive", "no ACP agent mapped to this service."
    if pin and pin != ACP_SOURCE and status != "disabled":
        detail = f"pinned to {pin}; {detail}"
        status = "ignored"
    return {"id": ACP_SOURCE, "kind": "context", "status": status, "detail": detail}


def _acp_detail(reading: AcpReading) -> str:
    signals = ", ".join(reading.signals) if reading.signals else "usage"
    if reading.used is not None and reading.size:
        window = f"{reading.used}/{reading.size} context tokens"
    else:
        window = "no context used/size"
    quota_note = ""
    if "quota" in reading.signals:
        quota_note = " _meta.quota is per-turn tokens, not the plan."
    return f"{reading.agent} {signals}: {window}.{quota_note} Log {reading.log_path.name}."
