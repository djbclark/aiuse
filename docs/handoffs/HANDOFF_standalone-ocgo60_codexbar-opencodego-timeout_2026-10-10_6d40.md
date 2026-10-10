---
schema_version: 1
handoff_id: 6d40
parent_handoff_ids: []
lineage: none
chain: [standalone-ocgo60]
repo: aiuse
workspace: main
branch: main
head_sha: 647f00553ee4e06d90a8108e5da0d4d236bfc917
created_at: 2026-10-10T09:32:27-0400
writer: grok
---

# Handoff — CodexBar OpenCode Go 60s, web only

`standalone-5441` (caam / Homebrew / Gemini, same morning) is a different
chain. It is not a parent. Do not resume that work from this file.

## The Goal

Raise the CodexBar OpenCode Go collector budget from 45s to 60s only if
CodexBar actually returns inside 60s, and prefer a cached CodexBar reading
over a forced rescan.

## Where We Are

The code and the docs are on `main` and pushed. The installed binary is not.

1. `1fc335d` — CodexBar provider `opencodego` uses `--source web` only, with
   a 60s budget. A non-timeout web miss returns no row. A timeout still
   feeds hang backoff and does not start a second scan.
2. `647f005` — the agent map, the README exception list, the concurrency
   cold-slot row, and the OpenCode Go verify command match that behavior.
3. `pyproject.toml` `version` is still `3.3.8`. PATH `aiuse` is
   `/opt/homebrew/Cellar/aiuse/3.3.8/libexec/bin/aiuse`
   (`~/.local/bin/aiuse` realpaths there) and prints `aiuse 3.3.8`. The
   sampler keeps the old 45s auto path until a release.
4. Working tree clean, `main` even with `origin/main`, at the SHA above
   before this handoff commit.
5. Upstream report, no pull request:
   <https://github.com/steipete/CodexBar/issues/4410>
6. Tier 1 log: `~/.local/state/handoffs/chains/standalone-ocgo60/SESSION_LOG.md`.

Mechanism, already in the repo: [`docs/opencode-go-quota.md`](../opencode-go-quota.md)
and the CodexBar bullet in [`docs/collector-concurrency.md`](../collector-concurrency.md).

## What We Tried

1. `--source local` on CodexBar 0.73.0. Rechecked 2026-10-10T13:22:25Z.
   Exit 1, `Error: --source must be auto|web|cli|oauth|api.` Local is an
   internal auto strategy, not a CLI source. The verify command in
   `docs/opencode-go-quota.md` used that flag. `647f005` removed it.
2. A CodexBar usage-result cache. There isn't one. `--refresh` is a
   `codexbar cost` flag. The Keychain cookie cache is not a usage snapshot.
   With no cookie and no API key, auto returns the local dollar-cap estimate
   and does not continue to web. Tag `v0.73.0`,
   `OpenCodeGoProviderDescriptor.resolveStrategies` and
   `OpenCodeGoLocalUsageFetchStrategy`.
3. Raising the global CodexBar timeout. Rejected. `docs/collector-concurrency.md`
   records `alibabatokenplan` still hanging past 60s on 2026-10-09.
4. Treating a quiet 6.25s auto run as proof the killed scan fits in 60s
   under sampler load. The 12:50Z kill was the auto argv during collection.
   The quiet retry finished in 6.25s. The duration under the 16-way collect
   was not remeasured. The shipped code no longer calls auto, so that gap
   stays open on purpose.
5. `just ci` on the first form of `1fc335d`: 939 pytest passed, then mypy
   failed because `provider_arg.lower()` was not narrowed. The fix binds
   `provider` first. The commit hook's full pre-commit, including mypy,
   passed. Full pytest was not re-run after that type-only line.
6. Filing against an existing CodexBar thread. This turn's `gh search` on
   `steipete/CodexBar` found no issues for `opencodego local sqlite` (open),
   `opencode local scan timeout` (closed), or `opencodego timeout` (open).
   Nearest merged PRs were #3796 and #2583, a different bug. The operator
   then asked for a new issue. That is #4410.

## Key Decisions

1. Chosen: `provider_timeouts.opencodego = 60`, and `_NO_AUTO_FALLBACK`
   contains `opencodego`. Antigravity still falls through from oauth to auto.
   Rejected: a global timeout bump, and keeping the auto fallback so a web
   miss would still show a row. The fallback is the rescan, and its estimate
   can show monthly headroom when the console is empty.
2. A web timeout still returns `CollectorTimeout` so hang backoff applies.
   A non-timeout miss returns `[]`, so a missing cookie does not become a
   standing CodexBar failure warning.
3. Native `opencode_go` timeout stays 45s. That collector is the
   authoritative console read and finished inside its window today.
4. Operator, this session: do not release. Homebrew stays 3.3.8.
5. Operator: fix the four stale doc lines. Done in `647f005`. README line
   304 stays "default 45s" because that global default is still true.
6. Operator: file the CodexBar issue, not a pull request. Done: #4410.
7. Operator: `graft build`. The local card
   `graft/tests/test_codexbar_parse.md` now names
   `test_opencodego_web_miss_does_not_rescan_local`. `.gitignore` line 32
   ignores `/graft/`, and `git ls-files graft` is empty, so there was
   nothing to commit. Do not hand-edit graft cards.

## Evidence & Data

1. CodexBar 0.73.0 at `/opt/homebrew/bin/codexbar`. `--web-timeout` default
   is 60s.
2. `--source web`, no browser cookie: 0.71s, rc 1, no OpenCode session
   cookies in browsers. A cookie-backed web fetch was never timed.
3. Auto, quiet retry of the killed argv: 6.25s, rc 0, `source` `local`,
   `dataConfidence` `estimated`, monthly about 0.4% used, shorter windows 0.
4. Native console the same day: plan `go`, 5-hour 0% used / 100% left,
   weekly about 96.41% used, monthly 100% used / 0% left.
   `used_percent` is consumed.
5. `~/.local/share/opencode/opencode.db` was 946MB, mtime 2026-10-10 00:30
   local. Tag `v0.73.0` `OpenCodeGoLocalUsageReader` caps are
   `$12` / `$30` / `$60` and the scan `json_extract`s message and part rows.
   The local checkout `~/src/CodexBar` is `v0.68.0-15-g77417776d`, behind
   origin, and dirty. That dirt is not this session's. Do not commit it.
6. Throttle file `~/.cache/aiuse/query-throttle/codexbar-timeouts.json`
   still records `opencodego` timeout 45.0 at `2026-10-10T12:50:05.812271Z`,
   count 1. The 1800s skip ended `13:20:05Z`. Do not delete the file.
   Deleting it would query immediately. The next 3.3.8 sample can run the
   old auto rescan again.
7. Probe temp files `/tmp/codexbar-opencodego-timing.json` and the
   `--source local` capture were removed.

## Operator Feedback

1. Measure before changing the timeout. If 60s is not enough, find out why.
   Cached or old CodexBar data was preferred over a forced rescan.
2. Do not run `aiuse watch --all-providers`. Do not refresh caam or Gemini.
   Do not cut a release unless asked. `just release` is the only release path.
3. On the decision walk: leave Homebrew 3.3.8, fix the four doc lines, file
   the CodexBar issue, refresh graft, then write this handoff.

## Where We're Going

1. Do not release and do not re-open the timeout. PATH stays Homebrew 3.3.8
   until the operator says `just release` in `/Users/djbclark/src/aiuse`.
   The script bumps `pyproject.toml` from 3.3.8. The tree to release is
   `1fc335d` plus `647f005` plus this handoff. After a release, the next
   CodexBar OpenCode Go query is
   `codexbar usage --provider opencodego --source web` with a 60s budget
   and no SQLite fallback.
2. Leave <https://github.com/steipete/CodexBar/issues/4410> as the upstream
   report. No CodexBar pull request unless the operator asks. Do not patch
   `~/src/CodexBar`.
3. Leave `~/.cache/aiuse/query-throttle/codexbar-timeouts.json` in place.
4. `graft/` is a local cache on this repo despite the graft block in
   `AGENTS.md` talking about git sync. The card is already refreshed. Do
   not hand-edit it and do not try to commit it.
5. Two bigteam job files have an empty `owner` and no `closed` key, so a
   query with an empty session id lists them. They are not this session:
   `~/.local/state/bigteam/caam-aiuse/jobs/collector.json` (`.done` exists)
   and `~/.local/state/bigteam/sipb-collector/jobs/sipb-impl.json` (no
   `.done` at 09:32 EDT). Do not take them over.
6. Do not run `aiuse watch --all-providers`. Do not re-enable opencode-zen.

## Detached jobs

none

## Quick Start

```bash
git -C /Users/djbclark/src/aiuse status -sb
git -C /Users/djbclark/src/aiuse rev-parse HEAD
aiuse --version
# expect: aiuse 3.3.8 until the operator says just release
python3 -c 'import json,pathlib; p=pathlib.Path.home()/".cache/aiuse/query-throttle/codexbar-timeouts.json"; print(json.loads(p.read_text()).get("opencodego"))'
```

Tests this session: targeted pytest 97 passed
(`test_codexbar_parse`, `test_codexbar_timeouts`, `test_query_throttle`,
`test_runner_consolidation`) before the mypy fix. `just ci` then reported
939 passed and mypy failed. After the type-only narrow, the commit hook's
pre-commit passed and full pytest was not re-run. The doc commit ran
prettier and markdownlint only.

Files in `1fc335d`: `src/aiuse/collectors/codexbar.py`, `src/aiuse/config.py`,
`config/config.example.toml`, `docs/collector-concurrency.md`,
`docs/opencode-go-quota.md`, `tests/test_codexbar_parse.py`,
`tests/test_codexbar_timeouts.py`.

Files in `647f005`: `README.md`, `docs/agent-doc-map.md`,
`docs/collector-concurrency.md`, `docs/opencode-go-quota.md`.
