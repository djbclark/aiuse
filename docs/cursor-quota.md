# Cursor quota reliability

Cursor's agent CLI is `cursor-agent` — every report line for this provider
carries that name (as `Cursor (cursor-agent)`, see `PROVIDER_DISPLAY_NAMES`
in `models.py`) so `grep -i cursor-agent` finds it.

**Symptom:** `ai` showed three identical “Cursor monthly quota (1/2/3)” bars,
invented separate dollar-at-risk for each, and raised **CONSERVE** on quota (3)
at 0% left while the Cursor dashboard still showed **Included ~61% used**
(~39% left) and On-Demand `$1.47 / $2`.

## Cause

CodexBar maps Cursor’s dashboard rows to `primary` / `secondary` / `tertiary`
plus `providerCost`:

| CodexBar                                        | Cursor UI / docs name                         | `aiuse` label             |
| ----------------------------------------------- | --------------------------------------------- | ------------------------- |
| `primary`                                       | **Included** / Cursor Models (overall)        | **Cursor included**       |
| `secondary`                                     | **Auto** (category of Included)               | **Cursor Auto**           |
| `tertiary`                                      | **Other Models** (was “API” in older UI copy) | **Cursor other models**   |
| `providerCost` (`period: Monthly`, `limit > 0`) | **On-Demand** `$used / $limit`                | usage credits / on-demand |

Without fixed slot labels, `_slot_label` fell back to “monthly quota (N)”.
Without `shared_allotment`, Auto and Other Models were scored as independent
burn windows, so a maxed Other Models category looked like a lockout even when
Included still had headroom. On-Demand was ignored (only OpenCode Zen’s
`providerCost` was read).

## Two monthly pools (not prepaid)

Cursor's confirmed billing model has **two independent monthly pools** for
individual plans (Pro / Pro+ / Ultra). Both reset with the billing cycle.
Other Models is **not** a non-expiring prepaid wallet (unlike DeepSeek /
OpenRouter) — unused headroom is use-or-lose at month end.

In `aiuse`, a Cursor CodexBar/OpenUsage row with those windows is classified as
`billing_kind: subscription_window` (because each slot has `resetsAt`). That
puts Other Models through pace / use-or-lose scoring like Included — burn,
conserve, or on-pace `mid` — never the prepaid `n/a` lane.

| Pool (Cursor docs) | What burns it                                                          | Included amount (individual)                        |
| ------------------ | ---------------------------------------------------------------------- | --------------------------------------------------- |
| **Cursor Models**  | First-party: **Cursor Grok 4.5**, **Composer 2.5**; Auto **Cost** mode | “Generous included usage” (size not published as $) |
| **Other Models**   | Third-party models at that model’s API price                           | Pro **$20**/mo · Pro+ **$70** · Ultra **$400**      |

Exhausting one pool has no effect on the other. After included Other Models is
gone, **On-Demand** (CodexBar `providerCost`) is the optional pay-as-you-go
dollar cap.

### What counts as Other Models

Anything you pick that is **not** Cursor Grok 4.5 or Composer 2.5 draws from
Other Models at the provider rate. Cursor’s model list currently includes
families such as:

- **Anthropic** — Claude Sonnet / Opus / Haiku (4.x–5.x lines and variants)
- **OpenAI** — GPT-5.x, GPT-5-Codex and related variants
- **Google** — Gemini 2.5 / 3.x Flash and Pro (and image preview where listed)
- **Moonshot** — Kimi K2.7 Code, Kimi K3
- **Z.ai** — GLM 5.2

Auto **Balance** / **Intelligence** can also charge Other Models when the
router picks a third-party model; Auto **Cost** stays on bundled Auto pricing
against the Cursor Models side. Canonical pricing table:
[Models & Pricing](https://cursor.com/docs/models-and-pricing).

## What `aiuse` does

1. Label slots **Cursor included** / **Cursor Auto** / **Cursor other models**.
2. Default `analysis.provider_overrides.cursor.shared_allotment: true`, plus
   `independent_pool_key()` hard-separating **Cursor other models** into its
   own pool (`cursor_other_models` in `analysis/pace.py`; still matches legacy
   “Cursor API” labels): **Included** governs the Included+Auto pool (Auto is
   its suppressed child), while **Other Models** is pace-scored independently
   and can raise its own CONSERVE/BURN alert without regard to Included/Auto’s
   state, and vice versa.
3. Parse on-demand `providerCost` into `usage_credits` when `limit > 0`.

## Verify

```bash
codexbar usage --provider cursor --source web --json --no-color
aiuse --full -q --no-tui
```

Expect Included ~39% left when the dashboard shows ~61% used, Auto as a
breakdown line, on-demand ~$0.53 remaining, and no CONSERVE on Included/Auto
solely because Other Models shows 100% used — but Other Models’ own exhaustion
now correctly raises its own CONSERVE/BURN alert (as its own independent pool,
not suppressed as a child of Included).
