---
schema_version: 1
handoff_id: 43a0
parent_handoff_ids: []
lineage: none
chain: [aiuse-fmv, aiuse-b05]
repo: aiuse
workspace: main
branch: main
head_sha: d4fb04ff7a83393836bb0d9f7fc1c3a146baec6b
created_at: 2026-10-14T23:15:56-04:00
writer: cursor-agent
---

# Handoff — clock matrix display, then a scored Fable row

## The Goal

Finish the ended Codex session `01a13cfe-c9a6-76a0-a499-4fcd0ea0b85d`, then the operator's display list: drop the Cursor cap sentence, drop the redundant `EXHAUSTED` tag, keep `watch --all` on the live board, label the pool `3rd-party`, show on-demand as dollars, and decide whether the Fable row should score.

## Where We Are

`main` is clean and pushed at `d4fb04f`. Beads `aiuse-fmv` and `aiuse-b05` are closed; Dolt was pushed after `aiuse-b05`. `aiuse-jcs` is a closed placeholder from a botched `bd create`; leave it closed.

The default clock matrix:

1. Cursor breakdown rows show the tighter remaining figure. The "total binds" sentence stays on `--full` and `--for-chat` only (`d183f83`).
2. The `EXHAUSTED` suffix is gone. The `empty` band is the row verdict; the clock cell is `100u/0l`. JSON summary lines still say `EXHAUSTED` (`45b8d48`).
3. `watch --all` / `--all-providers` stays on the live screen-only board. `--once` is the one-frame exit. Do not run the live sweep (`45b8d48`).
4. Display label is `3rd-party`. JSON `pool_family` stays `third_party` (`45b8d48`).
5. Cursor on-demand is `$X.XX (counts down)` after the last column, not a percent row, and it does not bind percent-left (`45b8d48`).
6. ACCT shows only when one service has two account labels that differ (`6d6f020`, `docs/pretty-display.md`).
7. The Fable row is scored. Rule lives in `_fable_display_row` and `docs/watch-mode.md` (`d4fb04f`).

A plain `aiuse watch` (no `--all`) has been running since 21:39 as pid 66296. It is not this session's process. Leave it.

Full `pytest` / `just ci` was not run after these commits. Pre-commit passed on each commit. Targeted suites that were run: matrix report tests (116) after the ACCT change; Fable tests (135, including `tests/test_claude_sublimits.py` and `tests/test_row_regressions.py`) after `d4fb04f`.

## What We Tried

A large replacement in `src/aiuse/watch.py` sat for minutes and looked like a memory loop. The file had already been written. Later edits were small replacements, not another whole-function rewrite.

Scoring a healthy Fable row from capacity dollars put it at 65 next to a shared Claude row at 62, so Fable ranked as more urgent. That fights the operator's read (Fable burns the pool faster and should sit lower). The dollar score is kept only when it is already under the shared row; otherwise it drops 12 points and stays in `mid`.

## Key Decisions

1. Fable is a scored peer, not `n/a` / `--`. Chosen after boards of both options. A cap at 2% left is `slow` 27 (bottom of 25–49), not `mid` beside a healthier cheaper row. A spent shared clock makes the Fable row `empty` 0; the week cell still shows the cap's own percent. A spent Fable cap does not change the shared row. The score is not added into a cross-vendor total and the row does not enter `suggest`.
2. Rejected: leaving the row unranked; scoring 2% left as `mid`; letting the raw capacity score outrank the shared row.
3. ACCT is per service, not "any two rows in the table differ."
4. `empty` and `EXHAUSTED` were different signals stacked on one row. The suffix is gone from the matrix. Summary text keeps `EXHAUSTED`.
5. `watch --all-providers` is still operator-only and screen-only. It no longer implies `--once`.

## Evidence & Data

Commits on `main`, oldest first: `d183f83`, `45b8d48`, `6d6f020`, `34a465f` (consult note, superseded by the implementation), `d4fb04f`.

Fable consult log (review-only, ~21s): `/tmp/aiuse-fable-consult.log`. The recommendation is now the implemented rule in `docs/watch-mode.md`, not only that file.

## Operator Feedback

The operator asked to see both boards with real 0–99 numbers, labeled as option 1 (leave unranked) or option 2 (peer score). Scenario 4 option 2 was the one with individual rankings, which is what they wanted, except 2% left must be the low end of `slow`, not `mid` next to a service that spends fewer tokens and has more left. They then chose that corrected option 2, and chose to update the three stale display sentences.

## Where We're Going

1. No product change is queued. Resume point is `d4fb04f` on `main`. Do not re-open the Fable score, the ACCT rule, or the on-demand dollar suffix unless the operator asks.
2. Optional: `just ci` from `/Users/djbclark/src/aiuse` if a later session wants the full suite this handoff did not run.
3. Do not run `aiuse watch --all-providers` without an explicit yes in the current session. The plain `aiuse watch` at pid 66296 is not this session's; do not kill it.

## Detached jobs

none

## Quick Start

```bash
cd /Users/djbclark/src/aiuse && git status -sb && git log --oneline -6
```

Read `docs/watch-mode.md` (Fable row) and `src/aiuse/report.py` (`_fable_display_row`, `_matrix_shows_account`). Tests: `tests/test_claude_sublimits.py`, `tests/test_row_regressions.py`.
