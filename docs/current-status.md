# Current status (long form)

Moved out of `AGENTS.md` (loaded into every agent session). Update this file,
not `AGENTS.md`, when the status line changes; keep the one-paragraph summary in
`AGENTS.md` in step.

**Status (2026-10-03):** Package/CLI **`aiuse`**. Fix-plan Steps **1–34**
done. Product issues **#1–#9** done. Packaging **3.2.6**
(PyPI/GitHub/Homebrew); **3.0.0** was the first advertised release.
**Schema 1.1 (2026-10-03):** self-describing output — per-window
`state`/`pool_family`, per-account `usable_now`/`binding_window`/`available_at`/
`age_seconds`, top-level `summary_lines`/`semantics`, `aiuse --available
[--live]` (cache-default routing shortlist, exit 3 = nothing usable),
`aiuse note-exhausted` (expiring agent-reported overrides), cache file mirrors
the envelope under `snapshot`, serve default port moved to **28787** with
fail-loudly + `/v1/health` identity. Operator's `aiuse-pools` stopgap can be
replaced by `aiuse --available`. See [`docs/json-contract.md`](docs/json-contract.md)
"How to read this". The
per-release narrative lives in [`docs/handoffs/`](docs/handoffs/); this line
tracks only what a fresh session must know. **18 registered collectors**
(cswap, CodexBar, caut, caam, OpenUsage.ai/.sh, OpenCode Go/Zen, tokscale,
hermes, muse, qwencloud, bailian, openrouter, deepseek, clinepass,
grok_billing, and `acp` — passive context fill from acp-run logs, not plan
quota; `aiuse
usage-sources` lists what is active per vendor and `[usage_sources]` can pin
one source. Native collectors are default-on and quiet when their
prerequisites are absent).
Grok rows carry both meters — the SuperGrok plan window (hourly/daily/weekly/
monthly reset parsed from the billing API's `currentPeriod`, see
[`docs/grok-quota.md`](docs/grok-quota.md)) beside the prepaid wallet — and a
wallet note never suppresses reset cells in the matrix. Account rows in
`aiuse --json` carry **`cli_binary`**: the local CLI that
_spends_ each provider's quota, not the quota-measurement tool a collector
shells out to — see [`docs/json-contract.md`](docs/json-contract.md) and
README's "Which vendor CLI is which". Prepaid/`n/a` band; history learning
`auto`. **Attribution (3.2.0):** the LaunchAgent fires `aiuse sample` every 3
minutes (it collects hourly when idle, every 15 min when a window moved, every
3 min in a burst), each snapshot carries a tokscale token ledger, and
`aiuse attribute` sets quota burned beside tokens per client — see
[`docs/attribution.md`](docs/attribution.md). Normal CLI **always
live-collects** (scheduled snapshots densify History only); `aiuse watch`
reuses a fresh snapshot instead, after re-reading `config.toml`, and skips one
collected under a different disable list or collector switch. Watch keys:
`u` collects now bypassing the cached snapshot; `a` (and
`aiuse watch --all-providers`) runs a one-time sweep of every provider —
`[disabled_services]` and collector disable flags cleared — shown on screen
only: no snapshot, ledger, or sampler-state writes (the cross-process query
throttle still applies). No collect ever overlaps another: manual `u`/`a`
never overlap (an `a` pressed behind a running refresh queues until it
finishes), the scheduled tick waits for a sweep, `u`/`a` share a ratified
2-minute minimum, and a `u` within 90s of an `a` is rolled into that run.
The sweep is **operator-only**; AI agents must not
run it without explicit permission (flagged in `--help`, README, AGENTS.md).
**No mandatory
numbered step.** Open-ended
"what next?" → [`docs/next-options.md`](docs/next-options.md) +
[`docs/handoffs/`](docs/handoffs/) — **do not restart at Step 1**.
