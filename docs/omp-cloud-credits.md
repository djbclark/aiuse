# omp: Claude cloud-session credits

Related reading: [`AGENTS.md`](../AGENTS.md) · [`README.md`](../README.md) ·
[`docs/provider-identity.md`](provider-identity.md)

claude.ai's Settings → Usage page shows an expiring promotional dollar credit
("Cloud session credits … $249 of $250 left, expires November 5"). The credit
applies to cloud sessions and to the **omp** TUI, which runs Claude through the
Claude API. It is not part of the Claude 5h/weekly plan windows, so aiuse
reports it as its own provider, `omp` (display name `omp`, CLI binary `omp`).

## Source

CodexBar already reads it: `codexbar usage --provider claude` carries it as
`usage.details[].rows[]` with `id == "claude-cloud-credits"` (`usageValue` =
remaining dollars, `progress.total` = grant, `secondaryValue` =
`Expires <UTC ISO>`). Upstream documents the underlying field as
`iguana_necktie` in the claude.ai/OAuth usage response
([CodexBar docs/claude.md](https://github.com/steipete/CodexBar/blob/main/docs/claude.md#cloud-session-credits)).
No extra login or request is needed.

## How aiuse models it

`collectors/codexbar.py:_omp_credits_from_row` derives a separate `omp`
`AccountUsage` from the Claude row (the same pattern as OpenCode Zen from the
OpenCode Go row):

1. One window, label `credits`, `used`/`remaining` percent of the grant, and
   `resets_at` = the **expiry**. The credit does not refill, so the unused
   balance is use-it-or-lose-it by that date. `window_minutes` stays unknown
   (the grant date is not reported), so there is no pace figure.
2. `balance_usd` carries the remaining dollars.
3. The window is a `subscription_window` billing kind; with no grant total it
   falls back to a plain `prepaid_balance`.

## Cache caveat

CodexBar's widget snapshot (aiuse's default cached path) has no credit data.
When the cache serves a Claude row, `_live_omp_supplement` makes one live
`codexbar usage --provider claude` (through the usual query gate and hang
backoff) just for this row. Failure drops the `omp` row, not the cached rows.
Disable with `[disabled_services] omp = "..."`.
