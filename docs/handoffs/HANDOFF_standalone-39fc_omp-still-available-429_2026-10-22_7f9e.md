---
schema_version: 1
handoff_id: 7f9e
parent_handoff_ids: ["876f"]
lineage: inferred
chain: [standalone-39fc]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 21e82c97b7480724143b7bd8ebf3022d547fb4a8
created_at: 2026-10-22T09:44:09-0400
writer: grok
---

# Handoff — omp still looks available while pi gets a weekly 429

## The Goal

Find why omp still looks spendable after the schema 1.4 shared-window gate, while a pi session on Haiku 4.5 is rejected by Anthropic until the Claude weekly reset. Do not spend omp. Do not cut another release unless the operator asks.

## Where We Are

Published aiuse is **3.9.2** (tag `v3.9.2` at `22ed6a1`, formula `6e8daef`, tap `~/src/homebrew-aiuse` `dc36a4f`). `origin/main` is clean at `21e82c9` (`docs: record published aiuse 3.9.2`). Cellar `/opt/homebrew/opt/aiuse/bin/aiuse` prints `aiuse 3.9.2`. PyPI: https://pypi.org/project/aiuse/3.9.2/ . GitHub: https://github.com/djbclark/aiuse/releases/tag/v3.9.2 .

The gate from parent `876f` is in this release (`c65e51a`). At 2026-10-22T13:42:51Z, `aiuse --available --json` (cache, age 406.7s, fresh) does **not** list omp. `excluded` length is 0. Available providers are zai, grok-bot, grok, codex, and cursor pools `auto` and `third_party`.

The snapshot those figures came from (`~/.cache/aiuse/snapshots/latest.json`, `collected_at` 2026-10-22T13:36:04Z) has **no omp account** and `collector_errors` `[]`. Providers present: antigravity, claude, codex, copilot, cursor, grok, grok-bot, opencode-go, sipb, zai. Claude (cswap) weekly is `100.0` used, `remaining_percent` 0, reset `2026-10-23T09:59:59Z`. Claude 5-hour is `0.0` used. `usable_now` on that Claude account is false.

An earlier checkout smoke the same morning (stdout was `/tmp/aiuse-smoke.json`, since deleted) did include omp: source `admin-api`, credits about `52.215` used / `47.785` left, `rate_gated_by` the Claude Code weekly reset `2026-10-23T10:00:00Z`. So the row existed, was gated, and then disappeared from the snapshot without an error.

The operator's pi TUI, just before this handoff, is not honoring that gate. Footer: `π`, Haiku 4.5, `~/src/plektra-djbclark`, branch `plk/main`, `(sub)`, context about 10%. Prompt was "are you working now?". Anthropic returned:

```text
429 rate_limit_error
request_id req_011CgHNHrvtsXdX14fpxmZsF
retry-after-ms=73284000
```

`73284000` ms is 20.357 hours. From 2026-10-22T13:42Z that lands on about 2026-10-23T10:04Z, the same Claude weekly reset. pi then stopped because `retry.maxDelayMs` is 300000.

## What We Tried

1. Treating cswap or the omp admin-api reading as bad data. Rejected the same day: cswap's Claude weekly `100%` used matches the 429's retry-after. The admin-api credit balance can be nonzero while the shared window is exhausted. That is the gate's whole point. See parent [876f](HANDOFF_standalone-39fc_paseo-omp-gate_2026-10-21_876f.md).
2. Blaming OpenUsage for the omp 429. OpenUsage was the bad Antigravity card (Gemini/Argon omitted). It is uninstalled and deprecated. It is not the omp source. Existing upstream thread, already searched, do not comment and do not open a duplicate: https://github.com/robinebers/openusage/issues/1422 . Closed unmerged PR: https://github.com/robinebers/openusage/pull/1423 .
3. Committing the dead session's dirty tree. `.codex/config.toml` added `[mcp_servers.graft]`, untracked `opencode.json` was the same MCP registration, and `AGENTS.md` had the blank line under `<!-- graft:start -->` removed. Graft has been CLI-only since 2026-10-18 (`docs/memory/reference_graft_wiring.md`). Prettier rejects `AGENTS.md` without that blank line. All three were dropped. The worktree was clean before this handoff.

## Key Decisions

1. caut, caam, OpenUsage.ai, and OpenUsage.sh stay deprecated for overlap. Collector code stays in tree and still runs if the binary is present and the collector is enabled. hermes and acp stay `enabled = false` in `~/.config/aiuse/config.toml` because the built-in default is on. sipb stays enabled with `[timeouts] sipb = 5`.
2. Safe to stop OpenUsage meant no correct board figure was supplied only by OpenUsage. That bar was met. Loss is the second opinion. Two checkouts remain on purpose: `~/src/openusage` and `~/src/janekbaraniewski_openusage`.
3. Full release 3.9.2 was an explicit operator request after `/loose` and a green test run. It overrides the earlier "leave the SIPB/hidden-pool fix unreleased" answer. Next release still only via `just release X.Y.Z` when the operator asks. Do not use `--skip-tests`.
4. A rate-gated omp row is not a spend target. `--available` already omits it. The operator still sees some surface as available. That surface is the open bug. Do not recommend burning omp before `2026-10-23T10:00:00Z`.
5. The stray graft MCP files stay out of git. Tracked `.mcp.json` still names a graft server with no `alwaysLoad`. Leave it; Claude is supposed to ignore project mcp json. Do not run `graft init --agents`.

## Evidence & Data

1. Git: `main` `21e82c97b7480724143b7bd8ebf3022d547fb4a8`, `## main...origin/main`, clean except this handoff once it is added. Release commits: `641502a` docs, `e4f4194` install-deps tests, `e69d9ca` justfile comment, `22ed6a1` version bump, `6e8daef` formula. Status lines in `AGENTS.md` and `docs/current-status.md` say packaging 3.9.2 as of 2026-10-22.
2. Tests this session: local pytest 1357 passed (53.53s, and again 53.68s inside `just release`). Targeted `tests/test_install_deps.py`: 3 passed. GitHub test runs green after the install-deps fix: https://github.com/djbclark/aiuse/actions/runs/39783310138 , https://github.com/djbclark/aiuse/actions/runs/39783891212 , https://github.com/djbclark/aiuse/actions/runs/39784567641 , https://github.com/djbclark/aiuse/actions/runs/39785134529 . Publish: https://github.com/djbclark/aiuse/actions/runs/39784602253 . The runs that failed before the test fix were https://github.com/djbclark/aiuse/actions/runs/39782686596 and https://github.com/djbclark/aiuse/actions/runs/39780874663 (two `tests/test_install_deps.py` cases still required OpenUsage.sh). `test.yml` for docs commit `21e82c9` was not watched.
3. Gate code, from `graft ask` (tokens saved ≈ 87,986): `apply_shared_rate_gate_to_snapshot` in `src/aiuse/analysis/selfdescribe.py` around lines 461–530. It returns false immediately when the snapshot has no omp account. `render_clock_matrix` is `src/aiuse/report.py` around lines 2025–2044. Contract: [docs/omp-cloud-credits.md](../omp-cloud-credits.md). Parent evidence that `--available` omitted omp on 2026-10-21T17:27:33Z is in [876f](HANDOFF_standalone-39fc_paseo-omp-gate_2026-10-21_876f.md).
4. Command that showed the current omission (cache, rc 0, stderr empty):

```bash
cd /Users/djbclark/src/aiuse
AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse --available --json -q
```

`generated_at` 2026-10-22T13:42:51Z, `source` `cache`, `fresh` true. omp is absent from `available`.

5. `~/ops/site-djbclark` was already pushed at `db7dce2` (OpenUsage dropped from the quota install) and was clean. Its Tier 1 pointer belongs to chain `aiuse-1qo`. Do not rewrite that pointer.
6. Homebrew formula caveats in `packaging/homebrew/aiuse.rb` still name caut, OpenUsage.ai, and OpenUsage.sh. Changing the caveat text is a formula commit, not part of this bug.
7. Files this session changed and pushed, besides the release script's own version and formula commits: `AGENTS.md`, `docs/current-status.md`, `docs/caam.md`, `docs/scheduling.md`, `docs/collectors-caut-openusage.md`, `docs/companion-stack.md`, `README.md`, `justfile`, `tests/test_install_deps.py`. Live configs (not git): `~/.config/aiuse/config.toml` disables caut, caam, hermes, acp, openusage_ai, openusage_sh. `~/.claude/settings.json` and `~/.codex/config.toml` no longer call OpenUsage hooks.

## Operator Feedback

1. omp is still showing as available, and the pi TUI in `~/src/plektra-djbclark` answered "are you working now?" with the 429 above. That is the next task.
2. Spell "deprecated". The maintainer will restore caut, caam, or either OpenUsage on request.
3. Do not run `aiuse watch --all-providers`. Do not kill the running watch. An earlier pid was 84222 in herdr workspace `w2F`, tab `aiuse-watch-t`, pane `w2F:p2R`. Confirm with `ps -p 84222 -o pid,lstart,command`. Read it only with `herdr pane read w2F:p2R --source visible` if the pane is still that watch. The watch was not restarted after the 3.9.2 upgrade.
4. Numbered lists. Do not commit another session's files. The three stray files from the dead session were judged and removed; do not put the graft MCP entries back.

## Where We're Going

1. Identify the surface that still shows omp as available, then make that surface agree with the 429. Start from the 13:36 snapshot having no omp account and no collector error, while `--available` also omits omp, while pi on Haiku 4.5 still tried the account and got `retry-after-ms=73284000`. Read `apply_shared_rate_gate_to_snapshot` (`src/aiuse/analysis/selfdescribe.py`, the early `if not omp_rows: return False`) and the watch pane above. Compare a fresh `AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse --json -q` omp account (`rate_gated_by`, `usable_now`, windows) with whatever the operator is looking at. Do not spend omp. Do not restart or kill the watch unless the operator asks. A live collect needs this machine's credentials; `~/ops/site-private/bin/bg` will refuse it (exit 3). Run it directly and write stdout, stderr, and `rc` to files.
2. Leftovers that are not this bug. Two OpenUsage git checkouts under `~/src/`. Empty dataless iCloud placeholder `~/Library/Mobile Documents/iCloud~com~robinebers~openusage~dev` (delete was Permission denied; do not sudo). Dated [docs/source-coverage.md](../source-coverage.md) and the README carry-forward parenthetical were left as history. Formula caveats still list the deprecated tools.
3. cswap re-login remains the operator's browser step, carried from [HANDOFF_zcode-omp-rate-gate-takeover_2026-10-21.md](HANDOFF_zcode-omp-rate-gate-takeover_2026-10-21.md).
4. Open-ended work after the omp display bug is [docs/next-options.md](../next-options.md). Do not restart fix-plan Steps 1–34.

## Detached jobs

none

This session did not own the job records under `~/.local/state/bigteam/`. `CLAUDE_CODE_SESSION_ID` was unset here, so a filter of `owner == ""` lists other sessions' jobs. Do not re-arm them from this handoff.

## Quick Start

```bash
cd /Users/djbclark/src/aiuse
git fetch origin && git pull --ff-only
git log --oneline -6
git status -sb
AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse --available --json -q
ps -p 84222 -o pid,lstart,command
```

Parent: [HANDOFF_standalone-39fc_paseo-omp-gate_2026-10-21_876f.md](HANDOFF_standalone-39fc_paseo-omp-gate_2026-10-21_876f.md). Gate contract: [docs/omp-cloud-credits.md](../omp-cloud-credits.md).
