---
schema_version: 1
handoff_id: f9fb
parent_handoff_ids: []
lineage: none
chain: [standalone-f9fb]
repo: aiuse
workspace: aiuse
branch: main
head_sha: b9174037ea4c21cd9344bf3c2a1520aeb1e80a16
created_at: 2026-11-01T10:52:00-05:00
writer: copilot-cli
---

# Handoff — Muse keychain prompt investigation

## The Goal

Stop the repeating macOS Keychain dialog ("security wants to access key
`ai.meta.dev.credentials`", Allow Always / Allow / Deny). "Allow Always" does
not stick and the dialog appears several times. Muse is meant to be off in
CodexBar and is in aiuse's `[disabled_services]`.

## Where We Are

Diagnosis only; no code changed. The prompt names `security`, so the caller is
the `security` CLI, not CodexBar (CodexBar uses the Security framework and its
dialog would name CodexBar). The only aiuse code that runs it is
`_read_muse_keychain_access_token` (`src/aiuse/collectors/muse.py:1366`:
`security find-generic-password -s ai.meta.dev.credentials -a meta -w`, 5s
timeout), reached via `_merge_subscription_windows` ->
`_muse_subscription_windows` (around line 1449).

Tree state: `main` at b917403 (3.12.1), clean of my edits. `AGENTS.md` modified
and `opencode.json` untracked belong to other sessions; not touched.

## What We Tried

1. Shimmed `security` on PATH and ran `AIUSE_SKIP_HOMEBREW_FORMULA=1
./aiuse-test.sh watch --once`: zero `security` calls. Muse is in
   `SINGLE_PROVIDER_COLLECTORS` (`config.py:449`), so `[disabled_services]`
   skips the whole collector. The sampler and a normal run are therefore not
   the cause.
2. `CodexBarCLI config dump`: muse `enabled: false`. Read
   `MuseCredentials.swift`: `hasLogin` runs a no-UI preflight on the same item
   even with Muse off. Comments say it cannot prompt; a legacy CLI-owned ACL
   might. No evidence either way.
3. `log show --last 15m` for the item: no hits. Not conclusive.
4. At 10:42 `ps` showed no `security` process, only SecurityAgent (the dialog),
   consistent with a short-lived `security` killed by aiuse's 5s timeout while
   the dialog stayed up.

## Key Decisions

Left PID 20844 (`aiuse watch`, started 08:57) untouched; it is the operator's
pane. Did not edit CodexBar or aiuse before the cause is confirmed.

## Decisions

- [decision] No code change yet; rejected: patching the Muse keychain read blindly; because the caller is unproven and the working tree run made no `security` calls.

## Relations

- decided_in [[HANDOFF_standalone-f9fb_muse-keychain-prompt_2026-11-01_f9fb]]

## Evidence & Data

- `src/aiuse/collectors/muse.py:1366`, `src/aiuse/config.py:449`
- `~/src/CodexBar/Sources/CodexBarCore/Providers/Muse/MuseCredentials.swift`
- Running: `/opt/homebrew/opt/aiuse/bin/aiuse watch` PID 20844 (Homebrew 3.12.1),
  `aiuse sample...` PID 15570, /Applications CodexBar and dev
  `~/src/CodexBar/CodexBar.app` (started 10:02) both running.

## Operator Feedback

Dialog was up at 10:43 naming `security` and `ai.meta.dev.credentials`. Not yet
answered: has the operator pressed `a` (all-providers sweep) in the watch pane
today?

## Where We're Going

1. Ask whether `a` was pressed in watch PID 20844; quit that watch and see if
   the prompts stop. If they recur, run `log stream --predicate 'process ==
"SecurityAgent" OR eventMessage CONTAINS "ai.meta.dev"'` and
   `ps -axo pid,ppid,command | rg security` the moment a dialog appears to name
   the parent.
2. If the sweep is the cause, change `_read_muse_keychain_access_token` to
   prefer `~/.config/muse/auth.json` and skip `security` unless explicitly
   opted in; add a test; keep schema/docs in sync (`docs/muse-quota.md`).
3. If CodexBar's preflight is the cause, PR upstream (home rule: non-aiuse code
   goes upstream) to skip Muse detection when the provider is disabled.

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git fetch origin && git pull --ff-only
rg -n "_read_muse_keychain_access_token" src/aiuse/collectors/muse.py
ps -axo pid,lstart,command | rg "aiuse (watch|sample)"
```
