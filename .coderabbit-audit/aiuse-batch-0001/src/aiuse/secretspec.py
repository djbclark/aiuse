"""Paths and declarations for aiuse-managed SecretSpec values.

The manifest is user configuration, not package data: an installed CLI must
not depend on a source checkout being present.  Its companion ``.env`` is
managed by SecretSpec and remains outside version control.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from aiuse.config import default_config_dir

_MANIFEST_NAME = "secretspec.toml"
_MANIFEST_CONTENT = """[project]
name = \"aiuse\"
revision = \"1.0\"

# Declarations only — SecretSpec stores values in its configured provider.
# https://secretspec.dev

[profiles.default]
OPENCODE_ZEN_COOKIE = { description = \"OpenCode console-session Cookie header for direct Zen balance collection; the Zen API key alone cannot read this balance.\", required = false }
"""


def default_manifest_path() -> Path:
    """Return the standard per-user manifest path for installed aiuse."""
    return default_config_dir() / _MANIFEST_NAME


def resolve_manifest_path(environ: Mapping[str, str] | None = None) -> Path:
    """Respect an explicit path, then prefer user config over a source checkout."""
    env = os.environ if environ is None else environ
    if explicit := str(env.get("SECRETSPEC_FILE") or "").strip():
        return Path(explicit).expanduser()
    user_path = default_manifest_path()
    if user_path.is_file():
        return user_path
    # Keeps existing source-checkout users working while directing new installs
    # to the durable XDG location above. This path does not exist in a wheel.
    source_path = Path(__file__).resolve().parents[2] / _MANIFEST_NAME
    return source_path if source_path.is_file() else user_path


def ensure_manifest(path: Path) -> None:
    """Create an aiuse declaration manifest once, without replacing anything."""
    if path.exists():
        if not path.is_file():
            raise OSError(f"SecretSpec manifest path is not a file: {path}")
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(_MANIFEST_CONTENT)
    except FileExistsError:
        return
