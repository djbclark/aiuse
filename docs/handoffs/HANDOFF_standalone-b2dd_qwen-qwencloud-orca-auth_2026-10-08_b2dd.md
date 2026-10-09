---
schema_version: 1
handoff_id: b2dd
parent_handoff_ids: []
lineage: none
chain: [standalone-b2dd]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 3d319b94f05a6b51d3b61dc5b104f1488c367b58
created_at: 2026-10-08T21:35:00-0400
writer: cursor-agent
---

# Handoff — qwen vs qwencloud, Orca, and local auth wiring

## The Goal

Clarify whether both `qwen` and `qwencloud` CLIs are needed, which one Orca
uses, and whether each can call real models on this machine; close the session
with durable follow-ups (loose audit).

## Where We Are

Investigation complete. No `aiuse` source changes. Operator chose to defer Qwen
Code coding-plan auth repair and to wire `DASHSCOPE_API_KEY` into the shell
via SecretSpec + `~/.bashrc.local` (done on disk, not in git).

## What We Tried

1. **Assumed `qwen` works out of the box** — re-probe without extra env:
   default `qwen -p` still returns `401 invalid access token or token expired`
   (coding-plan key path).
2. **Assumed qwencloud OAuth covers inference** — `qwencloud chat create`
   without API key returns `AUTH_REQUIRED`; OAuth alone is enough for
   `qwencloud usage summary` / aiuse collector only.

## Key Decisions

1. Keep both CLIs for different roles (`qwen` = agent TUI; `qwencloud` =
   platform + aiuse quota), not as redundant installs.
2. Orca uses **`qwen` only** (`qwen-code` agent, `detectCmd: qwen`); it does
   not invoke `qwencloud`.
3. Defer refresh of `BAILIAN_CODING_PLAN_API_KEY` (operator); no agent edits
   to `~/.qwen/settings.json` this session.
4. Export `DASHSCOPE_API_KEY` via SecretSpec + `~/.bashrc.local`, not
   plain-text export in tracked dotfiles.
5. Skip durable memory note for the Q&A.

## Evidence & Data

- Binaries: `/opt/homebrew/bin/qwen` → `qwen-code` 0.24.5; `/opt/homebrew/bin/qwencloud` → npm CLI 1.5.0.
- Orca: `orca/src/shared/tui-agent-config.ts` — agent id `qwen-code`, `detectCmd: 'qwen'`.
- aiuse: collector `qwencloud` runs `qwencloud usage summary`; maps provider display key `qwen` → canonical `qwencloud` (`docs/qwencloud-quota.md`).
- `qwencloud auth status`: authenticated (device flow, keychain); models list live.
- Inference probes (session): `qwencloud chat create` OK after `DASHSCOPE_API_KEY`; `qwen --model qwen3.8-max` OK with same key; default `qwen` 401.
- Machine-local changes (not in git):
  - `~/.config/aiuse/secretspec.toml` — added `DASHSCOPE_API_KEY` declaration + value via `secretspec set`.
  - `~/.bashrc.local` — tier-1 silent export from SecretSpec when unset.
- Git: `aiuse` clean on `main` @ `3d319b9`. `orca` dirty `.gitignore` on `sidebar-filter-query` — **not this session**.

## Operator Feedback

- Loose audit item 1: leave Qwen Code default auth broken until operator fixes.
- Loose audit item 2: wire `DASHSCOPE_API_KEY` for shell / `qwencloud chat`.
- Skipped optional chat-only memory note.

## Where We're Going

1. **Refresh or replace `BAILIAN_CODING_PLAN_API_KEY`** in Qwen Code settings so default model `qwen3.5-plus` works in Orca and headless `qwen -p` (or switch default to `qwen3.8-max` backed by the token-plan key already in SecretSpec).
2. Open a **new login shell** (or `source ~/.bashrc`) on machines where `qwencloud chat` should work without manual export — verify with `qwencloud chat create "Reply with exactly: OK" --model qwen3.8-max --format text`.
3. Optional: run `~/.config/bash/selftest.sh` after any further `~/.bashrc.local` edits (37 passed, 1 failed: `grok does not resolve` — pre-existing unrelated).

## Quick Start

```bash
# Qwen Code default still broken until coding-plan key fixed:
qwen -p 'Reply with exactly: OK'   # expect 401 today

# qwencloud inference after new shell (DASHSCOPE from secretspec):
bash -lc 'qwencloud chat create "Reply with exactly: OK" --model qwen3.8-max --format text'

# aiuse qwencloud quota (OAuth only):
qwencloud usage summary --format json | head

# Orca agent binary:
which qwen && rg "qwen-code" ~/src/orca/src/shared/tui-agent-config.ts
```
