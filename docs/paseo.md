# Paseo daemon as a usage source

Related reading: [`AGENTS.md`](../AGENTS.md) · [`README.md`](../README.md) ·
[`docs/provider-identity.md`](provider-identity.md) ·
[`docs/usage-sources.md`](usage-sources.md) if present

[Paseo](https://paseo.sh/) is a local daemon that manages coding agents
(Claude Code, Codex, Copilot, …) and keeps logins for those providers. Its
CLI can report the account quotas it sees, which makes it a **second source**
for providers aiuse already tracks — not a new provider.

## What aiuse calls

```bash
paseo usage ls --json
```

One local call against the daemon's **five-minute host cache** — no vendor
API traffic, never `--refresh`. Requires Paseo 0.12+ (`paseo usage` landed
there). A missing `paseo` binary returns no rows. If the binary is
present and the daemon is down, the CLI exits 1 and the collector records a
`paseo:` error like any other broken tool; per-account login problems are
reported by paseo as `status: unavailable` and simply emit no row.

Each entry carries `fetchedAt`/`successfulAt`; aiuse uses `successfulAt`
(when the daemon last actually refreshed that account) as the reading's
`collected_at`, so source arbitration treats paseo like any other source:
fresher wins, and paseo sits **behind every direct source** in the priority
tuples (`docs` header of `src/aiuse/collectors/runner.py`).

## Mapping

| paseo `sourceId` | aiuse provider | Notes                                                                                                                             |
| ---------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `claude`         | `claude`       | `five_hour` → Claude Code 5-hour; `weekly` → Claude Code weekly; `weekly_model_*` → `Claude Code weekly — <Model>` model sublimit |
| `codex`          | `codex`        | weekly window; `credits` balance lands in notes                                                                                   |
| `copilot`        | `copilot`      | only premium-request windows kept (`keep_copilot_report_window`)                                                                  |
| `grok`           | `grok`         | weekly window                                                                                                                     |
| `antigravity`    | `antigravity`  | generic window mapping                                                                                                            |

Balances become row **notes** (`paseo balance credits: 0 credits left`), not
structured fields, until their semantics are verified against a primary
source.

## Deliberately skipped

- `cursor` — aiuse's Cursor rows are CodexBar **pool families**
  (`docs/cursor-quota.md`); an account-level balance from paseo would fork
  the pool world.
- `omp` — omp rows need the operator-gate annotations and override
  calibration wired into the CodexBar/admin-api paths
  (`docs/omp-cloud-credits.md`).
- `opencode` — OpenCode Go has a native collector with lapsed-plan
  detection (`docs/opencode-go-quota.md`).
- `pi`, `muse`, and any unknown source id — no aiuse provider, or native
  collector keeps priority (`muse`).

## Config

On by default, silent when the binary is absent:

```toml
[collectors.paseo]
# enabled = true
```

or per-run `aiuse --no-paseo`. Not in `packaging/install-deps.sh` by design —
Paseo is a whole agent daemon the operator installs for its own sake
([paseo.sh](https://paseo.sh/)), not a quota helper; aiuse just reads it when
it is there.

**Read when:** a paseo account shows `unavailable`; wondering why a cursor /
omp / muse paseo entry does not appear; tuning source priority.
