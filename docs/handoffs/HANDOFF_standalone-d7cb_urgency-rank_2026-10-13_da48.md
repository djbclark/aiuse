---
schema_version: 1
handoff_id: da48
parent_handoff_ids: [93f7]
lineage: deterministic
chain: [standalone-d7cb, aiuse-57n, aiuse-p9f, aiuse-hcy]
repo: aiuse
workspace: main
branch: main
head_sha: 1ed6f98
created_at: 2026-10-13T16:58:57-04:00
writer: omp
---

# Handoff: `/steps` walk finished; operator paused the session

## The Goal

Resume of `93f7`: walk the open `/steps` items (5H label, windows that are in
use but have no reset time, aiuse-b31, aiuse-qm7) and implement what the
operator picks. For this session only: the lead was Opus 5.5 at medium effort
inside omp, sub-agents were Sonnet 5.5 (coding/review) and Haiku 5.5
(scouts), set through session-only `task.agentModelOverrides`. Do not document
that routing as a rule.

## Where We Are

`main` is at `1ed6f98`, pushed, and the tree is clean apart from this handoff.
`just ci` passes with **1089 tests**. Beads are synced.

Landed this session:

1. `7dc585c`: an unused sub-day rolling window shows its full length and the
   `↻` marker (`0u/100l/5h↻`), with the legend line
   `↻ = clock starts on first use` (operator picked `↻`). Windows of a day or
   longer get no span. Screen only. AGENTS.md now says to smoke-test with
   `AIUSE_SKIP_HOMEBREW_FORMULA=1`.
2. `f45ce72`: a dropped `resets_at` is carried forward from an earlier
   reading. The hook is `collectors/runner.py` `_carry_forward_resets`; the
   logic is `analysis/history.py` `find_carried_resets`. The value is marked
   estimated (`~`) and carries JSON provenance in `resets_at_carried_from`.
   Sub-day estimated deadlines read `~Nh`.
3. `93115b2`: 12-hour am/pm local time in every string a person reads.
   `format_clock` moved to `aiuse.timefmt` and gained `weekday=True`. JSON,
   filenames and ledger keys keep ISO.
4. `58cd533`: qm7 and b31. Value-at-risk uses the per-account price
   (`plan_monthly_price`). All bands are ordered by value-at-risk USD,
   log-scaled from $0.01 to $100. Beads aiuse-qm7 and aiuse-b31 are closed.
5. `1ed6f98`: the use and mid bands rank by
   `projected_waste_fraction × cycle value` and fall back to capacity when no
   pace is known; the slow band keeps capacity. `pick_suggestion` and the
   status line's burn head reuse the board's sort key, which moved to
   `aiuse.analysis.ranking` with no shims. Replaying 3192 snapshots, suggest
   matched the board's top row 22% of the time before and 96.6% after.

## What We Tried

1. The first smoke test of `↻` showed no marker. Cause: `.venv/bin/aiuse`
   re-execs the Homebrew 3.4.1 install (`prefer_homebrew_formula`). Setting
   `AIUSE_SKIP_HOMEBREW_FORMULA=1` fixed it, and the rule is now in AGENTS.md.
2. One commit failed because a sub-agent edited files while the pre-commit
   hook was running. Commit only when no sub-agent is editing the tree.

## Key Decisions

1. Marker: `↻` (U+21BB, East Asian Width N), used in the rows and the legend.
2. In-use window with no reset time: carry the last known reset forward,
   rather than leaving it blank or estimating first use + 5h. Evidence: CodexBar
   `widget-snapshot` drops the Antigravity 5h `resetsAt` and OpenUsage reports
   it (the Gemini 5h sequence 14:27 to 17:46Z on 2026-10-13).
3. 12-hour am/pm everywhere for people; the lead chose local time via the
   existing `format_clock`.
4. Ranking: qm7 first, then b31 using value-at-risk, which normalises for
   window length. Use and mid are then urgency-weighted by projected waste.
   Suggest is aligned to the board.

## Evidence & Data

1. Sampler cadence over the last 48h: median gap 10 min, maximum 61 min.
2. Carry-forward replay: all 18 in-use CodexBar readings get 17:37:26Z from
   the 14:27Z OpenUsage reading, and the used-0 readings get nothing.
3. Urgency replay: the mid band reordered in 37.7% of snapshots, use in 27.2%,
   slow in 0%. The board's top actionable row changed in 5.0% of snapshots.

## Operator Feedback

1. The operator worried the session was hung. It was not: high-effort Sonnet
   sub-agents took 12 to 44 minutes each. Next time, give progress updates
   unprompted during long waits.
2. "pause (at an opportune moment, without losing tokens by killing
   things)": UrgencyRank was left to finish, then committed. The session
   stopped here.

## Where We're Going

1. aiuse-57n (P3): suggest and the board disagree when a 5h burn shares a pool
   with a weekly conserve row (pool masking). This is the residual 100 of 3192.
2. aiuse-p9f (P3): should early windows (under 15% elapsed) be gated out of
   projected-waste ranking? Operator call. It would reorder the mid band in
   35.4% of snapshots.
3. aiuse-hcy (P3): open an upstream CodexBar PR so `widget-snapshot` keeps
   the Antigravity 5h `resetsAt` (AGENTS.md upstream rule).
4. Minor, not filed: a chronic-waste row keeps its old account label when it
   resolves to an anonymous live window (from 93f7).
5. No release unless the operator asks (`just release X.Y.Z`).

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git status -s && git log --oneline -6
bd show aiuse-57n aiuse-p9f aiuse-hcy
AIUSE_SKIP_HOMEBREW_FORMULA=1 COLUMNS=170 .venv/bin/aiuse watch --once --no-color
~/ops/site-private/bin/bg .venv/bin/python -m pytest -q   # expect 1089 passed
```
