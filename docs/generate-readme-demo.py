#!/usr/bin/env python3
"""Regenerate the README's colored clock-matrix example.

The README's hero example is real ``aiuse`` output (via
``render_clock_matrix``), not a screenshot or hand-typed text — it is built
from a small synthetic ``Snapshot``/alert set (fake accounts, no real quota
data) so it stays privacy-safe and reproducible.  GitHub's README renderer
does not support ANSI color in fenced code blocks, so this script emits a
plain-text table with a leading ``+``/``-``/`` `` diff marker per line
(green for "use it now", red for "already lost it" / fetch errors, unmarked
for everything on pace) and wraps it in a ```diff fence, which GitHub *does*
colorize.

The two legend lines ``render_clock_matrix`` prints (the "% used" header note
and the "dim % = clock inferred" footer) are dropped here: the dim/`+` markers
they explain cannot survive into a plain-text fence, and the README says the
same thing in prose underneath.

Usage: ``uv run python docs/generate-readme-demo.py`` and paste the output
between the `<!-- readme-demo:start -->` / `<!-- readme-demo:end -->` markers
in README.md.

**Keep this in sync with the README by regenerating, not by hand-editing the
fence.**  This script previously still emitted ``render_priority_ladder`` long
after the default display became the matrix, so running it would have silently
reverted the README's hero block to a display the tool no longer prints.
"""

from __future__ import annotations

from datetime import timedelta

from aiuse.config import DEFAULT_CONFIG
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
from aiuse.report import render_clock_matrix

# The real current instant. render_clock_matrix computes "days until
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

# First token of the two legend lines the matrix prints around the rows. They
# describe ANSI affordances (dim cells) that a plain-text fence cannot show.
_LEGEND_LINE_PREFIXES = {"%", "dim"}


def _window(label: str, remaining: float, days: float, minutes: int) -> QuotaWindow:
    """A demo window.

    ``minutes`` is required rather than optional: without a declared duration
    ``infer_window_clock`` falls back to distance-from-reset, which collapses
    every demo row into the WEEK column, and ``_window_value_usd`` returns None
    so the whole ``$ UNUSED`` column renders as em-dashes.
    """
    return QuotaWindow(
        label=label,
        remaining_percent=remaining,
        resets_at=NOW + timedelta(days=days),
        window_minutes=minutes,
    )


_5H, _WEEKLY, _MONTHLY = 300, 10080, 43200


def _demo_snapshot() -> Snapshot:
    return Snapshot(
        collected_at=NOW,
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="opencode-zen",
                account="you@gmail.com",
                error="No available fetch strategy for opencode-zen.",
            ),
            AccountUsage(
                source="codexbar",
                provider="deepseek",
                account="you@gmail.com",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=4.15,
            ),
            AccountUsage(
                source="codexbar",
                provider="openrouter",
                account="you@gmail.com",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=18.55,
            ),
            # A 5h window carved out of a weekly one: the point of the matrix is
            # that both are visible on the same row instead of one "governing"
            # window standing in for the account.
            AccountUsage(
                source="cswap",
                provider="claude",
                account="you@gmail.com",
                windows=[
                    _window("Claude Code 5-hour", 88.0, 0.13, _5H),
                    _window("Claude Code weekly", 23.0, 3.1, _WEEKLY),
                ],
            ),
            AccountUsage(
                source="codexbar",
                provider="codex",
                account="you@gmail.com",
                windows=[_window("Codex weekly quota", 46.0, 2.3, _WEEKLY)],
            ),
            # Cursor bills monthly, so it lands in a column nothing else uses —
            # which is exactly the "why does this one only show a monthly?"
            # question the em-dashes answer.
            AccountUsage(
                source="codexbar",
                provider="cursor",
                account="you@gmail.com",
                windows=[_window("Cursor included", 29.0, 3.3, _MONTHLY)],
            ),
            # Antigravity's Gemini and Claude/GPT pools are independent, so one
            # account legitimately produces two rows — under one name.
            AccountUsage(
                source="codexbar",
                provider="antigravity",
                account="you@gmail.com",
                windows=[
                    _window("Gemini 5-hour", 96.0, 0.17, _5H),
                    _window("Gemini weekly", 84.0, 4.0, _WEEKLY),
                ],
            ),
            # grok reports no duration at all, so it has no dollar figure — a
            # real limitation the demo should show rather than paper over.
            AccountUsage(
                source="tokscale",
                provider="grok",
                account="you@gmail.com",
                windows=[
                    QuotaWindow(
                        label="Grok usage limit",
                        remaining_percent=94.0,
                        resets_at=NOW + timedelta(days=6.6),
                    )
                ],
            ),
        ],
    )


def _demo_alerts() -> list[UseOrLoseAlert]:
    return [
        UseOrLoseAlert(
            urgency=Urgency.CRITICAL,
            provider="opencode-go",
            account="you@gmail.com",
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
            account="you@gmail.com",
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
    text = render_clock_matrix(
        _demo_alerts(),
        snapshot=_demo_snapshot(),
        # Ships the packaged defaults, not the operator's ~/.config/aiuse — the
        # demo must not vary with whoever regenerates it. `plans` is what prices
        # the `$ UNUSED` column; without it that column is entirely em-dashes.
        config=DEFAULT_CONFIG,
        color=False,
        # Wide enough that no column is shed, so the README shows the full table.
        width=132,
    )
    lines = []
    for line in text.splitlines():
        tag = line.split(None, 1)[0] if line.strip() else ""
        if tag in _LEGEND_LINE_PREFIXES:
            continue
        marker = _MARKER_BY_TAG.get(tag, " ")
        lines.append(f"{marker} {line}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("```diff")
    print(render_demo_diff_block())
    print("```")
