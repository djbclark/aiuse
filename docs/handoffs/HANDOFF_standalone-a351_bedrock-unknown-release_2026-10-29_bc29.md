---
schema_version: 1
handoff_id: bc29
parent_handoff_ids: []
lineage: none
chain: [standalone-a351]
repo: aiuse
workspace: main
branch: main
head_sha: 2f693c6cae2181aa8f2dd4242d3be54eca8ce4e2
created_at: 2026-10-29T16:08:00-0400
writer: grok
---

# Handoff — Bedrock UNKNOWN, release 3.10.5

This session ran in `/Users/djbclark/src/aiuse` on `main`. It does not own
`aiuse/main`. That pointer redirects to
`chains/standalone-132d/SESSION_LOG.md` (herdr monitor layout, operator said
stop on 2026-10-29). Resume this work with an explicit path:

`/baton ~/.local/state/handoffs/chains/standalone-a351/SESSION_LOG.md`

## The Goal

A normal install of `aiuse watch`, including the first run on a machine that
has never run a non-watch `aiuse`, must show a real Bedrock month-to-date
total or an honest unknown. A missing Cost Explorer figure must not be drawn
as `$0.00`. The correction has to be in the Homebrew and PyPI package, not
only in `./aiuse-test.sh`.

## Where We Are

Written against `2f693c6` (`Update Homebrew formula for v3.10.5`). Committing
this file is the next commit on `main`. Dirty: untracked `opencode.json` only.
Do not commit it (local graft MCP wiring;
`docs/memory/reference_graft_wiring.md`).

1. **3.10.5 is published and shows UNKNOWN.** Tag
   [v3.10.5](https://github.com/djbclark/aiuse/releases/tag/v3.10.5). PyPI
   [aiuse 3.10.5](https://pypi.org/project/aiuse/3.10.5/). Publish workflow
   [run 40941852328](https://github.com/djbclark/aiuse/actions/runs/40941852328)
   succeeded. Formula commit `2f693c6`. Tap
   [9bdd660](https://github.com/djbclark/homebrew-aiuse/commit/9bdd660).
   `/opt/homebrew/opt/aiuse/bin/aiuse --version` printed `aiuse 3.10.5`.
   The same binary's `watch --once --no-color -q --interval 86400` at
   3:42pm printed `n/a   -- bedrock       * [UNKNOWN]`.
2. **3.10.4 is the release that drew the false zero.** Tag
   [v3.10.4](https://github.com/djbclark/aiuse/releases/tag/v3.10.4),
   formula `2c61212`. Its board line was
   `n/a   -- bedrock       * [↑ $0.00 PAYG (as of 10/29 3:16pm)]`.
   Leave that tag in place.
3. **An already-open watch is still the old process** until the operator
   starts it again. Do not kill the Herdr pane. Do not pass
   `--all-providers` or press `a`.
4. **Status lines say 3.10.5.** `AGENTS.md`, `docs/current-status.md`, and
   `docs/packaging.md` (`Current published release`). The behavior note is
   `docs/bedrock-quota.md`.
5. **The operator's console total is still not on the board.** One Cost
   Explorer read, already spent, returned no October service costs for the
   account CodexBar is keyed to. Do not run that query again. Do not copy a
   console figure onto the row. The row stays `UNKNOWN` until a positive
   reading exists.
6. **This session is not a watch holder.** Quota guard level was `critical`
   at handoff time (`~/.local/state/ade/quota-guard.json`).

### What 3.10.5 changes

`97c1990` is the display fix. `890dba0` bumps the version. `2f693c6` is the
formula.

- A non-positive Bedrock figure is the n/a note `UNKNOWN`. The score stays
  `--`. It does not use the error band `??`.
- A positive total stays `↑ $X PAYG` plus `format_as_of`. As-of is not
  stamped on `UNKNOWN`.
- `_from_row` in `src/aiuse/collectors/codexbar.py` sets `usage_credits`
  only when `measured_bedrock_spend` is greater than zero. Otherwise
  `error` is `UNKNOWN` and `usage_credits` is omitted. Billing stays
  `payg_api`. `balance_usd` stays null. `spent_usd` is not this meter.
- The 6-hour gate in `src/aiuse/collectors/bedrock_gate.py` is unchanged.
  A zero attempt is still stored as `spent_usd` 0.0 so Cost Explorer is
  not retried inside the window. A later zero does not replace a cached
  positive. The board does not draw that stored zero.
- Watch reuses a snapshot younger than the interval, including on a first
  run, and the display layer turns a stored zero into `UNKNOWN` without
  collecting.

## What We Tried

1. **Error band `??` with the note UNKNOWN.** The operator read it as a
   row that would never work. Rejected.
2. **`dd629b5`, shipped in 3.10.4.** Watch reuse and a measured zero were
   both painted `↑ $0.00 PAYG` with an as-of time. The operator called that
   a lie: the figure is unknown and could be any amount. Reversed by
   `97c1990`.
3. **Waiting for Cost Explorer to catch up.** Rejected. The zero is not a
   delayed reading of a known empty month.
4. **One diagnostic `aws ce get-cost-and-usage`.** October 2026, monthly,
   unblended cost, grouped by service, using the keys CodexBar already
   has. Result: no services with cost, total 0.00, even without the
   Bedrock name filter. Those keys are not seeing the console bill. Do
   not repeat the call.
5. **CodexBar's own parser.** `BedrockUsageStats.swift` `fetchMonthlyCost`
   calls GetCostAndUsage in `us-east-1`, metric UnblendedCost, grouped by
   service, and keeps groups whose name contains "Bedrock". HTTP 400
   `DataUnavailableException` is replaced with empty results, which parse
   as 0.0. That is why aiuse must not display 0. No local CodexBar patch.
   Changes there go upstream as a pull request.

## Key Decisions

Show unknown as the word `UNKNOWN` on the n/a band. Keep a positive total
as pay-as-you-go with the reading time. Keep caching the zero attempt so
the 6-hour gate still holds, and keep a cached positive when a later read
is zero. Bedrock is pay-as-you-go, never prepaid. Do not set
`AccountUsage.spent_usd` for it. OpenCode Go and OpenCode Zen stay
disabled in `~/.config/aiuse/config.toml`. Do not commit `opencode.json`.
Do not run `aiuse watch --all-providers`. Smoke `./aiuse-test.sh watch`
before any other test when the change is on the watch path, then confirm
the Homebrew binary after a release. `.venv/bin` shadows `ai` and `aiuse`
on this Mac; the formula check is
`/opt/homebrew/opt/aiuse/bin/aiuse`.

## Decisions

- [decision] A missing or zero Bedrock total is the n/a note UNKNOWN; rejected: the error band, and rejected: drawing ↑ $0.00 PAYG; because a Cost Explorer zero is not a known empty month; decided_in [[HANDOFF_standalone-a351_bedrock-unknown-release_2026-10-29_bc29]]
- [decision] Cache the zero attempt and keep a later zero from replacing a positive cache; rejected: retrying Cost Explorer on every collect; because each call costs money and the operator capped fresh reads; decided_in [[HANDOFF_standalone-a351_bedrock-unknown-release_2026-10-29_bc29]]
- [decision] Ship the correction as 3.10.5; rejected: leaving the fix only in the source tree; because a clean install has to show it; decided_in [[HANDOFF_standalone-a351_bedrock-unknown-release_2026-10-29_bc29]]
- [ruling] "it should be UNKNOWN if it is not known, not lie and say $0 when it could be $100k for all we know." (djbclark, 2026-10-29)
- [ruling] "I want to be able to, from a clean install, be able to run aiuse watch and get correct information - either that no information is available yet or information, not an error." (djbclark, 2026-10-29)
- [ruling] "No I want aiuse to fix this even for people who do not use it via ai." (djbclark, 2026-10-29)
- [ruling] "bedrock is noot prepaid it is pay as you go" (djbclark, 2026-10-29)
- [ruling] "Please always do a basic test before saying you are done." Test `aiuse watch` before other tests. (djbclark, 2026-10-29)

## Relations

- decided_in [[HANDOFF_standalone-a351_bedrock-unknown-release_2026-10-29_bc29]]

## Evidence & Data

- Behavior: `docs/bedrock-quota.md`. Display helpers `_bedrock_display_spend` and `_api_inventory_note` in `src/aiuse/report.py`. Parse path `_from_row` in `src/aiuse/collectors/codexbar.py`. Gate `gated_bedrock_payload` in `src/aiuse/collectors/bedrock_gate.py` (`MIN_INTERVAL_SECONDS` 21600).
- Tests: `tests/test_bedrock_gate.py`, `tests/test_watch.py` (`test_watch_reuses_unknown_bedrock_snapshot_as_unknown`). Full suite 1453 passed in 20.43s before the fix commit, and again inside `just release` (19.27s).
- Smoke before the commit: `./aiuse-test.sh watch --once --no-color -q --interval 86400` printed `n/a   -- bedrock       * [UNKNOWN]` from the 3:16pm snapshot. After `brew upgrade`, the formula binary printed the same note at 3:42pm.
- Store last seen this session: `~/.local/state/aiuse/bedrock-spend.json` had `spent_usd` 0.0 and `fetched_at` `2026-10-29T18:21:02Z`. The board must not draw that 0.
- CodexBar issue search on 2026-10-29, repo `steipete/CodexBar`, terms `Bedrock DataUnavailable`, `Bedrock cost zero`, and `Bedrock`. No DataUnavailable thread. Nearest: [issue 3387](https://github.com/steipete/CodexBar/issues/3387) (closed, monitoring carries a cost), [issue 4794](https://github.com/steipete/CodexBar/issues/4794) (open, Claude Platform on AWS spend tracking), [issue 1415](https://github.com/steipete/CodexBar/issues/1415) (closed, track Claude usage via Bedrock).

## Operator Feedback

The word on the row is `UNKNOWN`. The band is n/a (`--`), not error
(`??`). A dollar amount appears only after a positive reading, with the
time of that reading. Direction: `↑` counts up (PAYG), `↓` counts down
(plan or prepaid). omp is the monthly Claude API grant and is not part of
this meter. Do not restart completed plan steps. Do not move Herdr panes.

## Where We're Going

1. Leave the shipped UNKNOWN row alone. The optional follow-up is a
   CodexBar upstream issue, then a pull request, so
   `DataUnavailableException` and the service-name filter stop becoming a
   measured 0. Read [issue 3387](https://github.com/steipete/CodexBar/issues/3387)
   and [issue 4794](https://github.com/steipete/CodexBar/issues/4794)
   before filing. Ask the operator before posting. Do not run Cost
   Explorer until the query is known to be the account that has the bill.
   Do not patch only the local CodexBar tree.
2. If the operator's open watch still shows `↑ $0.00 PAYG`, that process
   is 3.10.4. They restart it. Do not kill the pane.
3. Resume this chain with the baton path above. A bare `/baton` in this
   checkout follows `standalone-132d` and its "do not move panes" stop.

## Detached jobs

none

## Quick Start

```bash
git -C /Users/djbclark/src/aiuse status -sb
git -C /Users/djbclark/src/aiuse log --oneline -5
/opt/homebrew/opt/aiuse/bin/aiuse --version
/opt/homebrew/opt/aiuse/bin/aiuse watch --once --no-color -q --interval 86400
```

The watch command reuses a snapshot younger than 86400 seconds. A shorter
interval can collect. Collecting inside six hours of `fetched_at` in
`~/.local/state/aiuse/bedrock-spend.json` must not call Cost Explorer. Do
not pass `--all-providers`. Expect `n/a   -- bedrock       * [UNKNOWN]`
until a positive total exists.
