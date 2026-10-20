---
name: reference-graft-wiring
description: How graft is wired on this machine (CLI-only since 2026-10-18, tools deferred since 2026-10-10), the grep nudge hooks, and the watchdog that guards it
metadata:
  type: reference
---

graft (@nanonets/graft 0.20.0): MCP server `graft mcp` in ~/.claude.json, allowed in ~/.claude/settings.json, plus graft-hooks.cjs hooks (session-start, prompt, post-edit, tool-savings, stop). The `prompt` hook runs `graft ask -n 3` and injects pointers only when the prompt overlaps the top hit (skips prompts <12 chars).

Nudge: ~/.claude/hooks/graft_grep_nudge.py, PreToolUse matcher `Grep|Glob|Bash`; fires once per session (marker /tmp/graft-nudge-<sid>) in repos with graft/INDEX.md; Bash only when the command looks like grep/rg/ag/ack/find/fd, after stripping heredoc bodies and quoted strings (so search words in commit messages or file text stay silent). Verified firing in real sessions 2026-09-26. Tracked mirror: site-private/claude/hooks/. Hermes equivalent (2026-09-26): ~/.hermes/skills/graft/SKILL.md + ~/.hermes/agent-hooks/graft-nudge.py registered as hooks.pre_llm_call in ~/.hermes/config.yaml (allowlisted in shell-hooks-allowlist.json); committed in the ~/.hermes repo. `graft init --agents hermes` adds an AGENTS.md section per repo (done in aiuse, tendcf).

**Why:** `"alwaysLoad": true` in user-scope ~/.claude.json did NOT stop tool deferral (graft tools still needed ToolSearch). A project `.mcp.json` with `"alwaysLoad": true` DOES work: verified 2026-09-26 in a fresh ~~/src/aiuse session, graft tools arrived fully loaded. Committed in aiuse (ec62b79) and tendcf; in forks (crush, mobile) the same file is written but hidden via .git/info/exclude so upstream diffs stay clean. Gotcha (root cause read from dist/cli.js wireTarget, 0.20.0): `graft init --agents <ids>` first RETRACTS every host not listed — repo files AND global ones (~~/.claude.json mcpServers.graft, the 4 graft hooks + shim in ~/.claude/settings.json, ~/.codex hooks + [mcp_servers.graft], ~/.gemini/skills/graft). `--dry-run` only lists writes, never the retraction. Workaround: always name every wired host, currently `--agents claude agents antigravity hermes`; and because graft's Claude init rewrites the `graft` key in .mcp.json to its own shape (no alwaysLoad) run `git checkout -- .mcp.json` afterwards. Same overwrite recurs on version upgrade via the SessionStart reconcileWiring (upstream trailhq/Graft#118) — guarded since 2026-09-26 by ~/.claude/hooks/graft_mcp_alwaysload.py (SessionStart, re-adds the flag; mirror in site-private/claude/hooks/). To add only an instruction-file section to a repo, copy the fenced `<!-- graft:start -->…<!-- graft:end -->` block from aiuse/AGENTS.md instead of running init. 2026-09-26 the retraction happened here; globals were restored by running the full --agents list from a scratch git repo (repo-level writes land in the scratch dir, global ones in ~). Some sessions here have no Grep/Glob tools, hence the Bash matcher.

**How to apply:** in a repo without the project `.mcp.json`, graft tools are deferred; load them with one ToolSearch select of all five. `~/.claude/settings.json.bak-graft` was deleted 2026-09-26 once the nudge proved stable. ~/.claude is not in git; bezalel token in ~/.claude.json should be rotated (was leaked into a transcript 2026-09-26).

**Watchdog:** Hermes cron job "Graft Wiring Invariants" runs `~/.hermes/scripts/check-graft-wiring-invariants.sh` every 6h (no-agent; silent when healthy) and Telegrams the failed checks, evidence (graft version change, file mtimes, recent `graft init` in shell history, hermes config backups, last commits) plus a paste-ready Claude Code prompt to restore and find the cause. Upstream report of the retraction + dry-run omission: https://github.com/trailhq/Graft/issues/482 (2026-09-26).

Housekeeping 2026-09-26: forks (crush, mobile) hide /.mcp.json, /graft/ and /.ignore via .git/info/exclude so nothing graft-related reaches upstream diffs; tendcf untracked graft's session cache and tracks .ignore like aiuse; Antigravity's ~/.gemini/config/mcp_config.json was an empty (invalid) file so graft never registered there — seeded with the graft server; ~/.hermes/config.yaml is now force-tracked (only ${ENV} key refs) so the pre_llm_call hook survives a restore.

**Policy since 2026-10-18 (read this first; the paragraphs above describe the 2026-09-26 wiring and are history):**
graft is CLI-only. On 2026-10-10 djbclark deferred the MCP tools (aiuse df5c155, tendcf 765b3cd: graft was called in 6% of
sessions, 64% of its answers were followed by a read of the same file; `alwaysLoad` dropped, the graft_mcp_alwaysload.py
SessionStart guard removed from ~/.claude/settings.json). On 2026-10-18 the MCP server was deregistered from every TUI (95
MCP calls vs ~24,000 CLI uses in 14 days; djbclark-ade docs/mcp-servers.md) and `disabledMcpjsonServers` in
~~/.claude/settings.local.json keeps a project .mcp.json from re-enabling it. The Hermes watchdog
(~~/.hermes/scripts/check-graft-wiring-invariants.sh) still enforced the 2026-09-26 wiring until 2026-10-20 (failure streak
37, six "failures" that were these two rulings); since ~/.hermes 94e8d95 it fails on the opposite: a graft MCP entry in
~/.claude.json, ~/.codex/config.toml or ~/.gemini/config/mcp_config.json, the alwaysload guard wired, or `alwaysLoad: true`
in a repo .mcp.json (the forks crush and mobile still had it; dropped 2026-10-20). What it keeps guarding: the six
graft-hooks.cjs hooks and graft_grep_nudge.py in Claude, the Codex hooks and shim, the Antigravity skill, the Hermes
pre_llm_call hook, repo graphs, AGENTS.md sections and fork excludes. graft 0.23.0's SessionStart reconcile re-added
`mcpServers.graft` to ~/.claude.json after the 2026-10-16 upgrade; removed 2026-10-20 with `claude mcp remove graft -s user`
(backup ~/.claude.json.bak-pre-graft-mcp-remove-*). Never run `graft init --agents ...` to restore anything: it registers
the MCP server in every host it names and retracts the rest; hooks come back by inserting the raw entries and running
graft-hook-timeout-fix.py (hermes-worktrees memory project-graft-hook-timeout-units).
