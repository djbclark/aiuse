# Handoff — `[disabled_services]` overall-disable section (2026-10-09)

**Session:** zcode (djbclark), `~/src/aiuse`. Follows
[`HANDOFF_standalone-1731_…_2026-10-04_1731.md`](HANDOFF_standalone-1731_claude-md-symlink-recoveries-load-governor_2026-10-04_1731.md).

## What happened

The operator asked where aiuse still got grok data from (openusage/codexbar no
longer carry it, grok binary gone, `[collectors.grok_billing]` disabled). The
answer: **tokscale** — its cloud service still has the SuperGrok account linked
and reports a live `grok` weekly row that survived every collector-level gate.

That motivated a new config concept, implemented this session:

- **Top-level `[disabled_services]`** (`"provider" = reason | true`) — the
  _overall_ disable, stronger than `analysis.excluded_pools` (routing-only).
  A disabled service: provider-only collectors skip it (no subprocess, no
  authenticated fetch), its rows are dropped from every surface (report,
  chat, TUI, JSON, history, cache), `--available` never lists it (even from
  a cache written before the entry), and the reason travels on the snapshot
  as `disabled_services` + a `semantics` entry. Human full report gets a
  "Disabled by operator" section; chat report a 🚫 header line.
- Touch points: `models.Snapshot.disabled_services`, `config.py`
  (defaults/KNOWN_TOP_LEVEL_KEYS/validate), `collectors/runner.py`
  (`SINGLE_PROVIDER_COLLECTORS` map + ingest filter), `selfdescribe.py`,
  `cli.py:_run_available` (merges disabled into view-layer exclusions),
  `report.py`, `chat_format.py`, `analysis/history.py` (sampler envelope),
  README, `docs/json-contract.md`, `config.example.toml`. Tests:
  runner filtering/skip, config validation, `--available` honours it.

## Operator's live machine (already applied)

1. `~/.config/aiuse/config.toml`: added `[disabled_services]` with
   `"grok" = "operator 2026-10-08: preserve grokbot this week; …"`;
   **removed** `[collectors.grok_billing] enabled=false` (subsumed — the
   runner now skips it because grok is disabled) and the `"grok"` entry from
   `[analysis.excluded_pools]` (subsumed; section left empty with a pointer).
   Backup: `config.toml.bak-2026-10-09`.
2. **pipx venv reinstalled from the repo working tree**
   (`pipx install --force --python 3.14 ~/src/aiuse`) so the LaunchAgent
   (`aiuse sample`, every 180 s) and `aiuse`/`ai` on PATH run the new code.
   It still reports `3.2.9` — the version was not bumped; the next
   `just release X.Y.Z` (operator-gated) should ship this and the pipx
   install should be refreshed from PyPI afterwards
   (`pipx install --force aiuse`).
3. Verified live: `aiuse --json` collect has **zero grok rows / cross-checks**
   and `snapshot.disabled_services` carries the reason; `--available` has no
   grok; `cursor grok_bot` pool still listed (TIGHT 8% left, resets Sat Oct 10) per the standing rule that other vendors' grok-model pools stay.

## Still collector-level disabled (intentionally not moved)

`[collectors.caut] enabled=false` — the reason is the tool (disruptive
Keychain prompts), not the service; that is exactly a collector-level
concern. `[analysis.lapsed_accounts]` is an analysis-layer mark, unrelated.

## Open threads

- If the operator ever wants grokbot itself shelved for a week, that is a
  pool-level routing exclusion: `analysis.excluded_pools`
  `"cursor/grok_bot" = "reason"` (already supported).
- Optional follow-up: `aiuse doctor` could warn when a provider appears in
  `[disabled_services]` but also has an explicit `[collectors.*]` override.
