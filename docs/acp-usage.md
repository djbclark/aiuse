# ACP context usage

`acp-run --json` prints three usage objects for one turn. They are context
fill and per-turn tokens. They are not the 5-hour, weekly, or subscription
meter. Plan windows still come from cswap, CodexBar, caut, OpenUsage,
tokscale, and the native collectors, which keep working when the vendor TUI
is not installed.

Verified 2026-10-09 against `~/.local/state/acp-run/*.jsonl`:

| Field          | What it is                                                                                                                                         | Agents that emit it                                         |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `usage_update` | Last ACP `session/update` with `sessionUpdate: usage_update`. `used` / `size` are tokens in the context window. `cost` is present for some agents. | agy, claude, codex, copilot, devin, hermes, opencode, zcode |
| `usage`        | `PromptResponse.usage` for this turn (`totalTokens`, `inputTokens`, `outputTokens`, and cache/thought fields when the server sends them).          | claude, codex, copilot, zcode                               |
| `quota`        | `PromptResponse._meta.quota` (`token_count`, `model_usage`). Despite the name, these are per-turn token counts.                                    | claude, codex, zcode                                        |

agy's prompt response is only `stopReason`. cline, cursor, goose, grok, and
qwen answered a turn and put none of these fields on it. OpenCode's reading
is mapped to `opencode-go` and the note says it is not the Go plan meter.
Hermes ACP context stays on provider `hermes`; it is not folded into the
Hermes local-log vendor cost rows. zcode maps to `zai`. A 2026-10-09 zcode
log had `PromptResponse.usage` and `_meta.zcode`, not `_meta.quota`; the
parser still keeps `_meta.quota` when a later log sends it.

The collector reads the JSONL acp-run already wrote (`collectors.acp.log_dir`,
default `~/.local/state/acp-run`, or `AIUSE_ACP_LOG_DIR`). It does not start
a turn. Do not probe with `agy -p`.

## Blend and pin

Default is the existing source priority. A fresh ACP reading is copied onto
the selected quota row as `context_usage`. It is not a `windows[]` entry, so
pace and use-or-lose do not treat context fill as plan percent, and it is
not cross-checked against those percents. If no quota source returned a row,
the ACP reading is the row.

`[usage_sources]` forces one collector to be the only reading for a service:

```toml
[usage_sources]
claude = "cswap"    # ignore CodexBar, OpenUsage, ACP, …
antigravity = "acp" # context fill only; plan windows are dropped
# omitted, or "blend", keeps the default
```

`aiuse usage-sources` (or `--usage-sources`, `--json`) lists every source for
each vendor and whether it is active. Active means the tool is on PATH, a
credential env var is set, or a recent ACP log has usage fields. It does not
run a collect.

Logs older than `collectors.acp.max_age_hours` (default 168, `0` keeps all)
stay out of the snapshot and show as inactive on the flag.
