# Cursor quota reliability

**Symptom:** `ai` showed three identical “Cursor monthly quota (1/2/3)” bars,
invented separate dollar-at-risk for each, and raised **CONSERVE** on quota (3)
at 0% left while the Cursor dashboard still showed **Included ~61% used**
(~39% left) and On-Demand `$1.47 / $2`.

## Cause

CodexBar maps Cursor’s dashboard rows to `primary` / `secondary` / `tertiary`
plus `providerCost`:

| CodexBar                                        | Cursor UI                                 |
| ----------------------------------------------- | ----------------------------------------- |
| `primary`                                       | **Included** (overall included usage)     |
| `secondary`                                     | **Auto** (category breakdown of Included) |
| `tertiary`                                      | **API** (category breakdown of Included)  |
| `providerCost` (`period: Monthly`, `limit > 0`) | **On-Demand** `$used / $limit`            |

Without fixed slot labels, `_slot_label` fell back to “monthly quota (N)”.
Without `shared_allotment`, Auto/API were scored as independent burn windows,
so a maxed API category looked like a lockout even when Included still had
headroom. On-Demand was ignored (only OpenCode Zen’s `providerCost` was read).

## What `ai` does

Cursor's confirmed current billing model (2026-08) has **two independent
monthly pools** for individual plans: Included+Auto (first-party,
"Auto+Composer" in Cursor's own docs) and API (third-party, billed at
provider rates). Exhausting one has no effect on the other.

1. Label slots **Cursor included** / **Cursor Auto** / **Cursor API**.
2. Default `analysis.provider_overrides.cursor.shared_allotment: true`, plus
   `independent_pool_key()` hard-separating **Cursor API** into its own pool
   (`analysis/pace.py`): **Included** governs the Included+Auto pool (Auto is
   its suppressed child), while **API** is pace-scored independently and can
   raise its own CONSERVE/BURN alert without regard to Included/Auto's state,
   and vice versa.
3. Parse on-demand `providerCost` into `usage_credits` when `limit > 0`.

## Verify

```bash
codexbar usage --provider cursor --source web --json --no-color
ai --full -q --no-tui
```

Expect Included ~39% left when the dashboard shows ~61% used, Auto as a
breakdown line, on-demand ~$0.53 remaining, and no CONSERVE on Included/Auto
solely because API shows 100% used — but API's own exhaustion now correctly
raises its own CONSERVE/BURN alert (as its own independent pool, not
suppressed as a child of Included).
