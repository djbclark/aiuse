"""Styled static pretty display for `aiuse` (Rich; Textual stack)."""

from __future__ import annotations

import os
import sys
from typing import Any

from aiuse.models import Snapshot, UseOrLoseAlert


def textual_available() -> bool:
    """True when Rich (via Textual stack) can style the pretty report."""
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


def should_use_tui(
    *,
    as_json: bool = False,
    alerts_only: bool = False,
    no_tui: bool = False,
    stream: Any = None,
) -> bool:
    """Whether the pretty path should use the styled Rich report.

    Mirrors Rich's own ``Console.is_terminal`` env-var precedence
    (``TTY_COMPATIBLE`` then ``FORCE_COLOR``, see https://force-color.org/)
    so that forcing color for a piped/captured run (recording a demo,
    redirecting to a file, CI logs) also opts into the styled report
    instead of silently falling back to the classic plain-text renderer.
    """
    if as_json or alerts_only or no_tui:
        return False
    if not textual_available():
        return False
    tty_compatible = os.environ.get("TTY_COMPATIBLE", "")
    if tty_compatible == "0":
        return False
    if tty_compatible == "1":
        return True
    if os.environ.get("FORCE_COLOR"):
        return True
    out = stream if stream is not None else sys.stdout
    return bool(getattr(out, "isatty", lambda: False)())


def run_inline_report(
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
    from aiuse.tui.app import run_usage_app

    run_usage_app(
        snapshot,
        alerts,
        config=config,
        full=full,
        brief=brief,
        traditional_summary=traditional_summary,
        quiet=quiet,
        color=color,
    )
