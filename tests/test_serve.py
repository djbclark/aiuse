"""Loopback serve API (issue #5)."""

from __future__ import annotations

from datetime import timedelta

from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.serve import _payload_from_disk_row, _ServeState


def test_payload_from_disk_row_picks_suggestion():
    row = {
        "collected_at": utcnow().isoformat(),
        "accounts": [],
        "alerts": [
            {
                "urgency": "high",
                "provider": "claude",
                "account": "a@x.com",
                "window_label": "Claude Code weekly",
                "remaining_percent": 90,
                "days_until_reset": 2,
                "plan": None,
                "message": "burn",
                "source": "cswap",
                "score": 80,
                "kind": "burn",
            }
        ],
    }
    payload = _payload_from_disk_row(row, config={"analysis": {"learn_from_history": False}})
    assert payload["source"] == "cache"
    assert payload["suggestion"]["provider"] == "claude"
    assert payload["suggestion"]["kind"] == "burn"


def test_serve_state_live_collect(monkeypatch):
    snap = Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="weekly",
                        remaining_percent=50,
                        resets_at=utcnow() + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )
    alerts = [
        UseOrLoseAlert(
            urgency=Urgency.HIGH,
            provider="codex",
            account=None,
            window_label="weekly",
            remaining_percent=50,
            days_until_reset=3,
            plan=None,
            message="burn",
            source="codexbar",
            score=70,
            kind="burn",
        )
    ]
    monkeypatch.setattr("aiuse.serve.run_collectors", lambda _c: snap)
    monkeypatch.setattr("aiuse.serve.analyze_use_or_lose", lambda _s, _c: list(alerts))
    monkeypatch.setattr("aiuse.serve.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.serve.should_persist_snapshots", lambda _c: False)
    monkeypatch.setattr("aiuse.serve.load_recent_snapshots", lambda **_k: [])

    state = _ServeState(config={}, max_age_seconds=3600)
    payload = state.get_payload(refresh=True)
    assert payload["source"] == "live"
    assert payload["suggestion"]["provider"] == "codex"

    # Second call without refresh uses in-process cache
    payload2 = state.get_payload(refresh=False)
    assert payload2["source"] == "live"
    assert payload2["suggestion"]["score"] == 70


def test_http_handler_health(monkeypatch):
    from aiuse.serve import DEFAULT_HOST

    # Smoke: import path and host guard
    assert DEFAULT_HOST == "127.0.0.1"
    # refuse non-loopback
    from aiuse import serve as serve_mod

    code = serve_mod.run_serve(host="0.0.0.0", port=1)
    assert code == 1


def test_default_port_is_not_8787():
    # 8787 is commonly held by other loopback apps (collie-bridge on the
    # operator's Mac answers every path with HTML 200). Pin the moved default
    # and keep the CLI argparse default in sync with serve.DEFAULT_PORT.
    from aiuse import cli as cli_mod
    from aiuse.serve import DEFAULT_PORT

    assert DEFAULT_PORT == 28787
    parser = cli_mod.build_parser()
    got = [a for a in parser._actions if "--port" in a.option_strings]
    assert got and got[0].default == DEFAULT_PORT


class _StubResponder:
    """Minimal HTTP server impersonating whatever holds a port in tests."""

    def __init__(self, *, aiuse_health: bool) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: A003
                pass

            def do_GET(self):  # noqa: N802
                if aiuse_health:
                    body = b'{"ok": true, "service": "aiuse", "version": "9.9.9"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                else:
                    body = b"<html><body>collie says hi</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        import threading

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def test_probe_port_holder_free_aiuse_other():
    from aiuse.serve import probe_port_holder

    assert probe_port_holder("127.0.0.1", 1) is None  # nothing listens on tcp/1

    stub_aiuse = _StubResponder(aiuse_health=True)
    try:
        assert probe_port_holder("127.0.0.1", stub_aiuse.port) == "aiuse"
    finally:
        stub_aiuse.close()

    stub_other = _StubResponder(aiuse_health=False)
    try:
        assert probe_port_holder("127.0.0.1", stub_other.port) == "other"
    finally:
        stub_other.close()


def test_run_serve_refuses_port_held_by_non_aiuse(capsys):
    from aiuse import serve as serve_mod

    stub = _StubResponder(aiuse_health=False)
    try:
        code = serve_mod.run_serve(host="127.0.0.1", port=stub.port)
        assert code == 1
        err = capsys.readouterr().out
        assert "non-aiuse server" in err
    finally:
        stub.close()


def test_run_serve_refuses_second_aiuse(capsys):
    from aiuse import serve as serve_mod

    stub = _StubResponder(aiuse_health=True)
    try:
        code = serve_mod.run_serve(host="127.0.0.1", port=stub.port)
        assert code == 1
        err = capsys.readouterr().out
        assert "already listening" in err
    finally:
        stub.close()
