---
schema_version: 1
handoff_id: 876f
parent_handoff_ids: []
lineage: none
chain: [standalone-39fc]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 43472908179fb83283996a2d2046589dca8b78f4
created_at: 2026-10-21T13:32:00-0400
writer: grok
---

# Handoff — Paseo collector and omp matrix gate

## The Goal

Finish the herdr tab `Thu-4pm-Continue-t` (Paseo daemon as a second usage
source) and make the clock matrix stop offering omp while Claude's shared
weekly window is exhausted. The operator pasted a live 429
(`retry-after-ms=147469000`, about 41 hours). Then hand off while this
Grok pool still has headroom.

## Where We Are

The task is done. `main` matches `origin/main`. GitHub `test` on
[4347290](https://github.com/djbclark/aiuse/commit/4347290) is green:
[run 39643477126](https://github.com/djbclark/aiuse/actions/runs/39643477126)
(2m7s). Installed `aiuse` on PATH is still **3.9.0** and still draws the
old omp row. No release was cut.

Working tree, verified `git status -sb` at write time:

- `M .codex/config.toml` — local graft MCP client. Not this work.
- `?? opencode.json` — same, for OpenCode. Not this work.

Earlier notes, both already on main, are the narrative record. This file
is the resume point:

- [HANDOFF_paseo-and-omp-matrix-gate_2026-10-21.md](HANDOFF_paseo-and-omp-matrix-gate_2026-10-21.md)
- [HANDOFF_zcode-omp-rate-gate-takeover_2026-10-21.md](HANDOFF_zcode-omp-rate-gate-takeover_2026-10-21.md)

`head_sha` above is the CI-fix commit. The commit that adds this file
moves HEAD; trust `git rev-parse HEAD` over the frontmatter.

## What We Tried

1. "The rate gate is not firing" was wrong. `--json` / `--available`
   already omitted omp. `render_clock_matrix` drew the credit-expiry
   clock (~48% left, ~17 days) as band `mid` score 50. That is the cell
   that got tried and then 429'd.
2. Adding `"paseo"` to `KNOWN_COLLECTOR_KEYS` with a three-line
   `tokscale` / `hermes` / `muse` hunk matched twice. The other hit is
   `KNOWN_TIMEOUT_KEYS` (around line 370). Only the collector frozenset
   (after `"acp"`) gained `"paseo"`.
3. A bare `.venv/bin/python -m pytest` on this Mac is refused by
   `~/ops/site-private/bin/bg` (full suites run on GitHub Actions). The
   first push, [89d7791](https://github.com/djbclark/aiuse/commit/89d7791),
   failed [run 39642539062](https://github.com/djbclark/aiuse/actions/runs/39642539062)
   on four tests: unknown collector `paseo`, partial sampling left it
   enabled, and a missing binary raised `CollectorError` on the runner.
4. A hermes-ping went out before that red run finished. A second ping
   went out after 4347290 was green. Do not send a third about the same
   CI result.

## Key Decisions

1. A rate-gated omp row is `empty`, score 0, clocks cleared, note
   `rate-gated until <reset> · $<left> left`, and `burn_before_reset` is
   suppressed. The dollars stay visible. Hiding the row was rejected
   because the grant is still real. `requires_operator_approval` stays
   off (lifted 2026-10-19, commit 8a9dc43).
2. A missing `paseo` binary returns `[]`, same policy as qwencloud.
   Daemon-down (binary present, CLI exits 1) still records a collector
   error. Runner tests stub `collect_paseo` because this Mac has the
   binary and CI does not.
3. Paseo maps only `claude`, `codex`, `copilot`, `grok`, `antigravity`.
   `cursor`, `omp`, `opencode`, `pi`, and `muse` stay skipped. The dead
   tab's last edit (`muse-nope` → `muse` in the skip list) is the
   intended one.
4. No `just release`. `docs/current-status.md` still describes 3.7.1;
   `AGENTS.md` is the live status. Left that file alone.
5. Tier 1 pointer `~/.local/state/handoffs/aiuse/main/SESSION_LOG.md`
   redirected at `chains/aiuse-52v/` (updated 2026-10-17, active work
   closed, `head_sha` `97df531`). This handoff takes that pointer so
   `/baton` in this checkout resumes here. That chain's work was already
   closed.

## Evidence & Data

1. Commits on `main`: [e1705d2](https://github.com/djbclark/aiuse/commit/e1705d2)
   paseo collector, [c65e51a](https://github.com/djbclark/aiuse/commit/c65e51a)
   matrix gate, [89d7791](https://github.com/djbclark/aiuse/commit/89d7791)
   first handoff note, [4347290](https://github.com/djbclark/aiuse/commit/4347290)
   known-collector key plus a quiet missing binary.
2. Focused pytest before the CI fix: `tests/test_paseo.py`,
   `tests/test_attribution.py`, `tests/test_runner_consolidation.py`,
   `tests/test_codexbar_parse.py::test_generate_config_includes_collectors_section`,
   `tests/test_config.py` — 121 passed in 2.06s. Full suite is the green
   GitHub run above, not a local `pytest`.
3. `aiuse --version` on PATH (no skip) printed `aiuse 3.9.0` at
   2026-10-21 13:27 EDT.
4. `AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse --available --json` at
   `generated_at` 2026-10-21T17:27:33Z (age ~171s, fresh): available
   providers were codex, cursor (`auto` and `third_party`), grok, and
   grok-bot. `excluded` length 0. omp was absent.
5. Grok pool for this session: `used_percent` 91, `remaining_percent` 9,
   resets `2026-10-22T06:41:20Z` (02:41 EDT). grok-bot weekly:
   `remaining_percent` 74.95, resets `2026-10-24T21:57:39Z`.
6. The 429's retry-after lands on the Claude weekly reset Friday
   23 October 2026 6:00am EDT (`2026-10-23T10:00:00Z`). Admin-api credit
   reading at the earlier check: **$95.57 of $200**, observed
   `2026-10-21T16:24:41Z`, grant expires 2026-11-08. The matrix marks
   that reading `STALE`. Do not spend omp before the weekly reset.
7. Smoke the source tree with `AIUSE_SKIP_HOMEBREW_FORMULA=1`. The
   console entry re-execs into the Homebrew formula without it.

## Operator Feedback

1. Continue `Thu-4pm-Continue-t`. It died on a z.ai 5-hour limit after
   changing the paseo skip parametrize to include `muse`.
2. omp looked usable in the matrix and then returned the 429 above.
3. Keep track of this session's own quota and hand off before the pool
   runs out. That reading is item 5 under Evidence.

## Where We're Going

1. Nothing is owed on the Paseo collector or the omp matrix gate. Read
   this file, confirm `git -C /Users/djbclark/src/aiuse rev-parse HEAD`
   and `git status -sb`, then stop unless the operator names a new task.
   Open-ended "what next?" is
   [docs/next-options.md](../next-options.md). Do not restart fix-plan
   Steps 1–34.
2. Before another model batch, run
   `AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse --available --json`
   from `/Users/djbclark/src/aiuse` and read `remaining_percent`. At the
   reading above, the Grok pool had 9% left until
   `2026-10-22T06:41:20Z`. omp stays out of `--available` until Friday
   23 October 2026 6:00am EDT.
3. A release happens only when the operator asks, and only through
   `just release X.Y.Z`. Until then, PATH `aiuse` is 3.9.0.
4. cswap re-login is still the operator's browser step, carried from
   [HANDOFF_zcode-omp-rate-gate-takeover_2026-10-21.md](HANDOFF_zcode-omp-rate-gate-takeover_2026-10-21.md).
5. Leave `.codex/config.toml` and `opencode.json` unstaged.
6. Optional, not the default: `docs/current-status.md` still says
   packaging 3.7.1. `AGENTS.md` is the status that loads.

## Detached jobs

none

## Quick Start

```bash
cd /Users/djbclark/src/aiuse
git fetch origin && git pull --ff-only
git log --oneline -6
git status -sb
AIUSE_SKIP_HOMEBREW_FORMULA=1 .venv/bin/aiuse --available --json
```

Resume narrative:
[HANDOFF_paseo-and-omp-matrix-gate_2026-10-21.md](HANDOFF_paseo-and-omp-matrix-gate_2026-10-21.md).
Collector contract: [docs/paseo.md](../paseo.md). Gate contract:
[docs/omp-cloud-credits.md](../omp-cloud-credits.md).
