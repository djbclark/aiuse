"""Optional loopback probes for local LLM runtimes (advisory only)."""

from __future__ import annotations

import socket
from typing import Any

from aiuse.models import AccountUsage, BillingKind, Snapshot, Urgency, UseOrLoseAlert

# Safe loopback defaults — never remote hosts unless the operator configures them.
_DEFAULT_ENDPOINTS: list[dict[str, Any]] = [
    {"name": "Ollama", "host": "127.0.0.1", "port": 11434},
    {"name": "LM Studio", "host": "127.0.0.1", "port": 1234},
]


def maybe_local_runtime_alerts(
    snapshot: Snapshot,
    *,
    config: dict[str, Any] | None = None,
) -> list[UseOrLoseAlert]:
    """Return INFO alerts when local runtimes are up and cloud quotas look empty.

    Never ranks local models as burn/conserve. Default config has probing **off**.
    """
    analysis = (config or {}).get("analysis") if isinstance((config or {}).get("analysis"), dict) else {}
    cfg = analysis.get("local_runtimes") if isinstance(analysis.get("local_runtimes"), dict) else {}
    if not cfg.get("enabled"):
        return []

    when = str(cfg.get("when") or "empty").lower()
    if when not in ("empty", "always", "always_if_empty"):
        when = "empty"
    if when in ("empty", "always_if_empty") and not _subscriptions_look_empty(snapshot):
        return []

    endpoints = cfg.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        endpoints = list(_DEFAULT_ENDPOINTS)

    timeout = float(cfg.get("probe_timeout_seconds") or 0.35)
    alerts: list[UseOrLoseAlert] = []
    for entry in endpoints:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "local")
        host = str(entry.get("host") or "127.0.0.1")
        # Refuse non-loopback unless explicitly allowed (safety).
        if host not in ("127.0.0.1", "localhost", "::1") and not cfg.get("allow_non_loopback"):
            continue
        try:
            port = int(entry.get("port"))
        except (TypeError, ValueError):
            continue
        if not (1 <= port <= 65535):
            continue
        if not _tcp_open(host, port, timeout=timeout):
            continue
        where = f"{host}:{port}"
        alerts.append(
            UseOrLoseAlert(
                urgency=Urgency.INFO,
                provider="local",
                account=None,
                window_label=f"{name} @ {where}",
                remaining_percent=0.0,
                days_until_reset=None,
                plan=None,
                message=(
                    f"local: {name} reachable at {where} (not ranked — use when "
                    "cloud subscriptions are empty)."
                ),
                source="probe",
                score=0.0,
                kind="prepaid",  # inventory-style; n/a band, never burn urgency
            )
        )
    return alerts


def _subscriptions_look_empty(snapshot: Snapshot) -> bool:
    """True when every measured subscription window is at/near 0% remaining."""
    rems: list[float] = []
    for account in snapshot.accounts:
        if account.error and not account.windows:
            continue
        if account.billing_kind in (BillingKind.PREPAID_BALANCE, BillingKind.PAYG_API):
            continue
        for window in account.windows:
            rem = window.remaining()
            if rem is not None:
                rems.append(float(rem))
    if not rems:
        return False
    return all(r <= 1.0 for r in rems)


def _tcp_open(host: str, port: int, *, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
