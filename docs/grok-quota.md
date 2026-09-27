# Grok quota (SuperGrok windows + Extra Usage Credits)

**Related:** [`source-coverage.md`](source-coverage.md), [`zai-quota.md`](zai-quota.md),
[`json-contract.md`](json-contract.md), [`../README.md`](../README.md)

The canonical provider id `grok` covers two different meters that must not be
merged:

| Meter                 | What it is                                       | Where it comes from                               |
| --------------------- | ------------------------------------------------ | ------------------------------------------------- |
| SuperGrok weekly pool | Subscription window (use-or-lose)                | CodexBar `grok-cli-proxy`, OpenUsage.ai, tokscale |
| Extra Usage Credits   | Prepaid USD wallet purchased on top of SuperGrok | Native `grok_billing` collector (this doc)        |

CodexBar's `grok-cli-proxy` path exports the weekly pool only — purchased
Extra Usage Credits are invisible to it. The native `grok_billing` collector
supplements grok rows with that wallet instead of forking a second provider
identity.

## Source: the local `grok` CLI's billing endpoint

The collector reads the bearer token the Grok Build TUI (`grok`, the same CLI
this quota is spent with) stores in `~/.grok/auth.json` (`GROK_HOME` override
honored), then calls:

```
GET https://cli-chat-proxy.grok.com/v1/billing?format=credits
Authorization: Bearer <token>
```

`config.prepaidBalance.val` is **USD cents** (live-checked against ~$20
purchases). The same payload carries the plan period itself:
`config.currentPeriod` (`type` / `start` / `end`) and
`config.creditUsagePercent`, plus a `config.productUsage` list of per-product
pool percentages (e.g. GrokBuild vs GrokChat). Setup is just logging in once
with the `grok` CLI; no API key and no Keychain entry. Without
`~/.grok/auth.json` the collector is silent (`[]`) — grok rows simply come
from the other sources, as before.

## How the wallet surfaces

- Emitted as `usage_credits.remaining` on canonical provider `grok`, source
  `grok_billing`.
- The runner folds it into any other live grok row (CodexBar / OpenUsage /
  tokscale) and drops the standalone row. When **no** other collector reports
  grok, the standalone row is kept so a known prepaid wallet is not hidden
  behind a disabled source.
- The ladder and matrix append `· $X.XX extra credits` (or `extra credits
empty` at zero) to the grok line. The wallet is inventory, not a clock: in
  the matrix it renders as a trailing note and never suppresses the reset
  cells of a window on the same row.

## How the plan reset surfaces

Since 3.1.0 the collector also parses `currentPeriod` into a `QuotaWindow` so
the time until the plan resets survives with CodexBar disabled:

- `USAGE_PERIOD_TYPE_HOURLY` / `_DAILY` / `_WEEKLY` / `_MONTHLY` map to
  labels `hourly plan` / `daily plan` / `weekly plan` / `monthly plan` and
  nominal window minutes (60 / 1440 / 10080 / 43200); unknown types fall back
  to label `plan` with no minutes, and the clock is then inferred from the
  reset distance.
- `creditUsagePercent` becomes the window's `used_percent`; `currentPeriod.end`
  becomes `resets_at`.
- `productUsage` becomes a note (`Plan pools: GrokBuild 93% used · …`).
- A subscriber with an empty prepaid wallet still gets a row: the plan window
  alone is enough.
- On merge, the billing window fills only a host row that has **no** windows
  of its own — CodexBar's window wins, no duplication.

## Config

```toml
[collectors.grok_billing]
# enabled = false   # when you do not use the grok CLI on this machine

[timeouts]
# grok_billing = 15
```

## JSON

Account rows carry `cli_binary: "grok"`; the wallet is the optional
`usage_credits` object (`remaining` in USD), and the plan period is an
ordinary entry in `windows` (`label`, `used_percent`, `resets_at`,
`window_minutes`), per [`json-contract.md`](json-contract.md).
