"""Loopback HTTP query surface for agents (issue #5).

Bind **127.0.0.1 only**. Read-only JSON endpoints; no credentials in responses.
Default path uses the newest on-disk snapshot when fresh enough; ``?refresh=1``
forces a live collect.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiuse.analysis.history import history_insights, load_recent_snapshots, save_snapshot, should_persist_snapshots
from aiuse.analysis.local_runtimes import maybe_local_runtime_alerts
from aiuse.analysis.suggest import pick_suggestion, suggestion_to_dict
from aiuse.analysis.use_or_lose import analyze_use_or_lose
from aiuse.collectors.runner import run_collectors
from aiuse.config import load_config
from aiuse.models import (
    AccountUsage,
    BillingKind,
    PaceProfile,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    parse_dt,
    utcnow,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_MAX_AGE_SECONDS = 3600.0


def run_serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    config_path: str | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> int:
    """Block serving until KeyboardInterrupt. Returns process exit code."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"error: refuse to bind non-loopback host {host!r} (pass 127.0.0.1 / localhost only)",
            flush=True,
        )
        return 1

    config = load_config(config_path)
    state = _ServeState(config=config, max_age_seconds=max_age_seconds)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # Quiet default access log noise; still useful for debugging.
            print(f"[aiuse serve] {self.address_string()} {fmt % args}", flush=True)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)
            refresh = _truthy(qs.get("refresh", ["0"])[0])

            try:
                if path in ("/v1/health", "/health", "/"):
                    body = {"ok": True, "service": "aiuse", "version": _version()}
                    self._json(200, body)
                    return
                if path == "/v1/snapshot":
                    payload = state.get_payload(refresh=refresh)
                    self._json(200, {"snapshot": payload["snapshot"], "source": payload["source"]})
                    return
                if path == "/v1/suggest":
                    payload = state.get_payload(refresh=refresh)
                    self._json(
                        200,
                        {
                            "suggestion": payload["suggestion"],
                            "source": payload["source"],
                            "collected_at": payload["snapshot"].get("collected_at"),
                        },
                    )
                    return
                if path == "/v1/ladder":
                    payload = state.get_payload(refresh=refresh)
                    self._json(
                        200,
                        {
                            "alerts": payload["alerts"],
                            "source": payload["source"],
                            "collected_at": payload["snapshot"].get("collected_at"),
                        },
                    )
                    return
                if path == "/v1/status":
                    payload = state.get_payload(refresh=refresh)
                    # Human one-liner from cached/live alerts when possible.
                    from aiuse.report import render_status_line

                    snap = _snapshot_from_payload(payload)
                    alerts = _alerts_from_payload(payload)
                    line = render_status_line(snap, alerts)
                    self._json(200, {"status": line, "source": payload["source"]})
                    return
                self._json(404, {"error": "not found", "path": path})
            except Exception as exc:  # noqa: BLE001 — surface as JSON error
                self._json(500, {"error": str(exc)})

        def _json(self, code: int, obj: dict[str, Any]) -> None:
            data = json.dumps(obj, indent=2, default=str).encode("utf-8") + b"\n"
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print(
        f"aiuse serve  http://{host}:{port}/v1/  (loopback only; max_age={max_age_seconds:g}s; Ctrl-C to stop)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\naiuse serve stopped", flush=True)
    finally:
        server.server_close()
    return 0


class _ServeState:
    def __init__(self, *, config: dict[str, Any], max_age_seconds: float) -> None:
        self.config = config
        self.max_age_seconds = max_age_seconds
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    def get_payload(self, *, refresh: bool) -> dict[str, Any]:
        with self._lock:
            if not refresh:
                cached = self._from_disk_if_fresh()
                if cached is not None:
                    return cached
                if self._cache is not None and _payload_age_ok(self._cache, self.max_age_seconds):
                    return self._cache
            live = self._collect_live()
            self._cache = live
            return live

    def _from_disk_if_fresh(self) -> dict[str, Any] | None:
        rows = load_recent_snapshots(retention_days=90, max_count=1)
        if not rows:
            return None
        row = rows[0]
        if not _disk_row_age_ok(row, self.max_age_seconds):
            return None
        return _payload_from_disk_row(row, config=self.config)

    def _collect_live(self) -> dict[str, Any]:
        snapshot = run_collectors(self.config)
        alerts = analyze_use_or_lose(snapshot, self.config)
        alerts.extend(maybe_local_runtime_alerts(snapshot, config=self.config))
        analysis_cfg = self.config.get("analysis") if isinstance(self.config.get("analysis"), dict) else {}
        if should_persist_snapshots(analysis_cfg):
            try:
                save_snapshot(
                    snapshot,
                    alerts,
                    retention_days=int((analysis_cfg or {}).get("snapshot_retention_days") or 90),
                )
            except OSError:
                pass
        suggestion = suggestion_to_dict(pick_suggestion(alerts))
        return {
            "snapshot": snapshot.to_dict(),
            "alerts": [a.to_dict() for a in alerts],
            "suggestion": suggestion,
            "history": history_insights(snapshot, analysis_cfg=analysis_cfg),
            "source": "live",
        }


def _payload_from_disk_row(row: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
    alerts_raw = row.get("alerts") or []
    # Re-pick suggestion from stored alerts (stable field set).
    alerts = _alerts_from_dicts(alerts_raw)
    suggestion = suggestion_to_dict(pick_suggestion(alerts))
    snap = {
        "collected_at": row.get("collected_at"),
        "accounts": row.get("accounts") or [],
        "cross_checks": [],
        "collector_errors": [],
    }
    # Minimal history object without reloading learning (cheap path).
    analysis_cfg = config.get("analysis") if isinstance(config.get("analysis"), dict) else {}
    try:
        # Prefer full insights when learning is on (uses disk history).
        hist = history_insights(_snapshot_from_accounts_dict(snap), analysis_cfg=analysis_cfg)
    except Exception:  # noqa: BLE001
        hist = {"snapshot_count": 0, "learning_active": False}
    return {
        "snapshot": snap,
        "alerts": [a.to_dict() for a in alerts],
        "suggestion": suggestion,
        "history": hist,
        "source": "cache",
    }


def _disk_row_age_ok(row: dict[str, Any], max_age: float) -> bool:
    ts = row.get("collected_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = (utcnow() - dt).total_seconds()
    return 0 <= age <= max_age


def _payload_age_ok(payload: dict[str, Any], max_age: float) -> bool:
    snap = payload.get("snapshot") or {}
    return _disk_row_age_ok({"collected_at": snap.get("collected_at")}, max_age)


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _version() -> str:
    from aiuse import __version__

    return __version__


def _alerts_from_dicts(rows: list[Any]) -> list[UseOrLoseAlert]:
    out: list[UseOrLoseAlert] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            urgency = Urgency(str(row.get("urgency") or "info"))
        except ValueError:
            urgency = Urgency.INFO
        pace = None
        pace_raw = row.get("pace")
        if isinstance(pace_raw, dict):
            exhaust = parse_dt(pace_raw.get("projected_exhaust_at"))
            pace = PaceProfile(
                elapsed_fraction=pace_raw.get("elapsed_fraction"),
                used_fraction=float(pace_raw.get("used_fraction") or 0.0),
                pace_ratio=pace_raw.get("pace_ratio"),
                projected_used_fraction=pace_raw.get("projected_used_fraction"),
                projected_waste_fraction=pace_raw.get("projected_waste_fraction"),
                projected_waste_usd=pace_raw.get("projected_waste_usd"),
                projected_exhaust_at=exhaust,
                governing=bool(pace_raw.get("governing", True)),
                gated_by=pace_raw.get("gated_by"),
                confidence=str(pace_raw.get("confidence") or "measured"),
                learned_sample_count=int(pace_raw.get("learned_sample_count") or 0),
            )
        out.append(
            UseOrLoseAlert(
                urgency=urgency,
                provider=str(row.get("provider") or "unknown"),
                account=row.get("account"),
                window_label=str(row.get("window_label") or ""),
                remaining_percent=float(row.get("remaining_percent") or 0.0),
                days_until_reset=row.get("days_until_reset"),
                plan=row.get("plan"),
                message=str(row.get("message") or ""),
                source=str(row.get("source") or "cache"),
                score=float(row.get("score") or 0.0),
                window_minutes=row.get("window_minutes"),
                kind=str(row.get("kind") or "burn"),
                pace=pace,
                deadline_is_estimated=bool(row.get("deadline_is_estimated", False)),
            )
        )
    return out


def _alerts_from_payload(payload: dict[str, Any]) -> list[UseOrLoseAlert]:
    return _alerts_from_dicts(payload.get("alerts") or [])


def _snapshot_from_accounts_dict(snap: dict[str, Any]) -> Snapshot:
    accounts: list[AccountUsage] = []
    for row in snap.get("accounts") or []:
        if not isinstance(row, dict):
            continue
        windows = []
        for w in row.get("windows") or []:
            if not isinstance(w, dict):
                continue
            windows.append(
                QuotaWindow(
                    label=str(w.get("label") or ""),
                    used_percent=w.get("used_percent"),
                    remaining_percent=w.get("remaining_percent"),
                    resets_at=parse_dt(w.get("resets_at")),
                    window_minutes=w.get("window_minutes"),
                    reset_description=w.get("reset_description"),
                )
            )
        try:
            billing = BillingKind(str(row.get("billing_kind") or "unknown"))
        except ValueError:
            billing = BillingKind.UNKNOWN
        accounts.append(
            AccountUsage(
                source=str(row.get("source") or "cache"),
                provider=str(row.get("provider") or "unknown"),
                account=row.get("account"),
                plan=row.get("plan"),
                billing_kind=billing,
                windows=windows,
                balance_usd=row.get("balance_usd"),
                credits_remaining=row.get("credits_remaining"),
                error=row.get("error"),
                notes=list(row.get("notes") or []),
            )
        )
    collected = parse_dt(snap.get("collected_at")) or utcnow()
    return Snapshot(collected_at=collected, accounts=accounts)


def _snapshot_from_payload(payload: dict[str, Any]) -> Snapshot:
    return _snapshot_from_accounts_dict(payload.get("snapshot") or {})
