# What next (post-2.1.10) and competitive gap difficulty

**Date:** 2026-07-25  
**Status:** Product issues **#2–#9** shipped; fix-plan Steps **1–32** and **34**
done. No mandatory numbered step.  
**Related:** [`handoff.md`](handoff.md), [`competitive-landscape.md`](competitive-landscape.md),
[`AGENTS.md`](../AGENTS.md)

This note freezes the **recommended next actions** and an honest **effort map**
for remaining competitive gaps. Prefer this over re-deriving strategy from a
chat transcript.

---

## Product posture

`aiuse` is past “build the product.” Core ranking, five collectors, prepaid
`n/a`, shared allotment, suggest / status / serve, forecast fragments, History
learning, health probes, optional local-runtime notes, shared-semantics v0.1,
and packaging (**2.1.10**) are done.

High-ROI work is **distribution, data density, and input reliability** — not
cloning menubars, BI dashboards, or request routers.

---

## Recommended order (default)

| Priority | Action | Why | Effort | Tracker |
| -------- | ------ | --- | ------ | ------- |
| **1** | **Announce** | Best unpaid feedback channel; product ready enough | Low (0.5–2h, **operator only**) | [#10](https://github.com/djbclark/aiuse/issues/10) |
| **2** | **Let hourly LaunchAgent run** | History learning needs dense snapshots | None (already installed) | Site `com.djbclark.aiuse` |
| **3** | **Step 33 when unblocked** | Multi-account Claude is a real differentiator; stale cswap JSON is the main self-inflicted hole | Low–medium (1–3h **after** upstream) | [#1](https://github.com/djbclark/aiuse/issues/1) · [claude-swap#170](https://github.com/realiti4/claude-swap/issues/170) |
| **4** | **Optional polish only if pain is real** | MCP, History text, fixtures, peer tickets, watch | See issues below | [#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15) |
| **Avoid by default** | Menubar app, own scrapers, LiteLLM router, ccusage-as-plan-% | Wrong product identity / trust model | High | Do not open as default backlog |

**Zero-code coherent path:** post [#10](https://github.com/djbclark/aiuse/issues/10)
when ready, leave the agent collecting, poll cswap#170 occasionally.

Open-ended “what next?” → **do not restart** fix plan at Step 1.

---

## Open / parked trackers

| Issue | State | Notes |
| ----- | ----- | ----- |
| [#1](https://github.com/djbclark/aiuse/issues/1) | Open · **blocked** | Consume official cswap `lastGoodUsage` after #170 |
| [#2](https://github.com/djbclark/aiuse/issues/2)–[#9](https://github.com/djbclark/aiuse/issues/9) | **Closed** | Competitive-strategy pull shipped in 2.1.9 / 2.1.10 |
| [#10](https://github.com/djbclark/aiuse/issues/10) | Open · operator | Public announce draft (venues + copy); **do not auto-post** |
| [#11](https://github.com/djbclark/aiuse/issues/11) | Open · optional | Thin MCP stdio over `aiuse serve` payloads |
| [#12](https://github.com/djbclark/aiuse/issues/12) | Open · optional | Peer outreach for shared-quota-semantics (**last**) |
| [#13](https://github.com/djbclark/aiuse/issues/13) | Open · optional | Richer History / operational insights (text, not BI) |
| [#14](https://github.com/djbclark/aiuse/issues/14) | Open · optional | Optional `aiuse watch` pull refresh (not a menubar) |
| [#15](https://github.com/djbclark/aiuse/issues/15) | Open · optional | More shared-semantics golden fixtures |
| Step **35** | Parked | ccusage ≠ plan % — [`claude-local-usage.md`](claude-local-usage.md) |

---

## Competitive gaps: how hard to narrow further?

Remaining weaknesses vs quotabot / onWatch / Layer 1 (see
[`competitive-landscape.md`](competitive-landscape.md)) fall into three buckets.

### Easy wins (worth it *if* you feel the pain)

| Gap | Reality | Effort | Tracker |
| --- | ------- | ------ | ------- |
| MCP stdio on `serve` | Agents that only speak MCP can’t use loopback JSON easily | Medium (~1–3 days thin tools) | [#11](https://github.com/djbclark/aiuse/issues/11) |
| Richer History text | More narrative from existing snapshot data; no charts | Low–medium | [#13](https://github.com/djbclark/aiuse/issues/13) |
| More shared-semantics fixtures | Edge cases (Antigravity dual pools, Cursor Included, …) | Low | [#15](https://github.com/djbclark/aiuse/issues/15) |
| Collector reliability polish | Prefer web over local estimate; clearer cross-check messages | Low–medium | Incremental / Step 33 [#1](https://github.com/djbclark/aiuse/issues/1) |

Closes “quotabot has MCP” only for **read-only ranking**, not leases/LiteLLM.
Do MCP **only** if you actually use MCP clients against ranking daily.

### Medium work (partial close; diminishing returns)

| Gap | Reality | Effort | Tracker / policy |
| --- | ------- | ------ | ---------------- |
| Ambient “always on” | `status` is pull; no menubar | Medium for watch-mode CLI; **High** for native menubar | [#14](https://github.com/djbclark/aiuse/issues/14); prefer companions |
| Agent routing depth | No leases, budgets, LiteLLM, model registry | **High** | Out of scope; stay advisor |
| Local as capacity | Probes are INFO only | Medium to rank when empty; High for hardware-fit routing | Keep advisory by default |
| Forecast “loudness” | Fragments exist; no dashboard | Low–medium text/JSON; High for charts | Lean pace + waste $, not sparklines |

Best ambient strategy remains: **compose** CodexBar/OpenUsage + `aiuse`
([`companion-stack.md`](companion-stack.md)).

### Hard / don’t chase (wrong shape)

| Gap | Why hard | Policy |
| --- | -------- | ------ |
| Own multi-provider scrape matrix | Auth, ToS, drift, forever maintenance | Stay PATH-aggregator |
| Full history BI + anomaly product | Daemon + SQLite + dashboard = onWatch | Compose, don’t rebuild |
| Always-visible menubar parity | Swift/macOS app lifecycle | Document companion stack; stop |
| ccusage as plan % | Category error (local burn ≠ subscription windows) | Step 35 parked |

---

## Strategic synthesis

Cheap Layer-2 gaps that matter for a **human portfolio** tool are closed
(suggest, status, serve, forecast copy, History, health_path, local note,
semantics). What’s left is mostly:

1. **Depth that isn’t this product’s job** (menubar, BI, request router), or  
2. **Quality of inputs** (cswap last-good, collector honesty), or  
3. **Distribution** (announce + real users).

Narrowing “gaps with quotabot/onWatch” further is **easy only while it stays a
thin CLI**. Each step toward *their* product shape gets expensive and dilutes
the aggregator story.

### If we do one code thing

1. **Nothing code-related** until [#10](https://github.com/djbclark/aiuse/issues/10)
   ships or Step 33 ([#1](https://github.com/djbclark/aiuse/issues/1)) unblocks —
   highest leverage.  
2. **If agents need it:** thin MCP stdio ([#11](https://github.com/djbclark/aiuse/issues/11)).  
3. **If humans need it:** richer History text ([#13](https://github.com/djbclark/aiuse/issues/13))
   or optional `watch` ([#14](https://github.com/djbclark/aiuse/issues/14)), not a dashboard.  
4. **Never by default:** menubar, proxy, sixth collector from a decision peer.

---

## Better investment than more collectors

Reaffirmed from competitive strategy (do **not** add quotabot/onWatch/ccusage
as ranking inputs):

1. Collector reliability — especially Step 33 / cswap last-good.  
2. Decision-layer polish — ladder edge cases, shared allotment, prepaid `n/a`, denser history.  
3. Optional MCP stdio on top of `serve` — only if agents need native MCP.  
4. Companion stack docs — keep composing ambient + rank.  
5. Peer outreach for shared-quota-semantics — optional last ([#12](https://github.com/djbclark/aiuse/issues/12)).

---

## When this doc should change

- After [#10](https://github.com/djbclark/aiuse/issues/10) is posted (mark announce done).  
- When cswap#170 merges and [#1](https://github.com/djbclark/aiuse/issues/1) is implemented.  
- When an optional issue (#11–#15) is shipped or explicitly wontfix.  
- If product identity shifts (e.g. operator decides router *is* in scope) — update
  [`competitive-landscape.md`](competitive-landscape.md) first, then this file.
