---
schema_version: 1
handoff_id: b191
parent_handoff_ids: []
lineage: none
chain: [standalone-b191]
repo: aiuse
workspace: aiuse
branch: main
head_sha: fb87270402bd94268b1a53107e82989d8959f781
created_at: 2026-09-26T16:00:00-04:00
writer: claude-code
---

# Handoff — graft auto-use wiring for Claude Code

## The Goal

Make Claude Code use graft (@nanonets/graft 0.20.0 code-graph MCP) automatically
in indexed repos: tools loaded without ToolSearch, and a nudge away from raw
grep/glob. Work was picked up from a session launched in `~/` (a non-repo);
aiuse was only the working directory, no aiuse code was changed.

## Where We Are

Done: nudge hook extended and verified live; token backup deleted; hook mirrored
to site-private; reference memory written. Unverified: alwaysLoad.
Files changed this session:

1. `~/.claude/hooks/graft_grep_nudge.py` — now also handles `Bash` (only when the command matches grep/egrep/rg/ag/ack/find/fd).
2. `~/.claude/settings.json` — PreToolUse matcher `Grep|Glob` -> `Grep|Glob|Bash`.
3. `~/ops/site-private/claude/hooks/graft_grep_nudge.py` + `claude/README.md` — tracked mirror, pushed (`f7353a9`).
4. `docs/memory/reference_graft_wiring.md` + `MEMORY.md` — pushed (`fb87270`).
5. `~/src/aiuse/.mcp.json` — UNTRACKED, alwaysLoad test, see below.
6. Deleted `~/.claude.json.bak-graft` (held plaintext bezalel token).
   Tests: hook checked with fake stdin (5 cases: ls silent, grep fires, `git log --grep` silent, repeat silent, non-graft repo silent) and fired once in a real Bash call. No repo test suite run (no code change).
   Dirty tree: only untracked `.mcp.json`.

## What We Tried

1. `"alwaysLoad": true` on the `graft` entry in user-scope `~/.claude.json` — FAILED: graft tools were still deferred (needed ToolSearch) in a session started after the edit. Likely only honored in `.mcp.json`.
2. Testing the nudge via Grep/Glob — impossible: this session had no Grep or Glob tools (search via Bash only), so the original `Grep|Glob` matcher could never fire. That is why Bash was added.

## Key Decisions

1. Extend the matcher to Bash with a command regex, rather than all Bash. Rejected: matching every Bash call (too noisy). Known flaw: over-matches search words inside heredoc text.
2. Delete `.bak-graft` token backup now; keep `settings.json.bak-graft` until the nudge is trusted.
3. Left `.mcp.json` uncommitted because it is unverified. Rejected: committing it speculatively.
4. Did not track `~/.claude/settings.json` (56 KB, not audited for secrets); README documents the stanza instead.
5. Did not run `graft build` in `~/src/crush` (partial `graft/`, no INDEX.md; 4 live sessions).

## Evidence & Data

1. Indexed (have `graft/INDEX.md`): ~60 repos in `~/src` plus `~/ops/{stayturgid,site-djbclark,site-private}`; `~/src/crush/graft` partial.
2. graft prompt hook (`/opt/homebrew/lib/node_modules/@nanonets/graft/dist/claude/hooks.js`, `main('prompt')`): skips prompts <12 chars; runs `graft ask -n 3 --json`; injects pointers only if prompt overlaps the top hit.
3. `~/.claude` is not a git repo.
4. Graft tools deferred: `graft_check_freshness, file_api, find_all, find_code, repo_map, trace_calls`.

## Operator Feedback

"Yes, do everything you can do to make this work consistently, and fix all other issues." (approved all 5 proposals).

## Where We're Going

1. **Next action:** start a FRESH session in `~/src/aiuse` and check whether graft tools are available without ToolSearch (project `.mcp.json` alwaysLoad; may need MCP project-server approval). Works -> `git add .mcp.json`, commit, push. Fails -> delete `.mcp.json`; fall back to SessionStart hint + one ToolSearch, and update `reference_graft_wiring.md`.
2. Operator: rotate the bezalel bearer token (leaked into an earlier transcript).
3. After the nudge proves stable, delete `~/.claude/settings.json.bak-graft`.
4. Optional: tighten the nudge regex against heredoc false positives; `graft build` in `~/src/crush`.

## Quick Start

```bash
cd ~/src/aiuse && git status -s        # expect only ?? .mcp.json
cat .mcp.json
jq '.mcpServers.graft.alwaysLoad' ~/.claude.json   # do NOT cat the file (token)
```
