# Companion stack: ambient glance + ranked decision

`aiuse` is a **Layer 2** ranking CLI (“which pool next?”). It is not a menu-bar
quota monitor. Pair it with tools that already own **ambient** % bars.

## Recommended split

| Role                      | Tool                                                                                     | What you use it for                                                        |
| ------------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **Ambient** (always-on %) | [CodexBar](https://github.com/steipete/CodexBar), [OpenUsage](https://www.openusage.ai/) | Live bars in the menu bar; spot lockouts at a glance                       |
| **Rank / burn**           | **`aiuse`**                                                                              | Ordered ladder of expiring allotments; `aiuse suggest` for a single winner |
| **Multi-account Claude**  | [cswap](https://github.com/realiti4/claude-swap)                                         | Canonical Claude Code rows for every configured email                      |
| **Hourly history**        | LaunchAgent (`docs/scheduling.md`)                                                       | Persist snapshots + optional `learn_from_history`                          |
| **In-terminal board**     | **`aiuse watch`**                                                                        | Opt-in full-screen matrix; compose with CodexBar / OpenUsage for a menubar |

Install data sources: [`packaging/install-deps.sh`](../packaging/install-deps.sh)
or site `just install-aiuse-deps`.

## Typical operator loop

1. **Glance** CodexBar / OpenUsage when switching tasks.
2. **Decide** with `aiuse` (default clock matrix) or a one-liner:

   ```bash
   aiuse status          # one line for prompts / status bars
   aiuse prompt          # synonym of status
   aiuse suggest         # single best burn pool (or nothing urgent)
   aiuse suggest --json  # + top-level suggestion field
   aiuse --no-tui -q     # full ladder, quiet stderr meta
   ```

3. **Leave running** the hourly LaunchAgent so history densifies:

   ```bash
   just -f ~/ops/site-djbclark/justfile site-agents-status
   # or see docs/scheduling.md
   ```

## One-line status (`aiuse status` / `aiuse prompt`)

Stdout is a **single line**, suitable for shell prompts and status bars:

- Actionable burn/conserve: `use: Claude Code weekly 91% · 2 burn alerts`
- Nothing urgent: `ok: nothing urgent under current thresholds`
- Collect hard-fail: `error: no accounts (collectors failed)` (exit 1)

Does not start a menu-bar app. Uses the same collectors and exit codes as a
normal collect run (0 / 1 / 2).

## What not to expect from `aiuse`

- No Swift/macOS menu-bar binary (`status` is a **pull** one-liner, not a widget)
- No request routing / proxy / LiteLLM leases (stay Layer 2; use `suggest` / `serve` for ranked advice only)
- Ambient % for a single provider is better in CodexBar/OpenUsage
- Full history charts / anomaly BI — use onWatch-class tools; `aiuse` History is operational

## Related product surfaces (shipped)

| Command                | Doc                                                                      |
| ---------------------- | ------------------------------------------------------------------------ |
| `aiuse suggest`        | Single burn winner; JSON field in [`json-contract.md`](json-contract.md) |
| `aiuse serve`          | Loopback HTTP [`agent-api.md`](agent-api.md)                             |
| `aiuse --full` History | [`history-learning.md`](history-learning.md)                             |

## Related

- [`docs/competitive-landscape.md`](competitive-landscape.md) — Layer 1 vs Layer 2
- [`docs/scheduling.md`](scheduling.md) — LaunchAgent
- [`docs/collectors-caut-openusage.md`](collectors-caut-openusage.md) — OpenUsage HTTP
