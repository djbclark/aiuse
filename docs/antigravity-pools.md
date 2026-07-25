# Antigravity independent pools (Gemini vs Claude/GPT)

**Symptom:** `aiuse` listed Google AI / Antigravity once (one ladder row / one
governing window) even though the product has **two hard-separated budgets**.

## Cause

CodexBar (and OpenUsage) expose four windows for Antigravity:

| Window | Family |
| --- | --- |
| Gemini 5-hour | Gemini |
| Gemini weekly | Gemini |
| Claude/GPT 5-hour | non-Gemini (Claude / GPT routed through Google AI) |
| Claude/GPT weekly | non-Gemini |

Within each family, 5h ⊂ weekly (shared allotment). **Across** families the
budgets do not draw from each other — burning Gemini does not free Claude/GPT
quota and vice versa.

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

Cursor Included/Auto/API and Claude 5h⊂weekly stay single pools (no family
markers).

## Verify

```bash
codexbar usage --provider antigravity --format json --no-color
aiuse --no-tui -q
```

Expect two Antigravity lines (Gemini weekly and Claude/GPT weekly, or alerts
for each when off-pace), not a single combined row.
