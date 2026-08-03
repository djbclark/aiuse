# Claude Code local usage data (stats-cache, JSONL, ccusage)

**Researched:** 2026-07-23  
**Related:** [`cswap-reliability.md`](cswap-reliability.md) (subscription 5h/7d % via OAuth), [`../AGENTS.md`](../AGENTS.md)

## Short answer

Local Claude Code files and tools like **ccusage** are real and useful, but they
measure a **different quantity** than the website / `ai` report bars:

| Quantity        | Website “Current session / All models”                          | Local JSONL / `stats-cache` / ccusage                                    |
| --------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| What it is      | Subscription **utilization %** of Pro/Max windows (5h + weekly) | **Token counts** (and API-priced $ estimates) from turns on this machine |
| Source of truth | Anthropic OAuth usage API (`/api/oauth/usage`)                  | Files under `~/.claude/`                                                 |
| Multi-account   | One browser login                                               | One **active** Claude Code home (`~/.claude`); no per-email split        |
| Cross-device    | Includes other devices / claude.ai                              | **This machine only**                                                    |

For use-it-or-lose-it **quota %**, keep **cswap (+ cache hydrate) / CodexBar /
tokscale**. For “what burned tokens today / estimated API cost / 5h session
blocks of _local_ activity,” use local files or ccusage (bun/npm/homebrew all fine).

The claim “there is no native REST API for Pro/Max subscription usage” is **not
accurate for OAuth users**: cswap and CodexBar call
`https://api.anthropic.com/api/oauth/usage` with the Claude Code access token.
That is the same class of data the website Settings → Usage bars show (validated
against the site for both of our accounts). What _is_ true: there is no
documented public Console REST API that returns those bars for arbitrary API-key
billing without that OAuth session.

Upstream gap (still open as of research): Claude Code issue
[#21943](https://github.com/anthropics/claude-code/issues/21943) asks Anthropic
to write subscription bars into a local file or expose `/usage --json` — because
`stats-cache.json` tracks tokens but **not** quota %.

## What lives on disk (this machine)

### `~/.claude/stats-cache.json`

Present, small (~5–6 KB), schema version 4. Example fields:

- `lastComputedDate` — e.g. yesterday (can lag “today”)
- `dailyActivity[]` — `messageCount`, `sessionCount`, `toolCallCount` by date
- `dailyModelTokens[]` — coarse token totals by model/day
- `modelUsage{}` — lifetime-ish counters: `inputTokens`, `outputTokens`,
  `cacheReadInputTokens`, `cacheCreationInputTokens`, `costUSD` (often 0 on
  subscription)
- `totalSessions`, `totalMessages`, `hourCounts`, …

**No** `usedPercent`, `resetsAt`, or 5h/7d windows.

### Session JSONL under `~/.claude/projects/…/*.jsonl`

Hundreds of files. Assistant turns carry nested `message.usage` shaped like
API usage (`input_tokens`, `output_tokens`, cache create/read, …). Good for
reconstructing burn. No subscription %.

### `~/.claude.json` → `oauthAccount`

Identity for the **currently active** login (email, org UUID, billing type).
Matches cswap’s active slot when you last switched. Rate-limit _tier labels_
may appear; not live utilization bars.

### cswap’s cache (separate)

`~/.claude-swap-backup/cache/usage.json` — **does** store last-known 5h/7d %
from the OAuth usage API. That is subscription data, not JSONL parsing. See
[`cswap-reliability.md`](cswap-reliability.md).

## Community tool: ccusage

Already on PATH here: **ccusage 20.0.18** (Homebrew bottle; also runnable via
npm/bun if preferred).

Useful commands:

```bash
ccusage claude daily --json
ccusage claude monthly --json
ccusage blocks --json          # 5-hour *local activity* blocks (not plan %)
ccusage blocks --active --json
```

Observed on this host:

- `daily --json` returns large **token** and **estimated costUSD** totals
  (includes cache-read volume; costs are API-style estimates, not Pro plan $).
- `blocks --active` returned empty at research time (no active local block).
- Statusline mode expects hook stdin (not a standalone quota probe).

ccusage does **not** output “74% weekly / 100% weekly” as the website does.

Claude Code’s own `/usage` docs note that plan-limit breakdowns are approximate
and **local-session-history-based** for some breakdowns, while the plan bars
themselves come from the usage endpoint (with last-known bars if the endpoint
is rate-limited) — same split we see between OAuth % and JSONL tokens.

## How this fits `ai`

| Goal                                                         | Prefer                                                              |
| ------------------------------------------------------------ | ------------------------------------------------------------------- |
| Multi-account Claude Code 5h/7d % (use-or-lose)              | **cswap** (+ local usage-cache hydrate, CodexBar/tokscale fallback) |
| Cross-check active account vs website                        | CodexBar / tokscale / OAuth usage                                   |
| “How hard did I burn tokens today?” / model mix / fake API $ | **ccusage** or parse `stats-cache` / JSONL                          |
| Prepaid extra-usage $ on website                             | Separate (site credits; cswap `spend` when present) — not ccusage   |

Optional future work (only if product wants **activity** metrics alongside quota):

1. Shell out to `ccusage claude daily --json` (or `bunx ccusage@…`) as a
   secondary collector for notes / a “local burn” section — not as
   `used_percent` on subscription windows.
2. Or parse `stats-cache.json` in pure Python (no bun) for a lighter daily
   summary; same semantic limits.
3. Do **not** replace cswap with ccusage for alert ordering: token totals do
   not map cleanly to plan utilization (cache tokens, other devices, web vs
   Code, opaque plan weighting).

## Bun

Fine if we add a JS helper or pin `bunx ccusage`. Not required: Homebrew
`ccusage` is already a native binary, and Python can read `stats-cache.json`
without a JS runtime. Choose based on whether we want ccusage’s JSONL
aggregation or a thin cache file reader.
