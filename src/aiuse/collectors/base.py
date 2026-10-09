"""Shared subprocess helpers for collectors."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Any


class CollectorError(RuntimeError):
    pass


class CollectorTimeout(CollectorError):
    """A collector subprocess was killed at its timeout (it hung, not failed)."""


# Keep in sync with ai.config.DEFAULT_SUBPROCESS_TIMEOUT (import avoided to
# keep this module free of config load side effects for unit tests).
DEFAULT_RUN_TIMEOUT = 45.0


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def which_all(cmd: str, *, extra_paths: tuple[str, ...] = ()) -> list[str]:
    """Every executable named ``cmd`` on PATH, in PATH order, then ``extra_paths``.

    Duplicates (the same file reached through different symlinks or PATH
    entries) are dropped, keeping the first. Unlike :func:`which` this lets a
    collector look past a same-named binary from an unrelated product that
    happens to sit earlier on PATH.
    """
    seen: set[str] = set()
    found: list[str] = []
    search = [os.path.join(d, cmd) for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    search.extend(os.path.expanduser(p) for p in extra_paths)
    for path in search:
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        found.append(path)
    return found


def first_tool(
    cmd: str,
    accept: Callable[[str], bool],
    *,
    extra_paths: tuple[str, ...] = (),
) -> tuple[str | None, list[str]]:
    """The first ``cmd`` candidate that ``accept`` approves, plus the rejected ones.

    ``accept`` is called with each candidate path in :func:`which_all` order
    until one passes; the rejected candidates are returned for diagnostics.
    """
    rejected: list[str] = []
    for path in which_all(cmd, extra_paths=extra_paths):
        if accept(path):
            return path, rejected
        rejected.append(path)
    return None, rejected


def probe_output(argv: list[str], *, timeout: float = 5.0) -> str:
    """stdout+stderr of a short, detached, non-interactive probe ("" on failure).

    Same isolation as :func:`run_json` (no stdin, new session) so a binary
    that would open a TUI on /dev/tty fails fast instead of taking over the
    user's terminal.
    """
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


def run_json(
    argv: list[str],
    *,
    timeout: float = DEFAULT_RUN_TIMEOUT,
    allow_empty: bool = False,
) -> Any:
    """Run a command and parse JSON from stdout."""
    try:
        # Never inherit the caller's TTY on stdin. Tools like ``caut usage``
        # put stdin into raw mode; on timeout/kill that leaves the shell with
        # echo off until the user runs ``reset``.
        #
        # ``start_new_session`` also detaches the child from the controlling
        # terminal, so a tool that opens /dev/tty directly (a Go/bubbletea TUI
        # such as openusage.sh's ``openusage`` does this when stdin is not a
        # terminal) cannot switch the user's terminal into raw mode at all:
        # its open() fails and it exits instead of hanging until the timeout.
        # Collector children are non-interactive by contract, so nothing
        # legitimate needs the terminal.
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise CollectorError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorTimeout(f"timed out after {timeout}s: {' '.join(argv)}") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if not stdout:
        if allow_empty and proc.returncode == 0:
            return None
        detail = stderr or f"exit {proc.returncode}"
        raise CollectorError(f"no JSON from {' '.join(argv)}: {detail}")

    # Most tools emit clean JSON; try that path first.
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass

    # Some tools print banners before JSON, or more than one JSON value.
    # Prefer the candidate that consumes the most of stdout; if one cleanly
    # consumes to end-of-string, that is the payload (avoids returning a short
    # false positive like `[1]` from "Fetched [1] provider\n[{...}]").
    start_candidates = [i for i, ch in enumerate(stdout) if ch in "{["]
    if not start_candidates:
        raise CollectorError(f"no JSON object or array found in output from {' '.join(argv)}")
    decoder = json.JSONDecoder()
    best_obj: Any = None
    best_consumed = -1
    last_err: Exception | None = None
    for start in start_candidates:
        try:
            obj, end = decoder.raw_decode(stdout[start:])
        except json.JSONDecodeError as err:
            last_err = err
            continue
        if not stdout[start + end :].strip():
            return obj
        if end > best_consumed:
            best_obj, best_consumed = obj, end
    if best_obj is not None:
        return best_obj
    raise CollectorError(f"invalid JSON from {' '.join(argv)}: {last_err or 'parse failed'}") from last_err
