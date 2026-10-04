---
schema_version: 1
handoff_id: 8b00
parent_handoff_ids: []
lineage: none
chain: [standalone-9480]
repo: aiuse
workspace: aiuse
branch: main
head_sha: d12cdc4ccce7b62156c9832fd8fae0d9e3fed203
created_at: 2026-10-04T08:56:28-0400
writer: grok
---

# Handoff — Muse plan percent and OpenUsage.sh timeout

## The Goal

Show a Muse plan used-percent when Meta actually publishes one, and stop `aiuse watch` from reporting `openusage_sh: timed out after 45.0s` for an export that is still working. Both are on `main`. Do not cut a release unless the operator says `just release X.Y.Z`.

## Where We Are

Code is `d12cdc4` (parent `83386d0`). `head_sha` above is that commit. This file is the only commit after it; if HEAD is one ahead and the diff is only this handoff, the pointer is current. PATH `aiuse` is the pipx install from this checkout and already contains both commits. The version string is still 3.2.6. A running `aiuse watch` keeps the old process until it is quit with `q`.

This login's Muse row still shows pay-as-you-go spend. Meta did not send `subs_usage`. That is recorded in [`docs/muse-quota.md`](../muse-quota.md).

## What We Tried

1. Portal billing-banner, team billing routes, and subscription-quota do not carry this login's plan percent. Extra `/muse-code/*` and `/v1/subscription*` probes were 404 or empty. Stop looking there.
2. A free-tier or monthly dollar cap was not invented. The key payload for this login had `is_subs_active: false` and no `subs_usage`. The portal banner was a subscription upsell. Month-to-date spend was $0.00.
3. Raising the global 45s subprocess budget was rejected. Other collectors still fit that budget. Only `openusage-sh export` has a success path that crosses it (quiet runs about 32s and 34s; watch kills at 45.0s while the other collectors run). The same timeout is already in snapshots back through September.

## Key Decisions

1. Chosen: when `subs_usage` is present, attach the 5-hour and weekly windows and promote a `PAYG_API` row to `SUBSCRIPTION_WINDOW`. Month-to-date spend stays on `usage_credits`. Contract: [`docs/muse-quota.md`](../muse-quota.md). Commit `83386d0`.
2. Rejected: a monthly clock column. Meta bills monthly and publishes the 5-hour window and the week.
3. Chosen: built-in `timeouts.openusage_sh` is 90s. A generic `timeouts.default` does not erase it. An explicit `timeouts.openusage_sh` or `--timeout` still wins. Commit `d12cdc4`. Doc: [`docs/collector-concurrency.md`](../collector-concurrency.md).

## Evidence & Data

1. `just ci`: 773 passed, pre-commit, and `just --check`, once before each commit.
2. Live `load_config()` after the pipx upgrade resolved `openusage_sh` to 90.0 and `cswap` to 45.0. `browser-cookie3` stayed injected.
3. No Beads issues were in progress.

## Operator Feedback

1. Watch cells must stay aligned. The formatting redo already shipped as 3.2.5 and the package is 3.2.6. Do not re-release those.
2. Full PyPI and Homebrew releases only when the operator explicitly says `just release X.Y.Z`.
3. The Muse row should show percent used of the monthly plan, or of the free tier while this login is unpaid. Spent dollars alone are not that number. Do not invent the percent.

## Where We're Going

1. Quit the running `aiuse watch` with `q` and start it again. Confirm the `openusage_sh` timeout row is gone, and that Muse still shows spend until Meta sends `subs_usage`.
2. Do not `just release`. Do not POST `/muse-code/key` again to re-check this login.
3. The operator's next build task is outside this repo: make the existing site-private handoff skills visible to every TUI. Do not add a third handoff format under `docs/`.

## Quick Start

```bash
git -C ~/src/aiuse status -sb
git -C ~/src/aiuse log -2 --oneline
# quit any running `aiuse watch` with q, then:
aiuse watch
```
