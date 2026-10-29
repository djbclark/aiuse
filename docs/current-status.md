# Current status (long form)

Moved out of `AGENTS.md` (loaded into every agent session). Update this file,
not `AGENTS.md`, when the status line changes; keep the one-paragraph summary in
`AGENTS.md` in step.

**Status (2026-10-29):** Package/CLI **`aiuse`**. Fix-plan Steps **1–34**
done. Product issues **#1–#9** done. Packaging **3.10.4**
(PyPI/GitHub/Homebrew); **3.0.0** was the first advertised release.
**3.9.4:** the omp rate gate is a durable fail-closed latch — evidence
persisted in `~/.local/state/aiuse/rate-gates.json` (exhausted shared
window, or Anthropic 429 retry-after parsed from omp's own logs), gating
until its own reset regardless of missing/stale Claude readings;
`aiuse gate status|clear`; the watch `a` sweep leaves deprecated collectors
(caut, caam, OpenUsage) at their configured state
([`omp-cloud-credits.md`](docs/omp-cloud-credits.md)).
**Schema 1.1 (2026-10-03):** self-describing output — per-window
`state`/`pool_family`, per-account `usable_now`/`binding_window`/`available_at`/
`age_seconds`, top-level `summary_lines`/`semantics`, `aiuse --available
[--live]` (cache-default routing shortlist, exit 3 = nothing usable),
`aiuse note-exhausted` (expiring agent-reported overrides), cache file mirrors
the envelope under `snapshot`, serve default port moved to **28787** with
fail-loudly + `/v1/health` identity. Operator's `aiuse-pools` stopgap can be
replaced by `aiuse --available`. See [`docs/json-contract.md`](docs/json-contract.md)
"How to read this".
**Schema 1.2 (2026-10-12):** per-window pace (`pace_percent`, `pace_ratio`), per-account `overall_pace_percent`, PACE column replaces `$ UNUSED` in clock matrix with cell-level signed pace percentages and lined-up notes; pace in `summary_lines` and `aiuse --available`. Early readings (under 10% of the window elapsed) are marked `~` (JSON `pace_early`, `overall_pace_early`; released 3.4.1).
The
per-release narrative lives in [`docs/handoffs/`](docs/handoffs/); this line
tracks only what a fresh session must know. **21 registered collectors**
(cswap, CodexBar, caut, caam, OpenUsage.ai/.sh, OpenCode Go/Zen, tokscale,
hermes, muse, qwencloud, bailian, openrouter, deepseek, clinepass,
grok_billing, `paseo` — the Paseo daemon's cached usage report, mapped for claude/codex/copilot/grok/antigravity, quiet when the binary is absent — and `acp` — passive context fill from acp-run logs, not plan
quota; `aiuse
usage-sources` lists what is active per vendor and `[usage_sources]` can pin
one source. Native collectors are default-on and quiet when their
prerequisites are absent).
**Deprecated 2026-10-22 for overlap with cswap, CodexBar, tokscale, and
paseo:** caut, caam, OpenUsage.ai, and OpenUsage.sh. The code stays; the
maintainer is happy to re-add support on request
([`collectors-caut-openusage.md`](collectors-caut-openusage.md)).
**CodexBar HTTP (2026-10-29):** `collectors.codexbar.http_url` and
`http_token`. A healthy `/health` sends usage queries to that server, bearer
header only, and skips the widget snapshot. A down server falls back to the
snapshot and the CLI. The token stays in the operator config, never in git.
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
`aiuse watch --all-providers`) opens the live board on a screen-only sweep of every provider —
`[disabled_services]` and collector disable flags cleared — shown on screen
only: no snapshot, ledger, or sampler-state writes (the cross-process query
throttle still applies). No collect ever overlaps another: manual `u`/`a`
never overlap (an `a` pressed behind a running refresh queues until it
finishes), the scheduled tick waits for a sweep, `u`/`a` share a ratified
2-minute minimum, and a `u` within 90s of an `a` is rolled into that run.
The sweep is **operator-only**; AI agents must not
run it without explicit permission (flagged in `--help`, README, AGENTS.md).
**Watch first frame (2026-10-29):** a forced source-tree frame measured
before `2d6f501` took 17.5s. Collectors finished together at about 4.9s
(tokscale about 4.8s; CodexBar HTTP about 2.4s). The tokscale ledger
overlapped them and finished at about 6.8s; that ledger stays. About 9s of
that frame was `count_snapshots` JSON-parsing the snapshot directory, twice
per `analyze_use_or_lose`, and the frame analyzes twice when a credit
override or the shared rate gate applies. `2d6f501` counts timestamped names
and stops the auto learning check after two of them. A frame after that
commit was not re-timed. The inferred floor is the ledger, about 7s. SQLite
would not move that start: learning still loads about 30 snapshots (0.032s
in the same profile) and the frozen check loads 24.
`late_cycle_remaining_summary` and the `--full` history section still parse
up to 10,000 files.
**omp (2026-10-15, released in 3.7.0):** Claude cloud-session credits
(an expiring dollar grant, spent by the omp TUI through the Claude API) are a
separate provider `omp`, derived from CodexBar's Claude row. The matrix has a
new last column **OTHER** (omp `$left`, `Fable-cap`, wallets) and PACE
now follows SCOPE; JSON carries `spent_usd`, `requires_operator_approval` and
`ai_notice` (schema stays 1.3). AI agents must not use omp without asking the
operator. See [`docs/omp-cloud-credits.md`](omp-cloud-credits.md).
**No mandatory
numbered step.** Open-ended
"what next?" → [`docs/next-options.md`](docs/next-options.md) +
[`docs/handoffs/`](docs/handoffs/) — **do not restart at Step 1**.
