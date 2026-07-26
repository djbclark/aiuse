# Competitive landscape: multi-provider AI quota tools

**Date:** 2026-07-25 (refreshed after product issues **#2–#9** and packaging **2.1.10**)  
**Product:** [`aiuse`](https://github.com/djbclark/aiuse)  
**Audience:** operators and agents deciding product positioning or “what next?” features.

This note surveys tools that surface AI **subscription / coding-agent quotas**, then
focuses on the harder product question:

> **Given several paid token pools that reset, which pool should a human use _next_?**

Public product claims change quickly. Treat feature cells as **as of this date**, not
as a guarantee of current vendor behavior. Prefer each project’s README when deciding
to depend on it.

## What `aiuse` optimizes for

`aiuse` is **not** primarily a menu-bar quota widget and **not** a request proxy.

It:

1. **Collects** live allotments from **five** external data sources (PATH tools
   and/or OpenUsage loopback HTTP):

   | Source                                                                      | Interface                                    | Role                                         |
   | --------------------------------------------------------------------------- | -------------------------------------------- | -------------------------------------------- |
   | [**cswap**](https://github.com/realiti4/claude-swap)                        | `cswap list --json`                          | Multi-account Claude (**canonical**)         |
   | [**CodexBar**](https://codexbar.app/)                                       | `codexbar usage --format json`               | Broad live quotas (**preferred** non-Claude) |
   | [**caut**](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) | `caut usage --json`                          | Independent multi-provider peer / fill-in    |
   | [**OpenUsage**](https://www.openusage.ai/)                                  | CLI and/or `http://127.0.0.1:6736/v1/limits` | Independent peer / fill-in                   |
   | [**tokscale**](https://github.com/junhoyeo/tokscale)                        | `tokscale usage --json`                      | Independent peer; **preferred** for Copilot  |

   Install the full set: **`./packaging/install-deps.sh`** (also
   `just install-deps` in this repo, or site
   `just -f ~/ops/site-djbclark/justfile install-aiuse-deps`).

2. **Classifies** windows as burn / conserve / on-pace (pace scoring) and treats
   non-expiring prepaid / pay-as-you-go as inventory (`n/a`), not use-or-lose.
3. **Ranks** every account into a single **priority ladder** (read bottom → top).
4. **Suggests** a single best burn pool (`aiuse suggest` / JSON `suggestion` /
   `GET /v1/suggest`).
5. **Cross-checks** all live sources **pairwise** (correctness over minimal scrape
   count). Selection still picks one primary row per provider for the ladder.
6. **Surfaces forecasts** on ladder / status lines (`~lockout …`,
   `~N% unused@reset` from pace projections).
7. **Learns from history** when snapshots densify (`persist_snapshots` +
   `learn_from_history: auto`; `--full` History section).
8. **Exposes agent surfaces** without becoming a proxy: `aiuse serve`
   (loopback HTTP) plus stable JSON ([`json-contract.md`](json-contract.md),
   [`agent-api.md`](agent-api.md)). Full MCP stdio remains optional follow-up.
9. **Composes ambient** via companions + one-liner (`aiuse status` /
   `prompt` — [`companion-stack.md`](companion-stack.md)), not a native menubar.
10. **Optional local-runtime note** (Ollama / LM Studio TCP probe) when clouds
    look empty — advisory only, never ranked as burn ([#7](https://github.com/djbclark/aiuse/issues/7)).
11. **Dogfoods shared semantics** v0.1
    ([`shared-quota-semantics/`](shared-quota-semantics/)) for peer interop.

Default ladder tags (see also [`pretty-display.md`](pretty-display.md)):

```text
error → empty → n/a → slow → mid → use
```

That stack is still the product differentiator: **portfolio decision support**
over multiple subscriptions and accounts, not live telemetry alone.

---

## Two layers of the market

Most tools stop at **Layer 1**. Few reach **Layer 2**.

| Layer          | Question answered                                       | Typical UX                                            |
| -------------- | ------------------------------------------------------- | ----------------------------------------------------- |
| **1. Monitor** | How much is left? When does it reset? What did I spend? | Menu bar bars, tables, dashboards, status lines       |
| **2. Decide**  | _Which_ pool / account should get the next work?        | Ranked recommendations, routing advice, burn/conserve |

`aiuse` is intentionally a **Layer 2 thin aggregator** on top of Layer 1 tools
(the five sources above). Competitors that also scrape or own adapters still
usually ship only Layer 1 UX — or Layer 2 oriented toward **agent routing**
rather than a human use-or-lose ladder.

A readable Mac-oriented Layer 1 survey:
[AI Token Usage Monitors for macOS](https://denshub.com/en/ai-token-usage-monitors-macos/)
(Denis Rasulev, May 2026).

---

## Layer 1 — monitors and collectors

These help you _see_ quotas. They do not (or barely) _rank use-or-lose urgency_
across your whole portfolio.

| Project                                                                     | Form                               | Multi-provider                          | Ranking / “use next”?          | Relation to `aiuse`                                                             |
| --------------------------------------------------------------------------- | ---------------------------------- | --------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------- |
| **[CodexBar](https://codexbar.app/)**                                       | macOS menu bar + CLI               | Very wide (40+ coding agents)           | **No** (display / alerts)      | **Primary** non-Claude collector (in-tree)                                      |
| **[cswap](https://github.com/realiti4/claude-swap)**                        | Claude multi-account CLI           | Claude-focused                          | **No**                         | **Canonical** multi-account Claude (in-tree)                                    |
| **[tokscale](https://github.com/junhoyeo/tokscale)**                        | CLI (tokens / costs / some quotas) | Several                                 | **No**                         | Cross-check / Copilot-preferred (in-tree)                                       |
| **[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)** | Rust CLI (CodexBar-style)          | 16+                                     | **No** (tables / JSON)         | **Default** cross-check peer (in-tree)                                          |
| **[OpenUsage](https://www.openusage.ai/)**                                  | macOS app + CLI / loopback HTTP    | ~15 plugins                             | **No** (state API for scripts) | **Default** cross-check peer (in-tree)                                          |
| **[SessionWatcher](https://www.sessionwatcher.com/)**                       | Paid macOS menu bar                | Claude, Codex, Copilot, Cursor, Gemini… | **No**                         | Pure monitor                                                                    |
| **[UsageScope](https://www.usagescope.com/)**                               | Mac App Store menu bar             | Narrower set                            | **No**                         | Pure monitor                                                                    |
| **[ClaudeBar](https://github.com/tddworks/ClaudeBar)**                      | Free OSS menu bar                  | Several                                 | **No**                         | Pure monitor                                                                    |
| **[ccusage](https://ccusage.com/)**                                         | Terminal / local JSONL burn        | CLI agents                              | **No** (token _spend_ history) | Not plan 5h/7d authority (see [`claude-local-usage.md`](claude-local-usage.md)) |
| **[CUStats](https://custats.info/)**                                        | Web / live bars                    | Claude + Codex                          | **No**                         | Dual-provider monitor                                                           |
| Browser “AI usage tracker” extensions                                       | On-page meters                     | Per site                                | **No**                         | Single-dashboard convenience                                                    |

**Implication for “what pool next?”:** Layer 1 leaves the hard comparison to the
human. You can open CodexBar and _eyeball_ which bar is fullest before reset; nothing
scores pace, shared allotments, multi-account Claude, or prepaid vs subscription
economics for you. `aiuse` uses several Layer 1 tools as **inputs**, then ranks.

---

## Layer 2 — decision / ranking peers

These claim (or implement) some form of **automatic choice** among pools.

### quotabot ([blisspixel/quotabot](https://github.com/blisspixel/quotabot))

**Closest conceptual peer** to `aiuse` on ranking — oriented toward **live agent
routing** rather than a calm portfolio ladder.

| Aspect      | Behavior (public claims, as of this date)                                                                                           |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Job         | “htop for agentic AI quota plans” + **route the next request**                                                                      |
| Ranking     | `quotabot suggest` — confidence-weighted **runway**, with a **use-it-or-lose-it boost** for measured quota that would expire unused |
| Fallbacks   | Local runtimes (Ollama / LM Studio / Lemonade) as routing capacity when subscriptions are low                                       |
| Integration | Desktop widget, **MCP** (stdio + opt-in loopback HTTP), optional **LiteLLM** handoff with leases; **advisor, not a proxy**          |
| Maturity    | Early **0.x**, active development                                                                                                   |
| Data        | Own adapters + credentials/grants; not built as a shell-out to CodexBar/cswap                                                       |
| Trust       | Local-first, zero usage tokens on inference path; content-blind metadata                                                            |

**Human “what next?” fit:** strong for _“send this next agent call where?”_

Weaker as a paced **portfolio** view of burn vs conserve with multi-source
cross-checks and prepaid `n/a` economics (less emphasized than live routing).

### onWatch ([onllm-dev/onwatch](https://onwatch.onllm.dev/))

| Aspect                | Behavior (public claims, as of this date)                                                    |
| --------------------- | -------------------------------------------------------------------------------------------- |
| Job                   | Daemon + local web dashboard; history, anomalies, projections                                |
| Ranking               | **Cross-provider headroom** and “route work before limits”; burn-rate / exhaustion forecasts |
| Form                  | Go daemon, SQLite, `localhost:9211` dashboard; menubar beta (macOS); multi-OS                |
| Data                  | Own polling of provider metadata endpoints (~60s)                                            |
| Providers (marketing) | Anthropic, Codex, Synthetic, Z.ai, Copilot, MiniMax, Gemini CLI, Antigravity, …              |

**Human “what next?” fit:** good _capacity_ comparison (“Anthropic tight, Codex
open”) and **strong** historical / anomaly UX. Not a scored use-or-lose
**ladder** of burn/conserve/n/a, and less about plan-dollar waste than about not
hitting a wall mid-task.

### Narrower / adjacent

| Project                                             | Notes                                                                                      |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| **quotamax** (various mentions)                     | Claude Max–oriented “use it or lose it” helpers — single ecosystem, not multi-pool ranking |
| **One-off scripts** (e.g. multi-quota bash posts)   | Personal glue; rarely paced scoring or maintainable ranking                                |
| **afterburner** / task backends on flat-rate logins | Use subscriptions as _execution_ backends; different product class                         |

---

## Comprehensive comparison matrix

Legend: **Yes** / **Partial** / **No** / **N/A**. “Decision quality” is about
helping a human (or script) pick the **next token pool**, not raw telemetry accuracy.

| Capability                                   | **aiuse**                                               | **quotabot**                        | **onWatch**                        | **CodexBar / caut / OpenUsage alone** | **SessionWatcher / CUStats / ccusage** |
| -------------------------------------------- | ------------------------------------------------------- | ----------------------------------- | ---------------------------------- | ------------------------------------- | -------------------------------------- |
| Multi-provider live quotas                   | Yes (5 sources)                                         | Yes (own adapters)                  | Yes                                | Yes                                   | Partial                                |
| Multi-account Claude                         | Yes (cswap-first)                                       | Yes (claims)                        | Partial                            | Partial (varies)                      | Usually No                             |
| Always-on menu bar                           | No                                                      | Desktop widget                      | Menubar beta                       | CodexBar / OpenUsage Yes              | Often Yes                              |
| Multi-source cross-check                     | **Yes** (all-pairs default)                             | Own consistency / drift             | History/anomaly                    | N/A (single product)                  | Rare                                   |
| Local history / charts                       | Snapshots + History + learn                             | Yes (analytics)                     | **Strong**                         | Varies                                | Varies                                 |
| Exhaustion / waste _forecast_                | **Yes** (pace + ladder/status fragments)                | Runway in `suggest`                 | **Strong**                         | Rare                                  | Rare                                   |
| **Automatic rank of “use next”**             | **Yes** (ladder + `suggest`)                            | **Yes** (`suggest`)                 | Partial (headroom)                 | **No**                                | **No**                                 |
| Burn vs conserve classification              | **Yes** (pace mode)                                     | Partial (runway / spent)            | Partial                            | No                                    | No                                     |
| Shared allotment (5h ⊂ weekly)               | **Yes** (config)                                        | Card collapse for spent long window | Unclear                            | No                                    | No                                     |
| Prepaid / non-expiring treated as non-urgent | **Yes** (`n/a` band)                                    | Subscription-oriented               | Limits-focused                     | Often shows as “balance”              | N/A                                    |
| Plan $ / value-at-risk                       | Yes (config `monthly_price`)                            | Cost policy advanced                | Subscription intelligence claims   | Rare                                  | Rare                                   |
| Agent-facing API                             | **Partial** (`serve` loopback + JSON; no MCP stdio yet) | **Strong** (MCP, LiteLLM)           | Dashboard / API-ish                | OpenUsage HTTP state                  | No                                     |
| One-line ambient status                      | **Partial** (`status` / `prompt`)                       | Widget + strip                      | Menubar beta                       | **Strong**                            | Often Yes                              |
| Local runtime fallback                       | **Partial** (optional INFO probe)                       | **Strong** (routing capacity)       | Unclear                            | No                                    | No                                     |
| Cross-platform CLI                           | Yes (Python)                                            | Yes (Dart/CLI)                      | Yes (Go)                           | caut Yes; CodexBar/OpenUsage Mac      | Mixed                                  |
| Trust model                                  | Shells out to tools you already run                     | Own credential/grant story          | Local daemon, zero telemetry claim | App permissions / cookies             | Varies                                 |
| Dep install story                            | `packaging/install-deps.sh`                             | Own installer                       | Own installer                      | Per-app                               | Per-app                                |
| Shared ranking semantics package             | **Yes** (v0.1 in-tree)                                  | N/A                                 | N/A                                | N/A                                   | N/A                                    |
| Implementation maturity                      | Focused CLI; tests + packaging 2.1.x                    | Early 0.x                           | Active OSS product                 | CodexBar/OpenUsage mature             | Mixed                                  |

---

## Decision quality: “which token pool should I use next?”

This section scores products against a **human operator** who already pays for
several overlapping subscriptions (Claude Pro/Max × N accounts, Codex, Cursor,
Copilot, Grok, Gemini/Antigravity, OpenCode Go, prepaid API balances, …) and wants
to:

1. Avoid **wasting** allotments that reset unused.
2. Avoid **locking out** mid-task (over-burning a short window).
3. Ignore **non-deadlines** (prepaid API tokens that roll).
4. Decide in **seconds**, without opening five dashboards.
5. Trust the numbers more when **independent tools disagree** (multi-source).

### Decision dimensions

| Dimension                            | What “good” looks like                   | **aiuse**                                                  | **quotabot**                     | **onWatch**                   | **Layer 1 only**                |
| ------------------------------------ | ---------------------------------------- | ---------------------------------------------------------- | -------------------------------- | ----------------------------- | ------------------------------- |
| **A. Portfolio view**                | One ordered list of _all_ pools/accounts | Strong default ladder                                      | Cards + `suggest` winner         | Side-by-side dashboard        | Human must scan bars            |
| **B. Expiring vs non-expiring**      | Do not treat prepaid as “burn now”       | Strong (`prepaid` / `n/a`)                                 | Weaker (subs + local)            | Weaker (limits/resets)        | Easy to misread                 |
| **C. Pace / waste projection**       | Under-use vs over-use relative to reset  | Strong (pace + forecast copy)                              | Runway + expire boost            | Burn rate → exhaustion        | Mental math                     |
| **D. Short vs long window coupling** | 5h under weekly should not dominate      | Shared-allotment scoring                                   | Spent long window collapses card | Operator interprets both      | Often misleading                |
| **E. Multi-account same product**    | Which Claude email / org to use          | cswap multi-row + ladder                                   | Multi-account support claimed    | Codex multi-account beta      | Painful                         |
| **F. Economic weight**               | Prefer burning expensive waste first     | Plan price / value-at-risk                                 | Advanced cost policy             | “Upgrade/downgrade” analytics | Rare                            |
| **G. Next _request_ routing**        | Tell an agent where to send call N+1     | **Partial** (`suggest` + `serve`; no leases/MCP)           | **Strong**                       | Partial (capacity view)       | None                            |
| **H. Continuous ambient awareness**  | Glance without a deliberate deep report  | **Partial** (`status` + LaunchAgent; no menubar)           | Widget + watch                   | Dashboard / menubar           | **Strong** (CodexBar/OpenUsage) |
| **I. Trust / blast radius**          | Minimal new credential surface           | High (reuse PATH tools)                                    | Medium (own grants/logins)       | Medium (daemon + keys)        | Varies by app                   |
| **J. Measurement honesty**           | Spot tool disagreement                   | **Strong** (all-pairs cross-check)                         | Own adapters + drift checks      | History/anomaly               | Single source                   |
| **K. Automation / cron**             | Scriptable ranked output + exit codes    | Strong (`--json`, exit codes, hourly agent, `serve` cache) | Strong (MCP / JSON)              | Daemon snapshots              | OpenUsage HTTP / CLI JSON       |

### Where `aiuse` is **better** for a human deciding the next pool

1. **Explicit use-or-lose ranking UI.**  
   The priority ladder is purpose-built for “read bottom → top, pick what to burn.”
   Layer 1 tools force a visual scan. onWatch shows capacity; it does not
   consistently _order_ burn urgency with burn/conserve semantics.

2. **Single winner without becoming a router.**  
   `aiuse suggest` (and `/v1/suggest`) returns the best **burn** pool or null —
   enough for humans and light agents — without MCP leases or LiteLLM handoff.

3. **Pace-based burn vs conserve + forecast fragments.**  
   High remaining % near reset → **use**. Fast burn with lots of time left →
   **slow**. Ladder/status lines can show `~lockout …` and projected waste %.
   Most monitors only show remaining % and a timer.

4. **Shared allotment awareness.**  
   Claude/Gemini/OpenCode/Cursor-style nested windows are easy to misread
   (“5h is 100% free!” while weekly is already on pace). `aiuse` can score the
   **governing** window only. Menu bars often show both bars as peers.

5. **Prepaid honesty.**  
   Deepseek / OpenRouter-style purchased balances do not expire; ranking them as
   “100% left, use before reset” is actively harmful. `aiuse` puts them in
   **`n/a`** between empty and slow. Most monitors still display them as generic
   remaining capacity.

6. **Multi-account Claude as first-class.**  
   Via cswap, multiple emails appear as separate ladder rows with independent
   urgency — important when one Max account is empty and another is full.

7. **Multi-source correctness.**  
   With **cswap + CodexBar + caut + OpenUsage + tokscale** enabled by default,
   disagreements surface as cross-checks instead of silent single-tool wrongness.
   Selection still prefers the authoritative primary (cswap for Claude, etc.).

8. **Thin trust surface.**  
   No new long-lived scraper identity if you already use the five tools above.
   You inherit _their_ auth model (and their bugs), which is a deliberate trade.
   Operator install path: `packaging/install-deps.sh`.

9. **Scriptable operator workflow.**  
   LaunchAgent snapshots + `--json` + exit codes + optional `aiuse serve` support
   “hourly collect, human or agent decides later” without a full daemon product.

10. **Portable semantics package.**  
    [`shared-quota-semantics/`](shared-quota-semantics/) freezes enums, pace
    formulas, and golden fixtures so peers can interoperate without forking Python.

### Where `aiuse` is **worse** (or weaker) than alternatives

1. **Not a live ambient widget.**  
   CodexBar / SessionWatcher / OpenUsage / quotabot widget win for always-visible
   bars. `aiuse status` is a **pull** one-liner (prompt/status bar), not a menubar
   process. Bad for “am I about to die mid-prompt?” unless you also keep a menu bar.

2. **Not a full agent request router.**  
   quotabot’s MCP + LiteLLM leases + model/task budgets remain stronger when the
   _consumer_ is an agent picking a backend for the next call with reservations.
   `aiuse serve` is read-only ranking JSON on loopback — no proxy, no leases,
   no MCP stdio (yet).

3. **Depends on external collector quality (and count).**  
   Five sources improve honesty but cost wall-clock (still ~max of parallel
   collectors) and PATH complexity. If CodexBar is wrong about OpenCode Go local
   vs web, or cswap JSON is stale, ranking can still be wrong — cross-checks
   flag it more often than they auto-fix it.

4. **Weaker historical analytics UI.**  
   onWatch (and quotabot analytics) still beat `aiuse --full` History for rich
   charts, anomaly detection, and long-horizon storytelling. `aiuse` history is
   operational (learn burn rates, chronic underuse INFO), not a full BI dashboard.

5. **Local runtimes are notes, not capacity.**  
   Optional Ollama/LM Studio probes never become ranked burn targets. quotabot
   can **route** to local when clouds are empty; `aiuse` only reminds you they
   exist (when enabled).

6. **OpenUsage needs the app (or CLI) present.**  
   Unlike pure CLI tools, OpenUsage’s best path is menu-bar app + loopback (or
   Settings → Command Line install). Documented in
   [`collectors-caut-openusage.md`](collectors-caut-openusage.md).

### Head-to-head narrative (human operator)

| Scenario                                                                                                   | Better fit                                                 |
| ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| “I have five subscriptions and two Claude accounts; what should I spend this afternoon so nothing wastes?” | **`aiuse`**                                                |
| “My agent needs a backend for the next tool call without hitting a spent short window, with MCP/leases.”   | **quotabot**                                               |
| “Script or agent needs a single burn winner + ladder over loopback HTTP.”                                  | **`aiuse`** (`suggest` / `serve`) — lighter than MCP       |
| “Will I run out before dinner, and is something weird resetting early?”                                    | **onWatch** (or Layer 1 + mental math)                     |
| “I just want green/yellow/red in the menu bar while coding.”                                               | **CodexBar** and/or **OpenUsage**                          |
| “Shell prompt should show one urgency line.”                                                               | **`aiuse status`** (+ companions for bars)                 |
| “How many tokens did Claude Code burn locally this week?”                                                  | **ccusage** / local logs — not plan % (see project policy) |
| “Deepseek balance vs Claude weekly — do I rush Deepseek?”                                                  | **`aiuse`** (`n/a` vs use/slow)                            |
| “CodexBar says X, OpenUsage says Y — who do I trust?”                                                      | **`aiuse` cross-checks** surface the gap; you still decide |

**Synthesis:** for a **human deciding the next expiring pool**, `aiuse` remains
among the strongest options publicly documented, especially vs pure monitors.
With **#2–#9** shipped it also covers single-winner suggest, louder forecasts,
status one-liner, loopback agent API, History learning, health probes, optional
local notes, and shared-semantics dogfood — without becoming a menubar or router
product. **quotabot** remains the main peer on **agent routing depth**.
**onWatch** remains the main peer on **forecast UI and history BI**. Many
operators will reasonably run **CodexBar and/or OpenUsage (ambient) + `aiuse`
(rank)** rather than treating them as substitutes.

---

## Architecture positioning

```text
┌─────────────────────────────────────────────────────────────┐
|  Human / cron / agents (CLI, status, suggest, serve HTTP)    |
|    “what pool next?”  “exit if burn/conserve alerts”         |
└────────────────────────────┬────────────────────────────────┘
                             │
                      ┌──────▼──────┐
                      │   aiuse     │  rank, pace, n/a, suggest
                      │  (Layer 2)  │  history, serve, all-pairs
                      └──────┬──────┘
           ┌─────────┬───────┼───────┬─────────┐
           ▼         ▼       ▼       ▼         ▼
        cswap    CodexBar   caut  OpenUsage  tokscale
       (Claude)  (primary) (peer) (peer/HTTP) (peer/Copilot)
```

| Style                             | Examples                  | Pros                                               | Cons for “what next?”                               |
| --------------------------------- | ------------------------- | -------------------------------------------------- | --------------------------------------------------- |
| **Aggregator**                    | `aiuse`                   | Reuses mature collectors; multi-source cross-check | Ranking quality ≤ collector quality                 |
| **Integrated monitor+own fetch**  | CodexBar, caut, OpenUsage | Great ambient UX                                   | No portfolio ranking alone                          |
| **Integrated decision+own fetch** | quotabot, onWatch         | End-to-end control, routing/forecast               | Duplicates collector problem; different trust model |

---

## Should we add _more_ peers as collectors?

**Short answer:** only if they pass the rules below. **caut** and **OpenUsage**
already shipped as **default** peers. Do **not** add quotabot, onWatch,
or ccusage as ranking inputs.

`aiuse`’s collector contract is: shell out to a PATH tool (or loopback HTTP),
normalize to `AccountUsage` / `QuotaWindow`, prefer authoritative sources, keep
others as cross-checks ([`runner.py`](../src/aiuse/collectors/runner.py)).

### Candidate-by-candidate

| Candidate                                              | As a new `aiuse` collector?                        | Why                                                                      |
| ------------------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------------------ |
| **cswap / CodexBar / tokscale / caut / OpenUsage**     | **Already in** (default)                           | Full multi-source set; install via `packaging/install-deps.sh`           |
| **[onWatch](https://onwatch.onllm.dev/)**              | **No (as live collect)**                           | Daemon/history product; not a clean one-shot usage JSON feed for ranking |
| **[quotabot](https://github.com/blisspixel/quotabot)** | **No**                                             | Peer **decision/router**, not a passive data source                      |
| **SessionWatcher / ClaudeBar / UsageScope / CUStats**  | **No**                                             | Monitors without a stable aggregator API                                 |
| **[ccusage](https://ccusage.com/)** / local burn tools | **No for plan ranking**                            | Spend history ≠ subscription windows                                     |
| **Provider-native APIs**                               | **Out of scope** unless abandoning PATH-tool model | Would make `aiuse` a second CodexBar                                     |

### Decision rules (when a _sixth_ collector would be worth it)

1. **Unique signal** — plan window or account the five sources systematically miss,
   **or** a reliably independent read for a weak peer pair.
2. **Stable machine interface** — CLI `--json` or loopback HTTP with a documented schema.
3. **Optional until proven** — or default only if correctness benefit is clear (as with caut/OpenUsage).
4. **Latency budget** — fits concurrent collect; no required long-lived daemon for one-shot runs.
5. **Does not confuse ranking** — prepaid stays non-urgent; local burn never becomes plan %.

### Better investment than more collectors

Full effort map and trackers: [`next-options.md`](next-options.md).

1. **Collector reliability** — cswap last-good (Step 33 / [#1](https://github.com/djbclark/aiuse/issues/1)), CodexBar web-preferred providers.
2. **Decision layer polish** — ladder edge cases, shared allotment, prepaid `n/a`, denser history ([#13](https://github.com/djbclark/aiuse/issues/13)).
3. **Optional MCP stdio** on top of `aiuse serve` — only if agents need native MCP ([#11](https://github.com/djbclark/aiuse/issues/11)).
4. **Companion stack** — keep documenting CodexBar/OpenUsage ambient + `aiuse` rank (do not rebuild menubar); optional pull `watch` ([#14](https://github.com/djbclark/aiuse/issues/14)).
5. **Peer outreach** for shared-quota-semantics ([#12](https://github.com/djbclark/aiuse/issues/12)); more fixtures ([#15](https://github.com/djbclark/aiuse/issues/15)).

---

## Strategy: stay independent; borrow selectively

**Move code into a competitor:** **No.** Keep `aiuse` as a Layer 2 thin
aggregator. Merging into CodexBar/OpenUsage/caut/cswap/tokscale/quotabot/onWatch
either kills portfolio ranking, duplicates collector ownership, or forces a
router/menubar product shape that is not this project’s job.

**Move ideas into `aiuse`:** **Done for the competitive-strategy backlog**
(issues **#2–#9**, shipped in **2.1.9 / 2.1.10**):

| Idea (source)                                            | Issue                                            | Status in `aiuse`                                                          |
| -------------------------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------------- |
| `suggest`-style single winner (quotabot)                 | [#2](https://github.com/djbclark/aiuse/issues/2) | **Done** — `aiuse suggest` + JSON `suggestion`                             |
| Louder exhaustion / burn-rate forecast (onWatch)         | [#3](https://github.com/djbclark/aiuse/issues/3) | **Done** — ladder/status forecast fragments from pace                      |
| Ambient companion stack + one-liner (CodexBar/OpenUsage) | [#4](https://github.com/djbclark/aiuse/issues/4) | **Done** — [`companion-stack.md`](companion-stack.md), `status` / `prompt` |
| Optional MCP or loopback query surface                   | [#5](https://github.com/djbclark/aiuse/issues/5) | **Done (MVP)** — `aiuse serve`; MCP stdio deferred                         |
| Deeper History that teaches                              | [#6](https://github.com/djbclark/aiuse/issues/6) | **Done** — History section + `learn_from_history`                          |
| Local runtime fallback _note_ when clouds empty          | [#7](https://github.com/djbclark/aiuse/issues/7) | **Done** — optional INFO probes (off by default)                           |
| `health_path` / probe URL overrides                      | [#8](https://github.com/djbclark/aiuse/issues/8) | **Done** — collector `health_path` / `probe_url`                           |
| Shared quota-semantics package                           | [#9](https://github.com/djbclark/aiuse/issues/9) | **Done** — v0.1 in-tree + pytest dogfood                                   |

**Explicitly skip absorbing:** full menubar app, own scraper matrix, request
routing / LiteLLM leases, ccusage-as-plan-%.

**Still open / operator-owned (not competitive feature gaps):**

See also [`next-options.md`](next-options.md).

| Item                                                                                                  | Notes                                                     |
| ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Step 33 / [#1](https://github.com/djbclark/aiuse/issues/1)                                            | Blocked on cswap `lastGoodUsage` upstream                 |
| [#10](https://github.com/djbclark/aiuse/issues/10)                                                    | Public announce (operator posts)                          |
| [#11](https://github.com/djbclark/aiuse/issues/11)                                                    | Optional MCP stdio on top of `serve`                      |
| [#12](https://github.com/djbclark/aiuse/issues/12)–[#15](https://github.com/djbclark/aiuse/issues/15) | Optional polish (peer outreach, History, watch, fixtures) |

**Move ideas into competitors (semantics, not code merge):** **Yes,
selectively** — prepaid = no use-or-lose urgency, shared-allotment / governing
window, stable JSON window fields, optional use-or-lose sort modes. Package for
multi-language reuse:
[`shared-quota-semantics.md`](shared-quota-semantics.md) and
[`shared-quota-semantics/`](shared-quota-semantics/).

---

## Install / operator stack (this site)

| Piece                     | How                                                                                           |
| ------------------------- | --------------------------------------------------------------------------------------------- |
| **aiuse** itself          | `pipx install aiuse` / `brew install aiuse`                                                   |
| **All five data sources** | `./packaging/install-deps.sh` or `just install-deps`                                          |
| **Site wrapper**          | `just -f ~/ops/site-djbclark/justfile install-aiuse-deps` → execs the same script             |
| **Check only**            | `./packaging/install-deps.sh --check` or `just install-deps-check` / site `aiuse-deps-status` |
| **OpenUsage CLI**         | Optional: app Settings → Command Line → Install (HTTP works while app runs)                   |
| **Hourly collect**        | site LaunchAgent `com.djbclark.aiuse`                                                         |
| **Agent loopback**        | `aiuse serve` → [`agent-api.md`](agent-api.md)                                                |
| **Prompt one-liner**      | `aiuse status` / `aiuse prompt` → [`companion-stack.md`](companion-stack.md)                  |

Details: [`collectors-caut-openusage.md`](collectors-caut-openusage.md),
[`collector-concurrency.md`](collector-concurrency.md),
[`packaging.md`](packaging.md).

---

## Sources (snapshot 2026-07-25)

- Project code and docs in this repo (`README.md`, collectors, `packaging/install-deps.sh`,
  product surfaces for #2–#9).
- [quotabot README](https://github.com/blisspixel/quotabot)
- [onWatch product site](https://onwatch.onllm.dev/)
- [caut / coding_agent_usage_tracker](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)
- [CodexBar](https://codexbar.app/) / denshub macOS monitor survey
- [OpenUsage](https://www.openusage.ai/), [SessionWatcher](https://www.sessionwatcher.com/),
  [CUStats](https://custats.info/), [ccusage](https://ccusage.com/),
  [tokscale](https://github.com/junhoyeo/tokscale)

When this landscape drifts, update **this file** and the links in `AGENTS.md` /
`README.md` rather than tool-private memory.
