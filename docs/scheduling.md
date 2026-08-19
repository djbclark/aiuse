# Scheduling `aiuse` (LaunchAgent)

Regular runs accumulate snapshots under `~/.cache/aiuse/snapshots` when
`analysis.persist_snapshots` is true (see [`history-learning.md`](history-learning.md)).
This is the foundation for history insights; with `learn_from_history: auto`
(default), scoring starts using history once enough snapshots exist.

**Primary for this operator:** manage the agent from
[`~/ops/site-djbclark`](https://github.com/djbclark/site-djbclark) role
`site_agents` (label `com.djbclark.aiuse`, every hour).

```bash
cd ~/ops/site-djbclark
just site-agents-apply
just site-agents-status
```

Requires `~/.local/bin/aiuse` (`pipx install aiuse`). The role enables
`persist_snapshots` in `~/.config/aiuse/config.toml` and sets LaunchAgent
`PATH` so `cswap` / `codexbar` / `caut` / `openusage-sh` / `tokscale` resolve,
and OpenUsage.ai is available (CLI on `PATH` and/or OpenUsage.app running for
`:6736`). Install:
`packaging/install-deps.sh` or site `just install-aiuse-deps`.

**Generic template** (other machines / non-Ansible): [`packaging/launchd/`](../packaging/launchd/)
and `./packaging/launchd/install.sh`.

**Cadence:** every **hour** (`StartInterval` 3600) + `RunAtLoad`.  
**Footnote:** cron one-liner at the bottom.

Exit codes (for log / monitor tooling): see [`json-contract.md`](json-contract.md).

| Code  | Meaning for a scheduled run                                               |
| ----- | ------------------------------------------------------------------------- |
| **0** | OK, no burn/conserve alerts                                               |
| **1** | Hard failure (treat as error)                                             |
| **2** | OK collection; at least one burn/conserve alert (not a scheduler failure) |

## Verify (site-djbclark)

```bash
just site-agents-status
launchctl print "gui/$(id -u)/com.djbclark.aiuse" | head
ls -lt ~/.cache/aiuse/snapshots | head
tail -n 5 ~/.local/state/aiuse.error.log
aiuse --full -q --no-tui | head -20   # look for "History: N snapshots …"
```

## Uninstall (site-djbclark)

```bash
uid=$(id -u)
launchctl bootout "gui/$uid/com.djbclark.aiuse"
# Or remove the aiuse tasks from site_agents and re-apply after deleting the plist.
```

## Cron footnote

```cron
0 * * * *  /path/to/aiuse -q --json >>"$HOME/.local/state/aiuse.cron.stdout.log" 2>>"$HOME/.local/state/aiuse.cron.stderr.log"
```

Prefer LaunchAgent on macOS (sleep/wake and `RunAtLoad` behave better than cron).
Ensure cron’s `PATH` includes Homebrew and `~/.local/bin`.

`aiuse watch` is not a scheduler. If `analysis.persist_snapshots` is on, each
watch refresh also writes a History snapshot — denser than hourly, only while
the board is open. See [`watch-mode.md`](watch-mode.md).
