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


def reexec_target(
    module_file: str | Path = __file__,
    argv0: str | None = None,
    prefixes: tuple[Path, ...] | None = None,
) -> Path | None:
    """Return the binary a long-lived process should re-exec into after an upgrade.

    When ``brew upgrade aiuse`` (or ``pipx upgrade``) replaces the running
    install while ``aiuse watch`` is open, the old Cellar/venv directory is
    unlinked or ``/opt/homebrew/opt/aiuse`` points at a newer Cellar version.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("AIUSE_SKIP_HOMEBREW_FORMULA"):
        return None
    mod_path = Path(module_file)
    missing = not mod_path.exists()
    cmd = argv0 if argv0 is not None else (sys.argv[0] if sys.argv else "aiuse")
    target = formula_command(cmd, prefixes)
    if target is None and missing:
        target = formula_command("aiuse", prefixes)
    if target is not None:
        if missing:
            return target
        if "Cellar" in mod_path.parts:
            try:
                mod_path.resolve().relative_to(target.resolve().parent.parent)
            except (ValueError, OSError):
                return target
        return None
    if missing and cmd:
        candidate = Path(cmd)
        if os.access(candidate, os.X_OK):
            return candidate
    return None
