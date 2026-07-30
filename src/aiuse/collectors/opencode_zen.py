"""Collect the OpenCode Zen prepaid balance directly from OpenCode billing.

This is intentionally separate from CodexBar: it provides a second client
implementation of the same server-authoritative billing source.  OpenCode does
not expose this balance through its API key, so the collector first asks the
project's SecretSpec manifest for ``OPENCODE_ZEN_COOKIE``. An explicit
``AIUSE_OPENCODE_ZEN_COOKIE`` environment variable overrides that lookup. The
value is never written to config, snapshots, logs, or error messages.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests

from aiuse.models import AccountUsage, BillingKind

from .base import CollectorError

_BASE_URL = "https://opencode.ai"
_SERVER_URL = f"{_BASE_URL}/_server"
_WORKSPACES_SERVER_ID = "def39973159c7f0483d8793a822b8dbb10d067e12c65455fcb4608459ba0234f"
_BILLING_SERVER_ID = "c83b78a614689c38ebee981f9b39a8b377716db85c1fd7dbab604adc02d3313d"
_BALANCE_SCALE = 100_000_000.0
_COOKIE_ENV = "AIUSE_OPENCODE_ZEN_COOKIE"
_COOKIE_SECRET = "OPENCODE_ZEN_COOKIE"
_WORKSPACE_ENV = "AIUSE_OPENCODE_ZEN_WORKSPACE_ID"
_USER_AGENT = "aiuse OpenCode Zen collector"
_SECRETSPEC_MANIFEST = Path(__file__).resolve().parents[3] / "secretspec.toml"
_SECRETSPEC_TIMEOUT = 5.0


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
    workspace = _workspace_id(str(env.get(_WORKSPACE_ENV) or ""))
    if workspace is None:
        workspace = _first_workspace(_fetch_server(_WORKSPACES_SERVER_ID, None, cookie, timeout))
    if workspace is None:
        raise CollectorError("OpenCode Zen billing: workspace id missing from authenticated response")
    raw = _fetch_server(_BILLING_SERVER_ID, [workspace], cookie, timeout)
    balance = _parse_billing_balance(raw)
    if balance is None:
        raise CollectorError("OpenCode Zen billing: authenticated response did not include a balance")
    return [
        AccountUsage(
            source="opencode_zen",
            provider="opencode-zen",
            billing_kind=BillingKind.PREPAID_BALANCE,
            balance_usd=balance,
            notes=["Live data fetched directly from OpenCode Zen billing."],
        )
    ]


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
    manifest = str(env.get("SECRETSPEC_FILE") or _SECRETSPEC_MANIFEST)
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


def _fetch_server(server_id: str, args: list[str] | None, cookie: str, timeout: float) -> str:
    query: dict[str, str] = {"id": server_id}
    if args is not None:
        query["args"] = json.dumps(args, separators=(",", ":"))
    try:
        response = requests.get(
            _SERVER_URL,
            params=query,
            timeout=timeout,
            headers={
                "Accept": "text/javascript, application/json;q=0.9, */*;q=0.8",
                "Cookie": cookie,
                "Origin": _BASE_URL,
                "Referer": _BASE_URL,
                "User-Agent": _USER_AGENT,
                "X-Server-Id": server_id,
                "X-Server-Instance": f"server-fn:{uuid.uuid4()}",
            },
        )
        response.raise_for_status()
        return response.text
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CollectorError(f"OpenCode Zen billing returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise CollectorError(f"OpenCode Zen billing request failed: {exc.__class__.__name__}") from exc


def _workspace_id(value: str) -> str | None:
    # OpenCode uses ``work_``; accept the earlier ``work_`` spelling as a
    # compatibility fallback for serialized historical responses.
    match = re.search(r"\bw(?:rk|ork)_[A-Za-z0-9]+\b", value)
    return match.group(0) if match else None


def _first_workspace(text: str) -> str | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _workspace_id(text)
    return _find_workspace(parsed)


def _find_workspace(value: Any) -> str | None:
    if isinstance(value, str):
        return _workspace_id(value)
    if isinstance(value, dict):
        for item in value.values():
            if workspace := _find_workspace(item):
                return workspace
    if isinstance(value, list):
        for item in value:
            if workspace := _find_workspace(item):
                return workspace
    return None


def _parse_billing_balance(text: str) -> float | None:
    """Parse OpenCode's serialized billing response without retaining its body."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None and (raw := _find_raw_balance(parsed)) is not None:
        return raw / _BALANCE_SCALE
    customer = re.search(r'(?:"customerID"|customerID)\s*:\s*(?:\$R\[\d+\]\s*=\s*)?"[^"\n]+"', text)
    balance = re.search(r'(?:"balance"|balance)\s*:\s*(?:\$R\[\d+\]\s*=\s*)?(-?[0-9]+(?:\.[0-9]+)?)', text)
    if customer and balance:
        captured = balance.group(1)
        if isinstance(captured, str):
            return float(captured) / _BALANCE_SCALE
    return None


def _find_raw_balance(value: Any) -> float | None:
    if isinstance(value, dict):
        customer = value.get("customerID")
        balance = value.get("balance")
        if isinstance(customer, str) and customer and isinstance(balance, (str, int, float)):
            return float(balance)
        for item in value.values():
            if (found := _find_raw_balance(item)) is not None:
                return found
    if isinstance(value, list):
        for item in value:
            if (found := _find_raw_balance(item)) is not None:
                return found
    return None
