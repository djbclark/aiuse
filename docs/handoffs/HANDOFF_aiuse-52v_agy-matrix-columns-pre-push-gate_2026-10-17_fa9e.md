---
schema_version: 1
handoff_id: fa9e
parent_handoff_ids: []
lineage: none
chain: [aiuse-52v, aiuse-lqu, aiuse-hcy]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 82fb0aeda3d1673e6a2bbcdc293c66f02a64f606
created_at: 2026-10-17T22:52:31-04:00
writer: claude-code
---

# Handoff — agy matrix columns, fold-mark alignment, pre-push gate

## The Goal

djbclark: "at least agy and grok but I think also others are missing entries
under 5H, WEEK, and/or MONTH columns where there should be entries", then
"how about cursor?", then "The l in agy isn't lined up". Find which matrix
rows really lack a window their source reports, fix those, and leave the
rows that are right alone.

## Where We Are

All of it is done, committed and pushed; `main` == `origin/main` at
`82fb0ae`; only the untracked `opencode.json` (not this session's) is in the
tree. Five commits this session:

1. `e0a7af2` fix(codexbar): widget-snapshot rows borrow a slot clock only when
   kind and pool agree — the real agy bug (closes `aiuse-52v`).
2. `6e2c9c1` docs(antigravity): per-row widget windows ship upstream in
   CodexBar v0.77.0 (#4529) — corrects a wrong claim in the first doc note.
3. `0dfce54` fix(matrix): hang the fold mark after the reset span so the u/l
   column stays aligned (closes `aiuse-lqu`).
4. `7822d86` test(cursor): pin the burn-rate retention clock to the fixture
   date — a date flake that turned red at UTC 2026-10-18, unrelated.
5. `82fb0ae` ci: run the pre-commit pre-push stage (`just ci`) from the tracked
   beads pre-push hook — the gate `AGENTS.md` promised never existed here.

Outside the repo: CodexBar on this Mac was switched from a stale local build
(`~/src/CodexBar/CodexBar.app`, 0.68.1, built 2026-09-27) to the installed
cask 0.77.0, with djbclark's approval; `~/src/CodexBar` itself left as is.
Home instruction item 33 (`~/ops/site-djbclark/home-agents.md` + the cursor
mirror) and
`~/ops/site-private/memory/feedback_exit_status_through_pipe.md` gained a
third incident (below), both pushed.

Row verdicts, each checked against the source's raw output:

| Row      | Verdict                                                                                                                                        |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| agy      | **Bug**: all five windows carried 300 min + the 5h reset → stacked in 5H. Fixed.                                                               |
| grok     | Correct: CodexBar, the billing endpoint (`USAGE_PERIOD_TYPE_WEEKLY`) and tokscale all give one weekly; web confirms SuperGrok has no 5h clock. |
| Grok Bot | Correct: one weekly (tokscale and CodexBar `cursor-grok-bot`).                                                                                 |
| cursor   | Correct: all three CodexBar slots are the same monthly window (44640 min, resets Nov 9); no 5h/weekly exists.                                  |
| others   | Match their sources (copilot monthly, codex 5h+week, oc-go three, claude 5h+week, fable weekly sublimit, zai 5h+week).                         |

## What We Tried

1. **Live CodexBar call to patch the cached snapshot** — rejected before
   coding: `codexbar usage --provider antigravity` has full per-row windows
   but takes 6.5 s per run (measured), too slow for the cached path.
2. **First doc note said upstream "could" write per-row `window` blocks** —
   wrong: `steipete/CodexBar#4529` (opened from djbclark's fork 2026-10-14)
   was merged 2026-10-16 and `git tag --contains` puts it in v0.77.0. The
   snapshot still lacked them only because the running app was the old
   local build. Corrected in `6e2c9c1`; `aiuse-hcy` closed.
3. **First alignment test filtered rows with `"u/" in line`** — caught the
   `u/l = …` legend line too (it starts at column 0; the `+` legend is the
   indented one). Final filter: `line[:5].strip() in {"empty","slow","mid","use"}`.
4. **Reported the first full pytest run as green** — it had `1 failed`
   (the cursor date flake). The command string ended in `tail`, so the
   background-task notification's "exit code 0" was `tail`'s, and the
   `rc=` line written to the log was never read. Two pushes went out on that.
   The pre-push gate would have caught it but did not exist (see 5).
5. **`pre-commit install --install-hooks` to get the pre-push hook** —
   refuses: `core.hooksPath` = `.beads/hooks` (tracked, from `bd init`), and
   the beads pre-push only ran `bd hooks run pre-push`. Fixed by carrying
   pre-commit's pre-push stanza inside that file, the way `.beads/hooks/pre-commit`
   already did. The push of `82fb0ae` is the first run of the gate here:
   `CI all-files quality gate ... Passed` in the push log.

## Key Decisions

1. **Match a `percentLeft`-only widget row to a slot block only when the
   row's duration kind (id/title) and pool prefix (`_SLOT_POOL_PREFIXES`)
   agree with the block and the percents match** (`_row_window_kind`,
   `_row_slot_candidates` in `src/aiuse/collectors/codexbar.py`). Rejected:
   percent-only matching (the bug), and requiring a pool match for every
   provider (only antigravity has named pools; others keep the old order).
2. **An unmatched weekly row keeps its nominal 10080 min and no reset.** An
   unused Antigravity weekly's reset slides with the clock (CodexBar live
   showed exactly now+7d), so an empty span is honest; an in-use one is
   carried forward from the last OpenUsage reading (verified:
   `window_series_key` gives `antigravity:claude_gpt:weekly` for both
   sources, both `account=None`).
3. **The `+` fold mark hangs after the reset span, like `↻`**, counted in
   `w_spans`; `_clock_percent_text` no longer includes it. Rejected: a
   dedicated mark column (`0u/100l /4h` looks like a typo) and replacing the
   `/` separator (`0u/100l+4h` reads as "plus 4h").
4. **Switch the running CodexBar to the cask, not rebuild the fork** —
   operator chose it; the fork clone stays on its merged branch untouched.
5. **Wire `just ci` into the tracked hook rather than document-only** —
   operator chose it, accepting that the gate checks the working tree, so
   another session's dirty file can fail a push (it did: the not-mine
   `AGENTS.md` blank-line hunk; `prettier --write` restored it, as agreed).

## Evidence & Data

1. Widget snapshot (`~/Library/Group Containers/Y5PE65HELJ.com.steipete.codexbar/widget-snapshot.json`)
   from the old build: antigravity `usageRows` = 5 rows with `percentLeft`
   only; `primary`/`secondary` both `{usedPercent:0, windowMinutes:300}`.
   After the switch: rows carry `window` and the matrix shows
   `0u/100l/7d+` / `0u/100l/6d` in WEEK.
2. The snapshot's own cross-check had flagged it: "Claude/GPT weekly reset
   times differ by 9295 minutes".
3. Tests: full suite `1206 passed` (rc read from `pytest-full3.rc`), mypy
   clean on changed files, `just ci` green on the final push. New tests:
   `tests/test_codexbar_cache.py` (2), `tests/test_report.py` (fold-mark
   alignment), `tests/test_cursor_pools_fixup.py` (clock pinned).
4. Docs: `docs/antigravity-pools.md` new section "Widget-snapshot rows and
   their clocks (2026-10-17)"; `AGENTS.md` Conventions bullet on the
   pre-push gate rewritten.
5. Live Antigravity CLI query: 6.50 s wall (`/usr/bin/time`).

## Operator Feedback

1. Mid-turn: "how about cursor?" — answered from CodexBar raw (monthly only).
2. Mid-turn: "The l in agy isn't lined up" with a paste — became `aiuse-lqu`.
3. Approved: switch CodexBar app; leave `~/src/CodexBar`; wire the hook;
   `prettier --write AGENTS.md` to clear the foreign hunk.
4. Standing rules exercised: every bullet list numbered; `hermes-ping` with
   full URLs on completion; commit+push per unit; tests through `bg`.

## Where We're Going

1. **Nothing is owed.** Next session starts from `docs/next-options.md` as
   usual. If the matrix is checked again: expect agy WEEK cells with real
   resets only while the cask CodexBar (0.77.0+) is the running app; a
   `0u/100l` WEEK cell with no span means the old-build snapshot shape is
   back (`ps -axo pid,comm | rg CodexBar.app` shows which app runs).
2. Optional: `~/src/CodexBar` is still on `fix/antigravity-widget-row-window`
   (merged upstream) with untracked `.ignore` and `graft/`; operator said
   leave it.
3. Optional: `opencode.json` untracked in this repo (created 2026-10-17
   06:31 by another session); not touched.
4. Watch: the hook's `INSTALL_PYTHON` is an absolute path to this clone's
   `.venv`, same as the committed pre-commit hook; a clone elsewhere needs
   both re-pointed (or `pre-commit` on PATH — the hook falls back to it).

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git fetch origin && git pull --ff-only && git log --oneline -6
AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse | rg -n "SERVICE|agy|codex"
ps -axo pid,lstart,comm | rg "CodexBar.app/Contents/MacOS/CodexBar$"
~/ops/site-private/bin/bg .venv/bin/python -m pytest -q > /tmp/t.log 2>&1; echo "rc=$?" > /tmp/t.rc; cat /tmp/t.rc; tail -1 /tmp/t.log
```
