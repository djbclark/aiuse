# Attribution: what spent the quota

`aiuse` reports how full each quota window is. It could not say **which
client** filled it. On 2026-09-25 a ClinePass subscription lost half a month of
quota in one afternoon and nothing on the machine recorded who spent it.
Three pieces close that gap:

1. **Token ledgers** record who spent tokens.
2. **Adaptive sampling** (`aiuse sample`) reads the meters more often while
   they are moving.
3. **`aiuse attribute`** sets the two side by side.

## Token ledgers

| Ledger   | What it sees                                          | How it is read                               |
| -------- | ----------------------------------------------------- | -------------------------------------------- |
| tokscale | Every agent CLI's session files, per client and model | Sampled beside each snapshot                 |
| LiteLLM  | Every request through a local LiteLLM proxy, per key  | Queried from its spend log, exact timestamps |

**tokscale.** `tokscale models --json` gives cumulative tokens per client and
model for a local day. `aiuse` stores that total with each persisted snapshot
under `~/.cache/aiuse/ledger/`; the difference between two samples is what was
spent between them. On by default when `tokscale` is on `PATH`
(`[attribution] ledger = "auto"`).

**LiteLLM** (optional). Covers callers that leave no session file: background
services, cron jobs, anything that talks to a proxy. Point `aiuse` at the
proxy's Postgres spend database:

```toml
[attribution.litellm]
database_url = "postgresql://me@127.0.0.1:5433/litellm"

# model_group (fnmatch) -> aiuse provider id, for routes whose upstream cannot
# be told from the api_base (a local bridge, say).
[attribution.litellm.provider_map]
"grok-sub" = "grok"
```

It needs `psql` on `PATH`. Rows are grouped by the virtual key's alias, so give
each client its own key; requests made with a shared master key appear as
`unkeyed:<user agent>`.

Rows are matched to a quota provider by a built-in table (tokscale's upstream
name, the client for clients that draw one subscription, the proxy's
`api_base` host). A router client such as Hermes reaches a vendor through
whichever credential it holds, so its ambiguous upstreams are left unmapped
and listed at the foot of the report. Map them yourself:

```toml
[attribution.provider_map]
"hermes/openai" = "codex" # tokscale "client/provider", fnmatch
```

## Adaptive sampling

`aiuse sample` is the scheduled entry point. The scheduler fires it every
three minutes; most firings return at once.

| Tier   | When                                 | Cadence | Collects                   |
| ------ | ------------------------------------ | ------- | -------------------------- |
| idle   | No window moved                      | 60 min  | Everything                 |
| active | Some window rose 0.5 points          | 15 min  | Everything                 |
| burst  | A window is burning fast (see below) | 3 min   | The burning providers only |

A burst needs both: the window is rising at 8 points an hour, **and** at three
times the pace that would exactly exhaust it. The second test keeps a 5-hour
window's ordinary 20 points an hour from counting, while a weekly window at 8
does. Rising a tier takes one sample; falling takes three quiet ones.

The burst rate is measured against a reading at least 9 minutes old (0.6 ×
`active_interval`), never against the sample just before. Meters report whole
points, so a single tick between two samples four minutes apart would read as
15 points an hour.

Burst samples are partial, so they are filed under `~/.cache/aiuse/samples/`
and never become `latest.json`. History learning and `aiuse serve` do not read
them. A full collection still happens every 15 minutes during a burst.

```toml
[sampling]
idle_interval = 3600
active_interval = 900
burst_interval = 180
burst_percent_per_hour = 8.0
burst_pace_ratio = 3.0
cooldown_samples = 3
```

`aiuse sample --force` collects now. State is in
`~/.cache/aiuse/sampler-state.json`. Scheduling: [`scheduling.md`](scheduling.md).

## `aiuse attribute`

```bash
aiuse attribute                      # last 24 hours
aiuse attribute --since 7d --provider clinepass --intervals
aiuse attribute --since 2026-09-25T10:00 --until 2026-09-25T23:00 --json
```

For each provider: points burned per window, tokens per client and model from
each ledger with its share, and a tokens-per-point rate against the longest
window that moved. `--intervals` adds one line per sample interval in which a
window rose, with who spent tokens in it.

Reading it:

1. **Burned points are not used-percent.** A window that reset twice can burn
   more than 100. A fall counts as a reset only when the reset time moved or
   passed; otherwise it is a bad reading and is skipped.
2. **Tokens and points are different units.** Vendors weight models and cached
   tokens their own way. The report puts them side by side and derives a rate;
   it does not convert one into the other.
3. **The two ledgers can overlap.** A client that logs its own sessions and
   also routes through the proxy shows in both. They are listed separately and
   never summed.
4. **A ledger only covers the time it was sampled.** The header says when the
   session-file ledger started, and its rate is computed over that span only.
