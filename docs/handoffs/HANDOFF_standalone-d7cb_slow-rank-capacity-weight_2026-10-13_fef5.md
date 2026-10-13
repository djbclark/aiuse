---
schema_version: 1
handoff_id: fef5
parent_handoff_ids: [d7cb]
lineage: deterministic
chain: [standalone-d7cb, aiuse-b31]
repo: aiuse
workspace: main
branch: main
head_sha: ff569c5b605a4928d619f04bc8841fd76327f6f4
created_at: 2026-10-13T13:30:00-04:00
writer: claude-code
---

# Handoff — slow-band ranking redesign (capacity-weighted) decided; no code written yet

## The Goal

Fix three `aiuse watch` / scoring problems diagnosed in parent handoff `d7cb`
(`HANDOFF_standalone-d7cb_quota-investigation-and-bugs_2026-10-13_d7cb.md`):
missing 5H countdowns, the Codex ghost row, and `agy gemini` scoring `slow 42`.
This session resumed from `d7cb`, corrected one of its claims, and settled the
design for the third problem. **No source file was changed.**

## Where We Are

State: `main` at `ff569c5`, working tree clean (before this handoff commit), no
tests run this session, no detached jobs.

1. **Open bead filed:** `aiuse-b31` (P2), all-bands capacity weighting follow-up.
2. **Items 1 and 2 of `d7cb` (account-drift in `resolve_live_window`, history-alert
   suppression when the live pool is exhausted) are unchanged and still to do.**
   Operator approved the plan in principle; the final go-ahead prompt was
   interrupted by this `/handoff`, so ask once at the start of the next session
   or just proceed (the operator's `/steps` answers already chose every design
   point below).
3. **Item 3 (slow rank) is now designed, see Key Decisions.**

## What We Tried

1. **`d7cb` claim corrected:** it says `slow 42` came from "pace +42%". It does
   not. `_queue_score` (`src/aiuse/report.py:497`) gives a `slow` row
   `25 + round(clamped_readiness * 24 / 100)`, so 42 implies readiness ≈ 71
   (inferred from the formula, not measured). Readiness
   (`_conserve_use_readiness`, `report.py:442`) is
   `100 - lockout_lead_days * 100/7`: it measures how soon the lockout ends, and
   ignores absolute headroom. Gemini weekly resets in ~2 days, so it sorts ahead of
   slow rows with more headroom (OpenCode Go monthly: 8% left, 28 days to reset,
   readiness ≈ 0).
2. **Rejected: move near-empty rows to `empty` (`<2%` cutoff).** Operator: that
   would be a lie, the pool is not empty. (`alert_priority_band`,
   `report.py:409`, currently uses `rem <= 1.0`.)
3. **Rejected: scale readiness by `min(1, remaining%/10)`.** Operator wants the
   number to mean attractiveness (tokens available), not a headroom patch on a
   recovery-speed measure.
4. **Rejected: sort by remaining % alone.** A 60%-left row locked out for a week
   would outrank a 9%-left row that recovers tomorrow, and it ignores plan size.

## Key Decisions

1. **Meaning of the number (operator, 2026-10-13):** "how attractive it is to use
   that pool at the moment, e.g. which service has the most tokens available
   during the current time period."
2. **Capacity proxy = `monthly_price` from `[plans.<provider>]`** (already exists:
   `src/aiuse/config.py` ~L141-198, `config/config.example.toml:162`).
   Usable capacity = `remaining% x monthly_price`. Claude Max ($200) at 5% left
   should outrank Gemini ($20) at 1.2% left.
3. **No plan-tier detection exists:** snapshot `plan` is `None` for claude, codex,
   antigravity, grok and zai (cswap reports no tier). Default to the **lowest tier**;
   the operator overrides in TOML (e.g. `[plans.claude] monthly_price = 200`).
   Also add a per-account override (`[plans.claude."me@x"]`) so two Claude accounts
   on different tiers work. Verify what the built-in claude `monthly_price`
   default is before relying on it.
4. **Scope: slow band now, all bands as a follow-up bead** (`aiuse-b31`). Slow band:
   order by `remaining% x monthly_price`, recovery speed (existing readiness) as
   the tiebreak. Keep `_BAND_EMPTY` threshold at `rem <= 1.0`; update `config.py`
   `DEFAULT_CONFIG` and `config/config.example.toml` together (repo rule).
5. **All-bands redesign** (`aiuse-b31`) needs window-length normalisation (a 5h 50%
   is not a weekly 50%) and will reorder the whole queue; review separately.
6. **Do the work in-session, not via `/bigteam`:** the units are small and entangled
   in `report.py` / `use_or_lose.py`. The operator did not choose between this and
   delegation (the prompt was interrupted); either is acceptable.

## Evidence & Data

1. Latest snapshot `~/.cache/aiuse/snapshots/latest.json`, conserve alerts:
   Gemini weekly 99.92% used (0.08% left, 2.09 d to reset); OpenCode Go monthly
   8.27% left, 28.0 d; Claude Code weekly (djbclark@gmail.com) 5.0% left, 2.71 d;
   Codex weekly (2) 69% left, 6.6 d. (Numbers drift: the Gemini row was 1.22% left at
   the time of `d7cb`.)
2. Relevant code: `alert_priority_band` `report.py:397`, `_conserve_use_readiness`
   `:442`, `_alert_queue_priority` `:457` (conserve key is
   `(readiness, -score, -rem)`), `_queue_score` `:480`, `_build_matrix_rows` `:1239`
   (line 1269 compares conserve candidates).
3. Codex ghost-row evidence is in `d7cb` (account `djbclark@gmail.com` in history
   vs `None` live).

## Operator Feedback

1. Do not put agy in `empty`: it would be false. Its numeric rating seemed
   wrong versus other slow options (confirmed: correct instinct).
2. The score should reflect tokens available now; Claude should rank higher because
   it is the $200 plan, so each percent is worth more tokens. If plan level cannot
   be detected, assume the lowest and allow a TOML override.
3. Slow band now; all bands as a follow-up bead.

## Where We're Going

1. **Next action:** baseline tests, then implement in order, committing and pushing
   each unit (`AGENTS.md` pre-authorises push):
   1.1. `~/ops/site-private/bin/bg .venv/bin/python -m pytest -q` (baseline).
   1.2. `resolve_live_window` account-drift fix (`src/aiuse/analysis/history.py`)
   plus a regression test with the Codex `djbclark@gmail.com` vs `None` shape.
   1.3. Suppress `source="history"` alerts when the live pool's governing window is
   exhausted (`src/aiuse/analysis/use_or_lose.py`).
   1.4. Slow-band capacity ordering (Decisions 2-4) with config + docs +
   `config.example.toml` and a test using the Gemini ($20, 1.2%) vs Claude
   ($200, 5%) shapes.
2. Before each commit: `uv run --extra dev ruff format <files>` and
   `uv run --extra dev mypy <files>`; before pushing `just ci`. Run `graft build`
   after the code changes.
3. Work `aiuse-b31` only when the operator asks; no release unless asked.
4. 5H countdowns (`d7cb` item 1) are mostly upstream (no `resetsAt`, unstarted
   rolling windows); consider labelling "starts on first use" rather than blank.
   Not yet decided with the operator.

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git status -s && bd show aiuse-b31
~/ops/site-private/bin/bg .venv/bin/python -m pytest -q
rg -n "_conserve_use_readiness|_queue_score" src/aiuse/report.py
rg -n "monthly_price" src/aiuse/config.py config/config.example.toml
```
