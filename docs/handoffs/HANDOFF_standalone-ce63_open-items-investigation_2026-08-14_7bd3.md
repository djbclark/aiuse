---
schema_version: 1
handoff_id: 7bd3
parent_handoff_ids: [067d]
lineage: deterministic
chain: [standalone-ce63]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 69d259d07dc0ad316c884aa601c6dda241dc2130
created_at: 2026-08-14T11:12:46-0400
writer: claude-code
---

# Handoff — Shipped the display redesign; investigated the three open items

## The Goal

Session opened with `/baton`. Tier 1 (chain `standalone-ce63`) named the next
action: get operator review of the 17 uncommitted feature files from the
previous session, then commit. The operator answered two batched questions:

1. Review method → **"Just commit it"** (skip the walkthrough).
2. The open `--for-chat` granularity item → **"Fix it now, then commit."**

Then, in order: **"Tell me about the open items"**, and **"Can we finish the
work of the agents that died?"**

All of it is done except the second workflow run, which was still in flight
when this was written (see Where We're Going, item 1).

## Where We Are

**Git state**

- Branch `main`, `head_sha` `69d259d07dc0ad316c884aa601c6dda241dc2130`
- Working tree **clean**. Nothing unpushed (`origin/main..HEAD` empty).
- `69d259d` "Redesign the usage display and reconcile all three output
  formats" — 18 files, +1807 / −142 — is committed **and pushed**.
- Tests: `uv run --extra dev pytest -q` → **495 passed** (was 482).
- Gate: `just check` → **PASS**.

**What 69d259d contains**

The previous session's 17 uncommitted files (clock matrix, lowercase service
handles, terminal-width adaptation, agy slot→pool collector fix, reset-cycle
clustering, three-format reconciliation) _plus_ this session's `--for-chat`
work, as one commit. See "Key Decisions" for why it was not split.

**This session's code changes (inside 69d259d)**

| File                         | Change                                                                                                                                                                                                                                                |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/aiuse/chat_format.py`   | `_PoolEntry`, `_group_rows_into_pools()`, `_render_pool_entry()`, `_row_notes()`, `_window_line_label()`, `_has_reportable_usage()`; `_apply_governing_warnings()` rewritten over pool entries; action items dedupe per pool; no-data accounts bucket |
| `src/aiuse/analysis/pace.py` | `POOL_SCOPE_LABELS` + `pool_scope_label()` added beside `independent_pool_key`                                                                                                                                                                        |
| `src/aiuse/report.py`        | `_POOL_SCOPE_LABELS` now sourced from `analysis/pace.py`                                                                                                                                                                                              |
| `tests/test_chat_format.py`  | +13 tests: `TestPoolGrouping`, `TestActionItemsPerPool`, `TestNoDataAccounts`                                                                                                                                                                         |

**What `--for-chat` looks like now**

```
🔴 **claude · djbclark@gmail.com**
   ↳ Claude Code 5-hour — `0% left · resets in 2h 36m`
     · Exhausted
   ↳ Claude Code weekly — `94% left · resets in 6d 22h`
🟠 **agy · djbclark@gmail.com · gemini**
   ↳ 5-hour — `68% left · resets in 3h 32m`
   ↳ weekly — `82% left · resets in 6d 5h`
     · Pace: `1.59×` normal — projected to exhaust before reset
🟢 **cursor · djbclark@gmail.com · Cursor other models**
   ↳ `100% left · resets in 19d 11h`

⚠️ **ERRORS**
   ↳ oc-go (default): no usage data
```

Single-window pools keep the old compact form byte-for-byte; only
multi-window pools gained the sub-list.

## What We Tried

Chronological. The expensive-to-rediscover part.

1. **Test helper silently substituted a window into an "empty" account.**
   `_account()` in `tests/test_chat_format.py` does
   `windows=windows or [_window()]`, so `_account(windows=[])` returns an
   account **with** a window. The no-data test asserted against the wrong
   fixture and failed confusingly. Fixed by adding `_empty_account()` that
   builds `AccountUsage` directly. **Falsy-default helpers cannot express
   "empty".**

2. **Asserted an exact deadline string and it was never going to match.**
   `test_per_window_notes_are_indented_under_their_window` asserted
   `resets in 3h`. The module's `NOW` fixture is 2026-08-04 while real now is
   2026-08-14, so `days_until_reset()` is negative and the renderer prints
   `reset imminent`. Switched to a prefix match on the stable part of the line.

3. **`render_clock_matrix(alerts, snap, config)` → TypeError.** Everything
   after `alerts` is keyword-only (`report.py:947`). Correct call is
   `render_clock_matrix(alerts, snapshot=snap, config=cfg, color=False)`.

4. **No `Snapshot` deserializer exists.** Tried `Snapshot.from_dict(...)` to
   render saved snapshots — there is `to_dict` but no inverse anywhere.
   `load_recent_snapshots()` returns raw dicts, not objects. Worked around by
   collecting in-process: `run_collectors(load_config())`.

5. **A regex probe reported the opposite of the truth.** Searched a snapshot
   with `re.finditer(r'.{90}name not supplied by CodexBar', txt)` and got zero
   hits — because `.` does not match newlines and the JSON is pretty-printed.
   The file had two occurrences. Nearly concluded the stale-label problem was
   already gone. **Use `str.count` / a JSON walk, not a context regex, to
   answer "is this string present".**

6. **Nearly declared a subagent wrong when it was right.** Grepped
   `used_percent` in `docs/json-contract.md`, got hits at lines 172 and 189,
   and was about to refute the agent's "it's missing" claim. Those lines are
   the `windows[]` and `usage_credits` tables; the `alerts[]` table at
   202–219 genuinely lacks it. **Check which table a grep hit lands in.**

7. **A `cd` + `$(cat file)` bash pipeline produced a fabricated count.**
   Reported "30 of the newest 30 snapshots are stale" when the newest was
   demonstrably clean. Re-ran the same question in Python with absolute paths
   and got the real answer. Any count that contradicts a direct inspection is
   the count that is wrong.

8. **First workflow run lost 3 of 5 agents** to `You've hit your session
limit · resets 11:30am (America/New_York)` — both verify agents and the
   entire `full-width` investigation. Resumed with `resumeFromRunId` so the
   two completed investigations replayed from cache; that run was still in
   flight at write time.

9. **Both subagents concluded "agy is closed" and both were wrong** — as was
   this session's own first summary to the operator. Everyone grepped
   historical snapshot _files_ for `Antigravity quota` and reasoned about
   retention windows. Nobody asked **what is still writing them**. See
   Evidence for the actual mechanism. **When something looks flushed, check
   the producer, not the corpus.**

## Key Decisions

- **One commit for all 18 files, not several.** The operator said "just commit
  it". Splitting the previous session's five features from this session's
  `--for-chat` work would need hunk-level surgery inside `report.py` and
  `chat_format.py`, where the two interleave. Rejected as error-prone for no
  reviewer benefit.
- **Pushed, and pushing this handoff too.** `AGENTS.md:177-182` says "Commit
  early and often; push after every commit", explicitly names "finished
  investigation docs" as a commit opportunity, and says "Do not wait for
  separate push authorization." **Note for the next session:** the previous
  session left `d3a567f` unpushed reasoning that "aiuse declares no
  memory-is-data push exception" — that applied the ops-suite framing to a
  repo which has a _broader_ blanket push rule. Do not flip-flop on this
  again; aiuse pushes.
- **Single-window pools keep the compact chat form.** Only pools with 2+
  windows gain the heading + sub-list. Keeps most output byte-identical and
  the diff small. _Rejected:_ uniform sub-list rendering, which added a line
  to every single-window entry for no information.
- **Pool prefix stripped from window labels inside a named pool** — "Gemini
  5-hour" renders as "5-hour" under a `gemini` heading. Falls back to the full
  label if stripping would empty it.
- **Entry emoji is the worst of the pool's windows.** An exhausted 5-hour
  governs the entry even beside a 94% weekly, because the shorter window is
  carved out of the longer one.
- **Action items dedupe per pool, not per account.** agy's Gemini and
  Claude/GPT are separate things to act on, and the table already rows them
  separately.
- **`POOL_SCOPE_LABELS` moved to `analysis/pace.py`.** _Rejected:_ importing
  `report._POOL_SCOPE_LABELS` into `chat_format` — a private cross-module
  import, and `report` already imports `chat_format` lazily at
  `report.py:2021`.
- **Fixed the no-data-account omission beyond the asked scope** (an account
  with no error, no windows and no balance vanished from chat while the table
  showed it). Same defect family as the requested fix; flagged explicitly to
  the operator as scope expansion with an offer to revert.
- **Resumed the killed workflow rather than re-running it.** Same script, so
  the two survivors replayed from cache and only the three dead agents
  re-ran. Editing the script would have risked invalidating the cache.

## Evidence & Data

**The finding that overturned the recorded item.** The old antigravity label
is **still being generated**, hourly:

```
2026-08-14T133938  ok     ['Gemini 5-hour', 'Gemini weekly', 'Claude/GPT 5-hour', ...]
2026-08-14T143958  STALE  ['Google AI / Antigravity quota 1 (name not supplied by CodexBar)', ...]
2026-08-14T150806  ok     ['Gemini 5-hour', 'Claude/GPT 5-hour']
```

Mechanism, fully traced:

- `~/Library/LaunchAgents/com.djbclark.aiuse.plist` runs
  `/Users/djbclark/.local/bin/aiuse -q` on `StartInterval 3600` (hourly, :39).
- That binary is pipx **3.0.16**;
  `grep -c "_SLOT_POOL_PREFIXES" ~/.local/pipx/venvs/aiuse/lib/python3.14/site-packages/aiuse/collectors/codexbar.py`
  → **0**. It predates the fix.
- The fix only matters when CodexBar returns its _bare slot_ shape; under the
  _titled_ shape labels arrive pre-named and even the old binary looks correct.
  Hence 12:39 and 13:39 clean, 14:39 stale — roughly 1 in 3.

**It is harmless but accumulating.** Stale rows carry `window_minutes: None`,
and `history.py:361` (`if not window_minutes or window_minutes > 360:
continue`) drops them **before** `window_series_key` on line 370 ever reads the
label. Verified: 0 of 261 snapshots carry the old label inside `alerts[]`, so
the `serve.py:157` replay path is empirically clean too.

**The alias remedy proposed in 067d cannot work:**

```
window_series_key('antigravity','Gemini 5-hour', None) -> antigravity:gemini:?
window_series_key('antigravity','Gemini 5-hour', 300)  -> antigravity:gemini:5h
window_series_key('antigravity','Google AI / Antigravity quota 1 (…)', None)
                                                       -> antigravity:-:?
```

The old label does not even produce a `gemini` component, and the duration
suffix comes from `window_minutes`, which the bare shape never supplies.

**The 90-day horizon in 067d was the wrong bound.** `load_recent_snapshots`
defaults to `max_count=30` (`history.py:78`); at the observed cadence the 30
newest span roughly an hour. Retention (90d, `history.py:15`) is enforced
**only on read** — nothing in `src/` ever deletes a snapshot; the sole
`unlink` is a `.tmp` cleanup at `history.py:71`. The directory grows
unboundedly (223 → 261 files during this session alone, partly from this
session's own test runs).

**A second, live instance of the same defect.** The generic fallback still
fires every collection for two other providers, present in the newest
snapshot and visible in `ai --json` today (absent from table and chat):

```
.accounts[6].windows[0].label  = 'Deepseek quota 1 (name not supplied by CodexBar)'
.accounts[10].windows[0].label = 'Openrouter quota 1 (name not supplied by CodexBar)'
```

`_SLOT_POOL_PREFIXES` (`collectors/codexbar.py:490`) maps antigravity only.

**`--full` is width-invariant and its rules are narrower than its content.**
Byte-identical at COLUMNS=200/120/100/80/64. Measured line lengths:

```
 80| ================================================================================
 83| History: 234 snapshots in /Users/djbclark/.cache/aiuse/snapshots (learning auto/on)
 89| agy · account=… · plan=Google AI Pro · selected live source: CodexBar
 97|     $0.13 · flex:░ throttled · 50requests/cycle · pace 1.1x (blended w/ history, 16 samples)
```

`ACTION_PLAN_WIDTH = 80` at `report.py:54`; used at `:97` (as
`terminal_width()`'s default), `:202`, `:619`, and `tui/builders.py:16,63`.
So the deferral note in 067d ("widening the rules would look absurd") targets
the wrong risk: at every width the rules are already **too narrow** for their
own content, and lines soft-wrap at COLUMNS ≤ 96. The fix is
`min(terminal_width(), TABLE_MAX_WIDTH)`, the clamp the table already uses.

**The redesign is unreleased.** `git describe --tags HEAD` →
`v3.0.16-4-g69d259d`. The installed 3.0.16 has 0 `render_clock_matrix` and 1
`render_priority_ladder`. So `main`'s README advertises a display that cannot
currently be installed — _and_ the unreleased state is what keeps the hourly
job writing stale labels.

**Doc drift, verified by hand:**

- `render_priority_ladder` (`report.py:613`) has **zero callers in `src/`** —
  only `tests/` and `docs/generate-readme-demo.py`. But `AGENTS.md:130`
  routes display work to `docs/pretty-display.md`, which still documents it as
  the default renderer, under the pre-rename `ai.*` package path.
- `docs/generate-readme-demo.py` still emits the old ladder
  (`- empty oc-go · you@example.com · OpenCode Go weekly quota: 0% left`)
  while `README.md:34` claims its hero block "is real `aiuse` output". Anyone
  regenerating silently reverts the README.
- `docs/json-contract.md` `alerts[]` table (lines 202–219) lacks
  `used_percent`, which 69d259d shipped. Present in the `windows[]` table.
- `AGENTS.md:14` says packaging **3.0.2** (actual 3.0.16, agreed across
  `pyproject.toml:7`, `__init__.py:3`, the Homebrew formula, and `git tag`).
- `AGENTS.md` contradicts itself: `:14` "Steps **1–34** done" vs `:115`
  "**1–32 and 34 done**". `:168` says "Python 3.14" while
  `requires-python = ">=3.11"` and `.venv` is Python 3.13.14.
- `docs/handoff.md` (singular) is a 215-line archive titled "(current)",
  asserting 3.0.12 in three places and 454 tests. **`AGENTS.md` points a
  fresh agent at it four times** (`:30`, `:32`, `:114`, `:154`), plus
  `docs/index.md:48`, `docs/agent-api.md:6`, `docs/source-coverage.md:14`,
  `docs/next-options.md:5,8,68`. **Zero** in-repo references point at
  `docs/handoffs/`.

**Cross-format agreement, one live collection:** 13 table rows vs 13 chat
entities (9 subscription entries + 3 prepaid + 1 no-data). Before the change,
agy alone was 4 chat entries against 2 table rows.

**Test progression:** 482 → 495 (+13).

## Operator Feedback

- **"Just commit it"** — declined the diff walkthrough for the 17 files.
- **"Fix it now, then commit"** — chose to close the `--for-chat` granularity
  item in the same batch rather than defer it.
- **"Tell me about the open items"** — wanted the three deferred items
  explained, which is what turned up the live agy recurrence.
- **"Can we finish the work of the agents that died?"** — wanted the killed
  subagent work completed rather than abandoned.
- Has **not** asked for a commit split, a version bump, a tag, or a release.
  `AGENTS.md:184-186`: full releases only when the operator explicitly asks.

## Where We're Going

1. **THE NEXT ACTION — collect the resumed workflow's verify results.** Run
   `wf_cc03b97d-0bc` was resumed and still running at write time; it re-ran
   `investigate:full-width`, `verify:stale-snapshots`, `verify:docs-drift`
   (and then `verify:full-width`). Read them with:
   `python3 -c "import json;[print(json.loads(l).get('type'), str(json.loads(l).get('result'))[:400]) for l in open('/Users/djbclark/.claude/projects/-Users-djbclark-src-aiuse/10c8401e-2a06-4999-af8a-30eb1cb5b44f/subagents/workflows/wf_cc03b97d-0bc/journal.jsonl')]"`
   If it died on the session limit again (resets 11:30am America/New_York),
   re-resume: `Workflow({scriptPath: "/Users/djbclark/.claude/projects/-Users-djbclark-src-aiuse/10c8401e-2a06-4999-af8a-30eb1cb5b44f/workflows/scripts/aiuse-open-items-wf_cc03b97d-0bc.js", resumeFromRunId: "wf_cc03b97d-0bc"})`.
   Treat every finding it returns as a lead until re-derived — the two that
   already completed both got the agy question wrong.

2. **Decide the release question — it gates everything else.** Cutting 3.0.17
   stops the hourly job writing stale labels, closes the README-vs-installed
   gap, and fixes which version number the doc cleanups should write. It
   requires explicit operator sign-off (`AGENTS.md:184-186`). If the answer is
   "not yet", say so in the docs rather than describing `main` as if shipped.

3. **Fix the three docs that will cause a wrong action** (not the cosmetic
   sweep): `docs/pretty-display.md` rewritten for `render_clock_matrix` /
   % used / `terminal_width` and corrected `ai.*` → `aiuse.*` paths; add
   `used_percent` to the `alerts[]` table in `docs/json-contract.md`; and
   either repoint `docs/generate-readme-demo.py` at the matrix renderer or
   delete it and drop README's "real output" claim. If repointing, **diff its
   output against README's fenced block** — its synthetic snapshot may lack
   `window_minutes`, letting `infer_window_clock()`'s fallback pick different
   columns.

4. **Extend `_SLOT_POOL_PREFIXES` or improve the generic fallback** so
   deepseek and openrouter stop emitting
   `<Provider> quota N (name not supplied by CodexBar)`.
   `src/aiuse/collectors/codexbar.py:490-521`. Unlike antigravity these are
   prepaid/no-expiry accounts whose window label never reaches the table or
   chat, so the payoff is `--json` cleanliness and snapshot hygiene only.

5. **`--full` width clamp**: `min(terminal_width(), TABLE_MAX_WIDTH)` in place
   of the bare `ACTION_PLAN_WIDTH` at `report.py:202` and `:619`. Cheap; makes
   the rules bound their own content.

6. **One-liners**: `AGENTS.md:14` version 3.0.2 → current; a pointer from
   `AGENTS.md:32`/`:114` and `docs/index.md:48` to `docs/handoffs/`; retitle
   `docs/handoff.md` from "(current)" to an archive title naming its real
   endpoint (3.0.12 / 2026-08-11). Do **not** delete it — it holds per-release
   forensic detail (workflow run IDs, tap SHAs) that exists nowhere else.

7. **Deliberately NOT doing**: the rest of the AGENTS.md sweep (collector
   count, "Python 3.14", the step-33 contradiction, five missing doc-table
   rows) and README's five "priority ladder" wordings. Real but inert — nobody
   acts wrongly because of them. Batch into a session that has those files
   open anyway.

## Quick Start

```bash
cd ~/src/aiuse

# 1. Confirm the state this handoff describes
git rev-parse HEAD              # expect 69d259d…
git status -s                   # expect empty
git log --oneline origin/main..HEAD   # expect empty (all pushed)
uv run --extra dev pytest -q    # expect 495 passed
just check                      # expect PASS

# 2. See the reconciled formats on one collection
COLUMNS=110 uv run ai --no-tui
uv run ai --for-chat            # agy = 2 entries, matching the table's 2 rows

# 3. Reproduce the live stale-label finding
grep -c "_SLOT_POOL_PREFIXES" \
  ~/.local/pipx/venvs/aiuse/lib/python3.14/site-packages/aiuse/collectors/codexbar.py   # -> 0
grep -A3 StartInterval ~/Library/LaunchAgents/com.djbclark.aiuse.plist                  # -> 3600
python3 -c "
import glob,json,os
d=os.path.expanduser('~/.cache/aiuse/snapshots')
for f in sorted(glob.glob(os.path.join(d,'*.json')))[-40:]:
    if 'latest' in f: continue
    labs=[w.get('label') for a in json.load(open(f)).get('accounts',[])
          if 'grav' in a.get('provider','') for w in a.get('windows',[])]
    print(os.path.basename(f)[:19], 'STALE' if any('quota' in (l or '') for l in labs) else 'ok', labs)
"

# 4. The other live instance (json only)
uv run ai --json | grep "name not supplied"     # -> Deepseek / Openrouter

# 5. Prove --full is width-invariant
for w in 200 80 64; do echo "== $w =="; COLUMNS=$w uv run ai --full --no-tui 2>/dev/null | head -5; done

# 6. Confirm the redesign is unreleased
git describe --tags HEAD        # -> v3.0.16-4-g69d259d
```

**Where the new code lives**

| Concern              | Location                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------- |
| Chat pool grouping   | `src/aiuse/chat_format.py` `_PoolEntry`, `_group_rows_into_pools()`                               |
| Chat entry rendering | `src/aiuse/chat_format.py` `_render_pool_entry()`, `_row_notes()`, `_window_line_label()`         |
| No-data accounts     | `src/aiuse/chat_format.py` `_has_reportable_usage()` + the ERRORS section                         |
| Pool vocabulary      | `src/aiuse/analysis/pace.py` `POOL_SCOPE_LABELS`, `pool_scope_label()`                            |
| agy slot→pool        | `src/aiuse/collectors/codexbar.py:490` `_SLOT_POOL_PREFIXES`, `_slot_label()`                     |
| Stale-label filter   | `src/aiuse/analysis/history.py:361` (guard) → `:370` (`window_series_key`)                        |
| `--full` width pin   | `src/aiuse/report.py:54` `ACTION_PLAN_WIDTH`, used `:202`, `:619`                                 |
| Tests                | `TestPoolGrouping`, `TestActionItemsPerPool`, `TestNoDataAccounts` in `tests/test_chat_format.py` |
