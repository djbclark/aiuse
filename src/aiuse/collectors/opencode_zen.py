"""Collect the OpenCode Zen prepaid balance directly from OpenCode billing.

This is intentionally separate from CodexBar: it provides a second client
implementation of the same server-authoritative billing source.  OpenCode does
not expose this balance through its API key, so the collector first asks the
project's SecretSpec manifest for ``OPENCODE_ZEN_COOKIE``. An explicit
``AIUSE_OPENCODE_ZEN_COOKIE`` environment variable overrides that lookup. The
value is never written to config, snapshots, logs, or error messages.

OpenCode replaced its server-rendered console with a single-page app in
September 2026: the old ``/_server?id=<build-hash>`` server functions now
redirect to ``/console/login``. Both OpenCode collectors therefore speak the
console's JSON API under ``/console/api``, selecting a workspace with the
``x-org-id`` header. Authentication is the browser session cookie
(``__Host-console_session``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any

import requests

from aiuse.models import AccountUsage, BillingKind
from aiuse.secretspec import resolve_manifest_path

from .base import CollectorError

_BASE_URL = "https://opencode.ai"
_CONSOLE_URL = f"{_BASE_URL}/console"
_CONSOLE_API = f"{_CONSOLE_URL}/api"
_ORGS_PATH = "/orgs"
_BILLING_STATUS_PATH = "/billing/status"
_ORG_HEADER = "x-org-id"
_SESSION_COOKIE = "__Host-console_session"
# OpenCode reports money in micro-cents: 100_000_000 micro-cents == 1 USD.
_BALANCE_SCALE = 100_000_000.0
_COOKIE_ENV = "AIUSE_OPENCODE_ZEN_COOKIE"
_COOKIE_SECRET = "OPENCODE_ZEN_COOKIE"
_WORKSPACE_ENV = "AIUSE_OPENCODE_ZEN_WORKSPACE_ID"
_USER_AGENT = "aiuse OpenCode Zen collector"
_SECRETSPEC_TIMEOUT = 5.0
_SIGNED_OUT_HINT = (
    "OpenCode console session is not signed in; open https://opencode.ai/console/ "
    "in Chrome, then run `aiuse credential refresh opencode-zen --from chrome`"
)


def collect_opencode_zen(
    *,
    timeout: float = 45.0,
    environ: Mapping[str, str] | None = None,
) -> list[AccountUsage]:
    """Return the native Zen source, or nothing until a session cookie is supplied."""
    env = os.environ if environ is None else environ
    cookie = _resolve_cookie(env, timeout, allow_secretspec=environ is None)
    if not cookie:
        return []
    override = _workspace_id(str(env.get(_WORKSPACE_ENV) or ""))
    workspaces = [override] if override else list_workspaces(cookie, timeout, label="OpenCode Zen billing")
    if not workspaces:
        raise CollectorError(f"OpenCode Zen billing: no workspace for this session ({_SIGNED_OUT_HINT})")

    errors: list[str] = []
    for workspace in workspaces:
        try:
            status = _fetch_console(
                _BILLING_STATUS_PATH,
                cookie,
                timeout,
                org=workspace,
                label="OpenCode Zen billing",
            )
        except CollectorError as exc:
            errors.append(str(exc))
            continue
        balance = _balance_usd(status)
        if balance is None:
            continue
        return [
            AccountUsage(
                source="opencode_zen",
                provider="opencode-zen",
                billing_kind=BillingKind.PREPAID_BALANCE,
                balance_usd=balance,
                notes=["Live data fetched directly from OpenCode Zen billing."],
                raw=_billing_raw(status),
            )
        ]
    if errors:
        raise CollectorError(errors[0])
    raise CollectorError("OpenCode Zen billing: authenticated response did not include a balance")


def _resolve_cookie(env: Mapping[str, str], timeout: float, *, allow_secretspec: bool = True) -> str | None:
    """Return an explicit cookie or a SecretSpec value without exposing either."""
    explicit = str(env.get(_COOKIE_ENV) or "").strip()
    if explicit:
        return explicit
    if not allow_secretspec:
        return None
    executable = shutil.which("secretspec")
    if executable is None:
        return None
    manifest = str(resolve_manifest_path(env))
    try:
        result = subprocess.run(
            [
                executable,
                "get",
                "--file",
                manifest,
                "--reason",
                "aiuse OpenCode Zen balance collection",
                _COOKIE_SECRET,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=min(max(timeout, 0.1), _SECRETSPEC_TIMEOUT),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    cookie = result.stdout.strip()
    return cookie or None


def _fetch_console(
    path: str,
    cookie: str,
    timeout: float,
    *,
    org: str | None = None,
    label: str = "OpenCode console",
    allow_missing: bool = False,
) -> Any:
    """GET one ``/console/api`` route and return its parsed JSON body.

    ``allow_missing`` turns a 404 into ``None`` so callers can treat an absent
    resource (such as a workspace without a Go subscription) as data.
    """
    headers = {
        "Accept": "application/json",
        "Cookie": cookie,
        "Origin": _BASE_URL,
        "Referer": f"{_CONSOLE_URL}/",
        "User-Agent": _USER_AGENT,
    }
    if org:
        headers[_ORG_HEADER] = org
    try:
        response = requests.get(f"{_CONSOLE_API}{path}", timeout=timeout, headers=headers)
        if allow_missing and response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise CollectorError(f"{label}: {_SIGNED_OUT_HINT}")
        response.raise_for_status()
        body = response.text
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"{label} returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise CollectorError(f"{label} request failed: {exc.__class__.__name__}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise CollectorError(f"{label}: response was not JSON ({_SIGNED_OUT_HINT})") from exc


def list_workspaces(cookie: str, timeout: float, *, label: str = "OpenCode console") -> list[str]:
    """Return every workspace (org) id this console session can act for."""
    payload = _fetch_console(_ORGS_PATH, cookie, timeout, label=label)
    found: list[str] = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict):
            continue
        workspace = _workspace_id(str(entry.get("id") or ""))
        if workspace and workspace not in found:
            found.append(workspace)
    return found


def _workspace_id(value: str) -> str | None:
    # OpenCode uses ``work_``; accept the earlier ``work_`` spelling as a
    # compatibility fallback for serialized historical responses.
    match = re.search(r"\bw(?:rk|ork)_[A-Za-z0-9]+\b", value)
    return match.group(0) if match else None


def micro_cents_to_usd(value: Any) -> float | None:
    """Convert OpenCode's string/number micro-cents into dollars."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / _BALANCE_SCALE
    if isinstance(value, str):
        try:
            return float(value.strip()) / _BALANCE_SCALE
        except ValueError:
            return None
    return None


def _balance_usd(status: Any) -> float | None:
    if not isinstance(status, dict):
        return None
    return micro_cents_to_usd(status.get("balanceMicroCents"))


def _billing_raw(status: Any) -> dict[str, Any]:
    """Keep only non-identifying billing shape; never retain session material."""
    if not isinstance(status, dict):
        return {}
    raw: dict[str, Any] = {}
    for key in ("billingMode", "mode"):
        value = status.get(key)
        if isinstance(value, str):
            raw[key] = value
    available = micro_cents_to_usd(status.get("availableMicroCents"))
    if available is not None:
        raw["available_usd"] = available
    return raw
