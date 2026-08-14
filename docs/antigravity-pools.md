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

CodexBar (and OpenUsage) expose four windows for Antigravity:

| Window            | Family                                                       |
| ----------------- | ------------------------------------------------------------ |
| Gemini 5-hour     | Gemini (Google)                                              |
| Gemini weekly     | Gemini (Google)                                              |
| Claude/GPT 5-hour | Google non-Gemini (Claude/GPT via Google AI — **not** cswap) |
| Claude/GPT weekly | Google non-Gemini                                            |

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

Cursor Included/Auto stay one pool (Other Models is separate via
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
