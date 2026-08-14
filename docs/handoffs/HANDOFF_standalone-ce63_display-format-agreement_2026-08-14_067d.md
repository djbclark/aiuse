---
schema_version: 1
handoff_id: 067d
parent_handoff_ids: [0791]
lineage: deterministic
chain: [standalone-ce63]
repo: aiuse
workspace: aiuse
branch: main
head_sha: cf6f84d6f57c80291e70f24c80140f137cd37cbf
created_at: 2026-08-14T07:37:17-0400
writer: claude-code
---

# Handoff — Usage table redesign, provider renaming, and cross-format agreement

## The Goal

The operator opened with `/baton` and, after the resume plan, gave a
four-part brief that grew over the session:

1. Inspect the stray `aiuse.worktrees/golden-fixtures` worktree (it appeared
   in no chain's `workspaces` list).
2. **Regularize the default `ai` display.** The complaint, verbatim: "some
   seem to only have weekly percentages listed, some only daily, some it is
   unclear, we need to come up with a more regularized, information-dense
   display."
3. **Shorten service names** — "lets go through what we call the different
   services, I think we can shorten those to make more room available per
   line."
4. Later, as an explicit queue (lightly typo'd in the original, and
   clarified by the operator a moment later): fix the agy issue, delete the
   golden-fixtures worktree, and fix the reset-cycle bug — _then_ "Make sure
   all 3 major output formats — `aiuse`, `aiuse --for-chat`, and
   `aiuse --json` — agree and give reasonable output."

All four are complete. Nothing is committed except this handoff.

## Where We Are

**Git state**

- Branch `main`, `head_sha` `cf6f84d6f57c80291e70f24c80140f137cd37cbf`
- Working tree **dirty**: 17 modified files, +1397 / −94
- No commits made this session prior to this handoff. No tag, no release.
- `feature/golden-fixtures` branch and its worktree **deleted** (see below).

**Modified files**

```
README.md                        |  35 ++-
docs/cursor-quota.md             |   7 +-
docs/provider-identity.md        |   4 +-
src/aiuse/analysis/history.py    | 110 ++++++--
src/aiuse/chat_format.py         |  47 +++-
src/aiuse/cli.py                 |   2 +-
src/aiuse/collectors/codexbar.py |  47 ++++
src/aiuse/models.py              |  92 +++++--
src/aiuse/report.py              | 530 ++++++++++++++++++++++++++++++++++++++-
src/aiuse/tui/app.py             |   9 +-
tests/test_chat_format.py        | 176 ++++++++++++-
tests/test_cli.py                |  11 +-
tests/test_codexbar_parse.py     |  62 +++++
tests/test_history.py            | 155 +++++++++++-
tests/test_report.py             | 197 +++++++++++++--
tests/test_tui.py                |   5 +-
tests/test_use_or_lose.py        |   2 +-
```

**Tests**: `uv run --extra dev pytest -q` → **482 passed**.
**Gate**: `just check` → **PASS** (test, ruff, mypy, yamllint, markdownlint,
prettier, typos, just-check).

### What the default output looks like now

```
      SERVICE    ACCT     SCOPE           5H  WEEK MONTH   NEXT $ UNUSED
  % used — 0% untouched, 100% exhausted
error oc-go      —        —            no usage data
empty oc-zen     —        —            balance $-0.04 (no expiry)
empty copilot    —        —                —     —  100%   ~18d    $0.00
empty codex      gmail    —                —  100%     —   5.9d    $0.00
n/a   deepseek   CodexBar —            balance $9.51 (no expiry)
slow  claude     mit      —              50%   52%     —     4h    $3.31
slow  agy        gmail    gemini         13%   14%     —     4h    $5.91
slow  agy        gmail    claude/gpt      0%   >0%     —     5h    $6.90
mid   cursor     gmail    —                —     —  >0%+    19d   $19.90
mid   cursor     gmail    other models     —     —    0%    19d   $20.00
  dim % = clock inferred, not reported · + = >1 window on that clock, showing most-used
```

## What We Tried

Chronological. These are the dead ends and near-misses — the expensive part
to rediscover.

1. **Renaming providers broke 13 tests, and the operator asked to stop
   mid-flight.** The `PROVIDER_DISPLAY_NAMES` rewrite broke literal name
   assertions across `tests/test_chat_format.py` (1), `tests/test_cli.py` (2),
   `tests/test_report.py` (10). The operator asked to stop at the next
   convenient stopping point. Rather than leave a red tree _or_ discard it,
   was **stashed** (`git stash push -m "wip: short provider names…"`), the
   tree verified clean, and the plan written to Tier 1. Resumed with
   `git stash pop` next turn. **Lesson: stash, don't commit-half or revert,
   when told to stop with failing tests.**

2. **The matrix initially dropped alerts for accounts with no windows.**
   First `_build_matrix_rows()` built rows purely from `snapshot.accounts`.
   The old ladder rendered alerts _first_ and let accounts fill the gaps, so
   an alert on a window-less account still showed. Caught by
   `tests/test_cli.py::test_brief_mode_skips_usage_section`. Fixed by adding a
   three-pass build: account rows with windows → `_row_from_alert()` synthesis
   for uncovered alert pools → deferred error/prepaid note rows.

3. **`_group_into_reset_cycles()` first collapsed _all_ undated observations
   into a single cycle.** That broke the pre-existing
   `test_chronic_waste_detection`, whose fixture writes 7 daily snapshots with
   **no `resets_at` at all** — genuinely 7 different cycles of a 5h window.
   Fixed by anchoring undated samples to their **collection time** and
   clustering those on the same half-window threshold.

4. **`_within_lookback()` trusted "history is newest-first" and was wrong.**
   It took the first parseable entry as the newest. A new test naming files
   `s00.json`…`s11.json` (s00 = newest) returned 12 snapshots instead of 8.
   Cause: `load_recent_snapshots()` does `sorted(directory.iterdir(),
reverse=True)` — **it sorts by FILENAME**, and "newest-first" holds only
   because real snapshots are named by ISO timestamp. Fixed by scanning for
   `max(collected_at)` instead of trusting position.

5. **The first fix to the false "projected to exhaust" claim did nothing.**
   Added a `pace_ratio >= 1.0` guard to
   `_projected_exhaustion_before_reset()` — but placed it _after_ the
   `projected_used_fraction >= 1.0` check, which short-circuits and returns
   True first. Live output was unchanged. Only caught by re-running
   `ai --for-chat` rather than trusting the tests (the tests used
   `projected_used_fraction=0.05` and passed either way). Fixed by reordering
   the guard ahead of the projection check. **Verify user-visible output, not
   only the suite.**

6. **Adding the "% used" header note broke an ordering assertion.**
   `test_default_report_is_clock_matrix` asserted
   `text.index("empty") < text.index("use")`. The new note "% used — 0%
   untouched…" contains the substring "use" at index 57, before the `use` row.
   Replaced with a row-tag-based check (`tags.index("error") <
tags.index("empty") < tags.index("use")`).

7. **Narrow terminals truncated numbers mid-value, and the header wasn't
   clamped at all.** First render at `COLUMNS=60` sliced `NEXT`/`$` in half
   and let the header run past the clamp. Fixed with a column-shedding loop
   plus clamping the header and legend through the same `_line()` helper.

8. **Initial diagnosis of the agy duplicate rows was wrong.** First read was
   "two collectors disagree within one snapshot". Checking the actual
   snapshots disproved it: any single snapshot has _either_ titled _or_
   untitled agy windows, never both. The real cause was CodexBar's dual
   response shape plus a history-derived alert referencing the _other_ shape.
   Verified by rendering a saved unnamed-window snapshot directly and finding
   `src=history kind=burn label='Claude/GPT 5-hour'`.

## Key Decisions

**Chosen, with the alternatives that were rejected.**

- **Layout: clock matrix.** One row per account/pool, one column per
  5H/WEEK/MONTH clock, em-dash where the service has no window on that clock.
  _Rejected:_ (a) flat one-row-per-window table — fully regular but ~18 rows
  and repeats the service name; (b) grouped-by-service with indented windows —
  least repetition but ~2× rows and no scannable column. Operator picked the
  matrix from three previewed mockups.
- **Percentages are `used`, not `left`.** Operator: "100% = used up and 0% =
  not used at all yet." `QuotaWindow.used_percent` already existed and is
  populated for every provider, so this is a direct field read.
- **Names are lowercase CLI handles** (`agy`, `claude`, `codex`, `copilot`,
  `cursor`, `deepseek`, `grok`, `oc-go`, `oc-zen`, `openrouter`).
  _Rejected:_ Title-Case vendor shorts (`Antigravity`), which breaks the
  `grep -i agy` invariant documented at `models.py:74`; and short-in-table /
  long-in-`--full`, which means two vocabularies. Operator explicitly
  overrode the proposed bare `zen`/`ocgo` → **`oc-zen`/`oc-go`**. Documented
  that `grep -i opencode` no longer matches those two.
- **Color retained.** Operator: "don't lose the use of color, that is nice."
  Cell color tracks how full the bucket is (green → cyan → yellow → red); the
  band tag carries the judgment about what to _do_.
- **`$ UNUSED` is the MAX across a row's windows, not the sum.** A 5h window
  is carved out of the weekly budget above it, so summing double-counts. (The
  mockup shown to the operator used a sum, e.g. $6.76; the shipped code uses
  max, e.g. $6.69. Deliberate change, small magnitude.)
- **Same-clock windows in one pool fold to the most-consumed**, marked `+`
  (Cursor Included ⊂ Auto). The one that locks out first is the one that
  matters.
- **Column shedding order under width pressure: `$ UNUSED` → `NEXT` →
  `SCOPE`.** Scope is dropped last because pool names are what disambiguate
  two rows of the same service. `SCOPE` is also omitted entirely when no
  account has independent pools.
- **Reset-cycle clustering threshold: half a window.** Within one cycle a
  sliding reset drifts only by the polling gap; between genuine cycles a fixed
  reset jumps a full window. Half separates them at any realistic cadence.
- **`--for-chat` keeps "% left"; the table says "% used"; both are now
  labeled.** _Rejected:_ forcing chat to `% used` — "87% left" is the more
  actionable phrasing in prose, and the risk was never the convention itself
  but the fact that neither output said which one it used.
- **Historical snapshots are NOT rewritten** for the agy renaming. Code is
  fixed going forward; on-disk snapshots keep old labels until 90d retention
  ages them out.

## Evidence & Data

**Reset-cycle bug, measured before the fix:**

```
antigravity:claude_gpt:5h   cycles=4 avg=100.0
claude:-:5h                 cycles=2 avg=61.5
history files loaded: 30 → history[:7] slice spans
  2026-08-14T11:15:07 → 2026-08-14T11:24:51   (9 minutes 44 seconds)
```

Four "cycles" of a **5-hour** window inside ten minutes. Four real cycles
take 20 hours. Clusterer verified after the fix:

```
sliding 8 obs over 8min, 5h window -> 1 cycle
fixed 5h, 3 distinct resets        -> 3 cycles
fixed weekly, 3 distinct resets    -> 3 cycles
same 3 weekly stamps, monthly window -> 1 cycle
```

**agy slot→pool pairing, across 30 snapshots:**

```
q1 resets that also appear as Gemini 5-hour:    2
q1 resets that appear as Claude/GPT 5-hour:     0
q2 resets that appear as Claude/GPT 5-hour:     0   (Claude/GPT slides; exact
q2 resets that appear as Gemini 5-hour:         0    timestamps rarely repeat)
```

Slot 1 = Gemini (positive match, zero cross-match). Slot 2 = Claude/GPT by
elimination — there are only two pools. Gemini's reset is on a fixed schedule
(`01:56`, `06:56`, `15:25` — 5h apart); Claude/GPT's slides.

**Two CodexBar response shapes for the same account, minutes apart:**

```
--- 2026-08-14T111703Z.json  (titled, via extraRateWindows)
  'Gemini 5-hour'  'Gemini weekly'  'Claude/GPT 5-hour'  'Claude/GPT weekly'
--- 2026-08-14T111747Z.json  (bare primary/secondary slots)
  'Google AI / Antigravity quota 1 (name not supplied by CodexBar)'
  'Google AI / Antigravity quota 2 (name not supplied by CodexBar)'
```

**`window_minutes` is `None`** for antigravity, grok, deepseek and openrouter
(verified against a live snapshot) — which is why `infer_window_clock()`
needs its three-tier fallback. All 14 live windows bucket correctly.

**The pace contradiction, from `ai --json`:**

```
label=Claude Code weekly   ratio=0.6  proj_used=1.0  exhaust_at=2026-08-14T12:03:47Z
label=Claude/GPT weekly    ratio=0.0  proj_used=1.0  exhaust_at=2026-08-15T17:44:01Z
```

`projected_used_fraction` is blended with **learned history burn rates**;
`pace_ratio` describes only the current window. They diverge routinely. The
old copy printed one and asserted a conclusion drawn from the other:
`Pace: `0.00×` normal — projected to exhaust before reset`.

**Name widths:** `Google AI / Antigravity (agy)` 29 → `agy` 3;
`Cursor (cursor-agent)` 21 → `cursor` 6; service column 29 → 8. Accounts
`djbclark@gmail.com` (18) → `gmail`.

**Width bug:** `ACTION_PLAN_WIDTH = 80` hardcoded at `report.py:50`; only the
TUI adapted (`tui/app.py:106`). `grep -rn "get_terminal_size" src/` → **zero
hits**. Lines truncated with `…` even at `COLUMNS=200`.

**`golden-fixtures` worktree:** commit `00dcce5` landed on main as **PR #27**
(`2491c4c`); all 6 files byte-identical on main; branch 45 commits behind.
Abandoned post-merge, which is why it belonged to no chain. Deleted.

**Test progression:** 463 → 13 failed/450 passed (rename) → 463 (fixed) → 470
(+7 matrix) → 473 (+3 agy) → 477 (+4 reset-cycle) → **482** (+5 cross-format).

## Operator Feedback

- "don't lose the use of color, that is nice in the current display"
- "the percentages should be how much is used, so 100% = used up and 0% = not
  used at all yet"
- "use oc-zen for zen and oc-go for ocgo" — overriding the proposed bare
  `zen` / `ocgo`
- Asked mid-turn to stop at the next convenient stopping point, with 13
  tests red. Honoured by stashing, not by committing or reverting.
- Selected the clock matrix from three previewed layouts, and gave the
  naming/percentage notes as free-text annotations rather than picking an
  option outright.
- Has **not** asked for a commit, version bump, tag, or release of the
  feature work. Only this handoff is committed.

## Where We're Going

1. **THE NEXT ACTION — get the operator's review of the 17 uncommitted files,
   then commit.** Nothing in `src/` is committed. Run
   `cd ~/src/aiuse && git diff src/aiuse/report.py` (the bulk, +530) and
   `git diff --stat`. Do not bump the version or release without being asked.
2. Decide the **`--for-chat` granularity** question. It still lists every
   window flat, so agy shows 4 chat entries where the table shows 2 pool rows.
   Not wrong, but unreviewed. `chat_format.py` builds a `_WindowRow` per
   window; `partition_independent_pools()` in `analysis/pace.py` is what the
   table uses to group.
3. Consider whether `--full` should also adopt the terminal-width fix. Its
   section rules are still pinned to `ACTION_PLAN_WIDTH = 80`. Deliberately
   left alone this session — `--full` was not truncating, and widening it
   would produce absurdly long rule lines.
4. Watch for stale agy labels in history-derived alerts until the old
   snapshots age out (90d, `snapshot_retention_days`). If it becomes annoying
   sooner, the options are pruning `~/.cache/aiuse/snapshots/` or teaching
   `window_series_key()` to alias `Google AI / Antigravity quota 1` → Gemini.
5. Optional cleanup noted but not done: `AGENTS.md:14` still says "Status
   (2026-07-30) … packaging 3.0.2" (actual 3.0.16), and `docs/handoff.md`
   (singular, 2026-08-11) still needs reconciling with this `docs/handoffs/`
   directory.

## Quick Start

```bash
cd ~/src/aiuse

# 1. Confirm the state this handoff describes
git rev-parse HEAD              # expect cf6f84d…  (+ this handoff commit)
git status -s                   # expect 17 modified files, src/ + tests/ + docs/
uv run --extra dev pytest -q    # expect 482 passed
just check                      # expect PASS

# 2. See all three formats on one collection
COLUMNS=110 uv run ai --no-tui
uv run ai --for-chat
uv run ai --json | python3 -m json.tool | head -40

# 3. Prove the width fix and the column shedding
for w in 120 80 64 50; do echo "== $w =="; COLUMNS=$w uv run ai --no-tui 2>/dev/null; done

# 4. Re-measure the reset-cycle fix against real history
uv run python -c "
from aiuse.analysis.history import chronic_waste_summary
from aiuse.models import Snapshot, utcnow
for r in chronic_waste_summary(current=Snapshot(collected_at=utcnow(), accounts=[])):
    print(r['window_key'], 'cycles=', r['sample_count'], 'avg=', r['avg_remaining_pct'])
"

# 5. Exercise the agy shape that used to fork identity
uv run python -c "
from aiuse.collectors.codexbar import _slot_label
from aiuse.models import utcnow
from datetime import timedelta
b={'resetsAt': (utcnow()+timedelta(hours=4)).isoformat()}
print(_slot_label('antigravity',1,b), '|', _slot_label('antigravity',2,b))
"   # -> Gemini 5-hour | Claude/GPT 5-hour
```

**Where the new code lives**

| Concern          | Location                                                                                                                                                                          |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Table renderer   | `src/aiuse/report.py` `render_clock_matrix()`                                                                                                                                     |
| Row construction | `src/aiuse/report.py` `_build_matrix_rows()`, `_row_from_alert()`                                                                                                                 |
| Column shedding  | `src/aiuse/report.py`, the `for drop in ("value","next","scope")` loop                                                                                                            |
| Terminal width   | `src/aiuse/report.py` `terminal_width()`, `TABLE_MAX_WIDTH = 110`                                                                                                                 |
| Clock bucketing  | `src/aiuse/models.py` `infer_window_clock()`                                                                                                                                      |
| Service names    | `src/aiuse/models.py` `PROVIDER_DISPLAY_NAMES`                                                                                                                                    |
| agy slot→pool    | `src/aiuse/collectors/codexbar.py` `_SLOT_POOL_PREFIXES`, `_block_window_kind()`                                                                                                  |
| Reset cycles     | `src/aiuse/analysis/history.py` `_group_into_reset_cycles()`, `_within_lookback()`                                                                                                |
| Pace phrasing    | `src/aiuse/chat_format.py` `_projected_exhaustion_before_reset()`, `_projection_disagrees_with_ratio()`                                                                           |
| Tests            | `test_clock_matrix_*` (test_report.py), `test_antigravity_*` (test_codexbar_parse.py), `test_sliding_reset_*` (test_history.py), `test_all_three_formats_*` (test_chat_format.py) |
