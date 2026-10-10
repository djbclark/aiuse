"""SIPB LLMs (MIT) collector."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Mapping

import requests

from aiuse.models import AccountUsage, BillingKind
from aiuse.secretspec import resolve_manifest_path


def _resolve_key_via_secretspec(env: Mapping[str, str], timeout: float) -> str | None:
    executable = shutil.which("secretspec")
    if executable is None:
        executable = shutil.which("sudo-secretspec")
        if executable is None:
            return None
        return _resolve_via_sudo_secretspec(executable, timeout)

    manifest = str(resolve_manifest_path(env))
    try:
        result = subprocess.run(
            [
                executable,
                "get",
                "--file",
                manifest,
                "--reason",
                "aiuse SIPB LLM collection",
                "MIT_SIPB_API_KEY",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=min(max(timeout, 0.1), 3.0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        key = result.stdout.strip()
        if key:
            return key
    return None


def _resolve_via_sudo_secretspec(executable: str, timeout: float) -> str | None:
    try:
        result = subprocess.run(
            [executable, "get", "MIT_SIPB_API_KEY", "--reason", "aiuse SIPB LLM collection"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=min(max(timeout, 0.1), 3.0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        key = result.stdout.strip()
        if key:
            return key
    return None


def _resolve_key(env: Mapping[str, str], timeout: float) -> str | None:
    explicit = str(env.get("MIT_SIPB_API_KEY") or "").strip()
    if explicit:
        return explicit
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return _resolve_key_via_secretspec(env, timeout)
    return None


def collect_sipb(*, env: Mapping[str, str] | None = None, timeout: float = 120.0) -> list[AccountUsage]:
    """Collect status from MIT SIPB LLM server."""
    if env is None:
        env = os.environ

    api_key = _resolve_key(env, timeout)

    # Fast idle, slow under load.
    start = time.monotonic()

    base_url = "https://llms-dev-1.mit.edu/api"
    config_url = f"{base_url}/config"
    models_url = f"{base_url}/models"

    notes = ["SIPB LLMs (MIT): Unlimited quota.", "Slow (shared GPU) — use sparingly / as a last resort."]

    try:
        resp = requests.get(config_url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        err_str = f"SIPB server unreachable: {e}"
        # Make a down row
        return [
            AccountUsage(
                source="sipb",
                provider="sipb",
                billing_kind=BillingKind.UNKNOWN,
                error=err_str,
                notes=notes,
            )
        ]

    latency = time.monotonic() - start

    # Successfully fetched config, it's UP
    notes.append(f"Server is UP (latency: {latency:.2f}s).")

    raw = {"config": data, "status": {"up": True, "latency_s": round(latency, 3)}}

    if api_key:
        try:
            m_resp = requests.get(models_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
            m_resp.raise_for_status()
            models_data = m_resp.json()
            models = models_data.get("data", [])
            raw["models"] = models_data
            notes.append(f"Authenticated access enabled. {len(models)} models available.")
        except requests.RequestException:
            notes.append("Authenticated access failed (check MIT_SIPB_API_KEY).")

    return [
        AccountUsage(
            source="sipb",
            provider="sipb",
            billing_kind=BillingKind.UNKNOWN,
            notes=notes,
            raw=raw,
        )
    ]
