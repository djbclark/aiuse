# omp: Claude cloud-session credits

Related reading: [`AGENTS.md`](../AGENTS.md) · [`README.md`](../README.md) ·
[`docs/provider-identity.md`](provider-identity.md)

claude.ai's Settings → Usage page shows an expiring promotional dollar credit
("Cloud session credits … $249 of $250 left, expires November 5"). The credit
applies to cloud sessions and to the **omp** TUI, which runs Claude through the
Claude API. It is not part of the Claude 5h/weekly plan windows, so aiuse
reports it as its own provider, `omp` (display name `omp`, CLI binary `omp`).

## Source

Reading evolution (the intro's $250/Nov 5 figures were the first grant):

1. **CodexBar claude row** (`claude-cloud-credits`, field `iguana_necktie`) —
   the original 2026-10-15 source, still used live.
2. **Anthropic Admin API `cost_report`** — since #40 (2026-10-20) an hourly
   refresh job reconciles daily spend buckets against the operator's console
   reading (`aiuse set-credits omp`), so the row reflects the **current**
   promotional grant ($200 granted 2026-10-13, expires 2026-11-08, applies to
   Agent SDK / API / Batch / Playground / Managed Agents) rather than a stale
   grant snapshot. Buckets that ended before the operator's reading are
   already reflected in it (including purchased-credit spend); only
   post-reading spend decrements the balance.

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

## Rate-window gate (schema 1.4, 2026-10-21)

The grant is a **billing** pool, not a rate-limit exemption. omp spends Claude
through the account's Claude API access, so the account's shared multi-day
quota window rate-limits it independently of dollars left. Observed
2026-10-21: with the Claude weekly 100% used, Anthropic answered every omp
request (`claude-opus-5-5`, `claude-fable-5-1`, even the usage fetch) with
`429 "This request would exceed your account's rate limit"` and
`retry-after` equal to the weekly reset (~44h out), while most of the credit
grant remained. omp itself blocks its credential until that timestamp
(`tier:fable` and whole-credential scopes in `agent.db`).

aiuse fails closed for that state (`enrich_snapshot`,
[`json-contract.md`](json-contract.md#whats-new-in-schema-14-2026-10-21)):

1. When every Claude account with observed shared-window data shows an
   exhausted weekly/monthly window with a future reset, omp accounts get
   `rate_gated_by` (`{provider, account, window_label, resets_at, evidence}`),
   `usable_now: false`, `available_at` = the gate reset, and a note; the
   summary line says `credits: GATED` instead of `credits: ok`, then
   `-> NOT usable now [rate-gated: …]`, and `--available` omits omp. The
   window's JSON `state` stays `ok`. The gate clears itself once the reset
   passes. The clock matrix
   (plain `aiuse`, watch, and the inline TUI) draws the same row as `empty`
   with no spendable percent: `rate-gated until <reset> · $<left> left`.
   Dollars stay in the note so the grant is still visible.
2. Confirmed again the same day, 13:06 EDT: omp still looked like a normal
   `mid` row (48% left, `$95.57` in OTHER) and a live call returned
   `429` `rate_limit_error` with `retry-after-ms=147469000` (~41h), which
   lands on the weekly reset Fri Oct 23 6:00am. That is the same block the
   gate already computed; the matrix was the surface that still said usable.
3. Positive evidence only: missing Claude data never gates (a cswap outage
   must not corrupt omp's own reporting — operator rule, 2026-10-21), 5h
   windows never gate (the observed 429 pins the block to the weekly reset),
   and an exhausted model sublimit (Fable) gates only that model class, never
   the account.
4. From **3.9.3**, `save_snapshot` folds the admin-api credit reading into
   `latest.json`. CodexBar can omit the omp row without a collector error, and
   that cache file is what the sampler writes. `--available` already
   re-applied the reading at display time. A partial sample that did not ask
   for omp does not gain the row.

## Durable latch (2026-10-22, after the fifth regression)

The five fixes above (8373bd5, db4a6e9, c65e51a, 180a79f, 018bcfc) were all
display-layer: each re-derived the gate from whatever Claude quota data
happened to be present, so every input blink — the omp row vanishing, cswap
going stale, one headroom reading, the watch's `overrides or gate`
short-circuit — silently ungated omp again while Anthropic was still
refusing. The latch ends the class:

1. **Evidence is recorded when observed** to
   `~/.local/state/aiuse/rate-gates.json` (env `AIUSE_RATE_GATE_STORE`):
   either the exhausted shared window above (`claude-shared-window`), or an
   actual Anthropic 429 with its `retry-after` header parsed from omp's own
   logs (`omp-429-anthropic` — Anthropic's own statement of when the account
   can spend again; every 2026-10-21..22 retry-after landed exactly on the
   weekly reset). `save_snapshot` and `aiuse gate status` ingest the logs;
   ingestion is idempotent by request id.
2. **Fail closed until expiry.** Recorded evidence keeps omp gated until its
   own `resets_at` (+120s grace), whatever later readings say. Missing Claude
   rows, stale cswap data, and headroom blinks can no longer ungate a
   recorded gate. Deadlines come from event time (`response timestamp +
retry-after`), spans over 32 days are rejected (never clamped), and
   replaying an old log after a clear stays cleared (tombstone
   `cleared_through`). Only expiry or `aiuse gate clear omp` lifts a latch.
3. **Surfaces.** While a latch (not live derivation) holds the gate,
   `window_label` is `shared Claude window (latched)` and `evidence` names
   the latch source. `aiuse gate status [--json]` (exit 1 while gated, 0
   when nothing is latched) is the one-call machine surface for spend-path
   tools — the π/omp TUI itself never consults aiuse, so until omp learns to
   check (upstream PR is the long-term fix), a human or wrapper deciding to
   spend omp should check it first. `aiuse gate clear omp` is the operator
   escape hatch.

Design reviewed by codex gpt-6-astra on 2026-10-22 (its full recommendation:
persist scoped evidence records rather than one irreversible deadline, prefer
`$XDG_STATE_HOME` over cache, tombstone clears, reject-and-quarantine
implausible deadlines instead of clamping, keep the credits window `state`
as dollars, exit-code semantics 0/1/2 for a future `gate-status` probe, and
the end-state `prepare_snapshot` boundary unifying all surfaces — recorded
here for the follow-ups). Known follow-up: serve's cached payloads and
suggestions predate gate evaluation and can briefly disagree
([`docs/next-options.md`](next-options.md)).

## Operator gate (historical; lifted 2026-10-19)

The operator **lifted this gate on 2026-10-19** (commit 8a9dc43): the Claude
Max plan's API credits are a normal tier and no account carries
`requires_operator_approval` today. The section below records how the gate
behaved between 2026-10-15 and 2026-10-19, and the dispatch-side approval
mechanism still exists for future gates.

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

aiuse itself does not enforce the gate: the row still ranks and is still listed
by `--available`; the flag tells agents to ask. Enforcement lives in the agent
toolchain (djbclark-ade, commits 171980a and f3ffe98): `acp-run` and
`acp-dispatch` refuse to run an omp slice unless `ACP_OPERATOR_APPROVED=omp` is
set for that call, which an agent does only after the operator says yes for that
run. `bigteam`, `model-routing` and the global `home-agents.md` item 26 carry the
same rule. Running the `omp` TUI directly is not intercepted. The operator may
later make the gate configurable per provider; today it is fixed on omp.

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
