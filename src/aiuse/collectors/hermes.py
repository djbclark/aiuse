"""Collect API usage from local Hermes agent sessions."""

import json
from pathlib import Path

from aiuse.models import (
    AccountUsage,
    BillingKind,
    UsageCredits,
    canonical_provider,
)


def collect_hermes(*, timeout: float = 45.0) -> list[AccountUsage]:
    """Parse local latest-usage.json files for Hermes sessions."""
    # timeout is unused but accepted for uniformity with other collectors
    base_dir = Path.home() / ".local" / "state" / "hermes"
    if not base_dir.is_dir():
        return []

    costs_by_provider: dict[str, float] = {}
    tokens_in_by_provider: dict[str, int] = {}
    tokens_out_by_provider: dict[str, int] = {}
    failures_by_provider: dict[str, list[str]] = {}

    for path in base_dir.glob("*/latest-usage.json"):
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        provider = data.get("provider")
        if not isinstance(provider, str) or not provider:
            # Fallback if provider is null but failure reveals it
            failure = str(data.get("failure") or "")
            if "Codex" in failure:
                provider = "openai"  # which maps to codex
            elif "Claude" in failure or "Anthropic" in failure:
                provider = "anthropic"
            elif "Gemini" in failure or "Antigravity" in failure:
                provider = "gemini"
            else:
                provider = "unknown"

        cost = data.get("estimated_cost_usd")
        if not isinstance(cost, (int, float)):
            cost = 0.0

        tokens_in = data.get("input_tokens")
        if not isinstance(tokens_in, int):
            tokens_in = 0

        tokens_out = data.get("output_tokens")
        if not isinstance(tokens_out, int):
            tokens_out = 0

        costs_by_provider[provider] = costs_by_provider.get(provider, 0.0) + float(cost)
        tokens_in_by_provider[provider] = tokens_in_by_provider.get(provider, 0) + tokens_in
        tokens_out_by_provider[provider] = tokens_out_by_provider.get(provider, 0) + tokens_out

        if data.get("failed") and data.get("failure"):
            failures = failures_by_provider.setdefault(provider, [])
            failures.append(str(data["failure"]))

    # Map hermes/cline provider to aiuse provider IDs
    provider_mapping = {
        "anthropic": "claude",
        "openai": "codex",
        "gemini": "antigravity",
        "openrouter": "openrouter",
        "deepseek": "deepseek",
        "grok": "grok",
    }

    accounts: list[AccountUsage] = []

    for provider_name, cost in costs_by_provider.items():
        mapped = provider_mapping.get(provider_name, provider_name)
        mapped = canonical_provider(mapped)

        notes = ["Live data fetched from local Hermes session logs."]

        # Add metrics
        t_in = tokens_in_by_provider.get(provider_name, 0)
        t_out = tokens_out_by_provider.get(provider_name, 0)
        if t_in > 0 or t_out > 0:
            notes.append(f"Tokens: {t_in} in / {t_out} out")

        # Add failures
        error_str = None
        failures = failures_by_provider.get(provider_name, [])
        if failures:
            error_str = failures[-1]  # surface the most recent/any failure
            notes.append(f"Recorded failures: {len(failures)}")

        accounts.append(
            AccountUsage(
                source="hermes",
                provider=mapped,
                account=None,
                billing_kind=BillingKind.PAYG_API,
                usage_credits=UsageCredits(used=cost),
                notes=notes,
                error=error_str,
            )
        )

    # Make sure we emit at least an error row if there were ONLY failures without cost tracking yet
    for provider_name, failures in failures_by_provider.items():
        if provider_name not in costs_by_provider:
            mapped = provider_mapping.get(provider_name, provider_name)
            mapped = canonical_provider(mapped)
            accounts.append(
                AccountUsage(
                    source="hermes",
                    provider=mapped,
                    account=None,
                    billing_kind=BillingKind.PAYG_API,
                    error=failures[-1],
                    notes=["Failed session from local Hermes logs."],
                )
            )

    return accounts
