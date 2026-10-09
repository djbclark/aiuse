"""Terminal attribute save/restore helpers.

Some external CLIs put the terminal into raw/cbreak mode: ``caut usage`` on an
inherited stdin, and openusage.sh's ``openusage`` on /dev/tty directly when it
is started without a subcommand. If they are killed mid-run (collector
timeout) or fail to restore, the terminal is left without echo and without
output post-processing (every line of the report then starts where the
previous one ended) until the shell or ``reset`` fixes it. Collectors pass
``stdin=DEVNULL`` and run in a new session (see ``run_json``); this module is
belt-and-suspenders for any path that still reaches the terminal.

The snapshot covers whichever of stdin, stdout and stderr is the terminal (all
three are normally the same device), so ``aiuse </dev/null`` is protected too.
"""

from __future__ import annotations

import os
import sys
from typing import Any


def _terminal_fd() -> int | None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream is not None and stream.isatty():
                return stream.fileno()
        except (AttributeError, OSError, ValueError):
            continue
    return None


def save_stdin_tty() -> Any | None:
    """Snapshot termios attrs of the terminal (stdin, else stdout, else stderr).

    Returns an opaque token for ``restore_stdin_tty``; None when there is no
    terminal.
    """
    fd = _terminal_fd()
    if fd is None:
        return None
    try:
        import termios
    except ImportError:
        return None
    try:
        return (fd, termios.tcgetattr(fd))
    except (termios.error, OSError, ValueError):
        return None


def _owns_terminal(fd: int) -> bool:
    """True when this process may write terminal attributes without SIGTTOU.

    ``tcsetattr`` from a process that is not in the terminal's foreground
    process group raises SIGTTOU, which *stops* the process (``aiuse &``,
    ``brew test``, any job-control runner): a hang, not an error. Such a
    process has no business resetting someone else's terminal anyway; the
    foreground shell restores it at its next prompt.
    """
    try:
        return os.tcgetpgrp(fd) == os.getpgrp()
    except (OSError, AttributeError):
        return False


def restore_stdin_tty(saved: Any | None) -> None:
    """Restore termios attrs previously returned by ``save_stdin_tty``.

    A no-op when the attributes are unchanged (the common case) and when this
    process is not the terminal's foreground job (see ``_owns_terminal``).
    """
    if saved is None:
        return
    try:
        import termios
    except ImportError:
        return
    try:
        fd, attrs = saved
        fd = int(fd)
        if termios.tcgetattr(fd) == attrs:
            return
        if not _owns_terminal(fd):
            return
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except (termios.error, OSError, ValueError, TypeError):
        return
