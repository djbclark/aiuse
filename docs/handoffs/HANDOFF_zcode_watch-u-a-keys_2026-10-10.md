# Handoff — watch `u`/`a` keys + `--all-providers` (2026-10-10)

**Session:** zcode, `~/src/aiuse`. Follows
[`HANDOFF_grok_acp-usage-sources_2026-10-09.md`](HANDOFF_grok_acp-usage-sources_2026-10-09.md).

## What landed

Watch (operator request, 2026-10-10) gained two one-shot keys and a matching
CLI switch:

1. **`u` — update now.** Fires a refresh immediately instead of waiting out
   the interval, with `max_age=0` so the on-disk snapshot is never reused
   (`_WatchCollectionProcess.start(max_age=0.0)`). Ignored while a collect is
   already in flight. Persists like any watch refresh (the sampler then skips
   its own).
2. **`a` — one-time all-providers sweep.** `all_providers_config()` builds a
   throwaway config (clears `[disabled_services]`, re-enables every
   collector, CodexBar `providers = "all"`); a second
   `_WatchCollectionProcess(persist=False)` collects it beside the regular
   cycle; the board shows the result with a `ALL PROVIDERS · screen only`
   header marker until the next regular frame. **Nothing is recorded** —
   `collect_watch_frame(persist=False)` skips disk reuse, snapshot, ledger,
   and sampler-state writes. The cross-process `QueryGate` / timeout backoff
   still applies (deliberate: the sweep must not hammer a vendor the gate
   protects). Second `a` while one runs is ignored.
3. **`aiuse watch --all-providers`** — the sweep as a one-shot switch
   (implies `--once`), same screen-only semantics.

**Operator-only flag:** the sweep deliberately overrides disable flags that
exist for rate limits / cost / opt-outs, so AI agents must not run it (key or
switch) without specific permission. Flagged in `--help`, the stderr warning
on every `--all-providers` run, README (usage block, flags table, orchestration
blockquote), AGENTS.md Conventions, and here.

Docs: [`docs/watch-mode.md`](../watch-mode.md) (new "Interactive keys"
section), [`docs/current-status.md`](../current-status.md). Completions
(`--all-providers`) added to `completions/aiuse.{bash,zsh}`.

## Tests

`tests/test_watch.py`: `all_providers_config` builder (incl. no-mutation),
`persist=False` screen-only contract (disk read and `save_snapshot` both
`pytest.fail` if touched), render markers, `u` forces `max_age=0` start, `a`
sweep start config + view marker + no-restart-while-running, CLI `--all-providers`
one-shot (captures the swept config, asserts no recording) and `--json`
rejection. Full suite 910 passed; `just ci` green.

## Not done / next

No release cut (3.3.x packaging is operator-triggered via `just release`).
No bead filed (single-session feature, no follow-ups left open).
