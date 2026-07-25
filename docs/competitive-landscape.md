# Competitive landscape: multi-provider AI quota tools

**Date:** 2026-07-25 (updated after multi-source ship)  
**Product:** [`aiuse`](https://github.com/djbclark/aiuse) (packaging **2.1.7**)  
**Audience:** operators and agents deciding product positioning or “what next?” features.

This note surveys tools that surface AI **subscription / coding-agent quotas**, then
focuses on the harder product question:

> **Given several paid token pools that reset, which pool should a human use *next*?**

Public product claims change quickly. Treat feature cells as **as of this date**, not
as a guarantee of current vendor behavior. Prefer each project’s README when deciding
to depend on it.

## What `aiuse` optimizes for

`aiuse` is **not** primarily a menu-bar quota widget and **not** a request router.

It:

1. **Collects** live allotments from **five** external data sources (PATH tools
   and/or OpenUsage loopback HTTP):

   | Source | Interface | Role |
   | ------ | --------- | ---- |
   | [**cswap**](https://github.com/realiti4/claude-swap) | `cswap list --json` | Multi-account Claude (**canonical**) |
   | [**CodexBar**](https://codexbar.app/) | `codexbar usage --format json` | Broad live quotas (**preferred** non-Claude) |
   | [**caut**](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) | `caut usage --json` | Independent multi-provider peer / fill-in |
   | [**OpenUsage**](https://www.openusage.ai/) | CLI and/or `http://127.0.0.1:6736/v1/limits` | Independent peer / fill-in |
   | [**tokscale**](https://github.com/junhoyeo/tokscale) | `tokscale usage --json` | Independent peer; **preferred** for Copilot |

   Install the full set: **`./packaging/install-deps.sh`** (also
   `just install-deps` in this repo, or site
   `just -f ~/ops/site-djbclark/justfile install-aiuse-deps`, which shells out to
   that same script when the aiuse checkout is present).

2. **Classifies** windows as burn / conserve / on-pace (pace scoring) and treats
   non-expiring prepaid / pay-as-you-go as inventory (`n/a`), not use-or-lose.
3. **Ranks** every account into a single **priority ladder** for a human reading
   bottom → top: “what should I burn soon?” vs “already empty / no deadline.”
4. **Cross-checks** all live sources **pairwise** (correctness over minimal scrape
   count). Selection still picks one primary row per provider for the ladder.

Default ladder tags (see also [`pretty-display.md`](pretty-display.md)):

```text
error → empty → n/a → slow → mid → use
```

That stack is the product differentiator: **portfolio decision support** over multiple
subscriptions and accounts, not live telemetry alone.

---

## Two layers of the market

Most tools stop at **Layer 1**. Few reach **Layer 2**.

| Layer | Question answered | Typical UX |
| ----- | ----------------- | ---------- |
| **1. Monitor** | How much is left? When does it reset? What did I spend? | Menu bar bars, tables, dashboards, status lines |
| **2. Decide** | *Which* pool / account should get the next work? | Ranked recommendations, routing advice, burn/conserve |

`aiuse` is intentionally a **Layer 2 thin aggregator** on top of Layer 1 tools
(the five sources above). Competitors that also scrape or own adapters still
usually ship only Layer 1 UX.

A readable Mac-oriented Layer 1 survey:
[AI Token Usage Monitors for macOS](https://denshub.com/en/ai-token-usage-monitors-macos/)
(Denis Rasulev, May 2026).

---

## Layer 1 — monitors and collectors

These help you *see* quotas. They do not (or barely) *rank use-or-lose urgency*
across your whole portfolio.

| Project | Form | Multi-provider | Ranking / “use next”? | Relation to `aiuse` |
| ------- | ---- | -------------- | --------------------- | ------------------- |
| **[CodexBar](https://codexbar.app/)** | macOS menu bar + CLI | Very wide (40+ coding agents) | **No** (display / alerts) | **Primary** non-Claude collector (in-tree) |
| **[cswap](https://github.com/realiti4/claude-swap)** | Claude multi-account CLI | Claude-focused | **No** | **Canonical** multi-account Claude (in-tree) |
| **[tokscale](https://github.com/junhoyeo/tokscale)** | CLI (tokens / costs / some quotas) | Several | **No** | Cross-check / Copilot-preferred (in-tree) |
| **[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)** | Rust CLI (CodexBar-style) | 16+ | **No** (tables / JSON) | **Default** cross-check peer (in-tree) |
| **[OpenUsage](https://www.openusage.ai/)** | macOS app + CLI / loopback HTTP | ~15 plugins | **No** (state API for scripts) | **Default** cross-check peer (in-tree) |
| **[SessionWatcher](https://www.sessionwatcher.com/)** | Paid macOS menu bar | Claude, Codex, Copilot, Cursor, Gemini… | **No** | Pure monitor |
| **[UsageScope](https://www.usagescope.com/)** | Mac App Store menu bar | Narrower set | **No** | Pure monitor |
| **[ClaudeBar](https://github.com/tddworks/ClaudeBar)** | Free OSS menu bar | Several | **No** | Pure monitor |
| **[ccusage](https://ccusage.com/)** | Terminal / local JSONL burn | CLI agents | **No** (token *spend* history) | Not plan 5h/7d authority (see [`claude-local-usage.md`](claude-local-usage.md)) |
| **[CUStats](https://custats.info/)** | Web / live bars | Claude + Codex | **No** | Dual-provider monitor |
| Browser “AI usage tracker” extensions | On-page meters | Per site | **No** | Single-dashboard convenience |

**Implication for “what pool next?”:** Layer 1 leaves the hard comparison to the
human. You can open CodexBar and *eyeball* which bar is fullest before reset; nothing
scores pace, shared allotments, multi-account Claude, or prepaid vs subscription
economics for you. `aiuse` uses several Layer 1 tools as **inputs**, then ranks.

---

## Layer 2 — decision / ranking peers

These claim (or implement) some form of **automatic choice** among pools.

### quotabot ([blisspixel/quotabot](https://github.com/blisspixel/quotabot))

**Closest conceptual peer** to `aiuse` on ranking.

| Aspect | Behavior (public claims) |
| ------ | ------------------------ |
| Job | “htop for agentic AI quota plans” + **route the next request** |
| Ranking | `quotabot suggest` — confidence-weighted **runway**, with a **use-it-or-lose-it boost** for measured quota that would expire unused |
| Fallbacks | Local runtimes (Ollama / LM Studio / …) when subscriptions are low |
| Integration | Desktop widget, MCP, optional LiteLLM handoff; **advisor, not a proxy** |
| Maturity | Early **0.x**, active development |
| Data | Own adapters + credentials; not built as a shell-out to CodexBar/cswap |

**Human “what next?” fit:** strong for *“send this next agent call where?”*  
Weaker as a calm **portfolio** view of burn vs conserve over a week/month (less
emphasized than live routing).

### onWatch ([onllm-dev/onwatch](https://onwatch.onllm.dev/))

| Aspect | Behavior (public claims) |
| ------ | ------------------------ |
| Job | Daemon + local web dashboard; history, anomalies, projections |
| Ranking | **Cross-provider headroom** and “route work before limits”; burn-rate / exhaustion forecasts |
| Form | Go daemon, SQLite, `localhost` dashboard; menubar beta; multi-OS |
| Data | Own polling of provider metadata endpoints |

**Human “what next?” fit:** good *capacity* comparison (“Anthropic tight, Codex
open”). Not a scored use-or-lose **ladder** of burn/conserve/n/a, and less about
plan-dollar waste than about not hitting a wall mid-task.

### Narrower / adjacent

| Project | Notes |
| ------- | ----- |
| **quotamax** (various mentions) | Claude Max–oriented “use it or lose it” helpers — single ecosystem, not multi-pool ranking |
| **One-off scripts** (e.g. multi-quota bash posts) | Personal glue; rarely paced scoring or maintainable ranking |
| **afterburner** / task backends on flat-rate logins | Use subscriptions as *execution* backends; different product class |

---

## Comprehensive comparison matrix

Legend: **Yes** / **Partial** / **No** / **N/A**. “Decision quality” is about
helping a human pick the **next token pool**, not raw telemetry accuracy.

| Capability | **aiuse** | **quotabot** | **onWatch** | **CodexBar / caut / OpenUsage alone** | **SessionWatcher / CUStats / ccusage** |
| ---------- | --------- | ------------ | ----------- | ------------------------------------- | -------------------------------------- |
| Multi-provider live quotas | Yes (5 sources) | Yes (own adapters) | Yes | Yes | Partial |
| Multi-account Claude | Yes (cswap-first) | Yes (claims) | Partial | Partial (varies) | Usually No |
| Always-on menu bar | No | Desktop widget | Menubar beta | CodexBar / OpenUsage Yes | Often Yes |
| Multi-source cross-check | **Yes** (all-pairs default) | Own consistency | History/anomaly | N/A (single product) | Rare |
| Local history / charts | Snapshots + optional learn | Yes (analytics) | **Strong** | Varies | Varies |
| Exhaustion *forecast* | Pace / projected waste | Runway in `suggest` | **Strong** | Rare | Rare |
| **Automatic rank of “use next”** | **Yes** (priority ladder) | **Yes** (`suggest`) | Partial (headroom) | **No** | **No** |
| Burn vs conserve classification | **Yes** (pace mode) | Partial (runway / spent) | Partial | No | No |
| Shared allotment (5h ⊂ weekly) | **Yes** (config) | Card collapse for spent long window | Unclear | No | No |
| Prepaid / non-expiring treated as non-urgent | **Yes** (`n/a` band) | Subscription-oriented | Limits-focused | Often shows as “balance” | N/A |
| Plan $ / value-at-risk | Yes (config `monthly_price`) | Cost policy advanced | Subscription intelligence claims | Rare | Rare |
| Agent-facing routing (MCP / proxy advice) | JSON CLI only | **Strong** (MCP, LiteLLM) | Dashboard / API-ish | OpenUsage HTTP state | No |
| Cross-platform CLI | Yes (Python) | Yes (Dart/CLI) | Yes (Go) | caut Yes; CodexBar/OpenUsage Mac | Mixed |
| Trust model | Shells out to tools you already run | Own credential/grant story | Local daemon, zero telemetry claim | App permissions / cookies | Varies |
| Dep install story | `packaging/install-deps.sh` | Own installer | Own installer | Per-app | Per-app |
| Implementation maturity | Small focused CLI; tests + packaging | Early 0.x | Active OSS product | CodexBar/OpenUsage mature | Mixed |

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

| Dimension | What “good” looks like | **aiuse** | **quotabot** | **onWatch** | **Layer 1 only** |
| --------- | ---------------------- | --------- | ------------ | ----------- | ---------------- |
| **A. Portfolio view** | One ordered list of *all* pools/accounts | Strong default ladder | Cards + `suggest` winner | Side-by-side dashboard | Human must scan bars |
| **B. Expiring vs non-expiring** | Do not treat prepaid as “burn now” | Strong (`prepaid` / `n/a`) | Weaker (subs + local) | Weaker (limits/resets) | Easy to misread |
| **C. Pace / waste projection** | Under-use vs over-use relative to reset | Strong (pace / burn / conserve) | Runway + expire boost | Burn rate → exhaustion | Mental math |
| **D. Short vs long window coupling** | 5h under weekly should not dominate | Shared-allotment scoring | Spent long window collapses card | Operator interprets both | Often misleading |
| **E. Multi-account same product** | Which Claude email / org to use | cswap multi-row + ladder | Multi-account support claimed | Codex multi-account beta | Painful |
| **F. Economic weight** | Prefer burning expensive waste first | Plan price / value-at-risk | Advanced cost policy | “Upgrade/downgrade” analytics | Rare |
| **G. Next *request* routing** | Tell an agent where to send call N+1 | Weak (human CLI; JSON only) | **Strong** | Partial (see capacity) | None |
| **H. Continuous ambient awareness** | Glance without a deliberate `aiuse` run | Weak (run CLI / LaunchAgent) | Widget + watch | Dashboard / menubar | **Strong** (CodexBar/OpenUsage) |
| **I. Trust / blast radius** | Minimal new credential surface | High (reuse PATH tools) | Medium (own grants/logins) | Medium (daemon + keys) | Varies by app |
| **J. Measurement honesty** | Spot tool disagreement | **Strong** (all-pairs cross-check) | Own adapters | History/anomaly | Single source |
| **K. Automation / cron** | Scriptable ranked output + exit codes | Strong (`--json`, exit codes, hourly agent) | Strong (MCP / JSON) | Daemon snapshots | OpenUsage HTTP / CLI JSON |

### Where `aiuse` is **better** for a human deciding the next pool

1. **Explicit use-or-lose ranking UI.**  
   The priority ladder is purpose-built for “read bottom → top, pick what to burn.”
   Layer 1 tools force a visual scan. onWatch shows capacity; it does not
   consistently *order* burn urgency with burn/conserve semantics.

2. **Pace-based burn vs conserve.**  
   High remaining % near reset → **use**. Fast burn with lots of time left →
   **slow**. That distinction is the core of not thrashing short windows while
   wasting weekly/monthly caps. Most monitors only show remaining % and a timer.

3. **Shared allotment awareness.**  
   Claude/Gemini/OpenCode/Cursor-style nested windows are easy to misread
   (“5h is 100% free!” while weekly is already on pace). `aiuse` can score the
   **governing** window only. Menu bars often show both bars as peers.

4. **Prepaid honesty.**  
   Deepseek / OpenRouter-style purchased balances do not expire; ranking them as
   “100% left, use before reset” is actively harmful. `aiuse` puts them in
   **`n/a`** between empty and slow. Most monitors still display them as generic
   remaining capacity.

5. **Multi-account Claude as first-class.**  
   Via cswap, multiple emails appear as separate ladder rows with independent
   urgency — important when one Max account is empty and another is full.

6. **Multi-source correctness.**  
   With **cswap + CodexBar + caut + OpenUsage + tokscale** enabled by default,
   disagreements surface as cross-checks instead of silent single-tool wrongness.
   Selection still prefers the authoritative primary (cswap for Claude, etc.).

7. **Thin trust surface.**  
   No new long-lived scraper identity if you already use the five tools above.
   You inherit *their* auth model (and their bugs), which is a deliberate trade.
   Operator install path: `packaging/install-deps.sh`.

8. **Scriptable operator workflow.**  
   LaunchAgent snapshots + `--json` + exit codes support “hourly collect,
   human decides later from ladder/history” without a daemon product.

### Where `aiuse` is **worse** (or weaker) than alternatives

1. **Not a live ambient widget.**  
   CodexBar / SessionWatcher / OpenUsage win for always-visible bars. `aiuse`
   expects an intentional run (or scheduled persist + later review). Bad for
   “am I about to die mid-prompt?” unless you also keep a menu bar.

2. **Not an agent request router.**  
   quotabot’s `suggest` + MCP/LiteLLM path is better when the *consumer* is an
   agent picking a backend for the next call. `aiuse` optimizes the **human**
   portfolio decision, not automatic failover.

3. **Depends on external collector quality (and count).**  
   Five sources improve honesty but cost wall-clock (still ~max of parallel
   collectors) and PATH complexity. If CodexBar is wrong about OpenCode Go local
   vs web, or cswap JSON is stale, ranking can still be wrong — cross-checks
   flag it more often than they auto-fix it.

4. **Weaker historical analytics UI.**  
   onWatch (and quotabot analytics) beat `aiuse --full` History for rich charts,
   anomaly detection, and long-horizon “when do I burn?” storytelling.
   `aiuse` history is operational (learn burn rates, chronic underuse INFO), not
   a full BI dashboard.

5. **No automatic “switch provider now” in the IDE.**  
   Layer 1 menubar + quotabot routing close the loop faster for power users who
   want the tool to *act*. `aiuse` stops at ranked advice.

6. **OpenUsage needs the app (or CLI) present.**  
   Unlike pure CLI tools, OpenUsage’s best path is menu-bar app + loopback (or
   Settings → Command Line install). Documented in
   [`collectors-caut-openusage.md`](collectors-caut-openusage.md).

### Head-to-head narrative (human operator)

| Scenario | Better fit |
| -------- | ---------- |
| “I have five subscriptions and two Claude accounts; what should I spend this afternoon so nothing wastes?” | **`aiuse`** |
| “My agent needs a backend for the next tool call without hitting a spent short window.” | **quotabot** |
| “Will I run out before dinner, and is something weird resetting early?” | **onWatch** (or Layer 1 + mental math) |
| “I just want green/yellow/red in the menu bar while coding.” | **CodexBar** and/or **OpenUsage** |
| “How many tokens did Claude Code burn locally this week?” | **ccusage** / local logs — not plan % (see project policy) |
| “Deepseek balance vs Claude weekly — do I rush Deepseek?” | **`aiuse`** (`n/a` vs use/slow) |
| “CodexBar says X, OpenUsage says Y — who do I trust?” | **`aiuse` cross-checks** surface the gap; you still decide |

**Synthesis:** for a **human deciding the next expiring pool**, `aiuse` remains
among the strongest options publicly documented, especially vs pure monitors.
**quotabot** is the main peer on ranking, oriented toward **live routing** rather
than a use-or-lose portfolio ladder. **onWatch** is the main peer on **forecast
and history**. Many operators will reasonably run **CodexBar and/or OpenUsage
(ambient) + `aiuse` (rank)** rather than treating them as substitutes.

---

## Architecture positioning

```text
┌─────────────────────────────────────────────────────────────┐
|  Human / cron / scripts                                      |
|    “what pool next?”  “exit if burn/conserve alerts”         |
└────────────────────────────┬────────────────────────────────┘
                             │
                      ┌──────▼──────┐
                      │   aiuse     │  rank, pace, n/a, JSON
                      │  (Layer 2)  │  all-pairs cross-check
                      └──────┬──────┘
           ┌─────────┬───────┼───────┬─────────┐
           ▼         ▼       ▼       ▼         ▼
        cswap    CodexBar   caut  OpenUsage  tokscale
       (Claude)  (primary) (peer) (peer/HTTP) (peer/Copilot)
```

| Style | Examples | Pros | Cons for “what next?” |
| ----- | -------- | ---- | --------------------- |
| **Aggregator** | `aiuse` | Reuses mature collectors; multi-source cross-check | Ranking quality ≤ collector quality |
| **Integrated monitor+own fetch** | CodexBar, caut, OpenUsage | Great ambient UX | No portfolio ranking alone |
| **Integrated decision+own fetch** | quotabot, onWatch | End-to-end control, routing/forecast | Duplicates collector problem; different trust model |

---

## Should we add *more* peers as collectors?

**Short answer:** only if they pass the rules below. **caut** and **OpenUsage**
already shipped as **default** peers (2.1.6). Do **not** add quotabot, onWatch,
or ccusage as ranking inputs.

`aiuse`’s collector contract is: shell out to a PATH tool (or loopback HTTP),
normalize to `AccountUsage` / `QuotaWindow`, prefer authoritative sources, keep
others as cross-checks ([`runner.py`](../src/aiuse/collectors/runner.py)).

### Candidate-by-candidate (post-2.1.6)

| Candidate | As a new `aiuse` collector? | Why |
| --------- | --------------------------- | --- |
| **cswap / CodexBar / tokscale / caut / OpenUsage** | **Already in** (default) | Full multi-source set; install via `packaging/install-deps.sh` |
| **[onWatch](https://onwatch.onllm.dev/)** | **No (as live collect)** | Daemon/history product; not a clean one-shot usage JSON feed for ranking |
| **[quotabot](https://github.com/blisspixel/quotabot)** | **No** | Peer **decision/router**, not a passive data source |
| **SessionWatcher / ClaudeBar / UsageScope / CUStats** | **No** | Monitors without a stable aggregator API |
| **[ccusage](https://ccusage.com/)** / local burn tools | **No for plan ranking** | Spend history ≠ subscription windows |
| **Provider-native APIs** | **Out of scope** unless abandoning PATH-tool model | Would make `aiuse` a second CodexBar |

### Decision rules (when a *sixth* collector would be worth it)

1. **Unique signal** — plan window or account the five sources systematically miss,
   **or** a reliably independent read for a weak peer pair.
2. **Stable machine interface** — CLI `--json` or loopback HTTP with a documented schema.
3. **Optional until proven** — or default only if correctness benefit is clear (as with caut/OpenUsage).
4. **Latency budget** — fits concurrent collect; no required long-lived daemon for one-shot runs.
5. **Does not confuse ranking** — prepaid stays non-urgent; local burn never becomes plan %.

### Better investment than more collectors

1. **Collector reliability** — cswap last-good (Step 33), CodexBar web-preferred providers.
2. **Decision layer** — ladder edge cases, shared allotment, prepaid `n/a`, history blend.
3. **Optional `suggest`-style single winner** in JSON for agents — *output*, not a new input tool.
4. **Companion stack** — CodexBar/OpenUsage ambient + `aiuse` rank (document, don’t rebuild).

---

## Strategy: stay independent; borrow selectively

**Move code into a competitor:** **No.** Keep `aiuse` as a Layer 2 thin
aggregator. Merging into CodexBar/OpenUsage/caut/cswap/tokscale/quotabot/onWatch
either kills portfolio ranking, duplicates collector ownership, or forces a
router/menubar product shape that is not this project’s job.

**Move ideas into `aiuse`:** **Yes** — tracked as enhancement issues:

| Idea (source) | Issue |
| ------------- | ----- |
| `suggest`-style single winner (quotabot) | [#2](https://github.com/djbclark/aiuse/issues/2) |
| Louder exhaustion / burn-rate forecast (onWatch) | [#3](https://github.com/djbclark/aiuse/issues/3) |
| Ambient companion stack docs + optional one-liner (CodexBar/OpenUsage) | [#4](https://github.com/djbclark/aiuse/issues/4) |
| Optional MCP or loopback query surface (quotabot / OpenUsage HTTP) | [#5](https://github.com/djbclark/aiuse/issues/5) |
| Deeper History that teaches (onWatch + own snapshots) | [#6](https://github.com/djbclark/aiuse/issues/6) |
| Local runtime fallback *note* when clouds empty (quotabot) | [#7](https://github.com/djbclark/aiuse/issues/7) |
| `health_path` / probe URL overrides per collector | [#8](https://github.com/djbclark/aiuse/issues/8) |

**Explicitly skip absorbing:** full menubar app, own scraper matrix, request
routing / LiteLLM leases, ccusage-as-plan-%.

**Move ideas into competitors (semantics, not code merge):** **Yes,
selectively** — prepaid = no use-or-lose urgency, shared-allotment / governing
window, stable JSON window fields, optional use-or-lose sort modes. How to
package those for multi-language reuse (JSON Schema, YAML enums, golden
vectors, not a Python-only library):
[`shared-quota-semantics.md`](shared-quota-semantics.md).

---

## Install / operator stack (this site)

| Piece | How |
| ----- | --- |
| **aiuse** itself | `pipx install aiuse` / `brew install aiuse` |
| **All five data sources** | `./packaging/install-deps.sh` or `just install-deps` |
| **Site wrapper** | `just -f ~/ops/site-djbclark/justfile install-aiuse-deps` → execs the same script |
| **Check only** | `./packaging/install-deps.sh --check` or `just install-deps-check` / site `aiuse-deps-status` |
| **OpenUsage CLI** | Optional: app Settings → Command Line → Install (HTTP works while app runs) |
| **Hourly collect** | site LaunchAgent `com.djbclark.aiuse` |

Details: [`collectors-caut-openusage.md`](collectors-caut-openusage.md),
[`collector-concurrency.md`](collector-concurrency.md),
[`packaging.md`](packaging.md).

---

## Sources (snapshot 2026-07-25)

- Project code and docs in this repo (`README.md`, collectors, `packaging/install-deps.sh`).
- [quotabot README](https://github.com/blisspixel/quotabot)
- [onWatch product site](https://onwatch.onllm.dev/)
- [caut / coding_agent_usage_tracker](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)
- [CodexBar](https://codexbar.app/) / denshub macOS monitor survey
- [OpenUsage](https://www.openusage.ai/), [SessionWatcher](https://www.sessionwatcher.com/),
  [CUStats](https://custats.info/), [ccusage](https://ccusage.com/),
  [tokscale](https://github.com/junhoyeo/tokscale)

When this landscape drifts, update **this file** and the links in `AGENTS.md` /
`README.md` rather than tool-private memory.
