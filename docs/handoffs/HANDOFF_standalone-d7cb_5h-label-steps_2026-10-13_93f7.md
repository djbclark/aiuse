---
schema_version: 1
handoff_id: 93f7
parent_handoff_ids: [fef5]
lineage: deterministic
chain: [standalone-d7cb, aiuse-b31, aiuse-qm7]
repo: aiuse
workspace: main
branch: main
head_sha: 9723cc198ecaa0aee7d9703c9bb192fb6f7d1fd7
created_at: 2026-10-13T13:38:50-04:00
writer: claude-code
---

# Handoff — fef5 plan landed; mid-`/steps` on the 5H "starts on first use" label

## The Goal

Resume of `fef5` (`HANDOFF_standalone-d7cb_slow-rank-capacity-weight_2026-10-13_fef5.md`):
fix the Codex ghost row and capacity-weight the slow band, then walk the
remaining open items with the operator via `/steps`.

## Where We Are

State: `main` at `9723cc1`, pushed, tree clean (before this handoff commit).
Full suite **1007 passed**, `just ci` green on the combined tree. `graft build`
run. Beads synced (`bd dolt push`).

Landed this session (both via Sonnet/high Agent-tool sub-agents, reviewed by the
Opus lead):

1. `210a9ae` fix(history): `resolve_live_window` falls back to the series' sole
   live entry only when that entry is anonymous (Codex email -> `None` drift). A
   named record never borrows a differently named live account.
2. `d0dd1a0` fix(use-or-lose): history alerts dropped when the live window is
   `<= 1.0%` left (was `<= 0.0`; new `_EXHAUSTED_REMAINING_PERCENT`), and when the
   account fails to resolve but every live window of the same
   `provider:pool:duration` series is spent (`_series_exhausted`). Bead
   `aiuse-uzk` closed.
3. `9723cc1` feat(report): slow band ordered by `remaining% x monthly_price`
   (`_slow_capacity`), log-scaled into 25-49 (clamps 10..10000), readiness as
   tiebreak; `plan_monthly_price(plans, provider, account)` in `config.py` with
   `[plans.<p>."<account>"] monthly_price` override; unpriced provider = cheapest
   configured price; README, `config/config.example.toml`,
   `docs/shared-quota-semantics/formulas/pace.md` updated; new
   `tests/test_slow_capacity_rank.py`. One existing expectation changed
   (`test_action_score_has_contiguous_cross_band_boundaries`). Bead `aiuse-faf`
   closed.
4. Operator config (not in repo): appended `[plans.claude] monthly_price = 200`
   to `~/.config/aiuse/config.toml` (backup `config.toml.bak-2026-10-13-plans`);
   verified merge keeps name/notes/value_multiplier and resolves to 200.

## What We Tried

1. Verifying the ghost fix on the live board (`aiuse watch --once`) still showed
   `mid 50 codex/gmail 75u/25l`. Not a bug: watch reuses the stored alerts of
   `latest.json` (written 17:15Z, before the fix). Re-running
   `analyze_use_or_lose` on that snapshot with the new code yields only the
   Codex weekly conserve alert. The row clears at the next collect.

## Key Decisions

1. Unit A conservatism (sub-agent DECIDED, lead accepted): no named->named
   fallback; suppression on unresolved accounts requires ALL live accounts of the
   series spent.
2. Unit B (accepted): log scale for slow score; `config=None` uses
   `DEFAULT_CONFIG["plans"]`; within one pool the least-capacity conserve window
   governs.
3. **`/steps` item 1a (operator, 2026-10-13): unused rolling 5H windows get the
   full window length plus a marker, e.g. `0u/100l/5h<M>`, with a footer legend
   `<M> clock starts on first use`.** Rejected: word label `idle` (wider, no
   length), leaving it blank. Operator note: the row marker and the footer must
   use the **same** marker, an **unusual character**, and **he wants to be given
   choices** for it.

## Evidence & Data

1. Board after the fix (cached render): `slow 41 claude 0u/100l/4h 95u/5l/2d`
   above `slow 25 oc-go 0u/100l 83u/17l/5d 92u/8l/28d`; agy/gem now `empty`
   (`<1l` weekly).
2. Unused-window rows that would get the marker: `zai/zcode 0u/100l`,
   `oc-go 0u/100l`, `agy/c/gpt 0u/100l`. Cell format docstring:
   `render_clock_matrix` `src/aiuse/report.py` (~L1635); rows built in
   `_build_matrix_rows`.
3. Case 1b example: `agy/gem 45u/55l` (window in use, CodexBar antigravity /
   OpenCode Go `/go/status` send no `resetsAt`).

## Operator Feedback

1. Wants the 5H label item explained in detail and walked before the two beads.
2. Marker: same glyph in rows and footer, unusual, offer choices.
3. For this session only: all work in Claude + Claude sub-agents (API key), Opus
   lead, cheaper models for coding. Not to be documented as a rule.

## Where We're Going

1. **Next action: resume the `/steps` walk at item 1a's marker choice.** Ask one
   AskUserQuestion with candidate glyphs (check terminal width: prefer East Asian
   Width "N", single cell). Candidates: `↻` (U+21BB, N, "rolling, starts on
   use"), `∘` (U+2218, N), `‡` (U+2021, ambiguous width), `◌` (U+25CC, dotted
   circle "not started", ambiguous width). Then implement via a Sonnet sub-agent:
   marker on unused rolling windows (used 0, no `resets_at`, known duration) in
   the matrix cell, one footer legend line only when at least one marker is
   shown, tests; check `--json` is unaffected (screen-only) unless operator says
   otherwise; `just ci`, push.
2. `/steps` 1b: windows in use with no upstream reset (`agy/gem 45u/55l`).
   Options to offer: leave blank (honest unknown) recommended-or-not after
   checking sampler history density; estimate `~Nh` from first sample with
   used>0 + 5h (risky if samples were missed); file upstream issues/PRs for
   `resetsAt` (CodexBar antigravity, OpenCode Go) per the AGENTS.md upstream rule.
3. `/steps` item 2: `aiuse-b31` all-bands capacity weighting (needs window-length
   normalisation; reorders the whole queue) — operator go-ahead required.
4. `/steps` item 3: `aiuse-qm7` (P3) use `plan_monthly_price` per-account price in
   `use_or_lose._plan_meta` and `report._window_value_usd`.
5. Minor, cosmetic, not filed: when a named history alert resolves to an
   anonymous live window that is not exhausted, the row keeps the old account
   label ("gmail") (`chronic_waste_summary`: `live_entry.get("account") or
data["account"]`).
6. No release unless the operator asks (`just release X.Y.Z`).

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git status -s && git log --oneline -4
bd show aiuse-b31 aiuse-qm7
graft ask "render_clock_matrix cell format countdown" --source
~/ops/site-private/bin/bg .venv/bin/python -m pytest -q   # expect 1007 passed
```
