---
schema_version: 1
handoff_id: 81aa
parent_handoff_ids: []
lineage: none
chain: [aiuse-opencode]
repo: aiuse
workspace: /Users/djbclark/src/aiuse
branch: main
head_sha: 39b754cc13ce935f78525c3cf2ee0f0ace93c925
created_at: 2026-10-12T03:35:00Z
writer: opencode
---

# Handoff — a depleted clock now forces its row tag to `empty` (released in 3.3.11)

## The Goal

Operator reported (open-ended, no bead at the time): in the default clock
matrix a codex row showed `slow  29 codex  gmail  —  100u/0l/2h12m
47u/53l/6d11h  <- EXHAUSTED` — the tag column said `slow` while the row's own
right-hand marker said `EXHAUSTED`, and the operator expected `empty`.

## Where We Are

Done, end to end. Fixed in `1f8d77d`, released as **3.3.11** (PyPI OIDC +
GitHub release + Homebrew tap; `brew test` green; this Mac upgraded and both
`aiuse --version` / `ai --version` print 3.3.11). Bead `aiuse-rnx` closed and
Dolt-synced.

## What It Actually Was

The two indicators used different evidence and the tag could silently lose the
stronger one:

- The tag is the pool's **alert** band (`report.py:1619`, band from
  `pool_alert` at `report.py:1327`).
- The `EXHAUSTED` marker is computed from the **raw clock cells** (any clock
  ≥99% used → `report.py:1636-1642`, appended at `report.py:1670-1671`).
- A fully depleted window raises **no alert of its own**: zero projected waste
  classifies `on_pace` (`use_or_lose.py:418-419` `continue`). So the only alert
  for that pool was the sibling weekly conserve one → band `slow`.
- The fallback that would have said `empty` (`_unalerted_window_band`, keyed on
  the governing window) was only consulted when the pool had _no_ alert at all.

Proof it was a bug, not a design choice: the identical 5h depletion rendered
`empty 0` with the weekly on-pace (no alert) and `slow 40` with the weekly
conserve (alert) — the tag flipped on whether an unrelated alert existed.
`governing_partition` (`pace.py:104-125`) and `_BAND_ATTENTION`'s comment
(`report.py:1179-1189`) both already say a depleted window governs and should
make the pool read exhausted, matching `usable_now: false` in `--json`.

## Key Decisions

- Operator chose "tag the row `empty`" over the alternatives (align the ≤1%
  boundary too; fix the marker instead; document-only).
- Fix is narrow: merge the pool's own band with the alert band by
  `_BAND_ATTENTION`, i.e. an exhausted own-band (attention 4) beats a sibling
  `slow`/`use`; a sibling alert otherwise still wins. Clock cells, the
  `EXHAUSTED` marker, alerts, `--json` and the ladder renderer are unchanged.
- Deliberately **not** done: the ≤1% boundary. The marker fires at ≥99% used
  while `_unalerted_window_band` uses `remaining <= 0`, so a `0.4% left` window
  still tags `slow` (cells show `100u/<1l`). Operator picked option 1 only;
  this is the one remaining known edge, listed below.
- `render_priority_ladder` was left alone — it is test-only (no `src` caller).

## Evidence & Data

- Files: `src/aiuse/report.py` (`_build_matrix_rows`), `tests/test_report.py`
  (`test_clock_matrix_depleted_clock_reads_empty_despite_sibling_conserve_alert`),
  `docs/pretty-display.md` (empty-band bullet).
- Tests: 952 passed; `just ci` green.
- Also in this release: `fe59641` fixes a **pre-existing** `just ci` failure —
  the sipb row from `85ed4ff` broke the `docs/provider-identity.md` table
  padding (prettier), so the gate was red on a clean tree.
- Release: https://github.com/djbclark/aiuse/releases/tag/v3.3.11 ·
  https://pypi.org/project/aiuse/3.3.11/ · publish workflow success in 33s ·
  tap commit `1ebf067`.

## Where We're Going

1. Optional follow-up (operator's call): align the pool-level depleted test with
   the marker's ≥99% used so `0.4% left` also reads `empty`.
2. Nothing else mandatory. Open-ended "what next?" → `docs/next-options.md`
   (issues #16/#17/#18 then #11–#15).

## Detached jobs

None. (The interrupted `docs/handoffs` write was retried as this file.)

## Quick Start

```bash
cd ~/src/aiuse && git log --oneline -3     # 39b754c on main, pushed
git show 1f8d77d --stat                    # the fix
aiuse --version                            # 3.3.11 (Homebrew)
just ci                                    # green
```
