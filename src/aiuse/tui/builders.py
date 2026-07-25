"""Report sections for the styled pretty display (ANSI-colored, plan-glance last)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiuse.analysis.history import (
    compute_learned_burn_rates,
    history_section_lines,
    should_learn_from_history,
)
from aiuse.models import Snapshot, Urgency, UseOrLoseAlert, provider_display_name
from aiuse.report import (
    ACTION_PLAN_MAX_LINES,
    ACTION_PLAN_WIDTH,
    URGENCY_ICON,
    _capacity_summary_line,
    _human_deadline,
    _physical_line_count,
    _render_account,
    _render_action_plan,
    _render_brief_action_plan,
    _render_cross_checks,
    _sorted_accounts,
    _Style,
    _tips_lines,
)


@dataclass(frozen=True)
class ReportSection:
    """One titled block of lines (may include ANSI) for display."""

    title: str
    lines: list[str] = field(default_factory=list)
    kind: str = "body"  # header | providers | plan | plan-glance | errors | meta | tips | footer
    # Title may also carry ANSI when set via Style.
    title_ansi: str | None = None


def build_report_sections(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    *,
    config: dict[str, Any] | None = None,
    full: bool = False,
    brief: bool = False,
    traditional_summary: bool = False,
    glance_width: int | None = None,
) -> list[ReportSection]:
    """Build sections for the styled report.

    Default is glance-first; ``full=True`` is the long report. ``brief`` is an
    alias of default (ignored when ``full=True``). Glance is always last.
    """
    del brief, traditional_summary
    s = _Style(True)
    sections: list[ReportSection] = []
    accounts = _sorted_accounts(snapshot.accounts)
    n_accounts = len(accounts)
    n_actionable = sum(1 for a in alerts if a.urgency not in (Urgency.INFO, Urgency.NONE))
    width = glance_width if glance_width is not None else ACTION_PLAN_WIDTH

    meta = f"Collected at {snapshot.collected_at.isoformat()}"
    meta += f" · {n_accounts} account{'s' if n_accounts != 1 else ''}"
    if n_actionable:
        meta += f" · {n_actionable} alert{'s' if n_actionable != 1 else ''}"
    else:
        meta += " · no burn/conserve alerts"
    analysis_cfg = (config or {}).get("analysis") or {}
    if not isinstance(analysis_cfg, dict):
        analysis_cfg = {}
    hist_lines = [s.dim(meta)]
    title = "AI USAGE — USE IT OR LOSE IT"
    if full:
        title += " (full)"
    sections.append(
        ReportSection(
            title=title,
            title_ansi=s.bold(s.cyan(title)),
            lines=hist_lines,
            kind="header",
        )
    )

    if snapshot.collector_errors:
        sections.append(
            ReportSection(
                title="Collector errors",
                title_ansi=s.bold(s.red("Collector errors")),
                lines=[s.red(f"- {err}") for err in snapshot.collector_errors],
                kind="errors",
            )
        )

    waking_hours = float(analysis_cfg.get("waking_hours_per_day", 16))
    learned_burn_rates: dict[str, tuple[float, int]] = {}
    if full and should_learn_from_history(analysis_cfg):
        try:
            retention = int(analysis_cfg.get("snapshot_retention_days") or 90)
        except (TypeError, ValueError):
            retention = 90
        learned_burn_rates = compute_learned_burn_rates(
            current=snapshot, retention_days=retention
        )

    if full:
        sections.append(
            ReportSection(
                title="History",
                title_ansi=s.bold(s.cyan("History")),
                lines=[s.dim(line) for line in history_section_lines(snapshot, analysis_cfg=analysis_cfg)],
                kind="meta",
            )
        )

        provider_lines: list[str] = []
        if accounts:
            for acc in accounts:
                provider_lines.extend(
                    _render_account(
                        acc, s, config=config, learned_burn_rates=learned_burn_rates
                    )
                )
        else:
            provider_lines.append(s.dim("(no provider data collected)"))
        sections.append(
            ReportSection(
                title="Per-provider usage",
                title_ansi=s.bold(s.cyan("Per-provider usage")),
                lines=provider_lines,
                kind="providers",
            )
        )

        cross_lines = [
            s.dim(
                "Tools poll at different times; multi-account Claude is cswap-only. "
                "Gaps rarely mean both tools are wrong."
            )
        ]
        if snapshot.cross_checks:
            cross_lines.extend(_render_cross_checks(snapshot.cross_checks, s))
        else:
            cross_lines.append(s.dim("(no overlapping live measurements were available)"))
        sections.append(
            ReportSection(
                title="Cross-checks (informational)",
                title_ansi=s.bold(s.magenta("Cross-checks (informational)")),
                lines=cross_lines,
                kind="meta",
            )
        )
        sections.append(
            ReportSection(
                title="Tips",
                title_ansi=s.bold(s.cyan("Tips")),
                lines=list(_tips_lines(s)),
                kind="tips",
            )
        )
        detailed = _render_action_plan(
            alerts, s, width=width, waking_hours_per_day=waking_hours
        )
        detailed_lines = [line for line in detailed if line is not None]
        # Match classic full report: single plan when it fits; else detailed + glance.
        header_rows = 2  # title + rule in classic path
        if _physical_line_count(detailed_lines) + header_rows <= ACTION_PLAN_MAX_LINES:
            sections.append(
                ReportSection(
                    title="Action plan — use these before they reset",
                    title_ansi=s.bold(s.yellow("Action plan — use these before they reset")),
                    lines=detailed_lines,
                    kind="plan",
                )
            )
            return sections
        sections.append(
            ReportSection(
                title="Action plan (detailed)",
                title_ansi=s.bold(s.yellow("Action plan (detailed)")),
                lines=detailed_lines,
                kind="plan",
            )
        )

    else:
        capacity = _capacity_summary_line(alerts, s)
        if capacity:
            sections.append(
                ReportSection(
                    title="Capacity",
                    title_ansi=s.dim("Capacity"),
                    lines=[capacity],
                    kind="meta",
                )
            )

    glance = _render_brief_action_plan(
        alerts, s, width=width, max_lines=ACTION_PLAN_MAX_LINES - 2
    )
    sections.append(
        ReportSection(
            title="Action plan — at a glance",
            title_ansi=s.bold(s.yellow("Action plan — at a glance")),
            lines=[line for line in glance if line is not None],
            kind="plan-glance",
        )
    )
    if not full:
        sections.append(
            ReportSection(
                title="",
                lines=[s.dim("Detail: ai --full")],
                kind="footer",
            )
        )
    return sections


def alert_headline(alert: UseOrLoseAlert) -> str:
    """One-line headline for an alert (plain text)."""
    icon = URGENCY_ICON.get(alert.urgency, "   ").strip() or "·"
    who = alert.account or "default"
    if alert.kind == "prepaid":
        return (
            f"{icon} {provider_display_name(alert.provider)} · {who} · "
            f"{alert.window_label} (no expiry)"
        )
    when = _human_deadline(alert.days_until_reset)
    verb = "pace" if alert.kind == "conserve" else "use"
    return (
        f"{icon} {provider_display_name(alert.provider)} · {who} · "
        f"{alert.window_label}: {alert.remaining_percent:.0f}% left · {verb} {when}"
    )
