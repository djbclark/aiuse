#!/usr/bin/env python3
"""Regenerate the README's colored priority-ladder example.

The README's hero example is real ``aiuse`` output (via
``render_priority_ladder``), not a screenshot or hand-typed text — it is
built from a small synthetic ``Snapshot``/alert set (fake accounts, no real
quota data) so it stays privacy-safe and reproducible.  GitHub's README
renderer does not support ANSI color in fenced code blocks, so this script
emits a plain-text ladder with a leading ``+``/``-``/`` `` diff marker per
line (green for "use it now", red for "already lost it" / fetch errors,
unmarked for everything on pace) and wraps it in a ```diff fence, which
GitHub *does* colorize.

Usage: ``.venv/bin/python docs/generate-readme-demo.py`` and paste the
output between the `<!-- readme-demo:start -->` / `<!-- readme-demo:end -->`
markers in README.md.
"""

from __future__ import annotations

from datetime import timedelta

from aiuse.models import (
    AccountUsage,
    BillingKind,
    PaceProfile,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.report import render_priority_ladder

# The real current instant. render_priority_ladder computes "days until
# reset" (and lockout forecasts) for *unalerted* windows via
# QuotaWindow.days_until_reset(), which always measures against the real
# wall clock (it has no injectable "now"). A fixed historical constant here
# would make every reset look like it is in the past ("reset imminent or
# past") once enough real time has elapsed — use the live clock instead so
# the demo always reads correctly, at the cost of exact byte-reproducibility
# run to run.
NOW = utcnow()

# Band → diff marker: "+" (green) highlights capacity to burn now — the
# tool's core promise. "-" (red) flags capacity already lost and fetch
# errors. Everything else (n/a, slow, mid) is left unmarked/neutral.
_MARKER_BY_TAG = {
    "use": "+",
    "empty": "-",
    "error": "-",
}


def _window(label: str, remaining: float, days: float) -> QuotaWindow:
    return QuotaWindow(label=label, remaining_percent=remaining, resets_at=NOW + timedelta(days=days))


def _demo_snapshot() -> Snapshot:
    return Snapshot(
        collected_at=NOW,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="groq",
                account="you@example.com",
                error="No available fetch strategy for groq.",
            ),
            AccountUsage(
                source="codexbar",
                provider="deepseek",
                account="you@example.com",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=4.15,
            ),
            AccountUsage(
                source="codexbar",
                provider="openrouter",
                account="you@example.com",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=18.55,
            ),
            AccountUsage(
                source="cswap",
                provider="claude",
                account="you@example.com",
                windows=[_window("Claude Code weekly", 23.0, 3.1)],
            ),
            AccountUsage(
                source="codexbar",
                provider="codex",
                account="you@example.com",
                windows=[_window("Codex weekly quota", 46.0, 2.3)],
            ),
            AccountUsage(
                source="codexbar",
                provider="cursor",
                account="you@example.com",
                windows=[_window("Cursor included", 29.0, 3.3)],
            ),
            AccountUsage(
                source="codexbar",
                provider="gemini",
                account="you@example.com",
                windows=[_window("Gemini weekly", 84.0, 4.0)],
            ),
            AccountUsage(
                source="tokscale",
                provider="grok",
                account="you@example.com",
                windows=[_window("Grok usage limit", 94.0, 6.6)],
            ),
        ],
    )


def _demo_alerts() -> list[UseOrLoseAlert]:
    return [
        UseOrLoseAlert(
            urgency=Urgency.CRITICAL,
            provider="opencode",
            account="you@example.com",
            window_label="OpenCode Go weekly quota",
            remaining_percent=0.0,
            days_until_reset=4.5,
            plan=None,
            message="conserve",
            source="codexbar",
            score=95.0,
            kind="conserve",
            pace=PaceProfile(
                elapsed_fraction=0.3,
                used_fraction=1.0,
                pace_ratio=3.3,
                projected_used_fraction=1.0,
                projected_waste_fraction=None,
                projected_waste_usd=None,
                projected_exhaust_at=NOW - timedelta(hours=6),
            ),
        ),
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="antigravity",
            account="you@example.com",
            window_label="Claude/GPT weekly",
            remaining_percent=22.0,
            days_until_reset=1.2,
            plan=None,
            message="conserve",
            source="codexbar",
            score=70.0,
            kind="conserve",
            pace=PaceProfile(
                elapsed_fraction=0.85,
                used_fraction=0.78,
                pace_ratio=1.2,
                projected_used_fraction=1.0,
                projected_waste_fraction=None,
                projected_waste_usd=None,
                projected_exhaust_at=NOW + timedelta(hours=20),
            ),
        ),
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="copilot",
            account="default",
            window_label="GitHub Copilot premium requests",
            remaining_percent=42.0,
            days_until_reset=1.4,
            plan="pro",
            message="burn",
            source="tokscale",
            score=88.0,
            kind="burn",
            deadline_is_estimated=True,
            pace=PaceProfile(
                elapsed_fraction=0.55,
                used_fraction=0.58,
                pace_ratio=0.95,
                projected_used_fraction=0.58,
                projected_waste_fraction=0.42,
                projected_waste_usd=None,
                projected_exhaust_at=None,
            ),
        ),
    ]


def render_demo_diff_block() -> str:
    text = render_priority_ladder(_demo_alerts(), snapshot=_demo_snapshot(), color=False, width=132)
    lines = []
    for line in text.splitlines():
        tag = line.split(None, 1)[0] if line.strip() else ""
        marker = _MARKER_BY_TAG.get(tag, " ")
        lines.append(f"{marker} {line}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("```diff")
    print(render_demo_diff_block())
    print("```")
