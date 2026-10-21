# Handoff: omp rate gate + agy gemini-timeout-t takeover (2026-10-21)

Resume point for the work taken over from the agy session in herdr tab
`gemini-timeout-t` (Antigravity transcript
`~/.gemini/antigravity-cli/brain/0104e95c-4308-4269-a3fe-5de81548d2d5/`),
plus the operator's follow-up ask ("the omp/claude api seems to be rate
and/or time window limited so we need to update aiuse to deal with that").
Everything here is committed and pushed; bead **aiuse-osz** tracks it.

## What landed

1. **`ded288e` — stale-window hygiene** (the agy session's implementation,
   finished here): cswap hydration past reset / past its 30-minute trust
   window is dropped or exhausted-only; `_drop_reset_passed_windows` in
   `runner.py` expires cached windows whose reset passed (cswap outage →
   not-available line, never wrong numbers — operator rule 2026-10-21);
   `--providers` runs no longer overwrite `latest.json` with a partial
   snapshot; `aiuse watch` re-execs into the new binary after a
   `brew upgrade` instead of freezing on unlinked Cellar code;
   `anthropic_cost.py` reconciles the Admin API cost report against the
   operator's console override (pre/post-override bucket split), which is
   why the omp row now shows the **current** grant ($200 of 2026-10-13,
   expires 2026-11-08) instead of the dead $250/Nov-5 one.
2. **`8373bd5` — omp rate gate (schema 1.4)**: when every observed Claude
   account's shared weekly/monthly window is exhausted with a future reset,
   omp accounts carry `rate_gated_by`, `usable_now: false`,
   `available_at` = the gate reset, a note, and `--available` omits omp.
   Positive evidence only (missing Claude data / 5h windows / Fable
   sublimits never gate; self-clearing at reset). Evidence: 2026-10-21
   429s whose retry-after equaled the weekly reset while credits remained.
   Docs: `json-contract.md` 1.4 section, `omp-cloud-credits.md` rate-window
   gate section, AGENTS/README schema bumps, AGENTS omp-gate bullet marked
   lifted per 8a9dc43.

Validation: full suite green (1335 passed), ruff format + mypy + prettier
clean, live smoke: omp row gated until Fri Oct 23 6:00am with $89.56 left.

## Still open / next

- **cswap re-login is still the operator's blocker** (browser OAuth): log in
  with Claude Code, then `cswap add`. Until then Claude windows come from
  CodexBar reading cswap's cache; the new expiry logic keeps that honest.
- `--for-chat` shows a `↳ 🚫 Rate-gated …` line under omp (wired via
  `apply_shared_rate_gate_to_snapshot` in cli/watch); the **watch board's
  matrix cells** still do not render the gate (like the old operator-gate
  cell text) — follow-up polish if wanted.
- Schema 1.4 shipped without a release; next `just release` should be 3.9.0
  (minor: new field) when the operator asks.
- The agy tab `gemini-timeout-t` can be closed; its work is fully landed.
