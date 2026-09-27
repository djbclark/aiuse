---
schema_version: 1
handoff_id: aa64
parent_handoff_ids: [f1a4]
lineage: deterministic
chain: [aiuse-1qo, aiuse-e9d, aiuse-oja, aiuse-al9, a17f, f1a4, aa64]
repo: aiuse
workspace: aiuse
branch: main
head_sha: a85a73aeef7586ccc11fbfad7556f60b29c02bf7
created_at: 2026-09-27T12:30:00-04:00
writer: ZCode
---

# Handoff — grok plan reset shipped, release 3.1.0 closed out

Session-close handoff. Supersedes [f1a4](HANDOFF_zcode-grok-plan-reset-release-3-1-0_2026-09-27_f1a4.md)
(written mid-session, before the release ran) as the newest resume point; that
doc carries the full feature narrative — this one carries the close-out state.

## The Goal

Operator asked for grok to stop showing only the monetary countdown and also
show time until the plan resets (hourly/weekly/monthly), with the same fix
applied anywhere else in that situation; then, mid-session, to push a **3.1.0**
release once done and tested. Both done. This handoff was requested explicitly
at session close.

## Where We Are

**Everything landed, released, and verified. Tree clean, all pushed, beads
synced, no in-progress beads.**

- `ba16e21` — feat(grok): plan reset window beside the prepaid wallet.
  Two fixes in one: (1) the clock matrix's empty-band wallet note used to
  _replace_ the whole row, hiding the weekly reset cell CodexBar had already
  collected — wallets now ride along as `_MatrixRow.tail_note`; (2) the
  `grok_billing` collector now parses the billing payload's `currentPeriod`
  (`USAGE_PERIOD_TYPE_{HOURLY,DAILY,WEEKLY,MONTHLY}` → label + nominal
  minutes 60/1440/10080/43200) + `creditUsagePercent` into a `QuotaWindow`,
  and `productUsage` into a note. Empty-wallet subscribers get a row;
  merge folds the billing window only into window-less hosts.
- `ed37e1a` — docs: grok-quota.md "How the plan reset surfaces", AGENTS.md
  status → 3.1.0, packaging.md version note, mid-session handoff f1a4.
- `f471ed1` + `a85a73a` — release 3.1.0 via `just release 3.1.0
--notes-file …`: tag `v3.1.0`,
  [GitHub release](https://github.com/djbclark/aiuse/releases/tag/v3.1.0),
  [PyPI 3.1.0](https://pypi.org/project/aiuse/3.1.0/) via OIDC, Homebrew
  formula + tap refreshed, `brew test` green, pipx upgraded, `aiuse --version`
  / `ai --version` verified at 3.1.0.
- Live post-release check: pipx 3.1.0 renders
  `empty 0 grok … -> 100%/3d <- $14.42 extra credits`.
- 699 tests passing (+10 vs the 689 baseline), `just ci` fully green.

## What We Tried

- **Hunted the collector first — wrong (only) layer.** `aiuse --json` showed
  grok's weekly reset already present (CodexBar `window_minutes: 10080` +
  `resets_at`), so the collector looked innocent; the display was suppressing
  it. The correct fix needed _both_ layers: the display bug masked the host
  window, and the collector genuinely dropped the billing API's own reset
  data (grok loses reset visibility entirely when CodexBar is off).
- **Gating the wallet note on `_BAND_EMPTY` alone — caught before commit.**
  A usage_credits-only row (standalone billing wallet, UNKNOWN billing kind,
  no windows) lands in band `mid` with no clocks; EMPTY-only gating left it
  rendering as bare dashes. Final shape: `tail_note` only at EMPTY, but the
  note-as-row path is band-independent.
- **Assumed other prepaid collectors hid reset data — audit said no.**
  qwencloud already parses 5h/weekly/monthly `next_reset_at`; deepseek,
  oc-zen, openrouter, qwen token plan are true rolling wallets with no reset
  in their sources. muse is a spend-up meter. Nothing to add — the matrix was
  the only suppressing surface; ladder/chat/status/TUI all already rendered
  resets.
- **Inline `--notes '…'` through `just release` — failed.** just interpolates
  `{{args}}` unquoted, so parentheses in the notes text became bash syntax
  errors. Use `--notes-file <path>` (works, went into the release notes).

## Key Decisions

- **Wallet is inventory, not a clock** — it may annotate a row but never
  suppress reset cells. Rejected: keeping the old note-replaces-row behavior
  (that was the bug).
- **Labels deliberately name the period** (`weekly plan`) so
  `clock_from_label` buckets the window without guessing from reset distance.
- **`onDemandCap`/`onDemandUsed` not surfaced** — zero/dupe noise on the live
  account; revisit only if a payload shows nonzero caps.
- **3.1.0 minor, not patch**: new user-visible capability + JSON grok rows
  gain `windows` where they previously had none. Patch rejected as under-
  scoping a behavior change; the release script's policy allows next-minor.
- **Docs + AGENTS refresh committed _before_ running the release script** so
  the deterministic release ran on a clean tree (no `--allow-dirty`).
- **Auto-memory cleanup**: the stale "3.0.40 release pending go" memory was
  deleted (3.0.40 shipped earlier today; 3.1.0 shipped this session).

## Evidence & Data

- Live billing payload (2026-09-27): `currentPeriod` WEEKLY
  2026-09-24T06:41→2026-10-01T06:41 UTC, `creditUsagePercent` 100.0,
  `productUsage` GrokBuild 93% / GrokChat 7%, `prepaidBalance` 1446 cents.
- Matrix before → after:
  `empty 0 grok … $14.46 extra credits` →
  `empty 0 grok … -> 100%/3d <- $14.46 extra credits`.
- Commits this session: `ba16e21`, `ed37e1a`, `f471ed1`, `a85a73a` (on top of
  `78f3bb8`, the 3.0.40 close-out from the a17f/f1a4 thread).
- Tests touched: `tests/test_grok.py` (+6: plan window parsing, period-type
  mapping, empty-wallet row, merge fold/no-dup/window-only-keep),
  `tests/test_report.py` (+2: reset-beside-wallet, wallet-only note).

## Operator Feedback

- Mid-session: "After this is done and tested, we should push a 3.1.0
  release." — explicit full-release authorization (PyPI + Homebrew), which
  AGENTS.md otherwise reserves for explicit asks. Honored as one-shot for
  3.1.0 only; future releases still need their own go.
- Session close: "handoff" — this document.

## Where We're Going

1. **Nothing pending from this session.** For a fresh session: read AGENTS.md
   Active priorities and stop re-deriving — do not restart completed work.
   The only standing open thread is the operator-only public announce via
   [#10](https://github.com/djbclark/aiuse/issues/10); optional polish is
   [#11](https://github.com/djbclark/aiuse/issues/11)–[#15](https://github.com/djbclark/aiuse/issues/15)
   only if pain (`docs/next-options.md`).
2. Watch item (no action unless observed): `USAGE_PERIOD_TYPE_*` values beyond
   the four known ones fall back to label `plan` with clock inferred from
   reset distance — fine by design, but a new xAI period type would land
   there silently.

## Quick Start

```bash
git log --oneline v3.0.40..HEAD        # this session's release contents
aiuse 2>/dev/null | grep grok          # weekly cell + wallet on one line
.venv/bin/python -m pytest -q          # 699 passing
just release-dry 3.1.1                 # next release preview
```
