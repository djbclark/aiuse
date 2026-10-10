# Handoff — watch `u`/`a` keys + `--all-providers` (2026-10-10)

**Session:** zcode, `~/src/aiuse`. Follows
[`HANDOFF_grok_acp-usage-sources_2026-10-09.md`](HANDOFF_grok_acp-usage-sources_2026-10-09.md).

## Same-day refinement: manual-command throttling

After the keys landed, the operator tightened them (impatience must not
reach the vendors), enforced in `run_watch`'s `_gate_manual()`:

1. One manual command (`u`/`a`) in flight at a time, across kinds (`a` waits
   for a running `u` and vice versa). A _scheduled_ tick is not a manual
   command; the sweep may still run beside one, as before.
2. A `u` within 90s of an `a` (running or just finished) is **rolled into**
   the sweep — denied with a board note (`rolled into … (42s in)` /
   `covered by … (8s ago)`). Past 90s with the sweep still running it still
   waits.
3. `u` and `a` share one firing per **2 minutes** (anchored at actual start);
   a press inside the window gets `ready in 87s (2min minimum)`.

Denied presses explain themselves on the board for ~10s (`key_note` header
bit) instead of silently doing nothing. Constants `_MANUAL_COOLDOWN_S`,
`_SWEEP_ROLL_IN_S`, `_KEY_NOTE_S` in `watch.py`. Tests: 7 new cases in
`tests/test_watch.py` (roll-in, 90s boundary via injected clock, shared
cooldown, a-waits-for-u, throttle, cooldown expiry). Interpretation taken:
"either more than once every 2 minutes" = **shared** budget across both keys
(both poll every vendor); if the operator meant per-key timers, the anchor in
`_gate_manual` is the single place to change.

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
