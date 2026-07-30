# Live source coverage

`aiuse` deliberately keeps every successful live reading for cross-checking,
then selects one source for the priority ladder. A provider having two source
names does **not** necessarily mean two independent upstream authorities:
tools can share a browser session, an OAuth endpoint, or a billing API.

## Current local audit (2026-07-30)

This was collected with `aiuse -q --json` and the snapshot's cross-checks,
without recording account names or credentials. Availability remains
machine- and login-dependent.

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

## Practical implication

DeepSeek and OpenRouter are the only successfully reported services with one
client source on this machine today. Their prepaid balances are marked `n/a`
and never drive use-it-or-lose-it ranking, so their single-source status does
not change burn recommendations. Groq is surfaced as an error rather than
pretending an unavailable source is a balance.

For a fresh audit, run:

```bash
aiuse -q --json > /tmp/aiuse.json
```

Look at `snapshot.cross_checks`: an `unavailable` check names a service with
only one successful source in that run. The complete JSON shape is documented
in [`json-contract.md`](json-contract.md).
