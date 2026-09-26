---
name: reference-graft-wiring
description: How graft (code-graph MCP) is wired into Claude Code on this machine, the grep nudge hook, and what alwaysLoad does/doesn't do
metadata:
  type: reference
---

graft (@nanonets/graft 0.20.0): MCP server `graft mcp` in ~/.claude.json, allowed in ~/.claude/settings.json, plus graft-hooks.cjs hooks (session-start, prompt, post-edit, tool-savings, stop). The `prompt` hook runs `graft ask -n 3` and injects pointers only when the prompt overlaps the top hit (skips prompts <12 chars).

Nudge: ~/.claude/hooks/graft_grep_nudge.py, PreToolUse matcher `Grep|Glob|Bash`; fires once per session (marker /tmp/graft-nudge-<sid>) in repos with graft/INDEX.md; Bash only when the command looks like grep/rg/ag/ack/find/fd (can over-match text inside heredocs). Verified firing in a real session 2026-09-26. Tracked mirror: site-private/claude/hooks/.

**Why:** `"alwaysLoad": true` in user-scope ~/.claude.json did NOT stop tool deferral (graft tools still needed ToolSearch). A project ~/src/aiuse/.mcp.json with alwaysLoad was added 2026-09-26 but unverified (needs a fresh session). Some sessions here have no Grep/Glob tools, hence the Bash matcher.

**How to apply:** if graft tools are deferred, load them with one ToolSearch select of all five. ~/.claude is not in git; bezalel token in ~/.claude.json should be rotated (was leaked into a transcript 2026-09-26).
