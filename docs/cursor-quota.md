# Cursor quota reliability

Cursor's agent CLI is `cursor-agent`. Every report line for this provider is
named `cursor` (see `PROVIDER_DISPLAY_NAMES` in `models.py`).

**Symptom (historical):** three identical “Cursor monthly quota (1/2/3)” bars,
invented dollar-at-risk for each, and **CONSERVE** on quota (3) at 0% left while
the dashboard still showed **total ~61% used** (~39% left) and on-demand
`$1.47 / $2`.

## Cause

CodexBar maps Cursor’s dashboard rows to `primary` / `secondary` / `tertiary`
plus `providerCost` and `extraRateWindows`:

| CodexBar slot / id                              | Cursor UI / docs name                      | `aiuse` label | `pool_family` |
| ----------------------------------------------- | ------------------------------------------ | ------------- | ------------- |
| `primary`                                       | **Total** (monthly umbrella)               | **total**     | `total`       |
| `secondary`                                     | **Auto** (Cursor Auto/Composer)            | **auto**      | `auto`        |
| `tertiary`                                      | **Third Party** (named third-party models) | **3rd-party** | `third_party` |
| `providerCost` (`period: Monthly`, `limit > 0`) | **On-demand** `$used / $limit`             | **on-demand** | `on_demand`   |
| `extraRateWindows` id `cursor-grok-bot`         | **Grok Bot** weekly allowance              | **grok-bot**  | `grok_bot`    |

**Do not assume** `total = auto + third-party`. CodexBar only reports
percentages per row; aiuse treats **total** as the monthly ceiling row and
**auto** / **third-party** as breakdown sub-rows whose effective headroom is
`min(own, total)`.

**Grok-bot family (2026-10-14):** Cursor Grok Bot weekly allowance (counted on
your Cursor account; not shared with Grok app/CLI/grok.com usage; size may come
from a linked SuperGrok plan). It is not one of the monthly Cursor pools. aiuse
keys it on the stable window id `cursor-grok-bot` (never the display title
alone). Display label is exactly `grok-bot`; config/JSON family is `grok_bot`.

Without fixed slot labels, `_slot_label` fell back to “monthly quota (N)”.
On-demand was ignored (only OpenCode Zen’s `providerCost` was read initially).

## Monthly pools and spend cap (not prepaid)

Cursor exposes:

- **total** — umbrella for all monthly Cursor subscription usage (ceiling row;
  hidden by default when auto/third-party sub-rows are present).
- **auto** — Cursor Auto/Composer models (breakdown; not scored for headline pace).
- **third-party** — named third-party models at provider rates (breakdown; not
  scored for headline pace).
- **on-demand** — pay-as-you-go dollar cap (`providerCost`); separate from total.
- **grok-bot** — Cursor Grok Bot weekly allowance (counted on your Cursor
  account; not shared with Grok app/CLI/grok.com usage; size may come from a
  linked SuperGrok plan); never capped by total. Usage past the allowance
  overflows to Cursor on-demand spend if on-demand is enabled.

Both **total**, **auto**, and **third-party** reset with the billing cycle
(use-or-lose). **On-demand** is a spend cap, not a prepaid wallet. **grok-bot**
is weekly.

| Pool (aiuse label) | What burns it                                                                                                                                              |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **total**          | All monthly Cursor subscription usage (umbrella meter)                                                                                                     |
| **auto**           | Cursor Auto/Composer model selection                                                                                                                       |
| **third-party**    | Third-party models at that model’s API price                                                                                                               |
| **on-demand**      | Pay-as-you-go spend against the configured dollar cap                                                                                                      |
| **grok-bot**       | Cursor Grok Bot weekly allowance (counted on your Cursor account; not shared with Grok app/CLI/grok.com usage; size may come from a linked SuperGrok plan) |

CodexBar percentages for total, auto, and third-party are independent readings;
aiuse does not derive one from the others.

### Third-party examples

Anything that is not bundled Auto/Composer draws **third-party** at provider
rates — Anthropic Claude, OpenAI GPT, Google Gemini, Moonshot Kimi, Z.ai GLM,
etc. Auto **Balance** / **Intelligence** can route to third-party models;
Auto **Cost** stays on bundled pricing against the monthly pools.

Canonical pricing table:
[Models & Pricing](https://cursor.com/docs/models-and-pricing).

## What `aiuse` does

1. Label slots **total** / **auto** / **3rd-party** / **on-demand** /
   **grok-bot** (lowercase display labels).
2. Mark **total** as a ceiling (`role: ceiling` in JSON); **auto** and
   **third-party** as breakdown rows capped by total headroom when lower.
   The default clock matrix shows that tighter remaining figure on the
   breakdown row and omits the "own N% left, capped by total" sentence.
   `aiuse --full` and `aiuse --for-chat` still print it.
3. Default `analysis.provider_overrides.cursor.shared_allotment: true`, with
   `independent_pool_key()` separating on-demand and grok-bot.
4. Parse on-demand `providerCost` into `usage_credits` when `limit > 0`.
   The clock matrix prints that remainder as `$1.64 (counts down)` after the
   last column of the auto row. It is not a percent-left row, and it does not
   change any pool's used/left percent.
5. Hide ceiling **total** rows by default in human outputs and `--available`
   (`analysis.show_total_rows = false`); watch `a` / `--all-providers` shows
   them again (operator-only).
6. `[hidden_pools]` drops configured rows at collection; `[analysis.excluded_pools]`
   omits them from `--available` routing only.

`aiuse` sets no on-demand cap of its own — the figure is whatever Cursor reports.

## Verify

```bash
codexbar usage --provider cursor --source web --json --no-color
aiuse --full -q --no-tui
```

Expect **total** hidden on the default board when sub-rows exist, **auto** and
**third-party** visible, on-demand spend when configured, and **grok-bot** as its
own weekly row. Breakdown pace does not drive the account headline or exit code.
