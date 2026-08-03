"""Shared subprocess helpers for collectors."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any


class CollectorError(RuntimeError):
    pass


# Keep in sync with ai.config.DEFAULT_SUBPROCESS_TIMEOUT (import avoided to
# keep this module free of config load side effects for unit tests).
DEFAULT_RUN_TIMEOUT = 45.0


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


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
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CollectorError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorError(f"timed out after {timeout}s: {' '.join(argv)}") from exc

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
