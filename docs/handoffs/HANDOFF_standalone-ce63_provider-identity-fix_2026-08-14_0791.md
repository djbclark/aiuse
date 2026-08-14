---
schema_version: 1
handoff_id: 0791
parent_handoff_ids: []
lineage: none
chain: [standalone-ce63]
repo: aiuse
workspace: aiuse
branch: main
head_sha: a9d0c7ae15713a91807c3f35f5d0d07e48a15106
created_at: 2026-08-14T06:27:37-04:00
writer: claude-code
---

# Handoff — provider identity fix, released as 3.0.16

## The Goal

Started as a diagnostic question: **"Why is agy showing up under 2 different names here?"**
A single Antigravity subscription rendered as two vendors in one `ai` report:

```
mid   Gemini (agy) · default · Antigravity Gemini 5-hour: 94% left · use more each cycle
mid   Gemini (agy) · default · Antigravity Claude/GPT 5-hour: 100% left · use more each cycle
mid   Google AI / Antigravity (agy) · djbclark@gmail.com · Gemini weekly: 91% left · ok within 6.5 days
mid   Google AI / Antigravity (agy) · djbclark@gmail.com · Claude/GPT weekly: 100% left · ok within 6.5 days
```

The operator then scoped it up: **"Fix all of these things and also anything else
you find along the way"**, and finally **"commit everything, push, then do a full
release"**. All three are done. Released as **3.0.16**.

## Where We Are

Complete and shipped. Working tree clean, `main` == `origin/main` at `a9d0c7a`.

| Artifact       | State                                                                               |
| -------------- | ----------------------------------------------------------------------------------- |
| main           | `a9d0c7a`, clean, pushed                                                            |
| Tag            | `v3.0.16` pushed                                                                    |
| GitHub Release | published, not draft, sdist + wheel attached                                        |
| PyPI           | 3.0.16 is `latest` (OIDC publish workflow green)                                    |
| Homebrew tap   | `djbclark/homebrew-aiuse` @ `74906a9`, pushed                                       |
| Installed      | brew 3.0.16, pipx 3.0.16, `brew test` passed                                        |
| CI             | 5/5 runs green including `publish`                                                  |
| Tests          | 463 passed (was 454; +9 new regression tests)                                       |
| Gate           | ruff, ruff-format, mypy, markdownlint, prettier, bandit, semgrep, gitleaks all pass |

Three commits shipped:

- `b8c9b6d` — the fix
- `280e26c` — version bump (release script re-ran full suite)
- `a9d0c7a` — Homebrew formula

### Root cause (the reported symptom)

`aiuse` has two provider name spaces and they got crossed:

- `canonical_provider()` → `antigravity`, `opencode-go` — **identity and display**
- `provider_config_key()` → `gemini`, `opencode` — **`[plans]` / `provider_overrides` lookup only**

`analysis/history.py` normalized providers through `provider_config_key()` on
read (`history.py:286`, pre-fix), and `use_or_lose.py:589` passed that config key
straight into `provider_display_name()`. `PROVIDER_DISPLAY_NAMES` had a
`"gemini": "Gemini (agy)"` entry that made the leak _render_ instead of
falling back — papering over the bug with a second vendor name.

The `· default ·` half came from the same alert hardcoding `account=None`.
The `Antigravity `-prefixed labels came from a **second collector**: OpenUsage
reports the same subscription with `account=None` and `f"{display} {pretty}"`
labels, and source priority (`collectors/runner.py`) picks a different primary
per run, so history accumulated two label spellings for one window.

### Everything fixed (8 defects; only the first was reported)

1. **Two display names for one vendor.** `provider_display_name()` now
   canonicalizes first; `"gemini"` display entry deleted. New
   `canonical_provider()` + `PROVIDER_ID_ALIASES` in `models.py` is the single
   identity table, shared with `collectors/runner.py` (which had kept its own
   private `_PROVIDER_ALIASES` copy — the drift that allowed this).
2. **Label-forked history series.** New `window_series_key()` →
   `provider:pool:duration` (e.g. `antigravity:gemini:5h`), reusing
   `analysis/pace.py:independent_pool_key()`. Collector label variants collapse;
   genuinely independent pools (Gemini vs Claude/GPT) stay separate.
3. **Anonymous account.** History alerts adopt the live account via
   `resolve_live_window()`.
4. **`merge_learned_flexibility()` never matched** for aliased providers — built
   `antigravity:5h` against a store keyed `gemini:5h`. Antigravity and
   OpenCode Go silently received no learned burn rate. Store + all three
   consumers (`use_or_lose.py:365`, `report.py:1470`, `merge_learned_flexibility`)
   now canonical.
5. **`str(x.get("account", ""))` → `"none"`.** Snapshots store an explicit JSON
   `null` for anonymous rows; `str(None)` is `"None"`, so matching broke for
   _every_ anonymous-account provider (Copilot/tokscale, Grok, OpenCode Go,
   OpenRouter, all OpenUsage rows). Fixed in `_find_current_remaining` and
   `_account_window_key`.
6. **Shared-allotment suppression silently no-op'd.** It compared
   `(config provider, label.casefold())`; live children keyed
   `("gemini","gemini 5-hour")` while history held
   `("gemini","antigravity gemini 5-hour")`. Now `(series key, account)`.
7. **Two subscriptions of one provider averaged together.** `claude:-:5h`
   merged the gmail and mit.edu Claude accounts into one row under an arbitrary
   account. (This was a regression _I introduced_ in the first pass — see
   What We Tried.) Now grouped on `(series, account)`.
8. **Test suite wrote into the operator's live snapshot cache.** 12 `test_cli.py`
   tests persisted empty snapshots to `~/.cache/aiuse/snapshots/` — 90 files
   across 8 runs. Indistinguishable from real collections, and they displace
   genuine samples from the newest-N window `chronic_waste_summary()` reads.
   New `tests/conftest.py` autouse fixture seals it.

Plus: OpenUsage double-qualified labels whose `_RESOURCE_LABELS` entry already
names the pool (`_SELF_QUALIFIED_LABELS`); and the README demo _documented the
bug as expected output_ (`Gemini (agy)`, `Opencode`) because its generator
fixture used non-canonical provider ids.

## What We Tried

Chronological, including what failed — these are the expensive rediscoveries.

1. **Archaeology on the live snapshot directory — failed, twice.** Tried to
   reproduce the original report by reconstructing the 7 newest snapshots as of
   the 05:03 run and diffing old vs new code via `git stash push -- src/`. Both
   before _and_ after produced no history alerts, so the comparison proved
   nothing. Two reasons: (a) the reconstructed window contained only one
   OpenUsage snapshot, so the forked series had <2 samples; (b) **the directory
   was moving under me** — my own pytest runs were writing junk snapshots into
   it (defect 8), displacing real samples from the `[:7]` window.
   **Lesson: never validate against a mutating cache; build a deterministic
   fixture.** Switched to synthetic regression tests, which then correctly
   failed on pre-fix source (2 failures) and passed after.

2. **First leak-detector plugin silently did nothing.** Wrote a pytest plugin
   using `def pytest_runtest_call(item): ... yield ...` without
   `@pytest.hookimpl(hookwrapper=True)`. The bare `yield` made it a generator
   pytest never iterated, so it reported `(none)` while files were demonstrably
   being created. Adding the decorator immediately named all 12 culprits.
   **A green "no leaks" result from an unverified detector is not evidence.**

3. **First series-key design was account-blind — introduced a new bug.**
   `window_series_key(provider, label, minutes)` with no account merged the two
   Claude subscriptions into one `claude:-:5h` row reporting a single blended
   average. Caught only by eyeballing live `--json` output, _not_ by the test
   suite (which was green). Reworked to group on `(series, account)` with
   `resolve_live_window()` adopting an account for anonymous rows only when
   exactly one candidate exists.
   Rejected two simpler shapes: putting the account _in_ the key (re-forks the
   two collectors, the original bug) and leaving it out (merges subscriptions).

4. **Hypothesis that sliding `resets_at` starves burn-rate learning — measured
   and rejected.** Suspected `_find_current_remaining(match_resets=True)` almost
   never matched. Measured against real history: 118/170 (69.4%) strict matches,
   38 loose-only, 14 no match. Hypothesis wrong; only _cycle counting_ is
   affected (see Where We're Going).

5. **`git stash` cycles looked like data loss.** After a stash pop, `git status`
   showed only 3 of 6 edited source files as modified. Cause was not the stash:
   a **concurrent session had committed my in-progress edits** (see Key
   Decisions). Verified by `git show HEAD:src/aiuse/models.py`, which contained
   my comments verbatim.

## Key Decisions

**Chosen — canonicalize inside `provider_display_name()`** rather than fixing
each call site. One change makes every caller correct regardless of which
spelling it holds (report, TUI, chat_format, suggest, runner, history).
_Rejected:_ auditing ~40 call sites; keeping the `"gemini"` display entry
(it renders the bug rather than preventing it).

**Chosen — series key from the existing `independent_pool_key()`** in
`analysis/pace.py`. It already encodes the one distinction that matters
(hard-separated allotment families).
_Rejected:_ string-stripping vendor prefixes from labels (fragile, and every
new collector invents a new spelling); keying on label at all.

**Chosen — store learned burn rates under the canonical id** and update all
three consumers.
_Rejected:_ the one-line fix of making `merge_learned_flexibility()` call
`provider_config_key()`. That would have matched the store but kept config keys
leaking into the documented `--json` contract, where they don't join against
`accounts[]`.

**Chosen — `_chronic_series_key()` derives the key from provider + label when
`window_key` is absent.** `chronic_waste_summary()` only tracks short windows,
so the duration bucket is known. Suppression must not silently no-op on a record
of an older shape — that is exactly the failure mode being fixed. This also kept
an existing monkeypatched test meaningful instead of rewriting its fixture.

**Chosen — autouse `conftest.py` fixture** over patching 12 individual tests.
Makes the safe default structural: no future test can reach the real cache.

**Chosen — quarantine (move) the 90 junk snapshots**, not delete. They are in
the session scratchpad, reversible.

**Chosen — commit directly to `main`.** Default guidance is branch-first, but
this repo's history releases straight from main, `packaging/release.py` calls
`_ensure_on_main()`, and the operator explicitly asked to commit/push/release.

**Chosen — push the handoff commit.** `AGENTS.md:177` declares _"After each
commit, `git push` … Do not wait for separate push authorization."_

**Noted, not acted on — concurrent session collision.** Between the session
snapshot (`bbf1f0e`) and my first edits, another session committed `8b0e92d`
("Fix use-or-lose logic to mark completely exhausted windows as governing"),
which **swept my in-progress `models.py` / `history.py` / `runner.py` edits into
its commit**, then released them as **3.0.15**. Nothing was lost and the
combined state is green (the `pace.py` exhausted-window change is orthogonal to
the suppression work), but 3.0.15's commit message under-describes its contents.
Deliberately did not try to unwind another session's published release.

## Evidence & Data

**The two-name mechanism, from the actual snapshot** (`2026-08-14T050328`):
one account, five alerts, two of them `source: "history"`:

```
ACC   codexbar | antigravity | djbclark@gmail.com | [Gemini 5-hour, Gemini weekly, Claude/GPT 5-hour, Claude/GPT weekly]
ALERT {"urgency":"info","provider":"gemini","account":null,"window_label":"Antigravity Gemini 5-hour","source":"history"}
ALERT {"urgency":"info","provider":"gemini","account":null,"window_label":"Antigravity Claude/GPT 5-hour","source":"history"}
```

**Collector label fork**, surveyed across all snapshots:

```
('antigravity','djbclark@gmail.com','Gemini 5-hour')            n=90  codexbar
('antigravity', None,               'Antigravity Gemini 5-hour') n=25  openusage_ai  (20:32 → 02:32)
```

**Display-name mismatch before the fix:**

```
antigravity    live="Google AI / Antigravity (agy)"   history="Gemini (agy)"   <-- MISMATCH
opencode-go    live="OpenCode Go"                     history="Opencode"       <-- MISMATCH
claude         live="Claude Code"                     history="Claude Code"
cursor         live="Cursor (cursor-agent)"           history="Cursor (cursor-agent)"
```

**Test-cache leak**, found with a `hookwrapper` pytest plugin — 12 tests, all in
`tests/test_cli.py`: `test_brief_mode_skips_usage_section`,
`test_default_pretty_is_priority_ladder`, `test_full_mode_includes_providers`,
`test_alerts_only_includes_cross_check_warnings`,
`test_json_alerts_only_includes_structured_cross_check_warnings`,
`test_cli_timeout_flag_sets_force` (+2), `test_no_tokscale_works_when_collector_is_boolean_true`,
`test_main_exits_1_when_all_collectors_fail`, `test_main_exits_2_when_actionable_alerts`,
`test_main_exits_0_when_no_actionable_alerts`, `test_quiet_suppresses_progress_on_stderr`,
`test_without_quiet_prints_progress`.
90 empty-account snapshots quarantined; 185 real snapshots remained.
After `conftest.py`: real snapshot count before=276 after=276, detector reports `(none)`.

**Post-fix live output:**

```
mid   Google AI / Antigravity (agy) · djbclark@gmail.com · Gemini weekly: 88% left
mid   Google AI / Antigravity (agy) · djbclark@gmail.com · Claude/GPT weekly: 100% left
```

**Post-fix `--json` history** (canonical ids, new fields, accounts separated):

```json
{"provider":"antigravity","account":"djbclark@gmail.com","label":"Claude/GPT 5-hour","window_key":"antigravity:claude_gpt:5h","avg_remaining_pct":100.0,"sample_count":3}
{"provider":"claude","account":"djbclark@mit.edu","label":"Claude Code 5-hour","window_key":"claude:-:5h","avg_remaining_pct":100.0,"sample_count":5}
{"provider":"claude","account":"djbclark@gmail.com","label":"Claude Code 5-hour","window_key":"claude:-:5h","avg_remaining_pct":76.5,"sample_count":2}
```

**Open bug — sliding `resets_at`** (measured over the newest 7 snapshots,
spanning **10 minutes**):

```
provider / window                     distinct resets_at
antigravity / Claude/GPT 5-hour       5   <-- slides every collection
claude / Claude Code 5-hour           5   <-- slides every collection
claude / Claude Code weekly           7   <-- slides every collection
antigravity / Gemini weekly           1       (genuinely fixed boundary)
```

Antigravity's 5-hour reset is exactly `collected_at + 5h + 3s` each time.

**Files changed** (`b8c9b6d`, 603 insertions / 48 deletions):

```
AGENTS.md                          |   1 +
README.md                          |   5 +-
docs/antigravity-pools.md          |   5 +-
docs/generate-readme-demo.py       |   6 +-
docs/index.md                      |   1 +
docs/json-contract.md              |  24 ++
docs/provider-identity.md          | NEW
src/aiuse/analysis/history.py      |  84 +++--
src/aiuse/analysis/use_or_lose.py  |  75 ++++-
src/aiuse/collectors/openusage.py  |  15 +
src/aiuse/report.py                |   5 +-
tests/conftest.py                  | NEW
tests/test_history.py              | 292 +++
tests/test_use_or_lose.py          | 138 +++
```

`src/aiuse/models.py`, `src/aiuse/analysis/history.py` (first pass) and
`src/aiuse/collectors/runner.py` are **in `8b0e92d`/3.0.15**, not `b8c9b6d` —
see Key Decisions.

**New regression tests (9):** `test_window_series_key_is_stable_across_collector_label_variants`,
`test_chronic_waste_reports_live_account_and_label`,
`test_chronic_waste_merges_both_collectors_into_one_series`,
`test_learned_burn_rates_key_on_canonical_provider`,
`test_history_matches_snapshot_rows_with_null_account`,
`test_chronic_waste_keeps_two_accounts_of_one_provider_apart`,
`test_anonymous_history_row_does_not_borrow_a_sibling_account`,
`test_history_alert_uses_the_same_provider_name_as_live_rows`,
`test_history_child_is_suppressed_across_collector_label_variants`.
The last two fail on pre-fix source (verified by stashing `src/`).

## Operator Feedback

- **"Try to not ask me questions starting 5 minutes from now."** — Work
  autonomously; state assumptions and proceed rather than blocking. Honored for
  the rest of the session (no `AskUserQuestion` calls).
- **"Fix all of these things and also anything else you find along the way."** —
  Broad remit to fix adjacent defects, not just the reported one.
- **"3.0.15 is already released, next release should be 3.0.16."** — sent
  mid-turn; matched the plan already in flight.
- **"commit everything, push, then do a full release"** — the AGENTS.md gate
  ("full releases only when the operator explicitly asks") was satisfied.
- **"~/src/aiuse/.venv/bin/ should not be in the PATH please remove it."** —
  Investigated: **it was never on PATH.** Not in a clean login shell
  (`env -i bash -lc`), no shell rc reference, no `.envrc`/direnv, absent from
  `/etc/paths*` and `launchctl getenv PATH`. The Homebrew shadow warning was an
  artifact of running the release via `uv run`, which prepends the project venv
  to PATH for its child process only. **My earlier note calling it a persistent
  PATH precedence issue was wrong and was corrected.** `ai`/`aiuse` resolve to
  the pipx shim `~/.local/bin/ai → ~/.local/pipx/venvs/aiuse/bin/ai`, on 3.0.16.
  Standing preference inferred: the project venv should not shadow installed
  binaries.
- **"any loose ends?"** — Wants explicit, verified accounting, not reassurance.

## Where We're Going

1. **NEXT ACTION — fix reset-cycle counting in `chronic_waste_summary()`.**
   It counts _distinct `resets_at` values_ as distinct reset cycles, but most
   providers report a sliding reset recomputed each collection (evidence above:
   7 distinct values across 7 snapshots spanning 10 minutes). So `by_reset` —
   designed to hold one sample per cycle — holds one per _snapshot_; the `≥2
cycles` gate is met by running `ai` twice; and "over N cycles" in `--full`
   and `--json` actually means "over N collections". Pre-existing, untouched by
   3.0.16, currently masked in the priority ladder by the suppression fix but
   wrong in `--full` and `--json`. Needs a design decision: bucket by reset
   _boundary_ (e.g. round to the window period, or detect monotonic slide)
   rather than exact timestamp.
2. **Related — replace the `history[:7]` cap with a time-based lookback.** Seven
   snapshots is ~10 minutes at the current cadence, so for providers whose
   boundary genuinely is fixed (Gemini weekly) it can essentially never observe
   2 real cycles. The current rule is simultaneously too loose for sliding
   windows and too tight for fixed ones. `history.py:_DEFAULT_LOOKBACK_DAYS = 7`
   already exists and is **unused** — likely the intended mechanism.
3. Delete dead code: `_account_window_key()` is referenced only by its own test
   (`tests/test_history.py:60`), nothing in `src/`.
4. Refresh `AGENTS.md` "Active priorities" — still reads _Status (2026-07-30) …
   packaging 3.0.2_; actual is 3.0.16.
5. Reconcile the two handoff conventions: `docs/handoff.md` (singular, dated
   2026-08-11, referenced by `AGENTS.md` as "first stop after this file when
   resuming") vs this new `docs/handoffs/` directory. Either point `handoff.md`
   at the directory or fold it in.

## Quick Start

```bash
cd ~/src/aiuse
git log --oneline -3          # expect a9d0c7a / 280e26c / b8c9b6d
git status -s                 # expect clean

# Full quality gate (same as CI)
just check                    # test ruff mypy yamllint markdownlint prettier typos just-check
uv run --extra dev pytest -q  # expect 463 passed

# Reproduce the open cycle-counting bug (item 1)
python3 -c "
import glob,os,json
fs=sorted([f for f in glob.glob(os.path.expanduser('~/.cache/aiuse/snapshots/*.json')) if 'latest' not in f], reverse=True)[:7]
seen={}
for f in fs:
    d=json.load(open(f))
    for a in d['accounts']:
        for w in a.get('windows') or []:
            seen.setdefault((a['provider'],w['label']),set()).add(str(w.get('resets_at')))
for k,v in sorted(seen.items(), key=str):
    if len(v)>2: print(f'{k}: {len(v)} distinct resets_at  <-- slides')
"

# See the affected output
uv run python -m aiuse --full | sed -n '/## History/,/^## /p'
uv run python -m aiuse --json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['history']['chronic_underuse'], indent=2))"

# Key reading
docs/provider-identity.md     # the canonical-id vs config-key rule (written this session)
docs/json-contract.md         # history section: account / window_key fields
src/aiuse/analysis/history.py # window_series_key, live_window_index, resolve_live_window, chronic_waste_summary
```

**Do not** validate history behavior against `~/.cache/aiuse/snapshots/` — it
mutates under you (the hourly LaunchAgent, plus any `ai` run). Use
`patch("aiuse.analysis.history.snapshot_dir", return_value=tmp_path)` with a
built fixture, as the new tests do.
