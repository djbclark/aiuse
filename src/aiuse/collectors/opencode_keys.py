"""OpenCode API keys shared by the Go and Zen collectors.

``oc-go-key use`` switches which key the local OpenCode servers spend. That
key is a bearer token for ``GET /zen/go/v1/usage`` and
``GET /console/api/billing/status``. The console session cookie is a separate
browser login and goes stale when the active key changes.

Keys are read from the process environment or sudo-secretspec. They are never
written to config, snapshots, logs, or error text.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping

from .base import CollectorError

_REASON = "aiuse OpenCode Go and Zen collection"
_KEY_PREFIX = "OPENCODE_GO_API_KEY"
# Vault name -> the label `oc-go-key list` prints for that credential.
_KEY_LABELS = {
    "OPENCODE_GO_API_KEY": "go",
    "OPENCODE_GO_API_KEY_DJBCLARK": "djbclark",
    "OPENCODE_GO_API_KEY_DCLARKUS": "dclark",
    "OPENCODE_GO_API_KEY_TECHNOEUPHORA": "t-e",
}
_VAULT_KEY_NAMES = (
    "OPENCODE_GO_API_KEY_DJBCLARK",
    "OPENCODE_GO_API_KEY_DCLARKUS",
    "OPENCODE_GO_API_KEY_TECHNOEUPHORA",
    "OPENCODE_GO_API_KEY",
)
_SECRETSPEC_TIMEOUT = 5.0
_ACTIVE_LINE = re.compile(r"^\*\s+opencode\s+'([^']+)'")


def resolve_opencode_api_keys(
    env: Mapping[str, str],
    timeout: float,
    *,
    allow_vault: bool,
) -> list[tuple[str, str]] | None:
    """Return ``(account label, api key)`` for every configured key.

    ``None`` when no key is configured, so the caller can fall back to the
    console cookie. The active ``oc-go-key`` credential is marked with a
    leading ``*`` on its label. The key itself must not be logged.
    """
    specs = _keys_from_mapping(env)
    if not specs and allow_vault:
        specs = _keys_from_vault(timeout)
    if not specs:
        return None
    active = _active_credential_label() if allow_vault else None
    return [(display_label(name, active), key) for name, key in specs]


def display_label(secret_name: str, active: str | None) -> str:
    """Board label for one vault secret, starred when it is the active credential."""
    label = _KEY_LABELS.get(secret_name)
    if label is None and secret_name.startswith(f"{_KEY_PREFIX}_"):
        label = secret_name.removeprefix(f"{_KEY_PREFIX}_").lower()
    label = label or secret_name
    if active and label == active:
        return f"*{label}"
    return label


def active_label_from_list_output(text: str) -> str | None:
    """Parse the active Console credential label from ``oc-go-key list``."""
    for line in text.splitlines():
        match = _ACTIVE_LINE.match(line)
        if match:
            return match.group(1)
    return None


def _keys_from_mapping(env: Mapping[str, str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in _VAULT_KEY_NAMES:
        value = str(env.get(name) or "").strip()
        if value:
            found.append((name, value))
            seen.add(name)
    extras = sorted(
        key for key in env if key.startswith(f"{_KEY_PREFIX}_") and key not in seen and str(env.get(key) or "").strip()
    )
    for name in extras:
        found.append((name, str(env[name]).strip()))
    return found


def _keys_from_vault(timeout: float) -> list[tuple[str, str]]:
    executable = shutil.which("sudo-secretspec")
    if executable is None:
        return []
    found: list[tuple[str, str]] = []
    for name in _VAULT_KEY_NAMES:
        key = _vault_get(executable, name, timeout)
        if key:
            found.append((name, key))
    return found


def _vault_get(executable: str, name: str, timeout: float) -> str | None:
    try:
        result = subprocess.run(
            [executable, "get", name, "--reason", _REASON],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=min(max(timeout, 0.1), _SECRETSPEC_TIMEOUT),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CollectorError(f"sudo-secretspec timed out reading {name}") from exc
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    key = result.stdout.strip()
    if not key.startswith("oc_"):
        return None
    return key


def _active_credential_label() -> str | None:
    executable = shutil.which("oc-go-key")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "list"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return active_label_from_list_output(result.stdout)
