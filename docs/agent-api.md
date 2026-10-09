# Agent API (loopback HTTP and MCP stdio)

**Issue #5 (MVP, shipped):** `aiuse serve` exposes read-only ranking JSON on
**127.0.0.1 only** for multi-step agents. It is not a model proxy and does not
emit credentials. **Issue #11:** `aiuse mcp` serves the same payloads as MCP
tools over stdio for hosts that only speak MCP; see
[MCP stdio](#mcp-stdio-aiuse-mcp) below.

## Start

```bash
aiuse serve                 # http://127.0.0.1:28787/v1/
aiuse serve --port 28787 --max-age 3600
```

Stop with Ctrl-C.

### Default port and collision behaviour

The default port is **28787** (it was 8787 before the change). 8787 is held
by other loopback apps on this class of machine — on the operator's Mac it is
the registered `collie-bridge` port, whose web app answers **every** path with
HTML 200, so a client hitting `http://127.0.0.1:8787/v1/snapshot` while aiuse
serve was not running got plausible-looking HTML and no way to tell it was not
talking to aiuse.

Two guards address that:

1. **`aiuse serve` fails loudly instead of serving behind an imposter.**
   Before binding, it probes the target port. If a responder is already
   listening there it exits 1 with an error naming the situation — whether the
   responder identifies as aiuse ("already running") or not ("held by a
   non-aiuse server"; with an `lsof` hint and `--port` escape hatch). A bind
   failure races the same way into a clear error, not a traceback.
2. **Clients can identity-check the listener.** `GET /v1/health` returns
   `{"ok": true, "service": "aiuse", "version": "<x.y.z>"}` — a client should
   verify `service == "aiuse"` (and may pin a minimum `version`) before
   trusting any other endpoint, so an unrelated app on a reused port cannot be
   mistaken for aiuse.

## Endpoints

| Method | Path           | Notes                                                                                                                               |
| ------ | -------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/v1/health`   | Liveness + identity (`service: "aiuse"`, version)                                                                                   |
| GET    | `/v1/snapshot` | Latest snapshot object (schema 1.1: enriched windows/accounts), plus `source`, `age_seconds`, `fresh`, `summary_lines`, `semantics` |
| GET    | `/v1/ladder`   | Ranked `alerts[]`                                                                                                                   |
| GET    | `/v1/suggest`  | Single burn winner or null                                                                                                          |
| GET    | `/v1/status`   | One-line status string                                                                                                              |

Query:

| Param       | Default | Effect                                   |
| ----------- | ------- | ---------------------------------------- |
| `refresh=1` | off     | Force live collect (same as CLI collect) |
| `refresh=0` | on      | Prefer cache if younger than `--max-age` |

## Caching

1. Newest file under `~/.cache/aiuse/snapshots/` if age ≤ `--max-age`
2. Else in-process cache from last live collect
3. Else live collect (and persist when config allows)

## Examples

```bash
curl -sS 'http://127.0.0.1:28787/v1/health' | jq -e '.service == "aiuse"'   # identity check first
curl -sS 'http://127.0.0.1:28787/v1/suggest' | jq .
curl -sS 'http://127.0.0.1:28787/v1/ladder?refresh=1' | jq '.alerts[0]'
```

## MCP stdio (`aiuse mcp`)

[Issue #11](https://github.com/djbclark/aiuse/issues/11). `aiuse mcp` is a thin,
read-only [MCP](https://modelcontextprotocol.io/) server on stdin/stdout. Each
tool returns **exactly** the JSON body of the matching `aiuse serve` endpoint,
built by the same code (`serve.endpoint_body`) from the same cache path, so the
field shapes are the ones in [`json-contract.md`](json-contract.md) and the
table above. There is no second scoring path, no routing, no leases and no
credentials in output. Nothing listens on a port.

| Tool       | Same body as       | Arguments                       |
| ---------- | ------------------ | ------------------------------- |
| `health`   | `GET /v1/health`   | none                            |
| `suggest`  | `GET /v1/suggest`  | `refresh` (bool, default false) |
| `ladder`   | `GET /v1/ladder`   | `refresh`                       |
| `status`   | `GET /v1/status`   | `refresh`                       |
| `snapshot` | `GET /v1/snapshot` | `refresh`                       |

Each result carries the body twice, as `structuredContent` and as JSON text in
`content[0].text`, for hosts that read only one of them. A failed collect is a
tool result with `isError: true`, not a protocol error. Caching is the
[Caching](#caching) order above: `--max-age` (default 3600 s) bounds the on-disk
snapshot, and `refresh: true` collects live (tens of seconds). `--config` and
`--timeout` apply as for any collect.

Protocol: newline-delimited JSON-RPC 2.0, `initialize` handshake revisions
2024-11-05 through 2025-11-25. A 2026-07-28 ("modern") client's
`server/discover` probe gets "method not found", which the spec tells dual-era
clients to treat as a legacy server and fall back to `initialize`. Diagnostics
go to stderr only; collector output is kept off the protocol stream.

### Host configuration

Hosts start the server themselves. Use the absolute path from `command -v aiuse`:
GUI hosts such as Claude Desktop do not inherit your shell's `PATH`.

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`)
and **Cursor** (`~/.cursor/mcp.json`, or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "aiuse": {
      "command": "/opt/homebrew/bin/aiuse",
      "args": ["mcp"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add aiuse -- "$(command -v aiuse)" mcp
```

Quick manual check (one initialize, one tool call):

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"suggest","arguments":{}}}' \
  | aiuse mcp 2>/dev/null | jq -c '.result.structuredContent.suggestion // .result.serverInfo'
```

## Non-goals

- Request routing, model registry, task budgets, LiteLLM leases (quotabot territory)
- Binding non-loopback interfaces; MCP over HTTP (stdio only for now)
- Auth / multi-user serving
- Replacing `aiuse serve` (the MCP server is a second door onto the same payloads)

## Related

- [`json-contract.md`](json-contract.md) — field shapes for alerts/suggestion/history
- [`companion-stack.md`](companion-stack.md) — ambient vs rank
- [`next-options.md`](next-options.md) — whether MCP stdio is worth starting
