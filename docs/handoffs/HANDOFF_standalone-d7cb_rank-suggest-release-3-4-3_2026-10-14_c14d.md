---
schema_version: 1
handoff_id: c14d
parent_handoff_ids: [da48]
lineage: deterministic
chain: [standalone-d7cb, aiuse-p9f, aiuse-57n, aiuse-hcy]
repo: aiuse
workspace: main
branch: main
head_sha: b6a0761
created_at: 2026-10-14T14:22:40-04:00
writer: opencode
---

# Handoff: p9f and 57n shipped, aiuse 3.4.3 released

## The Goal

Resume `da48` (chain `standalone-d7cb`). The operator asked to verify the landed
ranking work, then work the open P3s **in order** — `aiuse-p9f`, `aiuse-57n`,
`aiuse-hcy` — and then cut the release that had been pending since `v3.4.2`.

## Where We Are

- `main` is clean and pushed (`b6a0761` at the time of writing, followed by this
  handoff and a Conventions docs commit). **1094 tests pass**; `just ci` green.
- **aiuse 3.4.3 is released**: PyPI (OIDC, `https://pypi.org/pypi/aiuse/3.4.3/json`
  returns 200), GitHub release `v3.4.3` (not draft/pre-release), annotated tag
  pushed, Homebrew 3.4.2 → 3.4.3 with `brew test djbclark/aiuse/aiuse` passing and
  both `aiuse --version` and `ai --version` confirming 3.4.3. Cut with
  `just release 3.4.3`, which also refreshed `docs/packaging.md`. `b6a0761` fixed
  the stale `packaging 3.4.1` line in `AGENTS.md`.
- The release carried the whole da48 batch that was unreleased since `v3.4.2`:
  the `↻` unused-rolling-window marker, reset carry-forward, 12-hour clocks,
  value-at-risk ordering and the urgency-weighted `use`/`mid` bands.
- `aiuse-p9f` **closed** — `485d73a`: an early window (under
  `analysis.pace.min_elapsed_fraction` elapsed, default 0.15) is no longer ranked by
  a projection `classify_pace` declines; it falls back to capacity. `_Pricing` now
  reads `min_elapsed_fraction` from config. The alert path is untouched, so an
  analyzer-approved early burn (short/prioritized carve-out) still ranks by its pace.
- `aiuse-57n` **closed** — `77e8c44`: `_BAND_ATTENTION` and the pool coverage key
  moved from `report.py` to `analysis/ranking.py`; new `ranking.pool_masked()` gives
  `pick_suggestion` the board's pool-collapse test, so a burn hidden behind a
  conserve/empty sibling in its own pool is no longer suggested.
- `aiuse-hcy` **open**: upstream PR #4529 is open and green (see Where We're Going
  1); the root cause and the fork doctrine are recorded on the bead and in
  `~/src/CodexBar/AGENTS.DJB.md`.
- Beads synced; no detached jobs.
- **Written mid-session**, so the numbers above are the state at writing: a parallel
  session landed the Cursor-pools batch (`4110d31`, `799e651`, `ea514c2`, …) and
  released **3.5.0** (`806a832`) on top of `b6a0761` while this handoff was being
  written. Re-derive counts from the tree rather than trusting these lines.

## What We Tried

1. Reproduced both P3 disagreements against cached snapshots rather than reasoning
   only: 3298 snapshots for p9f, 3300 for 57n, A/B through config
   (`min_elapsed_fraction` 0.0 vs 0.15) and through the old/new suggest rule.
2. The p9f replay measured a **larger** shift than the bead's estimate: the `mid`
   band reorders in **47.8%** of snapshots (bead guessed 35.4%) and the most
   actionable row changes in **19.3%**. `suggestion` never changes (0.0%), so the
   blast radius is no-alert rows only. Flagged to the operator; the config value is
   the escape hatch (`analysis.pace.min_elapsed_fraction = 0.0`).
3. Two existing tests pinned the old p9f behaviour (`tests/test_capacity_rank.py`,
   `tests/test_report.py`); both used a window inside the now-gated early zone, so
   their fixtures moved just past the gate rather than their assertions being
   weakened.
4. `just ci`'s semgrep hook reports "files were modified by this hook" on the first
   run after new content and aborts; running `pre-commit run --all-files` a second
   time passes. Budget for one extra run after any code change.

## Key Decisions

1. p9f: gate only the **no-alert** ranking path. An alert that exists is one the
   analyzer already approved, so `_alert_queue_priority` keeps using its pace.
2. 57n: align `suggest` to the board (option A in the bead), not the reverse. The
   shared table/key moved down into `analysis/ranking.py` so the board and suggest
   cannot drift; `report.py` imports them unchanged.
3. 57n caveat, recorded on the bead: `pool_masked` sees alert-versus-alert masking
   only. A pool whose own unalerted window state (an exhausted sibling) hides a burn
   is still invisible to `pick_suggestion`, which has no snapshot.
4. Release: keep the release in the session (operator asked) and update `AGENTS.md`'s
   stale version line, which `release.py` does not touch.

## Evidence & Data

1. p9f replay (3298 snapshots, gate off vs on): mid band reordered 47.8%, board row
   order changed 65.7%, most actionable row changed 19.3%, suggestion changed 0.0%.
2. 57n replay (3300 snapshots): 24 (0.73%) suggestions change, **all to `null`**; no
   suggestion is pool-masked afterwards; board output is byte-identical because the
   collapse rules did not move.
3. CodexBar ground truth captured from the live app-group file
   `~/Library/Group Containers/Y5PE65HELJ.com.steipete.codexbar/widget-snapshot.json`
   (installed CodexBar 0.75.0): every Antigravity `usageRows` entry has no `window`
   key at all, while `primary`/`secondary` carry `resetsAt`.

## Operator Feedback

1. "What are you doing with codexbar? Wanted to keep this to aiuse." — I had begun
   the `aiuse-hcy` upstream PR inside this aiuse session (fast-forwarded the
   `~/src/CodexBar` clone, branch `fix/antigravity-widget-row-window`, one-line fix
   plus a test, uncommitted; killed my own in-flight `swift test` build). **Do not do
   CodexBar work in an aiuse session.** The operator chose to leave the branch for a
   dedicated session; the finding now lives in bead `aiuse-hcy` instead.
2. Earlier: work the open P3s in order, then release. Both followed.
3. Later the same session the operator reversed the keep-it-to-aiuse call for this
   one task ("let's see if 4.1 has an issue/PR and if not if it is a super simple
   fix"): do the CodexBar work here, never build it on this Mac, verify on GitHub
   CI instead (Hyades has no macOS runner), and open the PR ready rather than draft
   because this repo defers macOS tests on drafts.

## Where We're Going

1. **`aiuse-hcy` — PR open and green, awaiting upstream review.**
   [steipete/CodexBar#4529](https://github.com/steipete/CodexBar/pull/4529) (branch
   `fix/antigravity-widget-row-window`, `cf7541af9`, in the `djbclark` fork):
   `antigravityWidgetRows` now passes `window:` gated on `usageKnown`, plus two
   focused tests. Verified by upstream GitHub macOS CI (`swift-test-macos` pass,
   24m02s) with `lint`, the Linux CLI builds and GitGuardian also green — **no local
   build**, per the fork doctrine in `~/src/CodexBar/AGENTS.DJB.md` (live,
   git-excluded; recovery mirror `fork/AGENTS.DJB.md` on the fork-only branch
   `djb/fork`, which is never a PR base). An adversarial security review caught a
   medium regression in the first cut (unknown lanes would have rendered an invented
   100% left), fixed before pushing and quoted in the PR body. Antigravity's 5h lanes
   exist only as usage rows, which is why aiuse needed the `f45ce72` carry-forward
   workaround. Next: answer upstream review; close the bead when it merges.
2. Optional trackers, no urgency: #11 MCP, #13 History, #14 watch, #15 fixtures, #16
   DeepSeek key; #35/#36 config discoverability.
3. `aiuse-juk` (dormant chain) still holds parked P1 beads and an Aug-decided
   question the operator deferred "to next baton".
4. No further release unless the operator asks.

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git status -s && git log --oneline -6      # expect clean; head is this handoff or later
bd show aiuse-hcy                                            # + its root-cause comment
gh release view v3.4.3
AIUSE_SKIP_HOMEBREW_FORMULA=1 COLUMNS=170 .venv/bin/aiuse watch --once --no-color
~/ops/site-private/bin/bg .venv/bin/python -m pytest -q     # expect 1094 passed
```
