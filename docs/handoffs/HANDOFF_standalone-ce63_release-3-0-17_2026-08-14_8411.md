---
schema_version: 1
handoff_id: 8411
parent_handoff_ids: [7bd3]
lineage: deterministic
chain: [standalone-ce63]
repo: aiuse
workspace: aiuse
branch: main
head_sha: fdebc0a6173fdf5d120ddf1a540d38b9a505cd83
created_at: 2026-08-14T17:22:43-0400
writer: claude-code
---

# Handoff — Discharged the open items and shipped 3.0.17

## The Goal

Session opened with `/baton`. Tier 1 (chain `standalone-ce63`) named one
pending action: collect the results of the resumed workflow run
`wf_cc03b97d-0bc`, whose three verify agents had died on a session usage limit
in the previous session.

This time **all five agents completed**. Their findings, plus a batched
operator decision, set the rest of the session: fix what the investigation
actually found, close the doc drift, and cut 3.0.17.

Operator decisions (batched up front, per the standing "don't pause between
plan phases" preference):

1. Release → **"Cut and ship 3.0.17"**
2. `--full` width → **"Fix the real clamp only (~8 lines)"**
3. Snapshots → **"Add write-side prune honoring retention"**

And later, mid-session: **"Stop that session first, then release"** (see
Operator Feedback and the concurrency incident below).

All of it is done.

## Where We Are

**Git state**

- Branch `main`, `head_sha` `fdebc0a6173fdf5d120ddf1a540d38b9a505cd83`
- Working tree **clean**. Nothing unpushed.
- `git describe --tags HEAD` → `v3.0.17-1-gfdebc0a` (the trailing commit is the
  Homebrew formula refresh, which the release script makes _after_ tagging).
- Tests: **505 passed** (was 495). Full `just check` gate: **PASS**.
- **Blockers: none.** One open question (the fate of `render_priority_ladder`)
  is parked in Where We're Going item 5; it blocks nothing.

**Commits this session**

| Commit    | Subject                                                        | Scale              |
| --------- | -------------------------------------------------------------- | ------------------ |
| `4ba6193` | Close four data-correctness gaps found investigating the items | 13 files, +447/−28 |
| `7391949` | docs: describe the display the tool actually prints            | 9 files, +277/−109 |
| `7f1f0a9` | Bump version to 3.0.17                                         | 4 files            |
| `fdebc0a` | Update Homebrew formula for v3.0.17                            | 1 file             |

`5540118` ("docs: correct the --full width finding in handoff 7bd3") is in this
range but is **not this session's** — see the concurrency incident.

**3.0.17 is fully published**

- Tag `v3.0.17`; GitHub Release published 2026-08-14T15:58:24Z (not draft, not
  prerelease); `publish.yml` run `31817098165` succeeded in 31s.
- PyPI has `aiuse-3.0.17-py3-none-any.whl` + `aiuse-3.0.17.tar.gz`.
  **Gotcha:** `https://pypi.org/pypi/aiuse/json` reported `3.0.16` as latest
  right after publish — CDN cache, not a failed upload. The per-version URL
  `https://pypi.org/pypi/aiuse/3.0.17/json` returned 200 immediately.
- Homebrew tap `~/src/homebrew-aiuse` at `b5e151d`; `brew test` passed.
- `aiuse --version`, `ai --version`, and the Homebrew cellar binary all report
  `3.0.17`.

## What We Tried

Chronological. The expensive-to-rediscover part.

1. **Nearly wrote a fabricated `window_minutes` into the collector.** The
   obvious fix for the forked-series bug was to have `_slot_label`'s sibling
   path write back the minutes it had already inferred via
   `_block_window_kind`. Rejected after reading `infer_window_clock`:
   `window_minutes` is the _declared_ field and drives the `inferred` flag that
   renders a cell dim. Writing a guess there would have silently promoted a
   guess to an authority **and** poisoned history, because
   `_block_window_kind`'s last tier is distance-to-reset — a weekly window seen
   a few hours before it resets is indistinguishable from a 5h one.

2. **Also rejected: a new persisted field.** `inferred_window_minutes` on
   `QuotaWindow`, written to `to_dict`. Correct but it changes the published
   JSON contract for an internal problem. The label text already carries the
   period, and `infer_window_clock` already treats a label match as _declared_
   (`inferred=False`), so a read-side helper was strictly smaller.

3. **My first `_slot_label` fallback was too broad and the suite caught it.**
   Gating "prepaid balance" on "no duration and no reset" alone made
   `_slot_label("antigravity", 3, {})` return
   `"Google AI / Antigravity prepaid balance"` — antigravity is a subscription.
   `test_antigravity_slot_label_falls_back_when_reset_is_absent` failed. Re-gated
   on `PREPAID_HINTS`. **An empty slot means "no data for a pool CodexBar knows
   about", not "this is credit".**

4. **Over-specified the width regression test and it failed on a true fix.**
   Built the fixture with deliberately long labels/emails; rows came out >110
   chars, so they were still truncated — by the _new_ 110 cap, not the old 80.
   Measured real widths, resized the fixture to land at 86 chars: above the old
   clamp, below the new one. **A regression test for "stops truncating" must
   sit between the two limits, not beyond both.**

5. **A destructive `Edit` replaced the `_window` helper with a single `>`.** An
   old_string/new_string slip blanked the function in
   `docs/generate-readme-demo.py`. The retry then failed with "Found 6 matches"
   because `>` is not unique. Recovered by locating the line
   (`grep -n "^>$"` → line 69) and splicing the replacement in by index with
   Python. **After a bad Edit, do not retry the same shape — find the damage
   by line number first.**

6. **Repointing the demo generator to the matrix produced a visibly worse
   README**: every row collapsed into `WEEK` and the entire `$ UNUSED` column
   was em-dashes. Two causes, both real: the synthetic windows carried no
   `window_minutes` (so `infer_window_clock` fell to reset-distance and bucketed
   everything the same), and the script passed **no config**, so
   `_window_value_usd` returned `None` for every row — it needs
   `plans[provider].monthly_price`. Fixed by declaring durations and rendering
   against `DEFAULT_CONFIG`. **This is exactly the failure 7bd3 predicted.**

7. **Two stale greps contradicted the real file state.** After editing
   `serve.py` and `tui/builders.py`, `grep -n` printed the pre-edit lines, which
   read as if my changes had been reverted. `git diff` showed both edits
   present. **When a grep and a diff disagree about the working tree, the diff
   is right.**

8. **Misread the concurrent commit as a pre-commit typos autofix.** The staged
   change to a _committed_ handoff looked like the `typos` hook rewriting
   verbatim quotes (a known hazard 7bd3 recorded). It was a whole other Claude
   session. Diagnosed properly only after checking file mtimes (11:34:30), the
   index mtime (11:34:47), and `ps`.

## Key Decisions

- **`effective_window_minutes(label, window_minutes)` in `models.py`, used by
  both the live and history sides.** Prefers the declared duration, falls back
  to the period the label names, and **deliberately stops there** — the
  distance-to-reset tier is excluded. Verified the fork actually closes:
  `('Gemini 5-hour', 300)` and `('Gemini 5-hour', None)` both key to
  `antigravity:gemini:5h`; the pre-fix generic label still keys to
  `antigravity:-:?` and stays excluded, which is wanted — resurrecting the
  forked identity was never the goal.
- **Prepaid labelling gated on `PREPAID_HINTS`, not on missing reset.** See
  failed approach 3.
- **`clamp_width` split out from `width` in `_render_brief_action_plan`.** One
  parameter was serving as both the section-rule width and the truncation
  width; that conflation _was_ the bug. The plain caller now passes
  `min(terminal_width(), TABLE_MAX_WIDTH)`; the styled caller passes its panel
  width, which is a truthful clamp. _Rejected:_ the operator's option 2 (clamp
  both `--full` renderers to 110), because it visibly shrinks today's
  full-terminal Rich rules and they chose the narrow fix.
- **Pruning reads the filename, never the file.** Parsing every JSON to decide
  what to delete would cost exactly the expense pruning exists to bound. Also:
  `retention_days <= 0` disables pruning rather than deleting everything;
  `latest.json`, the just-written file, and any unrecognized name are never
  candidates; the prune runs **after** the write and its failure is swallowed.
- **Legacy colon-format filenames are prunable too.** Initially they were
  unparsable and would have lingered forever. They are our own older format,
  so `_LEGACY_SNAPSHOT_TS_FORMATS` handles them — still without opening a file.
- **Pinned `$COLUMNS` in `tests/conftest.py` rather than per-test.** The suite
  was silently reading the developer's terminal; the new clamp made that worse.
  One autouse fixture at 120 columns.
- **Every new test verified to fail against pre-fix behavior.** Not assumed —
  actually run with the fix monkeypatched out (bare-slot test: 1 → 0 rows;
  clamp test: rows regain the `…`).
- **`docs/handoff.md` retitled, not deleted.** It is the only in-repo record of
  releases 2.1.16–3.0.12 and there is no `CHANGELOG.md`. Both verify agents
  independently reached the same conclusion.
- **Scope held where 7bd3 drew it.** Left "ladder" in the analytical docs
  (`competitive-landscape`, `source-coverage`, `antigravity-pools`), where it
  names the ranking concept the matrix inherited rather than a renderer, and
  left the `/v1/ladder` HTTP path alone — it is a published endpoint name.
  Fixed only the wordings that tell a user what the tool prints.
- **Committed by explicit path, never `git add -A`, while a second session was
  live.** See below.

## Evidence & Data

**The workflow's two overturning findings** (both independently re-derived by
hand before acting, per 7bd3's warning to treat agent output as leads):

1. _The stale agy labels were unreachable, not merely absent._ All 68 old-label
   rows carry `window_minutes: null`, and all three history consumers gate on a
   non-null duration (`history.py` burn-rate ~`:269-273`, chronic-waste `:363`,
   late-cycle `:684-690`). 0 of 260 snapshots had the string anywhere in their
   `alerts[]`. Both remedies 067d proposed were wrong: pruning would destroy 13%
   of the corpus (385 non-antigravity rows) to remove rows nothing reads, and
   the proposed alias is a provable no-op —
   `window_series_key('antigravity', 'Google AI / Antigravity quota 1 (…)', None)`
   → `antigravity:-:?`, which shares no component with `antigravity:gemini:5h`.

2. _`--full` has two renderers._ The plain path (`--no-tui`/piped) is pinned at
   `ACTION_PLAN_WIDTH`; the Rich path — what a tty user actually gets — is
   `console.width - _PANEL_CHROME`, **uncapped**. 067d's "widening the rules
   would look absurd" describes something that **already ships** on the path
   users see. And "not truncating" was true of the data, false of the code.

**Verified by hand before writing any fix:**

- `render_priority_ladder` — zero callers in `src/` (only its own `def`).
- `cli.py` ships `docs/json-contract.md` verbatim via `aiuse schema`, so the
  missing `used_percent` row was a **shipped contract defect**, not just docs.
  Confirmed the emitted alert keys included `used_percent` while the documented
  table did not.
- Snapshots: 301 files / 5.2 MB, and the only `unlink` in all of `src/` is the
  `.tmp` cleanup at `history.py:71`.

**The real shape of the prepaid rows** (from a live snapshot) — the thing that
made "quota 1" wrong twice over:

```
deepseek | prepaid_balance | balance_usd: 7.81
   label: Deepseek quota 1 (name not supplied by CodexBar)
   minutes: None | used%: 0.0 | rem%: 100.0 | resets: None
   reset_description: $7.81 (Paid: $7.81 / Granted: $0.00)
```

No duration, no reset, and a meaningless 0%-used. Now: `DeepSeek prepaid
balance` / `OpenRouter prepaid balance`, verified live (0 hits for
`name not supplied` in `ai --json`).

**Tests added (10), 495 → 505:**

```
test_effective_window_minutes_prefers_declared_then_label
test_bare_slot_shape_joins_the_titled_series_not_a_forked_one
test_prepaid_providers_report_a_balance_not_an_unnamed_quota
test_slot_label_names_a_window_by_its_reset_when_duration_is_missing
test_at_a_glance_clamps_to_the_terminal_not_the_rule_width
test_prune_removes_only_expired_snapshots
test_prune_never_touches_files_it_did_not_write
test_prune_disabled_rather_than_total_wipe_on_zero_retention
test_prune_handles_the_legacy_colon_filename_format
test_save_snapshot_prunes_and_keeps_what_it_just_wrote
```

**Release verified through the installed binary, not the repo.** This is the
check that matters, because the repo was never the problem:

```
grep -c '_SLOT_POOL_PREFIXES' \
  ~/.local/pipx/venvs/aiuse/lib/python3.14/site-packages/aiuse/collectors/codexbar.py
# -> 2   (was 0; that zero is why the hourly launchd job wrote stale labels)
```

`~/.local/bin/aiuse` → `~/.local/pipx/venvs/aiuse/bin/aiuse`, which is what
`~/Library/LaunchAgents/com.djbclark.aiuse.plist` runs hourly. The stale-label
generation is stopped **at its source**.

**Pruning is a no-op today.** A live run went 336 → 337 files: one written,
nothing pruned, because the oldest file is `2026-08-13T200227` — about a day
old against a 90-day retention. First real deletions ≈ 2026-11-11.

### Concurrency incident (recurring hazard, second occurrence)

A second Claude Code session — **PID 31785, `ttys004`, started 07:43** — was
live in this same working tree. While this session worked it edited
`docs/handoffs/…_7bd3.md` (mtime 11:34:30), staged it (index mtime 11:34:47),
committed `5540118`, and pushed. Its correction cites `clamp_width` and
`report.py:1474` — **code this session had written minutes earlier and not yet
committed**, so it was reading this session's uncommitted working tree.

Nothing was lost: its commit touched only the handoff, `4ba6193` touched only
this session's 13 files. But 7bd3 records the same hazard biting harder once
before — a concurrent session swept another's in-progress edits into its own
commit and shipped them as 3.0.15.

Response: ran the full gate, staged **by explicit path** (13 named files, never
`git add -A`), committed, pushed immediately to make the work
unclobberable — then surfaced it to the operator before touching the release.
Operator confirmed it was the session that had just run `/handoff` and closed
it; `ps` then showed only this session.

Detection commands that actually worked:

```bash
ps -o pid,tty,lstart,command -ax | grep -E "[c]laude$"
stat -f "%Sm" -t "%F %T" .git/index
```

## Operator Feedback

- **"Cut and ship 3.0.17"** — chose the full release over documenting `main` as
  unreleased. This is the explicit ask `AGENTS.md` requires for a release.
- **"Fix the real clamp only (~8 lines)"** — declined unifying the two `--full`
  width policies; did not want today's interactive rules to change.
- **"Add write-side prune honoring retention"** — accepted deleting files under
  `~/.cache/aiuse/` given the guards.
- **"Stop that session first, then release"** — would not release from a tree a
  second agent was committing to. Then: **"It should be closed now, check. It
  was just still open right after the /handoff."**
- Standing preference honored: batch questions up front, don't pause between
  plan phases ([[feedback_continuous_work_batch_questions]]).

## Where We're Going

1. **THE NEXT ACTION: nothing is pending.** Every item 7bd3 queued is
   discharged — items 1–6 done, item 7 ("deliberately NOT doing") consciously
   preserved. **Do not re-open its list without new evidence.** If you arrived
   here expecting queued work, that is the finding: verify with
   `git describe --tags HEAD` → `v3.0.17-1-gfdebc0a` and `aiuse --version` →
   `3.0.17`, then ask the operator what they want next.

2. **Check for concurrent sessions before any release or bulk commit.**
   `ps -o pid,tty,lstart,command -ax | grep -E "[c]laude$"` — expect exactly
   one. This has now bitten twice in this repo.

3. **Revisit pruning around 2026-11-11**, when files first cross the 90-day
   retention and `prune_snapshots` does real deletions. Until then it is
   exercised only by tests. Confirm with
   `ls ~/.cache/aiuse/snapshots/*.json | wc -l` trending flat rather than up.

4. **Optional, genuinely inert** (7bd3 parked these; still parked): the rest of
   the AGENTS.md sweep — "Five collectors" vs 8 modules in
   `src/aiuse/collectors/`, "Python 3.14" vs `requires-python = ">=3.11"` and a
   3.13.14 `.venv`, the `:14` "Steps 1–34 done" vs `:115` "1–32 and 34 done"
   contradiction, and doc-table rows missing for `docs/index.md`,
   `docs/source-coverage.md`, and two audit docs.

5. **Open question — `render_priority_ladder`.** Zero `src/` callers, ~20 tests
   pinning it, documented as superseded. Deleting it is defensible but drops
   that coverage of the band/urgency semantics the matrix inherited. Needs an
   operator call; not urgent.

6. **Watch the demo/README coupling.** `docs/generate-readme-demo.py` now feeds
   README's fence between `<!-- readme-demo:start -->` / `<!-- readme-demo:end -->`.
   Its `NEXT` column derives from the real wall clock (`NOW = utcnow()`), so
   regenerating always produces a small diff there. That is by design — do not
   "fix" it by freezing the clock, which would make every reset read as past.

## Quick Start

```bash
cd ~/src/aiuse

# 0. Confirm no other agent is in this tree (this has bitten twice)
ps -o pid,tty,lstart,command -ax | grep -E "[c]laude$"    # expect exactly one

# 1. Confirm the state this handoff describes
git rev-parse HEAD                    # fdebc0a…
git status -s                         # empty
git log --oneline origin/main..HEAD   # empty
git describe --tags HEAD              # v3.0.17-1-gfdebc0a
uv run --extra dev pytest -q          # 505 passed
just check                            # PASS

# 2. Confirm the release actually reached the thing that mattered
grep -c '_SLOT_POOL_PREFIXES' \
  ~/.local/pipx/venvs/aiuse/lib/python3.14/site-packages/aiuse/collectors/codexbar.py  # -> 2
aiuse --version && ai --version       # both 3.0.17

# 3. The four fixes, live
uv run ai --json | grep -c "name not supplied"        # -> 0
COLUMNS=110 uv run ai --no-tui | head -3              # clock matrix, % used
uv run python -c "
from aiuse.models import effective_window_minutes as e
from aiuse.analysis.history import window_series_key as k
for wm in (300, None):
    print(wm, '->', k('antigravity','Gemini 5-hour', e('Gemini 5-hour', wm)))
"                                                     # both antigravity:gemini:5h

# 4. Regenerate the README demo (expect only NEXT-column drift)
uv run python docs/generate-readme-demo.py
```

**Where this session's code lives**

| Concern                  | Location                                                                     |
| ------------------------ | ---------------------------------------------------------------------------- |
| Duration unification     | `src/aiuse/models.py` `effective_window_minutes()`, `clock_from_label()`     |
| History consumers        | `src/aiuse/analysis/history.py` — 4 call sites, live + 3 aggregations        |
| Prepaid slot labels      | `src/aiuse/collectors/codexbar.py` `_slot_label()`, gated on `PREPAID_HINTS` |
| At-a-glance clamp        | `src/aiuse/report.py` `_render_brief_action_plan(clamp_width=…)`             |
| Snapshot pruning         | `src/aiuse/analysis/history.py` `prune_snapshots()`, `_snapshot_file_time()` |
| Deterministic test width | `tests/conftest.py` `deterministic_terminal_width`                           |
| Display docs             | `docs/pretty-display.md` (rewritten, incl. "Superseded" section)             |
| README demo generator    | `docs/generate-readme-demo.py` → README `readme-demo` markers                |
