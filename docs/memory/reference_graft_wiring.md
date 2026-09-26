---
name: reference-graft-wiring
description: How graft (code-graph MCP) is wired into Claude Code on this machine, the grep nudge hook, and what alwaysLoad does/doesn't do
metadata:
  type: reference
---

graft (@nanonets/graft 0.20.0): MCP server `graft mcp` in ~/.claude.json, allowed in ~/.claude/settings.json, plus graft-hooks.cjs hooks (session-start, prompt, post-edit, tool-savings, stop). The `prompt` hook runs `graft ask -n 3` and injects pointers only when the prompt overlaps the top hit (skips prompts <12 chars).

Nudge: ~/.claude/hooks/graft_grep_nudge.py, PreToolUse matcher `Grep|Glob|Bash`; fires once per session (marker /tmp/graft-nudge-<sid>) in repos with graft/INDEX.md; Bash only when the command looks like grep/rg/ag/ack/find/fd, after stripping heredoc bodies and quoted strings (so search words in commit messages or file text stay silent). Verified firing in real sessions 2026-09-26. Tracked mirror: site-private/claude/hooks/. Hermes equivalent (2026-09-26): ~/.hermes/skills/graft/SKILL.md + ~/.hermes/agent-hooks/graft-nudge.py registered as hooks.pre_llm_call in ~/.hermes/config.yaml (allowlisted in shell-hooks-allowlist.json); committed in the ~/.hermes repo. `graft init --agents hermes` adds an AGENTS.md section per repo (done in aiuse, tendcf).

**Why:** `"alwaysLoad": true` in user-scope ~/.claude.json did NOT stop tool deferral (graft tools still needed ToolSearch). A project `.mcp.json` with `"alwaysLoad": true` DOES work: verified 2026-09-26 in a fresh ~/src/aiuse session, graft tools arrived fully loaded. Committed in aiuse (ec62b79) and tendcf; in forks (crush, mobile) the same file is written but hidden via .git/info/exclude so upstream diffs stay clean. Gotcha: `graft init` (any --agents) DELETES an existing .mcp.json — run `git checkout -- .mcp.json` afterwards. Some sessions here have no Grep/Glob tools, hence the Bash matcher.

**How to apply:** in a repo without the project `.mcp.json`, graft tools are deferred; load them with one ToolSearch select of all five. `~/.claude/settings.json.bak-graft` was deleted 2026-09-26 once the nudge proved stable. ~/.claude is not in git; bezalel token in ~/.claude.json should be rotated (was leaked into a transcript 2026-09-26).
