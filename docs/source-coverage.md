# Live source coverage

`aiuse` deliberately keeps every successful live reading for cross-checking,
then selects one source for the priority ladder. A provider having two source
names does **not** necessarily mean two independent upstream authorities:
tools can share a browser session, an OAuth endpoint, or a billing API.

## Current local audit (2026-08-02)

This was collected with `aiuse -q --json` and the snapshot's cross-checks,
without recording account names or credentials. Availability remains
machine- and login-dependent. Refreshed from the 2026-07-30 audit below
while triaging issues #16-#18 (source expansion); see
[`next-options.md`](next-options.md) and [`handoff.md`](handoff.md).

| Service            | Successful local client sources                | Interpretation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------ | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Claude Code        | cswap, OpenUsage.ai, tokscale                  | Multiple live measurements; cswap remains the multi-account authority.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Codex              | CodexBar, OpenUsage.ai, OpenUsage.sh, tokscale | Four agreeing live client sources.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| GitHub Copilot     | CodexBar, OpenUsage.ai, tokscale               | Multiple live measurements; tokscale remains the selection priority.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Cursor             | CodexBar, OpenUsage.ai                         | Down from 3 to 2, confirmed persistent (not a single-poll fluke) — diagnosed root cause: OpenUsage.sh's telemetry daemon serves a stale cached `UNKNOWN` for `cursor` even after a Homebrew upgrade (0.24.0→0.24.1) and a daemon restart, while a one-shot `openusage-sh export --source direct` succeeds immediately with correct live data. Filed upstream: [janekbaraniewski/openusage#293](https://github.com/janekbaraniewski/openusage/issues/293). No `aiuse`-side code change — Cursor's real quota tracking runs through CodexBar (already authoritative here) with OpenUsage.ai corroborating on-demand; losing OpenUsage.sh's third cross-check reading doesn't change any alert or verdict. Revisit only if upstream ships a fix and we want the extra corroboration back. |
| Grok               | CodexBar, OpenUsage.ai, tokscale               | **Changed from 0 to 3** since 2026-07-30 — CodexBar's earlier fetch error is gone (now succeeds via `grok-web`); all three agree (16% used). Likely resolves issue #18 with no `aiuse`-side code — verify live again, then consider closing rather than implementing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Google Antigravity | CodexBar, OpenUsage.ai                         | Two live client sources.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| OpenCode Go        | CodexBar, OpenUsage.ai                         | Two readings, but billing-web data and local estimates may disagree; prefer CodexBar web data.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| OpenCode Zen       | native `opencode_zen`                          | Down from 2 to 1 this run — CodexBar did not surface a Zen cross-check this poll. Single-poll observation, not confirmed as a persistent change.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| DeepSeek prepaid   | CodexBar only                                  | Unchanged — one successful client source. Issue #16's premise still holds.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| OpenRouter prepaid | CodexBar only                                  | Unchanged — one successful client source. Issue #17's premise still holds.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |

## Practical implication

**Cursor's missing third source is a diagnosed upstream bug, not an `aiuse`
gap.** OpenUsage.sh's telemetry daemon (not its direct-poll path) serves a
stale, day-old `UNKNOWN` snapshot for `cursor` regardless of binary version
or daemon restart — filed as
[janekbaraniewski/openusage#293](https://github.com/janekbaraniewski/openusage/issues/293).
No action needed here: Cursor's burn/conserve scoring is unaffected since
CodexBar (already authoritative) and OpenUsage.ai both still work.

DeepSeek and OpenRouter remain the only prepaid services with one client
source on this machine — issues #16 and #17 are still real, unblocked work.
**Groq is the one that changed materially**: it went from a hard CodexBar
fetch error (zero usable sources — the entire premise of issue #18) to three
agreeing live sources in this audit. Before spending #18's estimated 8-20h
building new client sources, re-verify with a second live run and check
whether this was a genuine upstream fix (CodexBar or xAI-side) or a
transient recovery — then likely close #18 as already resolved rather than
implementing anything. DeepSeek/OpenRouter prepaid balances are marked `n/a`
and never drive use-it-or-lose-it ranking, so their single-source status
does not change burn recommendations.

## Previous audit (2026-07-30)

| Service            | Successful local client sources                | Interpretation                                                                                                 |
| ------------------ | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Claude Code        | cswap, OpenUsage.ai, tokscale                  | Multiple live measurements; cswap remains the multi-account authority.                                         |
| Codex              | CodexBar, OpenUsage.ai, OpenUsage.sh, tokscale | Four agreeing live client sources.                                                                             |
| GitHub Copilot     | CodexBar, OpenUsage.ai, tokscale               | Multiple live measurements; tokscale remains the selection priority.                                           |
| Cursor             | CodexBar, OpenUsage.ai, OpenUsage.sh           | Multiple readings; the on-demand metric may differ by tool.                                                    |
| Grok               | CodexBar, OpenUsage.ai, tokscale               | Multiple live measurements.                                                                                    |
| Google Antigravity | CodexBar, OpenUsage.ai                         | Two live client sources.                                                                                       |
| OpenCode Go        | CodexBar, OpenUsage.ai                         | Two readings, but billing-web data and local estimates may disagree; prefer CodexBar web data.                 |
| OpenCode Zen       | CodexBar, native `opencode_zen`                | Two independent client implementations of the **same** OpenCode billing service, not two upstream authorities. |
| DeepSeek prepaid   | CodexBar only                                  | One successful client source.                                                                                  |
| OpenRouter prepaid | CodexBar only                                  | One successful client source.                                                                                  |
| Groq               | none (CodexBar fetch error)                    | No usable live reading in this audit.                                                                          |

For a fresh audit, run:

```bash
aiuse -q --json > /tmp/aiuse.json
```

Look at `snapshot.cross_checks`: an `unavailable` check names a service with
only one successful source in that run. The complete JSON shape is documented
in [`json-contract.md`](json-contract.md).
