---
schema_version: 1
handoff_id: f1a4
parent_handoff_ids: [a17f]
lineage: a17f
chain: [aiuse-1qo, aiuse-e9d, aiuse-oja, aiuse-al9, a17f, f1a4]
repo: aiuse
workspace: aiuse
branch: main
head_sha: ba16e2152aff2aef684dc69d196b40c2833931c1
created_at: 2026-09-27T12:10:02-04:00
writer: ZCode
---

# Handoff — grok plan reset window beside wallet, release 3.1.0

## The Goal

Operator: "Currently grok is only showing the monetary countdown. Also include
how long it is until (hourly/weekly/)monthly plan resets. Ditto for any other
TUIs that can be in a similar situation." Then, mid-session: after done and
tested, push a **3.1.0** release.

## Where We Are

**Feature done, tested, pushed (`ba16e21`); 3.1.0 release executed this
session** (operator-authorized full release).

Root cause was two layers deep:

1. **Display** — the clock matrix already had grok's weekly window (CodexBar
   carries `window_minutes: 10080` + `resets_at`), but the empty-band wallet
   note (`$14.46 extra credits`) **replaced the whole row** at render time, so
   the `100%/3d` cell never appeared. Fix: `_MatrixRow.tail_note` — the wallet
   now rides along as a dim trailing cell after the clock cells; note-only
   rendering is reserved for rows with genuinely no clocks. `_matrix_needed_width`
   accounts for the tail so zebra stripes span it. This is generic: any
   provider row with windows + an extra-usage wallet benefits (not grok-specific).
2. **Collector** — `grok_billing` parsed only `config.prepaidBalance` and
   dropped the reset data sitting in the same payload. It now parses
   `currentPeriod` (`USAGE_PERIOD_TYPE_{HOURLY,DAILY,WEEKLY,MONTHLY}` →
   `hourly/daily/weekly/monthly plan` labels + nominal minutes 60/1440/10080/43200,
   unknown → `plan`/None) + `creditUsagePercent` into a `QuotaWindow`, and
   `productUsage` into a `Plan pools: …` note. A subscriber with an **empty
   wallet** now still produces a row (window alone suffices); a wallet-only
   legacy payload keeps the old row shape. `_merge_grok_extra_credits` selects
   the billing row by wallet **or** windows and folds the billing window only
   into hosts that have none (CodexBar's window wins, no duplication).

Validation: 699 tests (689 → +10: 6 collector/merge, 2 matrix, plus widened
existing), `just ci` fully green. Live matrix before/after:
`empty 0 grok … $14.46 extra credits` → `empty 0 grok … -> 100%/3d <- $14.46 extra credits`.
Standalone (CodexBar-off) simulation renders the weekly clock from billing alone.

## Key Decisions

- **Wallet is inventory, not a clock** — it may annotate a row but never
  suppress reset cells. Tail note applies at `_BAND_EMPTY` only; the
  note-as-row path is now band-independent (wallet-only `mid` rows no longer
  render as bare dashes).
- Labels deliberately name the period (`weekly plan`) so `clock_from_label`
  buckets the window without guessing from reset distance — same trick the
  rest of the codebase uses.
- `onDemandCap`/`onDemandUsed` deliberately **not** surfaced (zero/dupe noise
  on this account); revisit only if a payload shows nonzero caps.
- Version 3.1.0 (minor, not patch): new user-visible capability + JSON rows
  gain windows where they had none. Script-enforced policy allows next-minor
  with patch reset.

## Evidence & Data

- Live billing payload (2026-09-27): `currentPeriod` WEEKLY
  2026-09-24→2026-10-01, `creditUsagePercent` 100.0, `productUsage`
  GrokBuild 93% / GrokChat 7%, `prepaidBalance` 1446 cents.
- Surfaces audited for the "ditto": full ladder, action plan, chat format,
  status/prompt, and TUI all already render resets from windows — the clock
  matrix was the only suppressing surface. True wallet-only rows (deepseek,
  oc-zen, openrouter, qwen token plan) have **no** reset in their sources;
  nothing to add there. qwencloud already parses 5h/weekly/monthly resets.
- Docs: `docs/grok-quota.md` gained "How the plan reset surfaces" (3.1.0);
  AGENTS.md status + packaging.md version note bumped to 3.1.0.

## Where We're Going

Nothing pending. Still parked: public announce via
[#10](https://github.com/djbclark/aiuse/issues/10) (operator-only), optional
[#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15).

## Quick Start

```bash
git log --oneline v3.0.40..HEAD         # this release's contents
aiuse 2>/dev/null | grep grok           # weekly cell + wallet on one line
just release-dry 3.1.1                  # next release preview
```
