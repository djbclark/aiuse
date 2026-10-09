"""Thin read-only MCP stdio server over the ``aiuse serve`` payloads (issue #11).

``aiuse mcp`` speaks newline-delimited JSON-RPC 2.0 on stdin/stdout, the MCP
stdio transport, and exposes one tool per ``aiuse serve`` endpoint: ``health``,
``suggest``, ``ladder``, ``status`` and ``snapshot``. Each tool returns exactly
the body the matching ``GET /v1/<name>`` returns (``serve.endpoint_body``), from
the same collect/cache path (``serve._ServeState``): no second scoring path.

Still an advisor only: no routing, no leases, no proxy, no credentials in
output, and nothing that writes beyond what ``aiuse serve`` already writes
(a live collect persists a snapshot when persistence is on).

Not cache-only. Every tool except ``health`` runs a live collect, in-process
and blocking, when the newest snapshot on disk is older than ``--max-age``
(default 3600 s) or when called with ``refresh=true``. A collect makes network
calls, runs collector subprocesses, may read the macOS Keychain (which can
raise a Keychain dialog), and writes a snapshot. It takes tens of seconds, and
the single-threaded loop answers nothing else meanwhile (review-2 4a).

Protocol scope: the ``initialize``-handshake revisions 2024-11-05 through
2025-11-25 (the "legacy" era in the 2026-07-28 spec). A modern client's
``server/discover`` probe gets "method not found", which the spec tells
dual-era clients to treat as a legacy server and fall back to ``initialize``.
No external MCP SDK: the server is a few JSON-RPC methods.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from collections.abc import Callable
from typing import Any, TextIO

from aiuse.serve import DEFAULT_MAX_AGE_SECONDS, ENDPOINTS, _ServeState, endpoint_body

# Newest first. The server answers with the client's version when it is listed
# here, else with the newest one (the client then decides whether to continue).
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_REFRESH_SCHEMA = {
    "type": "object",
    "properties": {
        "refresh": {
            "type": "boolean",
            "description": (
                "Collect live now instead of using the newest snapshot on disk "
                "(same as ?refresh=1 on aiuse serve). Slow: tens of seconds. Without it, "
                "a live collect still runs when that snapshot is older than --max-age."
            ),
        }
    },
    "additionalProperties": False,
}

_TOOL_TEXT = {
    "health": (
        "aiuse health",
        "Liveness and version of the aiuse advisor. Same body as GET /v1/health.",
    ),
    "suggest": (
        "Best pool to burn next",
        "The single best AI-subscription quota pool to spend next, or nothing urgent. "
        "Same body as GET /v1/suggest (suggestion field per docs/json-contract.md).",
    ),
    "ladder": (
        "Ranked burn/conserve ladder",
        "Every use-or-lose alert, ranked: which quota windows reset soon with "
        "allotment left. Same body as GET /v1/ladder.",
    ),
    "status": (
        "One-line quota status",
        "One human-readable line, as `aiuse status` prints. Same body as GET /v1/status.",
    ),
    "snapshot": (
        "Full quota snapshot",
        "Every account and quota window with freshness, summary lines and semantics. "
        "Same body as GET /v1/snapshot. Large.",
    ),
}


# Appended to every tool that can collect (all but health).
_COLLECT_NOTE = (
    " Reads the newest snapshot on disk; when it is older than --max-age (default 1 h), "
    "or refresh=true, it first collects live: network calls, collector subprocesses, "
    "possibly a macOS Keychain read or dialog, and a snapshot write (tens of seconds)."
)


def tool_definitions() -> list[dict[str, Any]]:
    """The ``tools/list`` entries, one per ``aiuse serve`` endpoint."""
    tools = []
    for name in ENDPOINTS:
        title, description = _TOOL_TEXT[name]
        if name != "health":
            description += _COLLECT_NOTE
        schema = (
            {"type": "object", "properties": {}, "additionalProperties": False} if name == "health" else _REFRESH_SCHEMA
        )
        tools.append(
            {
                "name": name,
                "title": title,
                "description": description,
                "inputSchema": schema,
                "annotations": {
                    "title": title,
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": name != "health",
                },
            }
        )
    return tools


def _version() -> str:
    from aiuse import __version__

    return __version__


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


class McpServer:
    """JSON-RPC dispatch for the stdio loop. Transport-free so tests drive it directly."""

    def __init__(self, state: _ServeState) -> None:
        self.state = state
        self.protocol_version: str | None = None

    def handle(self, message: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        """One decoded message (or a 2025-03-26 batch) -> response(s); None for notifications."""
        if isinstance(message, list):
            if not message:
                return _error(None, INVALID_REQUEST, "empty batch")
            responses = [r for r in (self._handle_one(m) for m in message) if r is not None]
            return responses or None
        return self._handle_one(message)

    def _handle_one(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method = message.get("method")
        is_request = "id" in message
        msg_id = message.get("id")
        if not isinstance(method, str):
            if "result" in message or "error" in message:
                return None  # a response: this server never sends requests, so ignore it
            return _error(msg_id, INVALID_REQUEST, "missing method") if is_request else None
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params must be an object") if is_request else None
        if not is_request:
            return None  # notifications/initialized, notifications/cancelled, ...
        try:
            if method == "initialize":
                return _result(msg_id, self._initialize(params))
            if method == "ping":
                return _result(msg_id, {})
            if method == "tools/list":
                return _result(msg_id, {"tools": tool_definitions()})
            if method == "tools/call":
                return self._tools_call(msg_id, params)
        except Exception as exc:  # noqa: BLE001 — never let one request kill the server
            return _error(msg_id, INTERNAL_ERROR, f"{exc.__class__.__name__}: {exc}")
        return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        self.protocol_version = version
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "aiuse", "title": "aiuse quota advisor", "version": _version()},
            "instructions": (
                "Advisor for AI-subscription quota: it never routes, spends or changes any account. "
                "Call `suggest` for the next pool to burn, `ladder` for the ranked list, `status` "
                "for one line. Answers come from the newest on-disk snapshot when it is younger "
                "than --max-age. Otherwise, or with refresh=true, the call first collects live "
                "(network, subprocesses, possibly a Keychain dialog, a snapshot write; tens of "
                "seconds), and the server answers nothing else until it finishes."
            ),
        }

    def _tools_call(self, msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if name not in ENDPOINTS:
            return _error(msg_id, INVALID_PARAMS, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
        refresh = arguments.get("refresh", False)
        if not isinstance(refresh, bool):
            return _error(msg_id, INVALID_PARAMS, "refresh must be a boolean")
        try:
            body = endpoint_body(self.state, str(name), refresh=refresh)
        except Exception as exc:  # noqa: BLE001 — a tool failure is a result, not a protocol error
            text = f"aiuse {name} failed: {exc.__class__.__name__}: {exc}"
            return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})
        # Round-trip so structuredContent is plain JSON (serve encodes with default=str too).
        text = json.dumps(body, indent=2, default=str)
        return _result(
            msg_id,
            {"content": [{"type": "text", "text": text}], "structuredContent": json.loads(text), "isError": False},
        )


def serve_stdio(server: McpServer, stdin: TextIO, stdout: TextIO) -> int:
    """Read one JSON message per line until EOF; write one response per line."""
    while True:
        line = stdin.readline()
        if not line:
            return 0
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            response: Any = _error(None, PARSE_ERROR, f"parse error: {exc}")
        else:
            # Anything a collector prints must not corrupt the protocol stream.
            with contextlib.redirect_stdout(sys.stderr):
                response = server.handle(message)
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":"), default=str) + "\n")
            stdout.flush()


def _claim_stdout_for_protocol() -> TextIO:
    """Move the protocol onto a private copy of fd 1 and point fd 1 at stderr.

    Collectors run subprocesses that inherit fd 1; anything they print would
    otherwise land in the JSON-RPC stream and break the client.
    """
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", closefd=False), encoding="utf-8", line_buffering=True)
    return io.TextIOWrapper(os.fdopen(protocol_fd, "wb"), encoding="utf-8", newline="\n")


def run_mcp(
    load_config: Callable[[], dict[str, Any]],
    *,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> int:
    """Entry point for ``aiuse mcp``: block on stdin until the client closes it."""
    out = _claim_stdout_for_protocol()
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    state = _ServeState(config=load_config(), max_age_seconds=max_age_seconds)
    print(f"aiuse mcp {_version()} on stdio (read-only; max_age={max_age_seconds:g}s)", file=sys.stderr, flush=True)
    try:
        return serve_stdio(McpServer(state), stdin, out)
    except KeyboardInterrupt:
        return 0
    finally:
        with contextlib.suppress(OSError, ValueError):
            out.flush()
