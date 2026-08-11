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
| Grok               | CodexBar, OpenUsage.ai, tokscale               | **Changed from 0 to 3** since 2026-07-30 — confirmed on two independent runs, not a fluke. Issue [#18](https://github.com/djbclark/aiuse/issues/18) closed as already resolved upstream, no `aiuse`-side code needed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Google Antigravity | CodexBar, OpenUsage.ai                         | Two live client sources.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| OpenCode Go        | CodexBar, OpenUsage.ai                         | Two readings, but billing-web data and local estimates may disagree; prefer CodexBar web data.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| OpenCode Zen       | native `opencode_zen`                          | Down from 2 to 1, confirmed persistent and diagnosed — root cause found in CodexBar's own Swift source (`OpenCodeGoUsageFetcher.swift`): a 250ms race (`optionalZenBalanceJoinGrace`) between the OpenCode Go subscription fetch and the Zen balance fetch silently drops Zen's data whenever the Zen request is the slower of the two, which is common under normal network conditions. Filed upstream: [steipete/CodexBar#2581](https://github.com/steipete/CodexBar/issues/2581). No `aiuse`-side code change — the native `opencode_zen` collector remains the authoritative source for this service regardless.                                                                                                                                                                   |
| DeepSeek prepaid   | CodexBar only                                  | Unchanged — one successful client source. Issue #16's premise still holds.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| OpenRouter prepaid | CodexBar, native `openrouter`                  | Two successful client sources. Issue #17 resolved.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

## Practical implication

**Cursor's missing third source is a diagnosed upstream bug, not an `aiuse`
gap.** OpenUsage.sh's telemetry daemon (not its direct-poll path) serves a
stale, day-old `UNKNOWN` snapshot for `cursor` regardless of binary version
or daemon restart — filed as
[janekbaraniewski/openusage#293](https://github.com/janekbaraniewski/openusage/issues/293).
No action needed here: Cursor's burn/conserve scoring is unaffected since
CodexBar (already authoritative) and OpenUsage.ai both still work.

**OpenCode Zen's missing second source is also a diagnosed upstream bug.**
CodexBar's own Go-subscription-vs-Zen-balance fetch race silently drops Zen
data under normal network timing — filed as
[steipete/CodexBar#2581](https://github.com/steipete/CodexBar/issues/2581).
No action needed: the native `opencode_zen` collector remains authoritative.

DeepSeek remains the only prepaid service with one client
source on this machine — issue #16 is still real, unblocked work. (OpenRouter resolved via #17).
**Groq was the one that changed materially**: confirmed across two
independent live runs, it went from a hard CodexBar fetch error (zero usable
sources — the entire premise of issue #18) to three agreeing live sources.
Closed #18 as already resolved upstream without implementing anything.
DeepSeek/OpenRouter prepaid balances are marked `n/a` and never drive
use-it-or-lose-it ranking, so their single-source status does not change
burn recommendations.

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
