"""Styled static pretty report via Rich (full scrollback; no Textual/Layout)."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from aiuse.models import Snapshot, UseOrLoseAlert
from aiuse.report import render_priority_ladder, render_stderr_meta
from aiuse.tui.builders import ReportSection, build_report_sections

# Panel chrome (borders + padding) eats columns from glance clamp width.
_PANEL_CHROME = 4


def _as_text(value: str) -> str | Text:
    if "\033" in value:
        return Text.from_ansi(value)
    return value


def _body_group(lines: list[str]) -> Group:
    parts: list[Any] = []
    for line in lines:
        if not line:
            parts.append(Text(""))
            continue
        parts.append(_as_text(line))
    return Group(*parts) if parts else Group(Text(""))


def _print_section(console: Console, section: ReportSection) -> None:
    if section.kind == "footer":
        for line in section.lines:
            console.print(_as_text(line) if isinstance(line, str) else line)
        console.print()
        return

    title = _as_text(section.title_ansi or section.title)
    body = _body_group(section.lines)

    if section.kind == "header":
        console.print(title)
        console.print(body)
        console.print()
        return

    if section.kind == "plan-glance":
        console.print(
            Panel(
                body,
                title=title,
                border_style="yellow",
                padding=(0, 1),
            )
        )
        console.print()
        return

    if section.kind == "meta" and section.title == "Capacity":
        console.print(body)
        console.print()
        return

    console.print(Rule(title, style="dim"))
    console.print(body)
    console.print()


def run_usage_app(
    snapshot: Snapshot,
    alerts: list[UseOrLoseAlert],
    *,
    config: dict[str, Any] | None = None,
    full: bool = False,
    brief: bool = False,
    traditional_summary: bool = False,
    quiet: bool = False,
    color: bool | None = None,
) -> None:
    """Print the styled report (default: priority ladder on stdout, meta on stderr)."""
    if not full:
        out = Console(highlight=False, soft_wrap=True, emoji=False)
        width = max(40, out.width)
        if not quiet:
            err = Console(file=sys.stderr, highlight=False, soft_wrap=True, emoji=False)
            meta = render_stderr_meta(snapshot, alerts, color=color)
            for line in meta.splitlines():
                err.print(_as_text(line))
        ladder = render_priority_ladder(
            alerts,
            snapshot=snapshot,
            color=color if color is not None else None,
            width=width,
        )
        for line in ladder.splitlines():
            out.print(_as_text(line) if line else "")
        return

    console = Console(highlight=False, soft_wrap=True, emoji=False)
    glance_width = max(40, console.width - _PANEL_CHROME)
    sections = build_report_sections(
        snapshot,
        alerts,
        config=config,
        full=True,
        brief=brief,
        traditional_summary=traditional_summary,
        glance_width=glance_width,
    )
    for section in sections:
        _print_section(console, section)
