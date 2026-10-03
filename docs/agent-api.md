# Agent API (loopback HTTP)

**Issue #5 (MVP, shipped):** `aiuse serve` exposes read-only ranking JSON on
**127.0.0.1 only** for multi-step agents. It is not a model proxy and does not
emit credentials. Full MCP stdio remains an optional follow-up if agents need
native MCP (see [`handoff.md`](handoff.md)).

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

| Method | Path           | Notes                                             |
| ------ | -------------- | ------------------------------------------------- |
| GET    | `/v1/health`   | Liveness + identity (`service: "aiuse"`, version) |
| GET    | `/v1/snapshot` | Latest snapshot object                            |
| GET    | `/v1/ladder`   | Ranked `alerts[]`                                 |
| GET    | `/v1/suggest`  | Single burn winner or null                        |
| GET    | `/v1/status`   | One-line status string                            |

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

## Non-goals (this MVP)

- Full MCP stdio server (optional follow-up: [#11](https://github.com/djbclark/aiuse/issues/11))
- Binding non-loopback interfaces
- Auth / multi-user serving

## Related

- [`json-contract.md`](json-contract.md) — field shapes for alerts/suggestion/history
- [`companion-stack.md`](companion-stack.md) — ambient vs rank
- [`next-options.md`](next-options.md) — whether MCP stdio is worth starting
