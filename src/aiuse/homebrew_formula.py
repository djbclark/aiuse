"""Prefer the Homebrew formula once that install can collect caam.

pipx (and an editable checkout) include ``aiuse.collectors.caam`` as soon as
this tree does. The Homebrew formula lags until a release. While the formula
lacks that module, this process is the install that has the collector. When
the formula gains it, the pipx entrypoint replaces itself with the formula
binary so PATH exercises the formula.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Stable opt prefixes. Do not call ``brew``; a Homebrew lock must not stall
# the sampler. ``/opt/aiuse`` is a symlink into the Cellar, and Path.resolve
# follows it.
FORMULA_PREFIXES: tuple[Path, ...] = (
    Path("/opt/homebrew/opt/aiuse"),
    Path("/usr/local/opt/aiuse"),
)
_COMMANDS = frozenset({"aiuse", "ai"})
_CAAM_REL = "libexec/lib/python*/site-packages/aiuse/collectors/caam.py"


def formula_command(argv0: str, prefixes: tuple[Path, ...] | None = None) -> Path | None:
    """Formula binary for this command, if that install contains the caam collector."""
    if prefixes is None:
        prefixes = FORMULA_PREFIXES
    name = Path(argv0).name
    if name not in _COMMANDS:
        return None
    for prefix in prefixes:
        binary = prefix / "bin" / name
        if not os.access(binary, os.X_OK):
            continue
        if any(prefix.glob(_CAAM_REL)):
            return binary
    return None


def formula_status_line(prefixes: tuple[Path, ...] | None = None) -> str:
    """One doctor line. Does not exec."""
    if prefixes is None:
        prefixes = FORMULA_PREFIXES
    for prefix in prefixes:
        if (prefix / "bin" / "aiuse").is_file():
            if any(prefix.glob(_CAAM_REL)):
                return f"Homebrew formula: {prefix} (includes the caam collector)"
            return f"Homebrew formula: {prefix} (no caam collector; this install is used)"
    return "Homebrew formula: not installed"


def prefer_homebrew_formula() -> None:
    """Replace this process with the formula binary when that install has caam.

    No-op under pytest, when ``AIUSE_SKIP_HOMEBREW_FORMULA`` is set, when this
    process already is the formula, or when the formula has no caam collector.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("AIUSE_SKIP_HOMEBREW_FORMULA"):
        return
    argv0 = sys.argv[0] if sys.argv else ""
    target = formula_command(argv0)
    if target is None:
        return
    try:
        if Path(argv0).resolve() == target.resolve():
            return
    except OSError:
        pass
    package_file = Path(__file__).resolve()
    for prefix in FORMULA_PREFIXES:
        try:
            package_file.relative_to(prefix.resolve())
            return
        except (ValueError, OSError):
            continue
    os.execv(os.fspath(target), [os.fspath(target), *sys.argv[1:]])
