---
schema_version: 1
handoff_id: d7cb
parent_handoff_ids: [p4c3]
lineage: inferred
chain: [standalone-d7cb]
repo: aiuse
workspace: main
branch: main
head_sha: 2113d39f62cea0f499325ee929f2f969d6d145a2
created_at: 2026-10-13T12:52:00-04:00
writer: antigravity
---

# Handoff — 5H countdowns, Codex ghost availability, and agy slow rating investigation

## The Goal

Investigate and document three user-reported behaviors in `aiuse watch` and priority scoring:

1. Many 5H windows lack countdown times until their next reset.
2. Codex showed availability (`mid 50 codex gmail — 75u/25l`) in `aiuse watch` while the Codex TUI reported a usage limit hit resetting at 1:47 PM.
3. Antigravity (`agy gemini`) is scored `slow 42` despite its governing weekly allotment being at 98.8% used / 1.2% left.

## Where We Are

Root causes identified, verified against snapshot data, and localized in source code:

1. **5H window countdowns:**
   - Rolling/sliding windows at 0% usage (`zai/zcode 0u/100l`, `oc-go 0u/100l`, `agy claude/gpt 0u/100l`) only start their 5-hour clock upon the first consumption. When unused, no reset event is scheduled.
   - Upstream APIs (`antigravity` status in CodexBar, OpenCode Go console `/go/status`) return usage percentages but omit `resetsAt` for short 5-hour meters even when active.
   - History-derived synthetic alert rows (`source == "history"`) compute sample averages and carry `days_until_reset: null`.
2. **Codex ghost availability:**
   - The live Codex row (`empty 0 codex — — 100u/0l/1h21m <- EXHAUSTED`) was completely accurate: 12:26:10 PM snapshot + 1h21m = exactly 1:47 PM reset.
   - The second row (`mid 50 codex gmail — 75u/25l`) was an orphaned history alert. Earlier snapshots recorded Codex under `account = "djbclark@gmail.com"`, while subsequent snapshots recorded `account = None`.
   - In `src/aiuse/analysis/use_or_lose.py`, `resolve_live_window` keyed on `(series_key, account)` failed to find the live account because of the `djbclark@gmail.com` vs `None` mismatch. It therefore failed to check `live_rem <= 0.0` (which would have suppressed the history alert).
   - In `src/aiuse/report.py`, uncovered alerts create a synthetic row via `_row_from_alert`, producing the phantom `mid 50 codex gmail` row.
3. **`agy gemini` `slow 42` rating:**
   - `Gemini weekly` has 1.22% remaining (98.78% used).
   - Under `aiuse`'s threshold definition, `exhausted` strictly requires `<= 1.0%` left. Because 1.22% > 1.0%, the window is classified as `tight`, not `exhausted`.
   - It raises a conserve alert in `_BAND_CONSERVE` (`slow`), with pace +42% yielding score 42.
   - In reality, 1.2% headroom in a weekly budget cannot sustain agent activity, but the scoring system currently treats it as usable in `slow` rather than `empty`.

## What We Tried

- Queried local snapshots (`~/.cache/aiuse/snapshots/2026-10-13T162610.548592Z.json` and `latest.json`) to trace exact raw accounts, windows, and alert payloads.
- Inspected CodexBar widget cache (`widget-snapshot.json`) to confirm upstream structure for `antigravity`, `zai`, and `codex`.
- Ran `aiuse --available --json` and verified that self-describing routing correctly identifies `Gemini weekly` as the binding window with 1.22% headroom.

## Key Decisions

1. **Fuzzy account resolution for single-account providers in history:** `resolve_live_window` in `src/aiuse/analysis/history.py` should match `None` to a named account (and vice-versa) when a provider has only one active account in the snapshot.
2. **Global provider exhaustion suppression:** If any governing live window for a provider/pool is exhausted (or near 0%), all historical burn/waste alerts for that provider/pool must be suppressed, even across account key drift.
3. **Tight threshold calibration:** In `src/aiuse/analysis/selfdescribe.py` and `use_or_lose.py`, re-evaluate whether the cutoff for `exhausted` should be `< 2.0%` or whether windows with `< 2.0%` remaining and high positive pace should be placed in `empty` rather than `slow`.

## Evidence & Data

- Snapshot `2026-10-13T162610.548592Z.json`:
  - `codex` live: `account=None`, `Codex 5-hour quota (1)` used=100.0%, resets at `17:47:40Z` (1:47 PM EDT).
  - `codex` alert: `account="djbclark@gmail.com"`, `source="history"`, `remaining_percent=25.0`, `used_percent=75.0`, `days_until_reset=null`.
  - `antigravity` live: `Gemini 5-hour` used=37.8%, rem=62.2%, resets_at=null; `Gemini weekly` used=98.78%, rem=1.22%, resets_at=`2026-10-15T19:09:49Z`.
- Source code locations:
  - `src/aiuse/report.py`: `_clock_plain`, `_format_reset_span`, `_build_matrix_rows`, `_row_from_alert`.
  - `src/aiuse/analysis/use_or_lose.py`: `analyze_use_or_lose` (chronic waste history loop).
  - `src/aiuse/analysis/history.py`: `resolve_live_window`, `chronic_waste_summary`.
  - `src/aiuse/analysis/selfdescribe.py`: `available_pools`, `window_state`.

## Operator Feedback

- 12:28 PM: Inquired why many 5H windows lack countdown times.
- 12:28 PM: Inquired why Codex showed availability while Codex TUI reported usage limit hit until 1:47 PM.
- 12:39 PM: Noted another agent pointed out `agy` has no room due to 1% weekly limit.
- 12:49 PM: Questioned whether `slow 42` for `agy` is too high, and requested `/handoff`.

## Where We're Going

1. **Fix account drift matching in `resolve_live_window`** (`src/aiuse/analysis/history.py`):
   When matching `(series_key, account)`, if exact match fails, check if there is an unambiguous live window matching `series_key` under `None` or single-account provider.
2. **Prevent history alerts from creating ghost rows when live pool is exhausted** (`src/aiuse/analysis/use_or_lose.py`):
   Check if the pool's governing window is depleted machine-wide before generating `source="history"` alerts.
3. **Calibrate near-empty threshold for `_BAND_EMPTY`** (`src/aiuse/report.py` / `use_or_lose.py`):
   Consider treating `< 2%` remaining as depleted or gating `_BAND_CONSERVE` so near-zero weekly quotas do not present as viable `slow` alternatives.
4. **Run test suite and quality gates**:
   `.venv/bin/python -m pytest -q` and `just ci`.

## Detached jobs

none

## Quick Start

- Inspect latest snapshot accounts & windows:
  `.venv/bin/python -c "import json; d=json.load(open('/Users/djbclark/.cache/aiuse/snapshots/latest.json')); print([(a['provider'], a.get('account'), [w['label'] for w in a.get('windows', [])]) for a in d['accounts']])"`
- Run tests:
  `.venv/bin/python -m pytest -q`
