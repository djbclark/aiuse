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
2. `balance_usd` carries the remaining dollars and `spent_usd` the dollars
   spent (CodexBar's `progress.used`, else grant minus remaining). `spent_usd`
   is deliberately not `usage_credits`, which means an overage wallet.
3. The window is a `subscription_window` billing kind; with no grant total it
   falls back to a plain `prepaid_balance` (still with `spent_usd` when
   CodexBar reports `progress.used`).

## Where it shows

1. **Board / matrix** (`aiuse`, `aiuse watch`): one `omp` row, SCOPE `claude`
   (pool key `omp_claude`), the credits percentage, and OTHER
   `$0.73/$249.27` = dollars spent / dollars left.
2. **`aiuse --json`** (schema 1.3, additive 2026-10-15):

   ```json
   {
     "provider": "omp",
     "cli_binary": "omp",
     "billing_kind": "subscription_window",
     "balance_usd": 249.269521,
     "spent_usd": 0.730479,
     "windows": [
       {
         "label": "credits",
         "window_id": "claude-cloud-credits",
         "remaining_percent": 99.71,
         "resets_at": "2026-11-05T07:59:00+00:00",
         "refill_capacity": 250.0,
         "refill_capacity_unit": "usd",
         "pool_family": "claude"
       }
     ]
   }
   ```

   `resets_at` is the expiry and `refill_capacity` the grant size. The
   `--available` entry carries `spent_usd` / `balance_usd`, and the summary
   line reads:

   ```text
   omp claude: credits: ok (<1% used / 100% left, $0.73 spent / $249.27 left of $250.00 expiring grant, expires Thu Nov 5 2:59am)
   ```

   Contract: [`json-contract.md`](json-contract.md#omp--an-expiring-dollar-grant).

3. **`aiuse --for-chat`**:

   ```text
   🟢 **omp · credits**
      ↳ `100% left · $0.73 spent · $249.27 left of $250.00 · expires in 20d 20h`
   ```

4. **`aiuse usage-sources`** lists `omp` with its one quota source, `codexbar`.

## Cache caveat

CodexBar's widget snapshot (aiuse's default cached path) has no credit data.
When the cache serves a Claude row, `_live_omp_supplement` makes one live
`codexbar usage --provider claude` (through the usual query gate and hang
backoff) just for this row. Failure drops the `omp` row, not the cached rows.

## Disabling

`[disabled_services] omp = "<reason>"` drops the row everywhere and skips the
live CodexBar query above.
