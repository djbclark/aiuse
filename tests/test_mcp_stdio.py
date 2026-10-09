"""Thin MCP stdio server over the aiuse serve payloads (issue #11)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest

from aiuse import cli
from aiuse.mcp_stdio import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpServer,
    serve_stdio,
    tool_definitions,
)
from aiuse.models import (
    AccountUsage,
    BillingKind,
    QuotaWindow,
    Snapshot,
    Urgency,
    UseOrLoseAlert,
    utcnow,
)
from aiuse.serve import ENDPOINTS, _ServeState, endpoint_body


def _snap() -> Snapshot:
    return Snapshot(
        collected_at=utcnow(),
        accounts=[
            AccountUsage(
                source="codexbar",
                provider="codex",
                billing_kind=BillingKind.SUBSCRIPTION_WINDOW,
                windows=[
                    QuotaWindow(
                        label="weekly",
                        used_percent=50,
                        remaining_percent=50,
                        resets_at=utcnow() + timedelta(days=3),
                        window_minutes=10080,
                    )
                ],
            )
        ],
    )


def _alerts() -> list[UseOrLoseAlert]:
    return [
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


@pytest.fixture
def mocked_state(monkeypatch) -> _ServeState:
    """A serve state whose live collect returns a fixed snapshot; no disk, no collectors."""
    monkeypatch.setattr("aiuse.serve.run_collectors", lambda _c: _snap())
    monkeypatch.setattr("aiuse.serve.analyze_use_or_lose", lambda _s, _c: _alerts())
    monkeypatch.setattr("aiuse.serve.maybe_local_runtime_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr("aiuse.serve.should_persist_snapshots", lambda _c: False)
    monkeypatch.setattr("aiuse.serve.load_recent_snapshots", lambda **_k: [])
    return _ServeState(config={}, max_age_seconds=3600)


def _call(server: McpServer, method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    message: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    response = server.handle(message)
    assert isinstance(response, dict)
    return response


def test_tools_list_registers_one_read_only_tool_per_serve_endpoint(mocked_state):
    response = _call(McpServer(mocked_state), "tools/list")
    tools = response["result"]["tools"]
    assert [t["name"] for t in tools] == list(ENDPOINTS)
    assert set(ENDPOINTS) == {"health", "suggest", "ladder", "status", "snapshot"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["description"]
    by_name = {t["name"]: t for t in tools}
    assert "refresh" in by_name["suggest"]["inputSchema"]["properties"]
    assert by_name["health"]["inputSchema"]["properties"] == {}


def test_initialize_echoes_a_supported_version_and_falls_back_to_the_newest(mocked_state):
    server = McpServer(mocked_state)
    result = _call(server, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "aiuse"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    unknown = _call(server, "initialize", {"protocolVersion": "1900-01-01"})["result"]
    assert unknown["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


@pytest.mark.parametrize("name", ["suggest", "ladder", "status", "snapshot", "health"])
def test_tool_call_returns_the_same_body_as_the_serve_endpoint(mocked_state, name):
    server = McpServer(mocked_state)
    result = _call(server, "tools/call", {"name": name, "arguments": {}})["result"]
    assert result["isError"] is False
    expected = json.loads(json.dumps(endpoint_body(mocked_state, name), default=str))
    if name == "snapshot":
        # freshness fields (age) tick between the two calls; compare the stable shape.
        assert result["structuredContent"].keys() == expected.keys()
        assert result["structuredContent"]["snapshot"] == expected["snapshot"]
    else:
        assert result["structuredContent"] == expected
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_suggest_tool_happy_path_with_mocked_snapshot(mocked_state):
    result = _call(McpServer(mocked_state), "tools/call", {"name": "suggest", "arguments": {"refresh": True}})
    body = result["result"]["structuredContent"]
    assert body["source"] == "live"
    assert body["suggestion"]["provider"] == "codex"
    assert body["suggestion"]["kind"] == "burn"


def test_protocol_errors(mocked_state):
    server = McpServer(mocked_state)
    assert _call(server, "tools/call", {"name": "route_request"})["error"]["code"] == INVALID_PARAMS
    assert _call(server, "tools/call", {"name": "suggest", "arguments": {"refresh": "yes"}})["error"]["code"] == (
        INVALID_PARAMS
    )
    # A 2026-07-28 client's probe: "method not found" tells it to fall back to initialize.
    assert _call(server, "server/discover")["error"]["code"] == METHOD_NOT_FOUND
    assert _call(server, "resources/list")["error"]["code"] == METHOD_NOT_FOUND
    assert _call(server, "ping")["result"] == {}
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 9, "result": {}}) is None


def test_a_failing_collect_is_a_tool_error_not_a_protocol_error(monkeypatch, mocked_state):
    def boom(_c):
        raise RuntimeError("collectors down")

    monkeypatch.setattr("aiuse.serve.run_collectors", boom)
    result = _call(McpServer(mocked_state), "tools/call", {"name": "ladder", "arguments": {"refresh": True}})
    assert result["result"]["isError"] is True
    assert "collectors down" in result["result"]["content"][0]["text"]


def test_serve_stdio_loop_answers_requests_skips_notifications_and_reports_parse_errors(mocked_state):
    stdin = StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                "{not json",
                "",
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status"}}),
            ]
        )
        + "\n"
    )
    stdout = StringIO()
    assert serve_stdio(McpServer(mocked_state), stdin, stdout) == 0
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [m.get("id") for m in lines] == [1, None, 2]
    assert lines[1]["error"]["code"] == PARSE_ERROR
    assert lines[2]["result"]["structuredContent"]["status"]


def test_cli_routes_bare_mcp_to_the_stdio_server(monkeypatch):
    seen: dict = {}

    def fake_run_mcp(load, *, max_age_seconds):
        seen.update(config=load(), max_age=max_age_seconds)
        return 0

    monkeypatch.setattr("aiuse.mcp_stdio.run_mcp", fake_run_mcp)
    assert cli.main(["mcp", "--max-age", "120", "--timeout", "9"]) == 0
    assert seen["max_age"] == 120.0
    assert seen["config"]["timeouts"]["default"] == 9.0


def test_tool_definitions_are_json_serializable():
    json.dumps(tool_definitions())


def test_aiuse_mcp_speaks_initialize_and_a_tool_call_over_real_stdio(tmp_path, monkeypatch):
    """End to end: a child ``aiuse mcp`` answers initialize, tools/list and suggest on its pipes."""
    from aiuse.analysis import history

    home = tmp_path / "home"
    snapshots = home / ".cache" / "aiuse" / "snapshots"
    monkeypatch.setattr(history, "snapshot_dir", lambda: snapshots)
    history.save_snapshot(_snap(), _alerts(), retention_days=90)
    assert list(snapshots.iterdir()), "seed snapshot was not written"

    # Every collector off, so a stale-cache bug can never reach a real tool.
    config = tmp_path / "config.toml"
    config.write_text(
        "".join(
            f"[collectors.{name}]\nenabled = false\n\n"
            for name in (
                "cswap codexbar caut openusage_ai openusage_sh opencode_zen opencode_go "
                "tokscale hermes muse qwencloud bailian"
            ).split()
        )
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "PYTHONUNBUFFERED": "1",
    }
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "suggest", "arguments": {}}},
    ]
    proc = subprocess.run(  # noqa: S603 — our own interpreter and module
        [sys.executable, "-m", "aiuse", "mcp", "--config", str(config), "--max-age", "3600"],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).parent,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3], proc.stdout
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert [t["name"] for t in responses[1]["result"]["tools"]] == list(ENDPOINTS)
    suggest = responses[2]["result"]
    assert suggest["isError"] is False
    assert suggest["structuredContent"]["source"] == "cache"
    assert suggest["structuredContent"]["suggestion"]["provider"] == "codex"
    assert "aiuse mcp" in proc.stderr
