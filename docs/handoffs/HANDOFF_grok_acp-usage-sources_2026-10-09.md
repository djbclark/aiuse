# Handoff — ACP context logs as a usage source (2026-10-09)

**Session:** Grok, `~/src/aiuse`, continued after compaction. Follows
[`HANDOFF_claude-fable_openusage-tui-staircase_2026-10-09_0130.md`](HANDOFF_claude-fable_openusage-tui-staircase_2026-10-09_0130.md).

## What landed

Passive collector `acp` (17th). It reads `~/.local/state/acp-run/*.jsonl`
(or `collectors.acp.log_dir` / `AIUSE_ACP_LOG_DIR`). It does not start an
ACP turn and it is not in `cli._EXTERNAL_TOOLS`, so a missing TUI does not
fail collection.

`usage_update` is context-window fill (`used` / `size`), stored as
`AccountUsage.context_usage`. It is not a `QuotaWindow`, so pace and
use-or-lose do not treat it as plan percent. `PromptResponse.usage` and
`_meta.quota` ride along as this turn's token counts. `_meta.quota` is
per-turn, not the subscription window.

Agents with a usage signal (verified 2026-10-09): agy, agy-refined, claude,
codex, copilot, devin, hermes, opencode, zcode. No usage fields: cline,
cursor, goose, grok, qwen. Plan windows stay on the other collectors.

Default blend copies ACP context onto the selected quota row. A blank
OpenUsage.ai Claude row still falls through to ACP. `[usage_sources]`
pins one collector; an empty pin is an error row, not a silent fallback.
`aiuse usage-sources` lists sources per vendor. Schema stays 1.1. No release.

Detail: [`docs/acp-usage.md`](../acp-usage.md).

## Tests

`tests/test_acp_usage.py` plus the runner concurrency patches that stub
`collect_acp`. Run through `~/ops/site-private/bin/bg`. First run: 1 failed
(note text case), 73 passed. After the assertion fix, the last-failed test
passed. Full suite was not run.
