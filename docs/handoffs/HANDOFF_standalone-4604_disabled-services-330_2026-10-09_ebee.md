---
schema_version: 1
handoff_id: ebee
parent_handoff_ids: []
lineage: none
chain: [standalone-4604]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 9065365388ef9ebeaf2cff6e70afef9154e81b4a
created_at: 2026-10-08T21:52:12-0400
writer: zcode
---

# Handoff — [disabled_services] overall disable, cursor "grok" rename, 3.3.0

Supersedes `HANDOFF_zcode-f31d_disabled-services-grok_2026-10-09_0110.md`
(same session, written mid-flight before the rename and release — its two open
threads are now resolved/refuted, see Key Decisions).

## The Goal

The operator wanted services that must temporarily not be used — even with
headroom — to be disableable _overall_, not just hidden from routing: grok
specifically, because the SuperGrok vendor quota is reserved for grokbot (the
xAI Grok Bot app), which delegated AI work must not drain. Plus: the term
"grokbot" must not appear in aiuse code — only in a comment in his personal
config — and Cursor's grok model pool is meant to be used until empty.

## Where We Are

Everything shipped and released as **3.3.0** (PyPI + Homebrew + pipx, all
verified). Live machine configured. One upstream PR open. No mandatory next
step in this repo.

## What We Tried

1. Collector-level disable (`[collectors.grok_billing] enabled=false`) —
   insufficient: tokscale's cloud still had SuperGrok linked and reported a
   live grok weekly row through a path no collector gate touched. This is what
   started the session.
2. `analysis.excluded_pools` — routing-only by design (a540f15): the full
   report kept showing grok as usable. Not wrong, just the wrong layer.
3. My first mental model of "grokbot" was wrong (treated it as Cursor's
   grok_bot pool). Operator corrected 2026-10-08: GrokBot is the xAI app; the
   SuperGrok disable exists to preserve quota FOR it; Cursor's grok pool is
   unrelated and fine to burn. The correction triggered the grok_bot→grok
   rename (3d319b9).

## Key Decisions

1. **Ingest-time filtering** for `[disabled_services]`: provider rows are
   dropped right after collection (every surface at once — report, chat, TUI,
   JSON, history, cache), plus provider-only collectors are skipped outright
   (`SINGLE_PROVIDER_COLLECTORS`, now in config.py). Rejected: presentation-
   layer-only marking (rows would still reach history/cache); collector-gating
   only (multi-provider collectors like tokscale would still report).
2. **View-layer defense too**: `--available` merges disabled_services into
   its exclusions, so a cache written before the entry (older build) still
   never routes there.
3. **Reason travels on the snapshot** (`snapshot.disabled_services` +
   semantics entry + report/chat sections) so absence is self-explaining.
4. **Naming (operator rule)**: cursor pool family is `grok`, never
   `grok_bot`; the term grokbot lives only in a comment in
   `~/.config/aiuse/config.toml`. REFUTED thread from the f31d handoff:
   do NOT add `cursor/grok` to excluded_pools — the operator wants that pool
   used until empty (TIGHT is informational; nothing in aiuse blocks routing).
5. **/steps outcomes (2026-10-09)**: file upstream CodexBar PR (done, #4383);
   release 3.3.0 now (done); add doctor drift warning (done, e1eca4b — warns
   when a provider-only collector override of a disabled provider has no
   effect; only non-default overrides flagged).
6. Live config: `[disabled_services] grok` with operator-worded reason;
   grok_billing + excluded_pools grok entries REMOVED (subsumed); caut stays
   collector-disabled (tool-level reason). Backup:
   `~/.config/aiuse/config.toml.bak-2026-10-09`.

## Evidence & Data

1. Commits: d7a3data (feature), 3d319b9 (rename), e1eca4b (doctor warning),
   9098f1f+655ca34 (release 3.3.0), 9065365 (docs+PR link). 0e2db66 is a
   Cursor session's handoff doc, not mine.
2. Release verified: tag v3.3.0 pushed, PyPI page live, Homebrew formula
   updated + `brew test` passed, `aiuse --version` → 3.3.0.
3. Tests: 823 passed; `just ci` green (after two prettier/ruff-format
   rounds — markdown tables and my long lines needed reflow).
4. Live behavior verified: fresh collect has zero grok rows/cross-checks;
   `aiuse --available` shows `cursor grok … TIGHT (8% left, resets Sat Oct
10)` and no vendor grok; cache carries the disabled_services reason.
5. Upstream PR: https://github.com/steipete/CodexBar/pull/4383 — renames
   `CursorSandUsageStatus.extraWindowTitle` "Grok Bot" → "Grok" (window id
   unchanged). Operator-supplied framing used verbatim-ish: canonically
   correct but confusing, since xAI's Grok Bot app cannot spend Cursor's
   "Grok Bot" tokens at all.
6. Nuance: pipx's aiuse 3.3.0 was built from the LOCAL repo spec (the release
   script's own `pipx upgrade` flow), not the PyPI wheel — same content, but
   a later `pipx upgrade` rebuilds from the working tree.

## Operator Feedback

1. "grokbot isn't a separate pool; it's just some the vendor xai grok can do…
   shouldn't be referenced from aiuse code, only in a comment in our personal
   config file."
2. "It is fine to continue using vendor cursor model grok even though it is
   low; that one we want to use until it is empty."
3. PR framing: concede "Grok Bot" may be canonically correct, but it's hella
   confusing given xAI's Grok Bot app can't use Cursor's "Grok Bot" tokens.

## Where We're Going

1. **THE next action (passive):** watch steipete/CodexBar#4383 — when merged
   and a CodexBar release ships, update the app; the window label in aiuse
   output then reads "Grok" end-to-end. Drop the "until it lands" note in
   `docs/cursor-quota.md` at that point.
2. Optional only: if the operator ever unlinks grok from his Tokscale account,
   nothing in aiuse needs to change (the row is filtered anyway).
3. Not parked, decided against: shelve cursor grok (refuted, see Key
   Decisions 4).

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git log --oneline -3            # expect 9065365 on top, clean tree
aiuse --available -q | grep -E 'grok|cursor'      # cursor grok listed, no vendor grok
aiuse --json -q 2>/dev/null | jq '.snapshot.disabled_services'
gh pr view 4383 --repo steipete/CodexBar --json state,title
.venv/bin/python -m pytest -q                     # via ~/ops/site-private/bin/bg
```
