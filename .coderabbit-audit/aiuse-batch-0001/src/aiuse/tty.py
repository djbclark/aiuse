"""Terminal attribute save/restore helpers.

Some external CLIs (notably ``caut usage``) put the inherited stdin TTY into
raw/cbreak mode. If they are killed mid-run (collector timeout) or fail to
restore, the parent shell loses echo until the user runs ``reset``. Collectors
should pass ``stdin=DEVNULL``; this module is belt-and-suspenders for any path
that still inherits a TTY.
"""

from __future__ import annotations

import sys
from typing import Any


def save_stdin_tty() -> Any | None:
    """Snapshot stdin termios attrs when stdin is a TTY; else None."""
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return None
    try:
        import termios
    except ImportError:
        return None
    try:
        return termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError, ValueError):
        return None


def restore_stdin_tty(saved: Any | None) -> None:
    """Restore stdin termios attrs previously returned by ``save_stdin_tty``."""
    if saved is None:
        return
    try:
        import termios
    except ImportError:
        return
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
    except (termios.error, OSError, ValueError):
        return
