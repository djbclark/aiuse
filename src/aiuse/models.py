"""Normalized data models for live provider quotas."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


@dataclass
class RoutingContext:
    """External routing context supplied by Hermes or another orchestrator."""

    primary_model: str
    primary_provider: str
    fallback_model: str | None = None
    fallback_provider: str | None = None

    def __post_init__(self):
        self.primary_provider = normalize_provider(self.primary_provider)
        if self.fallback_provider:
            self.fallback_provider = normalize_provider(self.fallback_provider)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_provider(provider: str) -> str:
    """Normalize external provider IDs (like 'openai-codex') to internal canonical keys."""
    return canonical_provider(provider)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any) -> int | None:
    number = coerce_float(value)
    return int(number) if number is not None else None


# Window-duration boundaries (minutes) shared by every collector that buckets a
# raw `windowMinutes` value into a human quota kind, and by the analysis layer
# that decides whether a window is a short rate-limit (not "monthly waste").
WINDOW_5H_MAX_MINUTES = 360
WINDOW_WEEKLY_MAX_MINUTES = 10080
WINDOW_MONTHLY_MAX_MINUTES = 44640


def classify_window_minutes(minutes: int | None) -> str | None:
    """Bucket a window's duration in minutes into '5h' | 'weekly' | 'monthly' | None."""
    if minutes is None:
        return None
    if minutes <= WINDOW_5H_MAX_MINUTES:
        return "5h"
    if minutes <= WINDOW_WEEKLY_MAX_MINUTES:
        return "weekly"
    if minutes <= WINDOW_MONTHLY_MAX_MINUTES:
        return "monthly"
    return None


PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    # Keyed by *canonical* provider id (see canonical_provider) — never by a
    # config key, or the same vendor prints under two names in one report.
    #
    # Every display name should contain its vendor CLI's name as a case-insensitive
    # substring, so `grep -i <cli-name>` finds the right report line. Most vendor
    # names already do (Claude/claude, Codex/codex, GitHub Copilot/copilot,
    # Grok/grok, OpenCode/opencode) — antigravity and cursor need an explicit
    # suffix since their CLI names ("agy", "cursor-agent") aren't substrings of
    # the plain vendor name.
    "antigravity": "Google AI / Antigravity (agy)",
    "claude": "Claude Code",
    "codex": "Codex",
    "copilot": "GitHub Copilot",
    "cursor": "Cursor (cursor-agent)",
    "grok": "Grok",
    "opencode-go": "OpenCode Go",
    "opencode-zen": "OpenCode Zen",
}

# Any provider spelling that may reach us — vendor ids from collectors, external
# orchestrator ids, and the config keys below — mapped to the one canonical
# provider id used for identity, matching and display.
#
# Note the direction: this table collapses *toward* the collector id, while
# PROVIDER_CONFIG_ALIASES maps the other way, toward the `[plans]` /
# `[provider_overrides]` config key. Config keys are included here (gemini,
# opencode) so a round trip through provider_config_key — or an old snapshot
# written before this normalization existed — still lands on one identity.
PROVIDER_ID_ALIASES: dict[str, str] = {
    "chatgpt": "codex",
    "openai-codex": "codex",
    "github-copilot": "copilot",
    "grok-build": "grok",
    "supergrok": "grok",
    "opencodego": "opencode-go",
    "opencode": "opencode-go",
    "gemini": "antigravity",
}

# Map canonical collector provider keys to config plan/override keys.
PROVIDER_CONFIG_ALIASES: dict[str, str] = {
    "antigravity": "gemini",
    "opencode-go": "opencode",
}

# Map external orchestrator (e.g. Hermes) provider IDs to aiuse canonical providers.
# Subset of PROVIDER_ID_ALIASES, kept for callers that import it by name.
EXTERNAL_PROVIDER_ALIASES: dict[str, str] = {
    "openai-codex": "codex",
}


def canonical_provider(provider: str) -> str:
    """Normalize any provider spelling to the canonical collector provider id.

    This is the identity key: two rows describing the same vendor subscription
    must agree here, whether they came from a collector, an external
    orchestrator, or a snapshot written by an older version.
    """
    if not provider:
        return provider
    key = provider.strip().lower().replace(" ", "-")
    return PROVIDER_ID_ALIASES.get(key, key)


def provider_display_name(provider: str) -> str:
    key = canonical_provider(provider)
    return PROVIDER_DISPLAY_NAMES.get(key, key.replace("-", " ").title())


def provider_config_key(provider: str) -> str:
    """Normalize a provider id for looking up plans / provider_overrides.

    Config lookup only. Never use the result as an identity or display key —
    it deliberately collapses onto the config's spelling of the vendor.
    """
    key = canonical_provider(provider)
    return PROVIDER_CONFIG_ALIASES.get(key, key)


def keep_copilot_report_window(label: str) -> bool:
    """Whether a Copilot quota window belongs in the use-or-lose report.

    Completions (inline autocomplete) and chat-message caps are not comparable to
    Claude/Codex/OpenCode subscription burn windows. Only premium requests are.
    """
    return "premium" in (label or "").lower()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # Common variants: ...Z, +00:00, space separator
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class BillingKind(str, Enum):
    """How the allotment is billed / expires."""

    SUBSCRIPTION_WINDOW = "subscription_window"  # resets on schedule; unused is lost
    PREPAID_BALANCE = "prepaid_balance"  # rolls until spent
    PAYG_API = "payg_api"  # pay as you go, no allotment
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    CRITICAL = "critical"  # lots remaining, resets very soon
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    NONE = "none"


class FlexibilityClass(str, Enum):
    BURSTABLE = "burstable"  # use all at once
    SEMI_THROTTLED = "semi"  # burst possible but day-capped
    THROTTLED = "throttled"  # strictly rate-limited per refill


WINDOW_NOMINAL_MINUTES = {"5h": 300, "weekly": 10080, "monthly": 43800}


def nominal_window_minutes(kind: str | None) -> int | None:
    return WINDOW_NOMINAL_MINUTES.get(kind) if kind else None


@dataclass
class PaceProfile:
    """Projected consumption pace for a quota window (Phase 2 scoring)."""

    elapsed_fraction: float | None
    used_fraction: float
    pace_ratio: float | None
    projected_used_fraction: float | None
    projected_waste_fraction: float | None
    projected_waste_usd: float | None
    projected_exhaust_at: datetime | None
    governing: bool = True
    gated_by: str | None = None  # label of the enclosing window, set on children
    confidence: str = "measured"  # measured | inferred | low
    # Set when pace blends snapshot history (sample count > 0).
    learned_sample_count: int = 0
    # True when the account has a real or config-confirmed overage/extra-usage
    # wallet available (see AccountUsage.usage_credits and provider_overrides
    # "overage_state"). Qualifies "conserve": lockout risk (hard ceiling) vs.
    # unplanned $ spend risk (soft ceiling, overage available).
    has_overage: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.projected_exhaust_at:
            d["projected_exhaust_at"] = self.projected_exhaust_at.isoformat()
        return d


@dataclass
class FlexibilityProfile:
    """Derived per-window consumption characteristics (not raw data)."""

    flexibility_class: FlexibilityClass
    consumption_flexibility: float  # 0.0–1.0 continuous
    value_at_risk_usd: float | None = None
    cycles_needed: int | None = None
    earliest_start_calendar: datetime | None = None
    effective_burn_minutes: float | None = None
    burn_estimate: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "flexibility_class": self.flexibility_class.value,
            "consumption_flexibility": self.consumption_flexibility,
            "value_at_risk_usd": self.value_at_risk_usd,
            "cycles_needed": self.cycles_needed,
            "effective_burn_minutes": self.effective_burn_minutes,
            "burn_estimate": self.burn_estimate,
        }
        if self.earliest_start_calendar:
            d["earliest_start_calendar"] = self.earliest_start_calendar.isoformat()
        else:
            d["earliest_start_calendar"] = None
        return d


@dataclass
class QuotaWindow:
    """A single rate-limit / credit window (5h, weekly, monthly, ...)."""

    label: str
    used_percent: float | None = None
    remaining_percent: float | None = None
    resets_at: datetime | None = None
    window_minutes: int | None = None
    reset_description: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # Per-refill capacity metadata (populated by collectors when known)
    refill_capacity: float | None = None
    refill_capacity_unit: str | None = None  # "tokens" | "requests" | "usd"
    internal_throttle: bool = False

    window_id: str | None = None

    def remaining(self) -> float | None:
        if self.remaining_percent is not None:
            return self.remaining_percent
        if self.used_percent is not None:
            return max(0.0, 100.0 - self.used_percent)
        return None

    def days_until_reset(self, now: datetime | None = None) -> float | None:
        if not self.resets_at:
            return None
        now = now or utcnow()
        return (self.resets_at - now).total_seconds() / 86400.0

    def reset_time_is_precise(self) -> bool:
        """Whether the source supplied a reset time rather than a date alone."""
        for key in ("resetsAt", "resets_at", "resetAt", "reset_at"):
            value = self.raw.get(key)
            if isinstance(value, str):
                return re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()) is None
        return True

    def same_measurement(self, other: "QuotaWindow") -> bool:
        """Whether two windows look like the same underlying measurement (for dedup)."""
        return (
            self.resets_at == other.resets_at
            and self.used_percent == other.used_percent
            and self.remaining_percent == other.remaining_percent
            and self.window_minutes == other.window_minutes
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.resets_at:
            d["resets_at"] = self.resets_at.isoformat()
        if "raw" in d:
            del d["raw"]
        return d


@dataclass
class UsageCredits:
    """Extra-usage / pay-as-you-go spend against a subscription (e.g. Claude).

    Distinct from prepaid ``balance_usd`` on pure API accounts: this is the
    optional overage wallet that sits *beside* 5h/weekly plan windows.
    """

    used: float | None = None
    limit: float | None = None
    remaining: float | None = None
    currency: str = "USD"
    used_percent: float | None = None
    resets_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "currency": self.currency,
            "used_percent": self.used_percent,
        }
        if self.resets_at is not None:
            d["resets_at"] = self.resets_at.isoformat()
        return d


@dataclass
class AccountUsage:
    """Normalized usage for one provider account."""

    source: str  # cswap | codexbar | caut | openusage_ai | openusage_sh | tokscale
    provider: str
    account: str | None = None
    plan: str | None = None
    billing_kind: BillingKind = BillingKind.UNKNOWN
    windows: list[QuotaWindow] = field(default_factory=list)
    balance_usd: float | None = None
    credits_remaining: float | None = None
    usage_credits: UsageCredits | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    provider_id: str | None = None
    service_id: str | None = None
    collector_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source": self.source,
            "provider": self.provider,
            "account": self.account,
            "plan": self.plan,
            "billing_kind": self.billing_kind.value,
            "windows": [w.to_dict() for w in self.windows],
            "balance_usd": self.balance_usd,
            "credits_remaining": self.credits_remaining,
            "error": self.error,
            "notes": self.notes,
            "provider_id": self.provider_id or self.provider,
            "service_id": self.service_id,
            "collector_id": self.collector_id or self.source,
        }
        if self.usage_credits is not None:
            d["usage_credits"] = self.usage_credits.to_dict()
        return d


@dataclass
class UseOrLoseAlert:
    """Recommendation to burn remaining subscription allotment before reset."""

    urgency: Urgency
    provider: str
    account: str | None
    window_label: str
    remaining_percent: float
    days_until_reset: float | None
    plan: str | None
    message: str
    source: str
    score: float  # higher = more important

    flexibility_profile: FlexibilityProfile | None = None
    window_minutes: int | None = None
    kind: str = "burn"  # burn | conserve | prepaid
    pace: PaceProfile | None = None
    deadline_is_estimated: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "urgency": self.urgency.value,
            "provider": self.provider,
            "account": self.account,
            "window_label": self.window_label,
            "remaining_percent": self.remaining_percent,
            "days_until_reset": self.days_until_reset,
            "plan": self.plan,
            "message": self.message,
            "source": self.source,
            "score": self.score,
            "window_minutes": self.window_minutes,
            "kind": self.kind,
            "deadline_is_estimated": self.deadline_is_estimated,
        }
        if self.flexibility_profile:
            d["consumption_analysis"] = self.flexibility_profile.to_dict()
        if self.pace is not None:
            d["pace"] = self.pace.to_dict()
        return d


@dataclass
class CrossCheck:
    """Comparison of overlapping live measurements from independent tools."""

    provider: str
    account: str | None
    status: str  # consistent | warning | unavailable
    sources: list[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Snapshot:
    """Full collection snapshot."""

    collected_at: datetime
    accounts: list[AccountUsage] = field(default_factory=list)
    cross_checks: list[CrossCheck] = field(default_factory=list)
    collector_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": self.collected_at.isoformat(),
            "accounts": [a.to_dict() for a in self.accounts],
            "cross_checks": [check.to_dict() for check in self.cross_checks],
            "collector_errors": self.collector_errors,
        }
