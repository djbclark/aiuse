# cswap reliability / Claude quota sources

**Status:** implemented (2026-07-23)  
**Related:** [`AGENTS.md`](../AGENTS.md) priority #1, [`../src/aiuse/collectors/cswap.py`](../src/aiuse/collectors/cswap.py), [`../src/aiuse/collectors/runner.py`](../src/aiuse/collectors/runner.py)

## Problem

`ai` treats **cswap** (`cswap list --json`) as the canonical multi-account Claude
Code source. Live runs showed two failure modes:

1. **Decision-stale JSON omission.** Human `cswap list` shows last-known 5h/7d
   usage with an age note (e.g. `· 1h ago`). The same account in
   `cswap list --json` can appear as `"usageStatus": "unavailable", "usage": null`.
2. **Hard authority with no fallback.** When every cswap row lacked live data
   (or the collector raised), selection still preferred only `source == "cswap"`,
   dropping healthy CodexBar / tokscale Claude measurements.

Observed on this machine (cswap 0.23.0): active MIT account OK in JSON; gmail
slot at **100% weekly** still visible in the human list (~1–2h old cache) but
JSON-unavailable. cswap had deferred the next poll until the weekly reset, so
decision-grade trust (`TRUST_MAX_AGE_S` = 1h) expired long before the next fetch.

That is exactly when a use-it-or-lose-it report most needs the number.

## Why JSON omits the data (upstream design)

claude-swap separates **display-grade** vs **decision-grade** usage:

| Surface                  | Source                      | Trust                                                                                                                                               |
| ------------------------ | --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Human `cswap list` / TUI | `lastGood` + age annotation | Show old data                                                                                                                                       |
| `cswap list --json`      | `entry.decision_value()`    | Only if age ≤ `STALE_OK_S` (5m) or `trust_extended` (failures / poll cadence), and never past `TRUST_MAX_AGE_S` (1h; 2h after a usage-endpoint 429) |

Comment in cswap `switcher._build_list_payload`: scripts must not act on
arbitrarily old data when `usageStatus == "ok"`. That is correct for
**auto-switch**, wrong for **quota reporting**.

Internal cache: `~/.claude-swap-backup/cache/usage.json` on macOS (or
`$XDG_DATA_HOME/claude-swap/cache/usage.json` on Linux). Fields include
`lastGood`, `fetchedAt`, `nextPollAt`.

Upstream usage API (what cswap already calls, not reimplemented here):

```text
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <oauth access token>
```

## Alternatives considered

| Option                                                | Multi-account?                    | Verdict                                                         |
| ----------------------------------------------------- | --------------------------------- | --------------------------------------------------------------- |
| CodexBar `usage --provider claude`                    | Typically **one** session account | Good cross-check / total-failure fallback only                  |
| tokscale Claude row                                   | Usually single / no email         | Same as CodexBar; include in cross-check                        |
| `ccusage`                                             | N/A                               | Historical local token burn, **not** subscription 5h/7d windows |
| Direct Anthropic OAuth usage API                      | Yes, if we hold every token       | Duplicates cswap credential/rate-limit machinery; out of scope  |
| Import `claude_swap` as a library                     | Yes                               | Private API, wrong venv, version-coupled                        |
| **Read cswap’s usage cache when JSON is unavailable** | Yes                               | **Chosen** — same data human list already trusts for display    |
| Upstream `cswap list --json` additive `usageLastGood` | Yes                               | Nice long-term; optional follow-up to claude-swap               |

## Decision

Keep **cswap as canonical multi-account identity and preferred numbers**, but:

1. **Hydrate** JSON-`unavailable` slots from cswap’s on-disk `lastGood` when
   present (source remains `cswap`; note the age / decision-stale recovery).
   Recompute countdown strings from `resets_at` at parse time so frozen
   fetch-time countdowns (e.g. `17h 8m` still sitting in the cache two hours
   later) do not mislead the report.
2. **Fall back** selection to live CodexBar, then tokscale, only when **no**
   cswap row has live data; emit a cross-check warning that multi-account may
   be incomplete.
3. **Cross-check** live cswap rows against both CodexBar and tokscale (email
   match; single anonymous peer only when there is one live cswap account).

Do **not** store or refresh OAuth tokens inside `ai`. Credentials and polling
cadence stay owned by cswap / CodexBar / tokscale.

## Implementation map

- `collectors/cswap.py` — `_load_usage_cache`, `_hydrate_from_cache`, wire into
  `_account_from_item`; clear hard `error` when windows are recovered.
- `collectors/runner.py` — Claude branch: live-cswap vs fallback; `_claude_cross_checks`
  takes tokscale rows.
- Tests — `tests/test_cswap_parse.py`, `tests/test_runner_consolidation.py`.

## What “good” looks like

- Exhausted inactive Claude Code accounts still appear with 5h/7d % and reset
  times (from cache), not a bare “unavailable” line.
- If cswap is completely down, the report still shows Claude from CodexBar or
  tokscale with an explicit fallback warning.
- Discrepancies between cswap and tokscale surface as cross-check warnings.

## Optional follow-ups (parked)

Deferred work lives at the **tail** of
[`fix-implementation-plan.md`](fix-implementation-plan.md) **Phase 7**.

- **Step 33 (upstream display-grade JSON):** open issue/PR
  [realiti4/claude-swap#170](https://github.com/realiti4/claude-swap/issues/170)
  (`feat(json): expose display-grade last-good usage`) already proposes additive
  `lastGoodUsage` / `lastGoodFetchedAt` / `lastGoodAgeSeconds` while keeping
  decision-grade `usage`/`usageStatus`. Once merged, prefer those fields and
  keep cache-file hydration only as a fallback for older cswap. Consumer work
  tracked in this repo: [djbclark/aiuse#1](https://github.com/djbclark/aiuse/issues/1).
- **Step 34 (usage credits):** landed in this repo — cswap `usage.spend` →
  structured `AccountUsage.usage_credits` + pretty report section.
- **Step 35 (local ccusage burn):** still deferred; not plan quota.

Note: fix-plan Steps 3 / 23 partially overlap the runner fallback and
cross-check work already landed here — narrow or mark done when executing
those numbered steps so they are not re-done blindly.

## Related: local JSONL / ccusage (not a substitute)

Parsing `~/.claude/stats-cache.json` or session `.jsonl`, or running **ccusage**,
gives **token burn** on this machine — not the website’s session/weekly % bars.
Those bars come from the OAuth usage API (what cswap already fetches). Details:
[`claude-local-usage.md`](claude-local-usage.md).
