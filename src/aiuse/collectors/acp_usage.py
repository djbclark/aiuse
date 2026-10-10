"""ACP context and per-turn token readings from acp-run JSONL logs.

``acp-run --json`` records each turn under ``~/.local/state/acp-run/``. The
result's ``usage_update`` is the last ACP ``session/update`` whose
``sessionUpdate`` is ``usage_update``: ``used`` / ``size`` tokens in the
context window, plus ``cost`` when the agent sends it. ``usage`` is
``PromptResponse.usage`` (this turn's token counts). ``quota`` is
``PromptResponse._meta.quota`` from claude-agent-acp, codex-acp, and
zcode-acp-server: per-turn token counts, not a 5h or weekly plan window.

This collector only reads those logs. It does not start a turn. An ACP
probe would spend quota, and ``agy -p`` is a separate burst limit this
process must not touch. Plan windows still come from the other collectors,
which do not need the vendor TUI to be installed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiuse.models import AccountUsage, BillingKind, ContextUsage

# Verified 2026-10-09 against ~/.local/state/acp-run. Agents absent from both
# maps are still parsed: a future server that emits usage_update is kept.
ACP_AGENT_PROVIDERS: dict[str, str] = {
    "agy": "antigravity",
    "agy-refined": "antigravity",
    "claude": "claude",
    "codex": "codex",
    "copilot": "copilot",
    "devin": "devin",
    "hermes": "hermes",
    "opencode": "opencode-go",
    "zcode": "zai",
}

# These servers answered a turn and put no usage_update, PromptResponse.usage,
# or _meta.quota on it. Plan quota for the same services still comes from the
# other collectors.
ACP_AGENTS_WITHOUT_USAGE: dict[str, str] = {
    "cline": "clinepass",
    "cursor": "cursor",
    "goose": "goose",
    "grok": "grok",
    "qwen": "qwencloud",
}

_LOG_NAME = re.compile(r"^(\d{8})-(\d{6})-(.+)-(\d+)\.jsonl$")
_TURN_KEYS = ("totalTokens", "inputTokens", "outputTokens", "thoughtTokens", "cachedReadTokens", "cachedWriteTokens")
_DEFAULT_MAX_AGE_HOURS = 168.0
_MAX_FILES_PER_AGENT = 12


def default_acp_log_dir() -> Path:
    """Log directory acp-run writes, unless ``AIUSE_ACP_LOG_DIR`` is set."""
    override = os.environ.get("AIUSE_ACP_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / "acp-run"


def resolve_acp_log_dir(log_dir: str | Path | None) -> Path:
    if log_dir:
        return Path(log_dir).expanduser()
    return default_acp_log_dir()


@dataclass
class AcpReading:
    """Newest ACP usage signal for one agent log."""

    agent: str
    provider: str
    log_path: Path
    measured_at: datetime
    used: int | None = None
    size: int | None = None
    cost_amount: float | None = None
    cost_currency: str | None = None
    turn_usage: dict[str, Any] | None = None
    turn_quota: dict[str, Any] | None = None
    signals: tuple[str, ...] = ()
    stale: bool = False

    def to_context(self) -> ContextUsage:
        used_percent, remaining_percent = _percents(self.used, self.size)
        return ContextUsage(
            used=self.used,
            size=self.size,
            used_percent=used_percent,
            remaining_percent=remaining_percent,
            cost_amount=self.cost_amount,
            cost_currency=self.cost_currency,
            agent=self.agent,
            log=str(self.log_path),
            measured_at=self.measured_at.isoformat(),
            turn_usage=dict(self.turn_usage) if self.turn_usage else None,
            turn_quota=dict(self.turn_quota) if self.turn_quota else None,
            signals=self.signals,
        )

    def to_account(self) -> AccountUsage:
        context = self.to_context()
        notes = [_reading_note(self)]
        return AccountUsage(
            source="acp",
            provider=self.provider,
            account=self.agent,
            billing_kind=BillingKind.UNKNOWN,
            notes=notes,
            context_usage=context,
            collected_at=self.measured_at,
            raw={"agent": self.agent, "log": str(self.log_path), "signals": list(self.signals)},
        )


@dataclass
class AcpScan:
    """One pass over the log directory."""

    readings: list[AcpReading] = field(default_factory=list)
    agents_without_usage: list[str] = field(default_factory=list)
    log_dir: Path | None = None
    log_dir_present: bool = False


def collect_acp(
    *,
    log_dir: str | Path | None = None,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    now: datetime | None = None,
) -> list[AccountUsage]:
    """Newest fresh ACP reading per provider. Stale logs are omitted."""
    scan = scan_acp_logs(log_dir=log_dir, max_age_hours=max_age_hours, now=now)
    newest: dict[str, AcpReading] = {}
    for reading in scan.readings:
        if reading.stale:
            continue
        current = newest.get(reading.provider)
        if current is None or reading.measured_at > current.measured_at:
            newest[reading.provider] = reading
    return [reading.to_account() for reading in newest.values()]


def scan_acp_logs(
    *,
    log_dir: str | Path | None = None,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    now: datetime | None = None,
) -> AcpScan:
    """Newest reading per agent, including ones older than ``max_age_hours``."""
    root = resolve_acp_log_dir(log_dir)
    scan = AcpScan(log_dir=root, log_dir_present=root.is_dir())
    if not scan.log_dir_present:
        scan.agents_without_usage = sorted(ACP_AGENTS_WITHOUT_USAGE)
        return scan

    by_agent: dict[str, list[Path]] = {}
    for path in root.glob("*.jsonl"):
        agent = _agent_of(path.name)
        if agent:
            by_agent.setdefault(agent, []).append(path)

    moment = now or datetime.now(timezone.utc)
    seen_with_usage: set[str] = set()
    for agent, paths in by_agent.items():
        paths.sort(key=lambda item: item.name, reverse=True)
        reading = _newest_reading(agent, paths[:_MAX_FILES_PER_AGENT], moment, max_age_hours)
        if reading is None:
            continue
        scan.readings.append(reading)
        seen_with_usage.add(agent)

    missing = set(ACP_AGENTS_WITHOUT_USAGE)
    missing.update(agent for agent in by_agent if agent not in seen_with_usage and agent not in ACP_AGENT_PROVIDERS)
    missing.difference_update(seen_with_usage)
    scan.agents_without_usage = sorted(missing)
    return scan


def _newest_reading(
    agent: str,
    paths: list[Path],
    now: datetime,
    max_age_hours: float,
) -> AcpReading | None:
    provider = ACP_AGENT_PROVIDERS.get(agent, agent)
    for path in paths:
        parsed = _parse_log(path)
        if parsed is None:
            continue
        measured_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        age_hours = (now - measured_at).total_seconds() / 3600.0
        stale = max_age_hours > 0 and age_hours > max_age_hours
        return AcpReading(
            agent=agent,
            provider=provider,
            log_path=path,
            measured_at=measured_at,
            stale=stale,
            **parsed,
        )
    return None


def _parse_log(path: Path) -> dict[str, Any] | None:
    used: int | None = None
    size: int | None = None
    cost_amount: float | None = None
    cost_currency: str | None = None
    turn_usage: dict[str, Any] | None = None
    turn_quota: dict[str, Any] | None = None
    signals: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or "usage" not in line and "quota" not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                kind = event.get("kind")
                if kind == "update" and data.get("sessionUpdate") == "usage_update":
                    _take_usage_update(data, signals)
                    used, size, cost_amount, cost_currency = _usage_update_fields(
                        data, used, size, cost_amount, cost_currency
                    )
                nested = data.get("usage_update")
                if isinstance(nested, dict):
                    _take_usage_update(nested, signals)
                    used, size, cost_amount, cost_currency = _usage_update_fields(
                        nested, used, size, cost_amount, cost_currency
                    )
                if kind == "prompt_response":
                    usage = data.get("usage")
                    if isinstance(usage, dict) and any(key in usage for key in _TURN_KEYS):
                        turn_usage = {key: usage[key] for key in _TURN_KEYS if key in usage}
                        if "usage" not in signals:
                            signals.append("usage")
                    meta = data.get("_meta")
                    if isinstance(meta, dict) and isinstance(meta.get("quota"), dict):
                        turn_quota = meta["quota"]
                        if "quota" not in signals:
                            signals.append("quota")
    except OSError:
        return None
    if used is None and size is None and turn_usage is None and turn_quota is None:
        return None
    return {
        "used": used,
        "size": size,
        "cost_amount": cost_amount,
        "cost_currency": cost_currency,
        "turn_usage": turn_usage,
        "turn_quota": turn_quota,
        "signals": tuple(signals),
    }


def _take_usage_update(payload: dict[str, Any], signals: list[str]) -> None:
    if "usage_update" not in signals and (
        _as_int(payload.get("used")) is not None or _as_int(payload.get("size")) is not None
    ):
        signals.append("usage_update")


def _usage_update_fields(
    payload: dict[str, Any],
    used: int | None,
    size: int | None,
    cost_amount: float | None,
    cost_currency: str | None,
) -> tuple[int | None, int | None, float | None, str | None]:
    parsed_used = _as_int(payload.get("used"))
    parsed_size = _as_int(payload.get("size"))
    if parsed_used is not None:
        used = parsed_used
    if parsed_size is not None:
        size = parsed_size
    cost = payload.get("cost")
    if isinstance(cost, dict) and isinstance(cost.get("amount"), (int, float)):
        cost_amount = float(cost["amount"])
        currency = cost.get("currency")
        if isinstance(currency, str) and currency.strip():
            cost_currency = currency.strip()
    return used, size, cost_amount, cost_currency


def _agent_of(name: str) -> str | None:
    match = _LOG_NAME.match(name)
    if not match:
        return None
    return match.group(3)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _percents(used: int | None, size: int | None) -> tuple[float | None, float | None]:
    if used is None or size is None or size <= 0:
        return None, None
    used_percent = round(100.0 * used / size, 2)
    return used_percent, round(max(0.0, 100.0 - used_percent), 2)


def _reading_note(reading: AcpReading) -> str:
    if reading.used is not None and reading.size:
        used_percent, _remaining = _percents(reading.used, reading.size)
        window = f"{reading.used} / {reading.size} tokens ({used_percent:.1f}% of the context window)"
    else:
        window = "no context used/size"
    parts = [
        f"ACP context from {reading.agent}: {window}.",
        "This is the session context window, not plan or subscription quota.",
    ]
    if reading.agent == "opencode":
        parts.append("OpenCode ACP context is not the OpenCode Go plan meter.")
    if reading.agent == "hermes":
        parts.append("Hermes ACP context is this session window, not the Hermes local-log vendor cost rows.")
    if reading.turn_usage and reading.turn_usage.get("totalTokens") is not None:
        parts.append(f"Last turn totalTokens={reading.turn_usage['totalTokens']}.")
    if reading.turn_quota is not None:
        parts.append("PromptResponse._meta.quota counts tokens in that turn, not the plan window.")
    parts.append(f"Log {reading.log_path.name}.")
    if reading.stale:
        parts.append("The log is older than collectors.acp.max_age_hours.")
    return " ".join(parts)
