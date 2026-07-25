# Agent API (loopback HTTP)

**Issue #5 (MVP, shipped):** `aiuse serve` exposes read-only ranking JSON on
**127.0.0.1 only** for multi-step agents. It is not a model proxy and does not
emit credentials. Full MCP stdio remains an optional follow-up if agents need
native MCP (see [`handoff.md`](handoff.md)).

## Start

```bash
aiuse serve                 # http://127.0.0.1:8787/v1/
aiuse serve --port 8787 --max-age 3600
```

Stop with Ctrl-C.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v1/health` | Liveness |
| GET | `/v1/snapshot` | Latest snapshot object |
| GET | `/v1/ladder` | Ranked `alerts[]` |
| GET | `/v1/suggest` | Single burn winner or null |
| GET | `/v1/status` | One-line status string |

Query:

| Param | Default | Effect |
| --- | --- | --- |
| `refresh=1` | off | Force live collect (same as CLI collect) |
| `refresh=0` | on | Prefer cache if younger than `--max-age` |

## Caching

1. Newest file under `~/.cache/aiuse/snapshots/` if age ≤ `--max-age`
2. Else in-process cache from last live collect
3. Else live collect (and persist when config allows)

## Examples

```bash
curl -sS 'http://127.0.0.1:8787/v1/suggest' | jq .
curl -sS 'http://127.0.0.1:8787/v1/ladder?refresh=1' | jq '.alerts[0]'
```

## Non-goals (this MVP)

- Full MCP stdio server (optional follow-up: [#11](https://github.com/djbclark/aiuse/issues/11))
- Binding non-loopback interfaces
- Auth / multi-user serving

## Related

- [`json-contract.md`](json-contract.md) — field shapes for alerts/suggestion/history
- [`companion-stack.md`](companion-stack.md) — ambient vs rank
- [`next-options.md`](next-options.md) — whether MCP stdio is worth starting
