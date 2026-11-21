# Antigravity independent pools (Gemini vs Claude/GPT)

**Symptom:** `aiuse` listed Google AI / Antigravity once (one ladder row / one
governing window) even though the product has **two hard-separated budgets**.

## Two different “Claude” products (do not merge)

| What you see                                       | Who sells it                                                  | How `aiuse` gets it                                                                | Provider id                               |
| -------------------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------- |
| **Claude Code** (5h + weekly)                      | **Anthropic** (Claude Pro/Max, etc.)                          | **cswap** (canonical multi-account), plus caut / OpenUsage / CodexBar cross-checks | `claude`                                  |
| **Gemini** + **Claude/GPT** bars under Antigravity | **Google** (Google AI Pro/Ultra via Antigravity / Gemini CLI) | CodexBar / OpenUsage `antigravity`                                                 | `antigravity` (config plans key `gemini`) |

Google’s Antigravity subscription can include allotments for **Gemini models** and a
separate allotment for **third-party models** (labeled Claude/GPT in CodexBar and
OpenUsage). That Google-sold Claude/GPT pool is **not** the same wallet as
Anthropic Claude via cswap — even when the email matches (e.g. both show
`you@gmail.com`). Ladder rows keep them distinct:

- `Claude Code · you@gmail.com · Claude Code weekly…` → Anthropic / cswap
- `Google AI / Antigravity · you@gmail.com · Claude/GPT weekly…` → Google’s
  non-Gemini pool
- `Google AI / Antigravity · you@gmail.com · Gemini weekly…` → Google Gemini

Never fold Antigravity Claude/GPT into `provider=claude` scoring, history keys,
or cswap multi-account logic.

## Cause

CodexBar (and OpenUsage, when its payload includes them) expose these
Antigravity windows:

| Window            | Family                                                       |
| ----------------- | ------------------------------------------------------------ |
| Gemini 5-hour     | Gemini (Google)                                              |
| Gemini weekly     | Gemini (Google)                                              |
| Claude/GPT 5-hour | Google non-Gemini (Claude/GPT via Google AI — **not** cswap) |
| Claude/GPT weekly | Google non-Gemini                                            |

CodexBar also sends a second window titled "Gemini weekly", id
`antigravity-quota-summary-gemini-argon-wtu-weekly`. That is the individual
quota the agy client enforces ("Individual quota reached… refreshes in 4 days,
14 hours"). It shares the Gemini pool with the other Gemini weekly. When it is
exhausted it governs the pool, so the row shows that window's reset rather than
the looser Gemini weekly beside it. On 2026-10-22 it was 100% used with reset
2026-10-27T02:35:35Z, and OpenUsage's `GET /v1/limits` omitted every Gemini
resource (`geminiSession`, `geminiWeekly`, and this individual window). A fresh
OpenUsage row must not replace CodexBar while that omission stands: freshness
only demotes a source that covers the same independent pools. The same gap is
[OpenUsage issue 1422](https://github.com/robinebers/openusage/issues/1422);
the proposed fix in pull request 1423 was closed without merging.

Within each family, 5h ⊂ weekly (shared allotment). **Across** families the
budgets do not draw from each other — burning Gemini does not free Claude/GPT
quota and vice versa. Neither pool is Anthropic’s Claude Code subscription.

`shared_allotment: true` on the `gemini` config key used to run
`governing_partition` over **all** windows on the account, so only one weekly
survived as the sole scored/listed pool. The priority ladder also keyed coverage
by `(provider, account)` only, so a single Antigravity row covered both families.

## What `aiuse` does

1. **`independent_pool_key` / `partition_independent_pools`** (`analysis/pace.py`)
   group windows by label markers (`Gemini…` vs `Claude/GPT…` /
   `nonGemini…`).
2. **Shared allotment** runs **per pool**: Gemini weekly governs Gemini 5h;
   Claude/GPT weekly governs Claude/GPT 5h; both weeklies can alert.
3. **Priority ladder** emits **one row per pool** and tracks coverage with a
   pool id so one family’s alert does not hide the other.

Cursor total/auto/third-party stay distinct families (third-party is separate via
`independent_pool_key`); Claude 5h⊂weekly stays a single pool (no family
markers).

## Verify

```bash
codexbar usage --provider antigravity --format json --no-color
aiuse --no-tui -q
```

Expect two Antigravity lines (Gemini weekly and Claude/GPT weekly, or alerts
for each when off-pace), not a single combined row. Both must print under the
same provider name — two pools of one subscription, not two vendors. See
[`provider-identity.md`](provider-identity.md) for why they once did not, and
for the canonical-id rule that keeps them together.

## Freshness (investigated 2026-10-03)

aiuse queries Antigravity at most once per 15 minutes (see "Query rate
limit" below); within that window a collect reuses the last
`codexbar usage --format json --provider antigravity` payload, and otherwise
the freshness of an Antigravity row is whatever CodexBar serves. CodexBar rows
carry their own measurement time (`usage.updatedAt`, one per provider row);
since schema 1.1 aiuse surfaces it as `collected_at` on the account with a
computed `age_seconds`, so "this Claude/GPT number is 40 minutes old" is
visible in `--json` itself.

Can `Claude/GPT 5-hour` (or weekly) lag a real exhaustion? Three distinct cases:

1. **Genuinely open, then drained** — the 2026-10-03 incident: the pool read
   0% used at probe time and one Opus-high review exhausted it ~35 minutes
   later. The number was correct both times; the conclusion "agy is fine"
   just had a short shelf life. Countermeasures now in the data:
   `age_seconds`/`fresh` on every read, and `aiuse note-exhausted
antigravity --family claude_gpt …` so a 429 one agent sees suppresses the
   pool for the next agent until the stated reset.
2. **CodexBar serving a stale row** — `codexbar usage` can reflect its last
   menu-bar refresh rather than a live API read. That is exactly what the
   per-account `collected_at` (from `usage.updatedAt`) exposes: a row older
   than the snapshot's `collected_at` was a cached read. Treat `fresh: false`
   Antigravity rows as unproven before routing an expensive task.
3. **Upstream frozen percent (2026-10-18)** — OpenUsage `nonGeminiWeekly` and
   CodexBar both held **identical** Claude/GPT weekly used%
   (`59.054479002952576` / rounded `59.0`) from 12:01Z through 13:45Z while
   work continued. A cache read at 13:48Z still showed 41% left and
   `fresh: true`. At 13:55Z agy returned `Individual quota reached` /
   `QUOTA_EXHAUSTED` with `quotaResetTimeStamp:2026-10-24T18:18:05Z` (the same
   weekly clock); by 14:00Z CodexBar reported weekly 100% used. This is
   Google's quota reading lagging, not an aiuse percent inversion. Fail-safes
   in aiuse: (a) `measurement_frozen` when the same used% spans ≥45 minutes of
   snapshot history **and** an acp-run turn spent that pool family during the
   flat span (claude-_/gpt-_ → `claude_gpt`; gemini-* does not flag Claude/GPT)
   — idle flats stay routable; `--available` drops only the spent-but-stuck
   pool; (b) recent acp-run logs with a multi-day Individual/QUOTA_EXHAUSTED
   refusal are auto-persisted as agent notes (same effect as `note-exhausted`).
   Prefer a fresh 429 / agent note over a cached "ok" percent.

## Query rate limit (2026-10-05)

agy answers quota probes in quick succession with 429s on every model, the
same burst limit that stops real work. So aiuse reads Antigravity's quota **at
most once every 15 minutes, across every aiuse process at once**: the
interactive CLI (including `--available --live`), the `aiuse sample`
LaunchAgent, `aiuse watch`, and `aiuse serve`.

How it works (`src/aiuse/collectors/throttle.py`):

1. Every live query of a gated provider takes an exclusive `flock` on
   `~/.cache/aiuse/query-throttle/antigravity.lock`, then reads
   `antigravity.json` (`queried_at`, `queried_by`, and the last payload per
   source). A second process that arrives mid-query waits, then sees the new
   `queried_at` instead of firing its own query.
2. **CodexBar:** `codexbar usage --provider antigravity` runs only when the last
   query is ≥ 15 minutes old. Otherwise the stored payload is reused; the
   account carries a note ("antigravity quota queried at most every 15m … reused
   CodexBar's result from …") and its `collected_at`/`age_seconds` still come
   from CodexBar's own `updatedAt`. A failed query (e.g. a 429) counts as a
   query; the error is repeated until the window passes.
3. **OpenUsage.ai:** a forced CLI refresh (`openusage --force`) re-queries every
   provider OpenUsage has enabled, so while Antigravity is throttled aiuse
   withholds `--force` and takes OpenUsage's cached reading. A forced refresh
   only spends the slot when its payload actually contains `antigravity`. The
   loopback HTTP path never forces a refresh and is not gated.
4. Configure with `[query_min_interval]` in `config.toml`
   (`antigravity = 900` is the default; `0` disables; other provider ids can
   be added).
5. **Cheap source first** ([#34](https://github.com/djbclark/aiuse/issues/34),
   2026-10-09). CodexBar's default `auto` source for antigravity reads the agy
   CLI, which spawns agy three times per probe and boots its language server
   each time. aiuse now passes `--source oauth` first (stored Google auth,
   spawns nothing) and falls back to `auto` only when that returns nothing
   usable.
6. **Empty-answer backoff** (#34). The gate counts consecutive live answers
   with no usable data (`empty_streak` in `antigravity.json`). From the third
   in a row the interval is multiplied by 4 per further empty answer (15m →
   1h), capped at 1h, and the reuse note says so ("stretched to 1h00m
   after 3 answers in a row with no usable data"). One usable answer resets
   it. OpenUsage's forced refresh does not touch the streak. This stops a
   logged-out agy from being launched every 15 minutes indefinitely. The cap
   was 6h until 2026-11-10: an offline placeholder stayed on the board ~5h
   after CodExBar recovered (the operator re-enabled antigravity in CodExBar,
   so its app polls on its own clock and had fresh data), and the row showed
   nothing until the latch expired — 1h bounds that blindness while still
   probing a dead client at most hourly.

Not covered, and why:

1. **CodexBar.app / OpenUsage.app polling on their own.** The menu-bar apps
   refresh on their own clocks (CodexBar `refreshFrequency`, global across
   providers, 5 minutes on this machine). As of 2026-10-05 Antigravity is
   **disabled** in CodexBar (`codexbar config providers`, shared by the app and
   the CLI) and absent from the running OpenUsage build's enabled providers, so
   neither app polls agy. Re-enabling it in CodexBar makes the app poll every
   `refreshFrequency` regardless of aiuse; set that to 15 minutes or more first.
2. **CodexBar bundled calls.** `collectors.codexbar.providers = "all"` (or the
   fallback when `codexbar config providers` fails) asks CodexBar for every
   provider in one call, which cannot be gated per provider. The default
   `"enabled"` path queries each provider separately and is gated.
3. **Using agy itself.** Interactive or delegated agy work is not a quota
   query; see the agy burst-limit rules in the home `AGENTS.md`.

## CLI burst lockout signal (2026-10-09, #33)

On 2026-10-03, the agy CLI returned 429 on every request while the quota
windows showed headroom and the then-used ACP client kept working. Clients
share subscription quota, but a client's burst lockout is a separate signal.
aiuse reads the CLI's own logs, passively, at report time and attaches
`client_limits` (`client: cli`, `state: limited`) to the antigravity account
and its routing entries. `usable_now` stays quota-based. The summary line
requires fresh quota and client checks before using the configured native
provider; it does not recommend a retired client. Format, config knob and
limits: [`json-contract.md`](json-contract.md) (section `client_limits`).
aiuse never sends a probe for this.

## Widget-snapshot rows and their clocks (2026-10-17)

CodexBar's `widget-snapshot.json` lists the five Antigravity pools as
`usageRows` with a title and `percentLeft` only; the length and reset live on
the two slot blocks (`primary`/`secondary`), which are the two pools' 5-hour
_or_ weekly windows depending on what CodexBar last showed. aiuse borrows a
row's clock from a slot block only when the row's duration kind (`5h` /
`weekly` from its id or title) and pool prefix (`Gemini` / `Claude/GPT`) agree
with that block and the percents match; a row no block describes keeps its
title's nominal length and no reset. Before this rule every row at 0% matched
the first slot, so both weeklies carried 300 minutes and the 5-hour reset and
the matrix stacked all five windows in the 5H column (the cross-check line
"Claude/GPT weekly reset times differ by 9295 minutes" was the symptom). An
unused weekly's reset slides with the clock, so the empty WEEK span is honest;
an in-use one gets its reset carried forward from the last OpenUsage reading
(`history.find_carried_resets`). The live `codexbar usage --provider
antigravity` call carries full per-row windows but costs ~6.5 s, so it is not
used to patch the cache. Upstream already writes a per-row `window` block for
every measured Antigravity row since CodexBar v0.77.0
([steipete/CodexBar#4529](https://github.com/steipete/CodexBar/pull/4529),
merged 2026-10-16); aiuse reads it when present and the slot-matching rule is
the fallback for snapshots written by an older build (on 2026-10-17 the
running app was a pre-0.77.0 local build, not the installed cask).

## Capacity errors and placeholder clocks (2026-11-21)

Two things the quota windows cannot show, both checked with live calls
(`agy -p`, one per model, 12:13Z: claude-sonnet-5-5, gpt-oss-120b,
gemini-3.8-flash and gemini-4-argon all answered, and the windows moved by the
plausible 0.4 to 5 points, so the numbers track real use).

- **Capacity error.** A Pi agent got `This model is unavailable right now
(Resource has been exhausted (e.g. check quota).)` from cloudcode-pa for
  claude-sonnet-5-5 and gemini-3.8-flash at about 12:06Z while every window
  showed headroom; the same models worked seven minutes later. The text itself
  says "not necessarily an account quota wall". aiuse reads such an error from
  a Paseo agent's state file (`scan_antigravity_capacity`) and adds a
  `client_limits` entry with `kind: "capacity"`, `state: "limited"`, a 10 minute
  cooldown and the failing model names. It never marks a pool exhausted and
  never touches `usable_now`. `Individual quota reached` / `QUOTA_EXHAUSTED`
  with a multi-day reset is a different thing and still becomes an exhaustion
  note.
- **Placeholder reset.** An unused window has no real clock (it starts on first
  use), but CodexBar fills `resetsAt` with its snapshot time plus the window
  length, so a 0% Claude/GPT 5-hour window showed `4h56m` for hours and slid
  forward at every refresh. The parser drops a reset that is within two minutes
  of `updatedAt + windowMinutes` on a 0%-used window; the cell reads `5h↻`
  until a real call starts the clock (the first test call turned it into a real
  17:12:52Z reset).

## Google AI Ultra 20x ($199.99/month) and API credits (checked 2026-11-21)

The operator moved from Google AI Pro ($19.99) to Ultra 20x ($199.99/month) on 2026-11-21, and
`[plans.gemini] monthly_price` is 199.99. Google's plan table ([plans
page](https://one.google.com/intl/en/about/google-ai-plans/?hl=en-US)) lists Ultra 5x at $99.99
and Ultra 20x at $199.99, with Antigravity agent requests "Highest" on the 20x tier.

- **Included API-style credits: $100/month of Google Cloud credits** (Pro $10, Ultra 5x $40, Ultra
  20x $100), from Google Developer Program premium folded into the plan
  ([announcement](https://blog.google/innovation-and-ai/technology/developers-tools/gdp-premium-ai-pro-ultra/)).
  They must be activated in the Google Developer Program, and they are Google Cloud credits (usable
  for Gemini API / Vertex usage billed to a Cloud project), not a Gemini API key balance. Whether
  an AI Studio key draws on them is not stated; assumed no until tested.
- **Antigravity "AI credits" are not included.** They are pay-as-you-go top-ups bought through Google
  One once the baseline quota runs out, deducted at standard API prices. The 25,000/month figure on
  the plans page is Google Flow credits, not Antigravity.
- aiuse does not read either balance; the Cloud credit is a prepaid-style pool worth tracking only
  if a Cloud billing collector is added.

## Gemini 4 model lineup and per-model pools (checked 2026-11-21)

Google's [models page](https://antigravity.google/docs/models) lists Gemini 4 Neon, Carbon, Argon and
Hydrogen as the current Gemini models (Carbon is Ultra-only, Argon and Neon need paid Pro or Ultra),
with Gemini 3.8 Flash for every plan; 3.7 Flash, 3.6 Flash and 3.1 Pro are Enterprise-only, and the
Claude 4.6 and GPT-OSS entries end on 2026-11-30. The in-app usage panel shows only two meters:
"Gemini Models" and "Claude and GPT models".

Live test on the Ultra 20x plan: one `agy --model gemini-4-<name> -p=...` call each to carbon, neon,
hydrogen and argon (14:20Z) all answered. CodexBar's next read (14:32Z) showed only the shared
`Gemini 5-hour` (+1.97 points) and `Gemini weekly` (+0.68) windows moving; the Claude/GPT windows
stayed at 0 and no per-model window (no `...-gemini-argon-wtu-weekly`, no Carbon window) was reported at all.
On the Ultra plan, then, no Gemini 4 model has its own quota window; Carbon is not special, and the
`gemini_argon` family only matters if CodexBar again reports an Argon window. Since schema 1.8 the model-to-family
mapping follows that: `gemini-4-argon*` maps to `gemini_argon` only on an account whose windows include an
Argon-own window (Google AI Pro reported "Gemini 4 Argon weekly"), and to the shared `gemini` family otherwise
(`pool_family_for_acp_model(provider, model, reported_families)`). An Argon refusal note or ACP turn, recorded under
its nominal `gemini_argon` family, is applied to `gemini` on an Ultra-shaped account. Open: a single
call per model with a read between each (15 minute gate) would show whether one of them draws a different share.

Placeholder resets: the 14:32Z live read still carried `Claude/GPT 5-hour` reset 19:32:13Z, which is
the snapshot time plus 300 minutes on a 0%-used window, so the 5.3.0 drop-the-placeholder rule (widget
snapshot path only) did not cover the live `codexbar usage` path. Fixed in 5.3.2: the live parser applies
the same rule to the slots and to `extraRateWindows` (the 14:47Z read showed the weekly window too:
reset 2026-11-28T14:47:13Z, a week less one second after `updatedAt`), so both Claude/GPT windows read `↻`
until a real call starts their clock.
