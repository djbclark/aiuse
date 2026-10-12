# Handoff: pace replaces $ display; released 3.4.0 (2026-10-12)

Resume point for aiuse after the pace-display work. Released as **3.4.0** (GitHub, PyPI, Homebrew).

## What changed

1. Clock matrix: `$ UNUSED` column → `PACE` (row's highest window pace; `—` when any window is
   exhausted). Every window cell ends in a signed pace percent (`78u/22l/3d17h +66%`). Free text
   (`<-`, `EXHAUSTED`, extra credits, n/a notes) now follows all fixed columns, fixing the alignment
   bug. Footer explains pace. Stderr header no longer prints "Available capacity $X".
2. Pace definition: `round((pace_ratio - 1) * 100)`, unblended, `pace_ratio = used / elapsed`
   (elapsed floored at 5%). One helper: `window_pace_percent` / `overall_pace_percent` in
   `src/aiuse/analysis/pace.py`, used by matrix, Fable line, `--available`, JSON, `--full`, `--for-chat`.
3. JSON schema **1.2**: windows `pace_percent`, `pace_ratio`; accounts `overall_pace_percent`
   (`docs/json-contract.md`).
4. `--full`: no estimated `$` (value at risk, per-window $); real balances/credits stay. Alert lines show
   the alert's projection pace as a signed percent, `(history-blended)` when history fed it.
5. `--for-chat`: pace inside each window's code span, overall pace on headers, projection-only
   sub-lines, legend line, "reset time unavailable" wording fix.

## Commits

af51743, 1d892a1, 7328e6a, 3b95ac0, 93cb9b9, then the 3.4.0 release commits.
Implemented by codex (gpt-6-luna, gpt-6.1-sol) and agy (gemini-3.8-flash-high) slices; briefs, reports
and batch log in `~/.local/state/bigteam/aiuse-pace-headline/`.

## Open

1. Low-priority bead: oc-go monthly pace uses a fixed 43200-minute month; the vendor uses calendar
   months. The +1351% reading on 2026-10-12 was real usage (vendor re-anchored the meter Oct 10).
2. Early in a window pace is exaggerated by the 5% elapsed floor (e.g. +1351% one day into a month).
   A display-side cap or a "too early" marker could help if it bothers the operator.
