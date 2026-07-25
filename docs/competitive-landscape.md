# Competitive landscape: multi-provider AI quota tools

**Date:** 2026-07-25  
**Product:** [`aiuse`](https://github.com/djbclark/aiuse) (current packaging **2.1.5**)  
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

1. **Collects** live allotments by shelling out to tools already on `PATH`
   ([cswap](https://github.com/realiti4/claude-swap),
   [CodexBar](https://codexbar.app/),
   [tokscale](https://github.com/junhoyeo/tokscale)).
2. **Classifies** windows as burn / conserve / on-pace (pace scoring) and treats
   non-expiring prepaid / pay-as-you-go as inventory (`n/a`), not use-or-lose.
3. **Ranks** every account into a single **priority ladder** for a human reading
   bottom → top: “what should I burn soon?” vs “already empty / no deadline.”

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

`aiuse` is intentionally a **Layer 2 thin aggregator** on top of Layer 1 collectors
(CodexBar / cswap / tokscale). Competitors that also scrape or own adapters still
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
| **[CodexBar](https://codexbar.app/)** | macOS menu bar + CLI | Very wide (40+ coding agents) | **No** (display / alerts) | Primary non-Claude collector |
| **[cswap](https://github.com/realiti4/claude-swap)** | Claude multi-account CLI | Claude-focused | **No** | Canonical multi-account Claude source |
| **[tokscale](https://github.com/junhoyeo/tokscale)** | CLI (tokens / costs / some quotas) | Several | **No** | Cross-check / fill-in collector |
| **[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)** | Rust CLI (CodexBar-style) | 16+ | **No** (tables / JSON) | Peer of CodexBar CLI; cross-platform |
| **[OpenUsage](https://www.openusage.ai/)** | macOS app + local HTTP API | ~15 plugins | **No** (state API for scripts) | Integration surface; not decisions |
| **[SessionWatcher](https://www.sessionwatcher.com/)** | Paid macOS menu bar | Claude, Codex, Copilot, Cursor, Gemini… | **No** | Pure monitor |
| **[UsageScope](https://www.usagescope.com/)** | Mac App Store menu bar | Narrower set | **No** | Pure monitor |
| **[ClaudeBar](https://github.com/tddworks/ClaudeBar)** | Free OSS menu bar | Several | **No** | Pure monitor |
| **[ccusage](https://ccusage.com/)** | Terminal / local JSONL burn | CLI agents | **No** (token *spend* history) | Not plan 5h/7d authority (see [`claude-local-usage.md`](claude-local-usage.md)) |
| **[CUStats](https://custats.info/)** | Web / live bars | Claude + Codex | **No** | Dual-provider monitor |
| Browser “AI usage tracker” extensions | On-page meters | Per site | **No** | Single-dashboard convenience |

**Implication for “what pool next?”:** Layer 1 leaves the hard comparison to the
human. You can open CodexBar and *eyeball* which bar is fullest before reset; nothing
scores pace, shared allotments, multi-account Claude, or prepaid vs subscription
economics for you.

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

| Capability | **aiuse** | **quotabot** | **onWatch** | **CodexBar / caut / OpenUsage** | **SessionWatcher / CUStats / ccusage** |
| ---------- | --------- | ------------ | ----------- | ------------------------------- | -------------------------------------- |
| Multi-provider live quotas | Yes (via collectors) | Yes (own adapters) | Yes | Yes | Partial |
| Multi-account Claude | Yes (cswap-first) | Yes (claims) | Partial | Partial (varies) | Usually No |
| Always-on menu bar | No | Desktop widget | Menubar beta | CodexBar / OpenUsage Yes | Often Yes |
| Local history / charts | Snapshots + optional learn | Yes (analytics) | **Strong** | Varies | Varies |
| Exhaustion *forecast* | Pace / projected waste | Runway in `suggest` | **Strong** | Rare | Rare |
| **Automatic rank of “use next”** | **Yes** (priority ladder) | **Yes** (`suggest`) | Partial (headroom) | **No** | **No** |
| Burn vs conserve classification | **Yes** (pace mode) | Partial (runway / spent) | Partial | No | No |
| Shared allotment (5h ⊂ weekly) | **Yes** (config) | Card collapse for spent long window | Unclear | No | No |
| Prepaid / non-expiring treated as non-urgent | **Yes** (`n/a` band) | Subscription-oriented | Limits-focused | Often shows as “balance” | N/A |
| Plan $ / value-at-risk | Yes (config `monthly_price`) | Cost policy advanced | Subscription intelligence claims | Rare | Rare |
| Agent-facing routing (MCP / proxy advice) | JSON CLI only | **Strong** (MCP, LiteLLM) | Dashboard / API-ish | OpenUsage HTTP state | No |
| Cross-platform CLI | Yes (Python) | Yes (Dart/CLI) | Yes (Go) | caut Yes; CodexBar Mac | Mixed |
| Trust model | Shells out to tools you already run | Own credential/grant story | Local daemon, zero telemetry claim | App permissions / cookies | Varies |
| Implementation maturity | Small focused CLI; tests + packaging | Early 0.x | Active OSS product | CodexBar mature | Mixed |

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
| **H. Continuous ambient awareness** | Glance without a deliberate `aiuse` run | Weak (run CLI / LaunchAgent) | Widget + watch | Dashboard / menubar | **Strong** |
| **I. Trust / blast radius** | Minimal new credential surface | High (reuse PATH tools) | Medium (own grants/logins) | Medium (daemon + keys) | Varies by app |
| **J. Automation / cron** | Scriptable ranked output + exit codes | Strong (`--json`, exit codes) | Strong (MCP / JSON) | Daemon snapshots | OpenUsage HTTP / CLI JSON |

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

6. **Thin trust surface.**  
   No new long-lived scraper identity if you already use CodexBar/cswap/tokscale.
   You inherit *their* auth model (and their bugs), which is a deliberate trade.

7. **Scriptable operator workflow.**  
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

3. **Depends on external collector quality.**  
   If CodexBar is wrong about OpenCode Go local vs web, or cswap JSON is stale,
   `aiuse` can rank garbage confidently. onWatch/quotabot own more of the
   fetch path (different failure modes, not zero risk).

4. **Weaker historical analytics UI.**  
   onWatch (and quotabot analytics) beat `aiuse --full` History for rich charts,
   anomaly detection, and long-horizon “when do I burn?” storytelling.
   `aiuse` history is operational (learn burn rates, chronic underuse INFO), not
   a full BI dashboard.

5. **No automatic “switch provider now” in the IDE.**  
   Layer 1 menubar + quotabot routing close the loop faster for power users who
   want the tool to *act*. `aiuse` stops at ranked advice.

6. **Provider coverage ceiling is collector coverage.**  
   CodexBar’s breadth helps, but exotic providers only appear if a collector
   does. quotabot/onWatch may cover a different set natively.

7. **OIDC / packaging is separate from product quality** — distribution is solid
   (PyPI + Homebrew), but that is orthogonal to decision quality.

### Head-to-head narrative (human operator)

| Scenario | Better fit |
| -------- | ---------- |
| “I have five subscriptions and two Claude accounts; what should I spend this afternoon so nothing wastes?” | **`aiuse`** |
| “My agent needs a backend for the next tool call without hitting a spent short window.” | **quotabot** |
| “Will I run out before dinner, and is something weird resetting early?” | **onWatch** (or Layer 1 + mental math) |
| “I just want green/yellow/red in the menu bar while coding.” | **CodexBar** (or SessionWatcher / OpenUsage) |
| “How many tokens did Claude Code burn locally this week?” | **ccusage** / local logs — not plan % (see project policy) |
| “Deepseek balance vs Claude weekly — do I rush Deepseek?” | **`aiuse`** (`n/a` vs use/slow) |

**Synthesis:** for a **human deciding the next expiring pool**, `aiuse` is among the
strongest options publicly documented, especially vs pure monitors. **quotabot** is
the main peer on ranking, oriented toward **live routing** rather than a
use-or-lose portfolio ladder. **onWatch** is the main peer on **forecast and
history**. Many operators will reasonably run **CodexBar (ambient) + `aiuse`
(decision)** rather than treating them as substitutes.

---

## Architecture positioning (why peers are not drop-in replacements)

```text
┌─────────────────────────────────────────────────────────────┐
|  Human / cron / scripts                                      |
|    “what pool next?”  “exit if burn alerts”                  |
└────────────────────────────┬────────────────────────────────┘
                             │
                      ┌──────▼──────┐
                      │   aiuse     │  rank, pace, n/a, JSON
                      └──────┬──────┘
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
       cswap            CodexBar           tokscale
     (Claude N)      (many providers)    (cross-check)
```

| Style | Examples | Pros | Cons for “what next?” |
| ----- | -------- | ---- | --------------------- |
| **Aggregator** | `aiuse` | Reuses mature collectors; multi-source cross-check | Ranking quality ≤ collector quality |
| **Integrated monitor+own fetch** | CodexBar, caut, OpenUsage | Great ambient UX | No portfolio ranking |
| **Integrated decision+own fetch** | quotabot, onWatch | End-to-end control, routing/forecast | Duplicates collector problem; different trust model |

---

## Gaps and opportunities (for `aiuse`)

Useful only if the product goal remains **human portfolio decisions**:

1. **Optional ambient companion** — not rebuild CodexBar; document “use CodexBar for
   glance, `aiuse` for rank,” or surface ladder via OpenUsage-style local HTTP.
2. **Agent-facing `suggest`** — one JSON “winner” for MCP (quotabot-shaped) without
   becoming a proxy; still grounded in pace + n/a rules.
3. **Richer history UX** — onWatch-level charts are not required, but clearer
   “this week you chronically underused X” already partially exists.
4. **Keep prepaid / shared-allotment edge** — easy for monitors to regress; treat as
   regression-tested product claims.
5. **Do not chase ccusage-as-plan-%** — local token burn ≠ subscription windows
   ([`claude-local-usage.md`](claude-local-usage.md)).

---

## Should we add peers as extra collectors?

**Short answer:** **not by default.** Almost every peer either **duplicates**
CodexBar/cswap/tokscale, answers a **different question** (local token burn, ambient
UI, request routing), or would **inflate runtime and auth surface** without improving
“which pool next?” ranking much. Prefer deepening the three existing collectors and
the decision layer.

`aiuse`’s collector contract is: shell out to a PATH tool, normalize to
`AccountUsage` / `QuotaWindow`, prefer authoritative sources, keep others as
cross-checks ([`runner.py`](../src/aiuse/collectors/runner.py)). New collectors only
pay off if they add **unique live plan windows** or a **reliably independent
measurement** of the same window.

### Candidate-by-candidate

| Candidate | As a new `aiuse` collector? | Why |
| --------- | --------------------------- | --- |
| **CodexBar / cswap / tokscale** | Already in | Keep investing here (timeouts, web vs local, cswap last-good, tokscale fan-out if upstream lands). |
| **[caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)** | **Only if** CodexBar is unavailable (Linux/Windows without CodexBar, or Mac users who refuse the app) | CodexBar-parity CLI with stable `caut.v1` JSON — high **overlap** with CodexBar on Mac. Optional `collectors.caut` as alternate multi-provider path, not a third concurrent scrape of the same cookies. |
| **[OpenUsage](https://www.openusage.ai/)** (`curl 127.0.0.1:6736/v1/usage`) | **Maybe optional later** | Nice local HTTP bus if the operator already runs OpenUsage. Does **not** replace collectors: still depends on another process and its plugins. Best as **opt-in** when CodexBar is broken for a provider OpenUsage still sees — not a default parallel fetch. |
| **[onWatch](https://onwatch.onllm.dev/)** | **No (as live collect)** | Different product: always-on daemon + SQLite history. Polling it would add a heavy dependency; for ranking you want **fresh** plan %, not a second cache of the same APIs. Reuse ideas (exhaustion forecast UX), not the binary as a collector. |
| **[quotabot](https://github.com/blisspixel/quotabot)** | **No** | Peer **decision/routing** layer (`suggest`, MCP), not a clean “usage JSON only” feed. Consuming it would either duplicate ranking or invent adapter-on-adapter. Better: learn from ranking UX; stay independent for portfolio scoring. |
| **SessionWatcher / ClaudeBar / UsageScope / CUStats** | **No** | Menu-bar / web monitors; no stable first-class machine API aimed at aggregators; high permission/auth coupling. |
| **[ccusage](https://ccusage.com/)** / OpenUsage.sh-style **local burn** tools | **No for plan ranking** | Local JSONL token *spend* is **not** subscription 5h/7d plan authority ([`claude-local-usage.md`](claude-local-usage.md)). At most a future optional “burn rate context” note — never a ladder driver. |
| **Provider-native APIs** (Anthropic usage, OpenAI Usage API, …) | **Out of scope unless** you abandon the PATH-tool model | Would make `aiuse` a second CodexBar. Explicit non-goal today. |

### Decision rules (when a fourth collector *would* be worth it)

Add a collector only if **all** of the following hold:

1. **Unique signal** — plan window or account that CodexBar + cswap + tokscale
   systematically miss (or mis-source), **or** a second independent live read for
   cross-check when tokscale is weak for that provider.
2. **Stable machine interface** — CLI `--json` or loopback HTTP with a documented
   schema (caut / OpenUsage style), not scraping a GUI.
3. **Optional by default** — `collectors.<name>.enabled: false` until proven; never
   force every operator to install the peer.
4. **Latency budget** — fits the concurrent 45s collect model; no long-lived daemon
   requirement for a one-shot `aiuse` run.
5. **Does not confuse ranking** — prepaid stays non-urgent; local burn never becomes
   plan %.

Under those rules, the **only** near-term candidates worth prototyping are:

| Priority | Candidate | Role |
| -------- | --------- | ---- |
| P2 (optional) | **caut** | CodexBar substitute on non-Mac / CodexBar-free hosts |
| P3 (optional) | **OpenUsage** HTTP | Opportunistic fill-in if already running; never required |
| Parked | Everything else | No collector work |

**Do not** add quotabot, onWatch, ccusage, or menubar-only apps as default data
sources. They compete with or confuse the decision layer more than they feed it.

### Better investment than more collectors

For “what pool next?” quality, higher ROI than a fourth scraper:

1. **Collector reliability** — cswap last-good (Step 33), CodexBar web-preferred
   providers, clearer error vs empty.
2. **Decision layer** — ladder edge cases, shared allotment, prepaid `n/a`, history
   burn blend (already partly done).
3. **Optional `suggest`-style single winner** in JSON for agents — *output* feature,
   not a new input tool.
4. **Document companion stack** — CodexBar (ambient) + `aiuse` (rank); OpenUsage
   optional if the user already likes it.

---

## Sources (snapshot 2026-07-25)

- Project code and docs in this repo (`README.md`, `docs/pretty-display.md`, collectors).
- [quotabot README](https://github.com/blisspixel/quotabot)
- [onWatch product site](https://onwatch.onllm.dev/)
- [caut / coding_agent_usage_tracker](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)
- [CodexBar](https://codexbar.app/) / denshub macOS monitor survey
- [OpenUsage](https://www.openusage.ai/), [SessionWatcher](https://www.sessionwatcher.com/),
  [CUStats](https://custats.info/), [ccusage](https://ccusage.com/),
  [tokscale](https://github.com/junhoyeo/tokscale)

When this landscape drifts, update **this file** and the links in `AGENTS.md` /
`README.md` rather than tool-private memory.
