# Venice quota notes

**Investigated:** 2026-11-10, after the operator asked why the Venice row shows
no info ("Balance unavailable for API calls") while expecting live data.

## What the row means

CodexBar's Venice provider (plugin `venice.js`) calls
`GET https://api.venice.ai/api/v1/billing/balance` with the stored API key and
renders the first matching branch of the response:

| Response                          | CodexBar shows                                  |
| --------------------------------- | ----------------------------------------------- |
| `canConsume: false`               | `100u/0l` + "Balance unavailable for API calls" |
| USD currency, `usd > 0`           | `$X.XX USD remaining`                           |
| DIEM staking (`diem`, allocation) | `DIEM X / Y epoch allocation` (usage %)         |
| any other positive balance        | remaining                                       |
| nothing positive                  | `100u/0l` + "No Venice API balance available"   |

So the message is **not** a fetch failure: it means Venice answered 200 with
`canConsume: false`. On this machine (2026-11-10) the account had
`balances.usd = $0.095`, no DIEM staking (`diem: null`) — below Venice's spend
buffer, hence blocked. Community reports say Venice silently enforces a credit
buffer, so a small positive balance can still be unspendable.

## Where the balance actually is

- `GET /api/v1/billing/balance` (Bearer API key; beta, unstable schema):
  `{canConsume, consumptionCurrency, balances{diem, usd, bundledCredits,
earnedCredits}, diemEpochAllocation}`. The local key lives in
  `~/.venice/config.json` (`api_key`).
- Venice web dashboard / API settings: <https://venice.ai/settings/api>.
- CodexBar's **Web** source can also read _subscription_ credits from a
  signed-in venice.ai browser tab (Clerk cookies, ~60 s lifetime — needs an
  active tab). Separate money from the API USD balance.
- Unauthenticated calls get HTTP 402 with an x402 offer (USDC on Base/Solana
  per-call payment) — Venice's newer payment rail, not needed for reads.

## Possible upstream improvement (not filed)

CodexBar's `venice.js` checks `!canConsume` first and hides the numbers; it
could show `$0.10 USD remaining · blocked (buffer)` in that branch so the row
carries the balance it already fetched. steipete/CodexBar (AGPL-3.0) — file as
a PR there if wanted; aiuse just relays CodexBar's text today.

## aiuse behavior

No aiuse bug: the row faithfully relays CodexBar's branch output. Venice is in
`[disabled_services]` (operator, 2026-10-08), so the row only appears on the
operator sweep (`watch a` / `--all-providers`).
