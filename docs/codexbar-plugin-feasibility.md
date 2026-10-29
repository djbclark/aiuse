# CodexBar plugin feasibility (2026-10-29)

**Verdict:** do not move aiuse into a CodexBar plugin. A plugin is one
sandboxed JavaScript file that fetches a single provider and returns one
generic usage snapshot. aiuse is the layer that picks among sources, ranks
what to burn, and speaks the agent contract. That layer cannot live inside
the plugin host.

Checked against CodexBar **0.82.0** (`origin/main` `5f93dc306`, "start 0.82.1
development"; the installed app and `/opt/homebrew/bin/codexbar` both print
0.82.0). The long-lived checkout `~/src/CodexBar` was left on
`fix/antigravity-widget-row-window` (one local commit, 184 commits behind
`origin/main`). Current source is the detached worktree
`~/src/CodexBar-main`.

## What a plugin is allowed to be

Source: [CodexBar `docs/plugins.md`](https://github.com/steipete/CodexBar/blob/main/docs/plugins.md)
on `origin/main`, and the same file in `~/src/CodexBar-main`. The accepted
design boundary is
[custom-provider-design.md](https://github.com/steipete/CodexBar/blob/main/docs/custom-provider-design.md)
(issue
[steipete/CodexBar#1735](https://github.com/steipete/CodexBar/issues/1735)):
the JavaScript path replaced the declarative JSON MVP, and the threat model
still binds.

A user plugin is one `.js` or `.ts` file in `~/.config/codexbar/providers/`,
or installed from Settings. There is no plugin catalog and no import
resolution. The file calls `defineProvider` once. `fetchUsage(ctx)` may use
only the host API.

Hard limits that matter here:

| Limit                                                                                                           | Why it blocks an aiuse port                                                                                      |
| --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| No Node, subprocesses, local files, databases, OAuth, WebViews, or native APIs                                  | Cannot run `cswap`, `tokscale`, `paseo`, `grok`, or read `~/.omp/logs`, acp-run JSONL, or a local SQLite history |
| Network only to 1–16 declared HTTPS origins (loopback / private HTTP only with a typed approval)                | Cannot discover providers, and cannot call whatever binary the operator installed                                |
| User-plugin cookies are Chrome-only, and only for declared domains                                              | Bundled providers keep their own browser order and local credential files                                        |
| Plugin `id` must not match a built-in provider                                                                  | A plugin cannot patch Claude, Grok, Cursor, or OpenCode                                                          |
| One snapshot: primary / secondary / tertiary / extra windows, one cost object, identity, details                | No cross-provider board, no source arbitration, no second reading of a built-in                                  |
| Source ≤ 1 MiB, HTTP response ≤ 1 MiB, persistent storage ≤ 64 KiB and 64 keys                                  | No snapshot history, learned burn rates, or rate-gate ledger                                                     |
| Excluded from widgets, status feeds, token accounts, OAuth, local cost scanners, and provider-specific payloads | The menu card is generic. Pace on a detail row is the only pace hook                                             |
| `codexbar plugins fetch` is app/CLI host behavior, not a plugin-defined subcommand                              | No `--available`, `note-exhausted`, `attribute`, `gate`, `serve`, or `watch`                                     |

`ctx.http` is GET/POST JSON or text to an approved origin. Redirects are
rejected. The host owns the auth header. `ctx.browser.cookieHeader` can
return a Chrome cookie header for a declared domain. That is the whole
local-machine capability.

## What CodexBar 0.82 already covers

`UsageProvider` in
`Sources/CodexBarCore/Providers/Providers.swift` has **110** cases, matching
the README's provider count. The installed CLI is the same release. aiuse's
native collectors mostly re-read sources this enum already has:

| aiuse collector                                                                                  | CodexBar 0.82                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `codexbar`                                                                                       | The app itself. `codexbar usage --format json` is already aiuse's preferred non-Claude source.                                                                                                                                                                                                                                                               |
| `cswap` (Claude, multi-account)                                                                  | Built-in `claude` (OAuth, cookies, or CLI). This machine keeps that source off because of Keychain prompts and uses cswap instead. A plugin cannot shell out to cswap, and it cannot replace the built-in id.                                                                                                                                                |
| `grok_billing`                                                                                   | `GrokCreditsProxyFetcher` calls `GET https://cli-chat-proxy.grok.com/v1/billing?format=credits` and maps `prepaidBalanceUSD`. [`docs/grok-quota.md`](grok-quota.md) still says the CodexBar proxy path cannot see Extra Usage Credits. That was true of an older export; it is not true of 0.82.                                                             |
| `opencode_zen`                                                                                   | OpenCode and OpenCode Go both read `GET https://opencode.ai/console/api/billing/status` (`balanceMicroCents`). The 250 ms `optionalZenBalanceJoinGrace` is still in `OpenCodeGoZenBalanceFetcher.swift`, so a slow optional join can still drop the balance on that path. The native collector remains a useful second client. It is not a missing provider. |
| `opencode_go`, `qwencloud`, `bailian` (`alibaba`), `clinepass`, `openrouter`, `deepseek`, `muse` | Matching built-ins (`opencodego`, `qwencloud`, `alibaba` / `alibabatokenplan`, `clinepass`, `openrouter`, `deepseek`, `muse`).                                                                                                                                                                                                                               |
| Cursor pools, including grok-bot                                                                 | `docs/cursor.md`: usage-summary plan and on-demand, Total / Cursor / Third Party bars, and `get-sand-usage-status` for Grok Bot weekly.                                                                                                                                                                                                                      |
| `tokscale` live quota                                                                            | Copilot and the Cursor pools above are native. CodexBar also vendors tokscale-shaped local cost readers. `tokscale usage --json` is still a separate cross-check, not a missing meter.                                                                                                                                                                       |
| `caut`, `caam`, `openusage_ai`, `openusage_sh`                                                   | Deprecated inside aiuse for this same overlap.                                                                                                                                                                                                                                                                                                               |

CodexBar also already shows reset countdowns, a monthly "daily target" pace
(0.80), Codex/Claude plan burndown, local cost scans, and `codexbar serve`.
Those overlap aiuse's burn display. They do not implement aiuse's
cross-provider rank or the schema 1.4 agent payload.

## What aiuse still does that a plugin cannot

These are the behaviors that are not a CodexBar provider. Each one needs a
capability the host withholds.

1. **Source arbitration.** `collectors/runner.py` keeps every successful
   reading, applies `PROVIDER_SOURCE_PRIORITY`, and emits `cross_checks`.
   A plugin cannot see another provider's snapshot.
2. **Agent contract.** [`docs/json-contract.md`](json-contract.md):
   `aiuse --json` schema 1.4, `aiuse --available`, `aiuse note-exhausted`,
   `aiuse attribute`, `aiuse serve`. Plugins do not add CLI verbs.
3. **omp grant and the shared Claude rate gate.** `aiuse set-credits` stores
   the expiring dollar grant. `rate_gates.py` latches on Anthropic 429s
   scanned from `~/.omp/logs`. CodexBar's `pi` provider is local Pi/OMP
   transcript cost (API-rate estimates, no subscription quota). Same
   letters, different meter. A plugin cannot read those logs, and it cannot
   attach `rate_gated_by` to the built-in Claude row.
4. **Paseo.** `paseo usage ls --json` is one local daemon call. No
   subprocess, so no plugin.
5. **ACP context fill.** `acp_usage.py` reads `~/.local/state/acp-run/`
   JSONL. The reading is context occupancy, and pace must not treat it as
   plan percent. Local files plus a non-quota shape.
6. **Watch matrix, hidden pools, learned burn.** The TUI, pool-family
   routing keys, and history-learned rates sit on the snapshot ledger.
   Plugin storage is 64 KiB of strings. Rendering stays a generic card.
7. **SIPB.** `https://llms-dev-1.mit.edu/api` plus a SecretSpec key is the
   only collector whose transport matches the host (HTTPS, key in a secure
   setting). The reading is an up/down probe with no burn meter. A plugin
   could show a detail row. It would not carry any of the rest of aiuse,
   and the key would have to be pasted into CodexBar instead of SecretSpec.

## What would be feasible, and why it is the wrong project

| Move                                                                                        | Fits the plugin API?                     | Worth doing?                                                                                                                                                                                                         |
| ------------------------------------------------------------------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| One new HTTPS-only provider CodexBar lacks, as a local plugin or an upstream bundled script | Yes                                      | Not for the current aiuse collector set. The quota-shaped ones are already built-ins. SIPB is the only transport match, and it is a status probe.                                                                    |
| Reimplement cswap, paseo, tokscale, ACP logs, or the omp latch as a user plugin             | No                                       | Needs subprocess or filesystem access the threat model rejects.                                                                                                                                                      |
| Teach a plugin to merge CodexBar's other providers into an `--available` board              | No                                       | Plugins are isolated providers. They do not receive the catalog.                                                                                                                                                     |
| Port the agent contract into CodexBar core (Swift CLI, not a plugin)                        | The host could, as a first-party feature | Duplicates aiuse, fights the "changes to non-aiuse code go upstream" rule for a product that is already the consumer of `codexbar usage --format json`, and still needs cswap and the omp log scan living somewhere. |
| Keep aiuse as the aggregator over CodexBar plus the few local tools CodexBar will not grow  | This is the current design               | Yes                                                                                                                                                                                                                  |

## Recommendation

Leave the split where it is. CodexBar owns per-provider fetch, menu bar, and
`usage --format json`. aiuse owns selection, burn ranking, and the agent
schema. When a future provider is a plain HTTPS quota API, add it as a
CodexBar plugin or an upstream provider PR, then let the existing `codexbar`
collector pick it up. Do not start a plugin whose job is "the rest of
aiuse."

Stale notes to fix only if someone is already editing them:
[`docs/grok-quota.md`](grok-quota.md) (Extra Usage Credits are in CodexBar
0.82) and the OpenCode Zen paragraph in
[`docs/source-coverage.md`](source-coverage.md) (the provider reads the
balance; the optional 250 ms join grace is the remaining race).
