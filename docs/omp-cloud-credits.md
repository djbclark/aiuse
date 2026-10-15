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
     "requires_operator_approval": true,
     "ai_notice": "AI: Do not use without asking operator",
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
   omp claude: credits: ok (<1% used / 100% left, $0.73 spent / $249.27 left of $250.00 expiring grant, expires Thu Nov 5 2:59am) — AI: Do not use without asking operator
   ```

   Contract: [`json-contract.md`](json-contract.md#omp--an-expiring-dollar-grant).

3. **`aiuse --for-chat`**:

   ```text
   🟢 **omp · credits**
      ↳ `100% left · $0.73 spent · $249.27 left of $250.00 · expires in 20d 20h`
      ↳ AI: Do not use without asking operator
   ```

4. **`aiuse usage-sources`** lists `omp` with its one quota source, `codexbar`.

## Operator gate

The operator (djbclark, 2026-10-15) gated this grant for AI agents: **an agent
must not spend it, route work to omp, or recommend burning it without asking
the operator first**, however much is left or how soon it expires. The
collector sets `AccountUsage.requires_operator_approval`; the wording lives
once, in `models.OPERATOR_APPROVAL_NOTICE`.

1. **Board / matrix**: the omp row shows `(AI: Do not use without asking
operator)`, dim, centered across the 5H and WEEK columns in place of their
   `->` arrows (so no arrow legend line comes from this row). The table widens
   5H + WEEK to fit it. A terminal too narrow for the full text shows the short
   wording `(AI: ask operator)` instead, and clamps even that only as a last
   resort. Data in 5H or WEEK would win over the notice.
2. **`aiuse --json`**: the account carries `requires_operator_approval: true`
   and `ai_notice: "AI: Do not use without asking operator"` (both omitted on
   every ungated account). `--available` routing entries and any alert raised
   for omp carry the same pair; the alert `message` (so `--alerts-only` too)
   and the summary line end with `— AI: Do not use without asking operator`.
3. **`aiuse --for-chat`**: a `↳ AI: Do not use without asking operator` line
   right under the status line, and the same suffix on an ACTION item.

aiuse does not enforce the gate: the row still ranks and is still listed by
`--available`; the flag tells agents to ask. The operator may later make the
gate configurable per provider; today it is fixed on omp.

## Cache caveat

CodexBar's widget snapshot (aiuse's default cached path) has no credit data.
When the cache serves a Claude row, `_live_omp_supplement` makes one live
`codexbar usage --provider claude` (through the usual query gate and hang
backoff) just for this row. Failure drops the `omp` row, not the cached rows.
Upstream request to carry the row in the snapshot:
[steipete/CodexBar#4546](https://github.com/steipete/CodexBar/issues/4546)
(filed 2026-10-15); drop the live call once it ships.

## Disabling

`[disabled_services] omp = "<reason>"` drops the row everywhere and skips the
live CodexBar query above.
