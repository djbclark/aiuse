# tokscale per-provider investigation (Step 32)

**Date:** 2026-07-23  
**Conclusion:** no true per-provider fan-out for subscription quota is available today.

## What we checked

| Path                     | Result                                                                          |
| ------------------------ | ------------------------------------------------------------------------------- |
| `tokscale usage --help`  | Only `--json`, `--light`, `--home` — **no `--provider`**                        |
| `tokscale codex`         | Account switcher + `status` for Codex OAuth, not a drop-in `usage --json` slice |
| `tokscale cursor`        | Auth/cache/sync for Cursor CSV usage — not subscription quota JSON              |
| `tokscale antigravity`   | Sync from language servers / local cache                                        |
| `tokscale trae` / `warp` | Login/sync for those products                                                   |
| `tokscale claude`        | **Does not exist**                                                              |

## Implications for `ai`

- Keep the **single** `tokscale usage --json` call with the shared **45s** timeout (`timeout_for`).
- Runner already prefers CodexBar when both have live data; tokscale is secondary / cross-check.
- True per-provider isolation needs an **upstream** `tokscale usage --provider <name>` (or equivalent).

## Upstream ask (if filing)

Add `tokscale usage --provider <id> --json` returning the same metric shape as one
element of the current multi-provider array, so clients can run concurrent
per-provider subprocesses with independent timeouts (mirror CodexBar).
