"""Collect API usage from local Cline task histories."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

from aiuse.models import (
    AccountUsage,
    BillingKind,
    UsageCredits,
)


def collect_cline(*, timeout: float = 45.0) -> list[AccountUsage]:
    """Parse local taskHistory.json files for Cline, across multiple environments."""
    system = platform.system()
    appdata = os.environ.get("APPDATA")
    home = Path.home()

    paths: list[Path] = []

    # 1. VS Code
    if system == "Darwin":
        paths.append(
            home
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "tasks"
            / "taskHistory.json"
        )
    elif system == "Windows" and appdata:
        paths.append(
            Path(appdata) / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "tasks" / "taskHistory.json"
        )
    else:  # Linux / other
        paths.append(
            home
            / ".config"
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "tasks"
            / "taskHistory.json"
        )

    # 2. Cursor
    if system == "Darwin":
        paths.append(
            home
            / "Library"
            / "Application Support"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "tasks"
            / "taskHistory.json"
        )
    elif system == "Windows" and appdata:
        paths.append(
            Path(appdata)
            / "Cursor"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "tasks"
            / "taskHistory.json"
        )
    else:  # Linux / other
        paths.append(
            home
            / ".config"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "tasks"
            / "taskHistory.json"
        )

    # 3. Cline CLI
    paths.append(home / ".cline" / "tasks" / "taskHistory.json")

    costs_by_provider: dict[str, float] = {}
    tokens_in_by_provider: dict[str, int] = {}
    tokens_out_by_provider: dict[str, int] = {}

    for path in paths:
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, list):
            continue

        for task in data:
            if not isinstance(task, dict):
                continue
            provider = task.get("apiProvider")
            if not isinstance(provider, str):
                provider = "unknown"

            cost = task.get("totalCost", 0.0)
            if not isinstance(cost, (int, float)):
                cost = 0.0

            tokens_in = task.get("tokensIn", 0)
            if not isinstance(tokens_in, int):
                tokens_in = 0

            tokens_out = task.get("tokensOut", 0)
            if not isinstance(tokens_out, int):
                tokens_out = 0

            costs_by_provider[provider] = costs_by_provider.get(provider, 0.0) + float(cost)
            tokens_in_by_provider[provider] = tokens_in_by_provider.get(provider, 0) + tokens_in
            tokens_out_by_provider[provider] = tokens_out_by_provider.get(provider, 0) + tokens_out

    # Map cline's apiProvider to aiuse provider IDs
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
        notes = ["Live data fetched from local Cline task history."]
        notes.append(f"tokens_in={tokens_in_by_provider.get(provider_name, 0)}")
        notes.append(f"tokens_out={tokens_out_by_provider.get(provider_name, 0)}")
        accounts.append(
            AccountUsage(
                source="cline",
                provider=mapped,
                account=None,
                billing_kind=BillingKind.PAYG_API,
                usage_credits=UsageCredits(used=cost),
                notes=notes,
            )
        )

    return accounts
