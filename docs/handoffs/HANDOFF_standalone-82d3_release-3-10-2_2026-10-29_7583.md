---
schema_version: 1
handoff_id: 7583
parent_handoff_ids: []
lineage: none
chain: [standalone-82d3]
repo: aiuse
workspace: main
branch: main
head_sha: 965bce7d6fee88c658dfa3cffa68674bddd1442a
created_at: 2026-10-29T10:15:00-0400
writer: grok
---

# Handoff — 3.10.2, note rows, CodexBar HTTP, filename counts

This session ran in `/Users/djbclark/src/aiuse` and committed from the cow
pasture `/Users/djbclark/.cow/pastures/aiuse/snapshot-count`. Both checkouts are
`main`. This chain does not own `aiuse/main`: that pointer redirects to
`chains/standalone-132d/SESSION_LOG.md` (herdr monitor layout, updated
2026-10-29T09:23:52-0400). Resume this work with an explicit path:

`/baton ~/.local/state/handoffs/chains/standalone-82d3/SESSION_LOG.md`

## The Goal

Make note-only matrix rows start at the first column, keep gifted OpenCode Go
off the default matrix, read CodexBar over its local HTTP server when that
server is healthy, speed the watch first frame by counting snapshots from
filenames, keep the tokscale ledger, and publish that work as **3.10.2**.
The release is published. Nothing in this list is still owed.

## Where We Are

Written against `965bce7` (`docs: status line is 3.10.2`). Committing this file
is the next commit on `main`. Dirty in both checkouts: untracked `opencode.json`
only. Do not commit it (local graft MCP wiring; `docs/memory/reference_graft_wiring.md`).

1. **Release 3.10.2 is published.** Tag
   [v3.10.2](https://github.com/djbclark/aiuse/releases/tag/v3.10.2)
   (published 2026-10-29T13:48:15Z). PyPI
   [aiuse 3.10.2](https://pypi.org/project/aiuse/3.10.2/). Publish workflow
   [run 40885313987](https://github.com/djbclark/aiuse/actions/runs/40885313987)
   succeeded on the version bump `425a0a5`. Formula commit on origin/main is
   `55a2774`. Tap
   [33f54ed](https://github.com/djbclark/homebrew-aiuse/commit/33f54ed).
   This handoff turn reconfirmed `/opt/homebrew/opt/aiuse/bin/aiuse --version`
   prints `aiuse 3.10.2`. Cellar path `/opt/homebrew/Cellar/aiuse/3.10.2`.
   `brew test` passed during the release. `~/.local/bin/aiuse` and `ai` are
   pipx shims and also printed `aiuse 3.10.2`. Homebrew warned that the
   pasture `.venv/bin` shadows those names when it is earlier on `PATH`.
2. **A watch process started before the upgrade is still the old binary**
   until that process is started again. Do not pass `--all-providers`.
3. **Status lines now say 3.10.2.** `AGENTS.md` and `docs/current-status.md`
   opened on packaging 3.9.4 dated 2026-10-22. `965bce7` updates those two
   opening lines to 2026-10-29 and 3.10.2. The historical 3.9.4 omp-latch
   sentence stays. `docs/packaging.md` already said 3.10.2 (the release script
   rewrote it).
4. **Pasture stays.** Operator chose to leave
   `~/.cow/pastures/aiuse/snapshot-count`. It is `main`, tracking origin, same
   tip as the primary once this handoff is pushed. Tracked
   `src/aiuse/credentials.py` was left in place when the pasture was created.
5. **This session is not a watch holder.** `bin/watch status` during the
   diagnosis showed cfengine, paseo, and load held by other Paseo agents.
   There is no `./aiuse-watch.sh`.

### What 3.10.2 contains

Commits on `main` after tag `v3.10.1` (`7554db2`), this session unless noted:

- `8590399` — other session's OpenUsage-uninstall handoff.
- `3cb1dcb` — OpenCode Go monthly window from `access.startsAt` (in the
  release; already on main before the note-row commit).
- `fa82092` — note-only matrix rows, and oc-go on the operator sweep only.
- `3b09fb6` — CodexBar HTTP when `/health` is ok.
- `e8fd216` — docs: HTTP is the preferred CodexBar source.
- `2d6f501` — count snapshots by filename.
- `39cb31a`, `cb03d3a` — other session: herdr-monitor handoff, rebased onto
  origin. Local object `89e98ec` is not the branch tip. Do not reset it.
- `167872c` — watch-frame timing and the SQLite judgment, in
  `docs/current-status.md`.
- `425a0a5`, `55a2774` — version bump and Homebrew formula.
- `965bce7` — status line is 3.10.2.

### Files this session changed

Note rows and the sweep: `src/aiuse/report.py` (`_situation_body`),
`src/aiuse/collectors/codexbar.py` (`include_disabled`,
`_disabled_known_query_ids`), `src/aiuse/watch.py` (`all_providers_config`),
`src/aiuse/collectors/runner.py`, `src/aiuse/config.py`, `docs/watch-mode.md`,
tests in `tests/test_report_layout.py`, `tests/test_watch.py`,
`tests/test_codexbar_parse.py`, `tests/test_codexbar_cache.py`,
`tests/test_omp_operator_gate.py`, `tests/test_window_pace.py`.

CodexBar HTTP: `src/aiuse/collectors/codexbar.py`, `runner.py`, `config.py`,
`config/config.example.toml`, `tests/test_codexbar_parse.py`,
`tests/test_config.py`. Docs: `README.md`, `docs/collectors-caut-openusage.md`,
`docs/collector-concurrency.md`, `docs/current-status.md`.

Filename count: `src/aiuse/analysis/history.py` (`count_snapshots`,
`_untimestamped_snapshot_counts`), `tests/test_history.py`
(`test_count_snapshots_uses_filename_time`).

`collect_with_ledger` was not changed. The tokscale ledger still runs.

### Tests

No tests were re-run in the handoff turn. Results from the implementation
turns:

- Note-row change: `just ci`, 1420 passed, then pushed.
- Filename count: focused history tests 97 passed in 0.74s. Full pytest
  1425 passed in 36.78s before `2d6f501`.
- `just release 3.10.2`: pytest 1425 passed in 20.79s. `brew test` passed.
- A watch frame after `2d6f501` was not re-timed.

## What We Tried

1. **First cut of 3.10.2 was aborted.** The operator chose "Cut 3.10.2", then
   said `stop` while pre-push CI was running. That task was killed. Local bump
   `b6ec395` was removed with `git reset --hard HEAD~1` on the primary. The
   remote stayed at `e8fd216`. No tag was published from that attempt. The
   later "do a full release" is the release that exists.
2. **`GET /usage?provider=all` timed out at 20 seconds.** Do not repeat that
   query. Per-provider HTTP queries are the path that returned.
3. **A diagnostic reported the token in a URL when it was not.** The next
   source line was the config key name `http_token`. The value was never
   placed in a query string. Do not print the token. Do not put it in a URL.
4. **A broad walk of `~/src` and `~/ops` looking for `aiuse-watch.sh` was
   killed.** There is no such script.
5. **The first timing script dropped query params.** The second run is the
   measurement to trust (17.503s, below). Calling the post-fix frame "about
   7s" was an inference. `docs/current-status.md` says a frame after `2d6f501`
   was not re-timed.
6. **Primary could not fast-forward while it was one commit ahead**
   (`89e98ec`, the other session's herdr-monitor handoff) and one behind.
   That other session rebased it to `39cb31a` plus `cb03d3a`. After `167872c`
   and after `55a2774`, the primary fast-forwarded. Do not rebase or reset
   `89e98ec`.
7. **An empty `CLAUDE_CODE_SESSION_ID` makes a bigteam job scan match other
   sessions.** Those jobs (litellm, firecrawl, tui follow-ups, and the rest)
   are not this session. Do not re-arm them.

## Key Decisions

Gifted OpenCode Go stays off the default matrix. The operator said that
omission is on purpose. The operator sweep (`aiuse watch --all-providers` and
the `a` key) is the only place those pools are collected. Agents still need
current-session permission before running that sweep. CodexBar's `"all"`
discovery stays enabled-only. `include_disabled` adds one CLI id per canonical
provider CodexBar has disabled that this run did not already collect. It does
not query every plugin.

CodexBar is available. Installed and latest at diagnosis was 0.82.0. When
`GET {base}/health` returns HTTP 200 and JSON `status` ok, within 3 seconds,
usage comes from that server. A blank URL, a non-http URL, userinfo, a
connection failure, or a non-ok health check uses the snapshot and CLI path.
An HTTP timeout becomes `CollectorTimeout` with no second CLI wait. A
connection loss, a non-200, or bad JSON falls back to the CLI. `http_url` and
`http_token` are known collector keys, not policy keys. `DEFAULT_CONFIG` does
not set a URL. The operator file `~/.config/aiuse/config.toml` (mode 0600)
already has both keys under `[collectors.codexbar]`. The token is
`Authorization: Bearer`. Never print it.

The tokscale ledger stays. The operator accepted the extra time. Snapshot
counts for the learning check use the filename clock
(`%Y-%m-%dT%H%M%S.%fZ`, legacy colon forms, `-1`/`-2` collision suffixes
stripped). `latest.json` is skipped by exact name. Only an untimestamped
`*.json` is opened. `should_learn_from_history` stops after two retained
files. `history_status_line` and `history_insights` still count up to 10,000.
Timestamped invalid JSON counts; the regression test locks that.
`load_recent_snapshots` still parses JSON for the burn-rate and frozen paths.

SQLite is not a watch-start project. Learning still loads about 30 snapshots
and the frozen check loads 24. Those were tenths of a second. Replacing the
one-JSON-file-per-sample contract would be a migration for a cost that
`2d6f501` already removed from watch start.

The release used `just release 3.10.2 --allow-dirty` from the pasture.
`--allow-dirty` skipped the clean-tree check so untracked `opencode.json`
would not block it. That file was not in the version commit. The version
commit adds only `pyproject.toml`, `src/aiuse/__init__.py`, `docs/packaging.md`,
and `uv.lock`. The formula commit is separate.

This handoff is a new chain, parent none. It does not rewrite
`aiuse/main/SESSION_LOG.md`.

## Decisions

- [decision] Keep gifted OpenCode Go off the default matrix and show it on the operator sweep only; rejected: adding it to the default matrix; because the operator said the default omission is on purpose.
- [decision] Read CodexBar over HTTP when /health is ok, and keep CLI fallback otherwise; rejected: marking CodexBar unavailable and querying every plugin; because the menubar app was up, 0.82.0 was already latest, and "all" stays enabled-only discovery.
- [decision] Count timestamped snapshots from the filename and keep the tokscale ledger; rejected: skipping the ledger and moving history to SQLite; because the operator accepted the ledger's extra time and SQLite would not move watch start.
- [decision] Publish 3.10.2 with just release --allow-dirty from the pasture; rejected: committing opencode.json to satisfy the clean-tree check; because that file is local graft wiring.
- [decision] Open chain standalone-82d3 and list the primary checkout as a side workspace; rejected: parenting standalone-132d or rewriting aiuse/main; because that pointer is the herdr-monitor session.
- [ruling] "I should say oc-go is not showing in --all. It not showing by default is on purpose." (djbclark, 2026-10-29)
- [ruling] "Let's keep tokscale, I can deal with an extra 2 seconds. But we should do A" (djbclark, 2026-10-29)
- [ruling] "stop" (djbclark, 2026-10-29)
- [ruling] "do a full release" (djbclark, 2026-10-29)
- [ruling] Chose "Add the note and commit", "Leave the pasture", then "Keep working" on the /loose walk (djbclark, 2026-10-29)

## Relations

- decided_in [[HANDOFF_standalone-82d3_release-3-10-2_2026-10-29_7583]]

## Evidence & Data

Forced source-tree frame **before** `2d6f501`, CodexBar HTTP up, `max_age=0`.
Wall 17.503s. Collectors overlapped and finished at 4.916s (tokscale about
4.841s, CodexBar HTTP about 2.441s, paseo 1.000s, cswap / grok / sipb under
0.2s). Ledger finished at 6.796s. First analyze ran until 12.734s, snapshot
save until 13.278s, second analyze until 17.505s because a credit override or
the shared rate gate re-runs analysis (`watch.py`, the `overrides_applied or
gate_applied` branch). One `analyze_use_or_lose` profile was 4.751s, of which
4.489s was `count_snapshots` twice inside `should_learn_from_history`. The
real learning `load_30` was 0.032s. CodexBar `/health` was 0.029s and HTTP 200. An isolated same-day `tokscale models` run was 6.461s. At diagnosis the
snapshot directory had 5026 files, about 293MB, and the ledger directory had
3395 files, about 7.5MB.

`late_cycle_remaining_summary` and `history_section_lines` still call
`load_recent_snapshots(..., max_count=10_000)`. Those are `--full` and history
insights, not the watch matrix.

The timing paragraph lives in `docs/current-status.md` under "Watch first
frame (2026-10-29)". Behavior lives in `src/aiuse/analysis/history.py`
(`count_snapshots`) and `src/aiuse/collectors/codexbar.py`
(`collect_codexbar`, `_codexbar_http_if_up`).

Scratch scripts `/tmp/aiuse-watch-time.py` and `/tmp/aiuse-3.10.2-notes.md`
were deleted. Other `/tmp/aiuse-*` logs are older and were left alone.

## Operator Feedback

The matrix request was a left-justified `* [TEXT HERE]` when the clock cells
are empty, with the SIPB row as the example. Clock rows keep SCOPE, PACE, and
the clocks. On a note-only gated row the operator-gate text folds inside the
brackets. Clock rows still center the gate cell.

CodexBar was not down. The operator had the menubar icon, had updated the CLI
tools from the GUI, and had enabled the HTTP server. The clipboard held the
server token at configuration time. It was stored in the operator toml and
must not be printed.

"A" was the filename count. The ledger stays. SQLite was asked about and
answered: it would not be a significant extra watch-start speedup. Do not
start that migration.

The first release answer was "1. Cut 3.10.2", then `stop`. The published cut
is the later "do a full release".

## Where We're Going

1. **Nothing is owed.** Leave the product on 3.10.2. If an `aiuse watch`
   process was started before this release, start that process again so it is
   the 3.10.2 binary. Check with `/opt/homebrew/opt/aiuse/bin/aiuse --version`.
   Do not pass `--all-providers`. Do not spend omp.
2. Resume this chain only from
   `~/.local/state/handoffs/chains/standalone-82d3/SESSION_LOG.md`. A bare
   `/baton` in `/Users/djbclark/src/aiuse` follows `aiuse/main`, which is
   `standalone-132d`. Do not rewrite that pointer.
3. Do not re-time the watch frame, start a SQLite migration, remove
   `~/.cow/pastures/aiuse/snapshot-count`, or commit `opencode.json`.
4. Do not print the CodexBar token, put it in a URL, or repeat
   `GET /usage?provider=all`.
5. `late_cycle_remaining_summary` and the `--full` history section still parse
   up to 10,000 snapshot files. That is not the watch-start path.
6. Optional, and only while already editing the status paragraph: `AGENTS.md`
   says 20 registered collectors and `docs/current-status.md` says 21. Verify
   the registry before changing either number.

## Detached jobs

none

This session had no bigteam job record. A scan with an empty
`CLAUDE_CODE_SESSION_ID` matches other sessions' open jobs. Those are not
this handoff's. Do not re-arm them. No `run_in_background` waiter is expected
to survive this session.

## Quick Start

```bash
git -C /Users/djbclark/src/aiuse fetch origin && git -C /Users/djbclark/src/aiuse pull --ff-only
/opt/homebrew/opt/aiuse/bin/aiuse --version
# resume THIS chain, not aiuse/main:
# /baton ~/.local/state/handoffs/chains/standalone-82d3/SESSION_LOG.md
```

Smoke a source-tree change with `AIUSE_SKIP_HOMEBREW_FORMULA=1`. The console
entry re-execs into the Homebrew formula otherwise. Commit from a claimed
pasture with `git-commit-own`, then fast-forward the primary. Push: aiuse
`AGENTS.md` says to push after every commit, and the pre-push hook runs
`just ci`.
