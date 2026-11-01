---
schema_version: 1
handoff_id: dead9
parent_handoff_ids: [f9fb]
lineage: none
chain: [standalone-f9fb]
repo: aiuse
workspace: aiuse
branch: main
head_sha: ded90dc779c1626ebc1be9fbf0e1bfba7381ff01
created_at: 2026-11-01T11:27:00-05:00
writer: antigravity
---

# Handoff — Muse Keychain prompt resolved

## The Goal

Stop the repeating macOS Keychain dialog ("security wants to access key `ai.meta.dev.credentials`", Allow Always / Allow / Deny) triggered during all-provider sweeps or unattended runs.

## Where We Are

Task complete and pushed.
Commit: `ded90dc` on `main` (`fix(muse): opt-in Keychain access and skip probing on inactive subscription`).

Key changes:

1. `_read_muse_keychain_access_token` now guards Keychain access behind explicit opt-in (`AIUSE_MUSE_KEYCHAIN=1`). By default, it does not shell out to `/usr/bin/security`, preventing unauthorized GUI authorization dialogs from popping during sweeps or unattended runs.
2. `_resolve_muse_access_token` checks:
   - `AIUSE_MUSE_ACCESS_TOKEN` / `MUSE_ACCESS_TOKEN` (highest priority).
   - `~/.config/muse/auth.json`: if `subs_active: false` (or `is_subs_active: false`), probing is skipped entirely because Meta reports no active plan.
   - `~/.config/muse/auth.json`: reads inline `providers.meta.access_token` if present.
   - Falls back to `_read_muse_keychain_access_token` only when opt-in is enabled.
3. Updated `_subscription_windows_for_local_login` and `_merge_subscription_windows` to pass through `env` while maintaining backwards compatibility for 1-argument test monkeypatches.
4. Added unit tests in `tests/test_muse.py` (26 passed). Full test suite passed (1545 passed in 72s).
5. Smoke tested with `AIUSE_SKIP_HOMEBREW_FORMULA=1 ./aiuse-test.sh watch --once` and `./aiuse-test.sh` (0 prompts, clean exit).
6. Documented token resolution precedence, inactive subscription behavior, and `AIUSE_MUSE_KEYCHAIN=1` opt-in in `docs/muse-quota.md`.

## Detached jobs

none

## Quick Start

```bash
cd ~/src/aiuse && git fetch origin && git pull --ff-only
.venv/bin/python -m pytest -q tests/test_muse.py
```
