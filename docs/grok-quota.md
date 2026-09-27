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
purchases). Setup is just logging in once with the `grok` CLI; no API key and
no Keychain entry. Without `~/.grok/auth.json` the collector is silent (`[]`)
— grok rows simply come from the other sources, as before.

## How the wallet surfaces

- Emitted as `usage_credits.remaining` on canonical provider `grok`, source
  `grok_billing`.
- The runner folds it into any other live grok row (CodexBar / OpenUsage /
  tokscale) and drops the standalone row. When **no** other collector reports
  grok, the standalone row is kept so a known prepaid wallet is not hidden
  behind a disabled source.
- The ladder and matrix append `· $X.XX extra credits` (or `extra credits
empty` at zero) to the grok line.

## Config

```toml
[collectors.grok_billing]
# enabled = false   # when you do not use the grok CLI on this machine

[timeouts]
# grok_billing = 15
```

## JSON

Account rows carry `cli_binary: "grok"`; the wallet is the optional
`usage_credits` object (`remaining` in USD), per
[`json-contract.md`](json-contract.md).
