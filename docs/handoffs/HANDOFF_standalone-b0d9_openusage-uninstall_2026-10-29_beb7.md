---
schema_version: 1
handoff_id: beb7
parent_handoff_ids: []
lineage: none
chain: [standalone-b0d9]
repo: aiuse
workspace: main
branch: main
head_sha: 7554db24986e6df89512d8d3216f78997ae4e0e2
created_at: 2026-10-29T02:42:55-0400
writer: claude-code
---

# Handoff — OpenUsage uninstall and toolchain cleanup

The session ran in `~/src/aiuse` but touched no aiuse file. All work was in
`djbclark-ade`, `~/ops/site-djbclark` and `~/ops/site-private`. This chain lists
every repo as a side workspace (the `aiuse/main` pointer belongs to the older
chain `standalone-39fc`), so resume with an explicit path:
`/baton ~/.local/state/handoffs/chains/standalone-b0d9/SESSION_LOG.md`.

## The Goal

djbclark: OpenUsage "somehow got reinstalled somewhere and started"; find out
whether we installed it and uninstall it, then remove its wiring from the
toolchain so nothing restores it.

## Where We Are

Uninstall is done and verified (no process, no login item, no plugin, no app
data). Toolchain cleanup is committed; one PR still needs merging, and a nightly
job will recreate one removed file until it is.

1. **Cause.** The app launched 2026-10-28 20:35 via LaunchServices from
   `~/src/openusage/dist/OpenUsage.app`, a gitignored build of 2026-10-04 that
   LaunchServices still had registered. `/Applications/OpenUsage.app` and the
   cask were already gone (removed 2026-10-22, see `registry/ports.yml`). The BTM
   login item pointed at the deleted `/Applications` copy. Who launched it is
   not in the logs. It was a dev build (`com.robinebers.openusage.dev`).
2. **Removed from the machine:** `dist/` (the only built copy of that app) and
   its `lsregister` entry; the "OpenUsage" login item; `~/Library` Preferences,
   Caches, Logs, Application Support, HTTPStorages; `~/.local/state/openusage`
   (hook spool); `~/.config/opencode/plugins/openusage-telemetry.ts`; both
   keychain iCloud-sync items (`...openusage.dev.icloud-sync-device-id.v1` and
   the non-dev one). Also ran `killall cfprefsd`. Left on purpose: macOS's
   `iCloud.com.robinebers.openusage.dev` CloudDocs container, and the source
   checkout `~/src/openusage` (branch `recover/openusage-cpu-patches`, untouched).
3. **Toolchain cleanup, committed:**
   - `djbclark-ade` branch `remove-openusage` (`dccbc9a`), opened as
     [PR 119](https://github.com/djbclark/djbclark-ade/pull/119): deletes the
     canonical `opencode-plugins/openusage-telemetry.ts`, strips the openusage
     handling from `bin/opencode-plugin-guard` (rtk only now), drops the three
     `claude-hook.sh` blocks from `agents/claude/settings.json.tmpl` and the
     `--previous-notify` fanout from `agents/codex/config.toml.tmpl`, trims
     `tests/test_rtk_opencode_plugin.py`, adds a note to
     `docs/opencode-plugin-v2.md`. Guard tests 12 passed.
   - `djbclark-ade` branch `fix-herdr-scrub-test` (`6365b72`), opened as
     [PR 120](https://github.com/djbclark/djbclark-ade/pull/120): the
     `test_herdr_ids_never_reach_the_agent` probe collected every `HERDR_*`
     variable; it now lists only the four pane ids, because `HERDR_BIN_PATH` and
     `HERDR_SOCKET_PATH` are documented to pass through (`agents/README.md`).
     16 passed inside herdr.
   - `site-djbclark` master: `b563ec6` (stale `cask "openusage"` out of
     `generated/Merged-Brewfile`, `install-openusage` recipe and status checks
     out of the justfile), `8cc5fb8` (caut recipes out of the justfile), `7662120`
     (ports.yml relaunch note; Jobber guard comment names rtk only).
   - `site-private` master: `7f8ba75` (OpenUsage memory note records the
     2026-10-29 uninstall and the `dist/` cause).
4. **Both PRs are open.** `pytest-linux` and `context-policy` passed, the macOS
   `pytest` check had no result at 02:40, mergeability unknown.

## What We Tried

1. First assumed the app was in `/Applications` or a Homebrew cask; neither
   existed. The lock file time and `log show` (launchd, runningboardd) gave the
   real path.
2. Ran the `djbclark-ade` template tests inside herdr and got four subtest
   failures; they looked like a scrub bug in `acp-run` but were a too-broad
   test probe (see PR 120).
3. Two tool-hook rejections cost reruns: a stray `sed() { :; }` in a command
   (use `gsed`), and `ls --time-style` (use `gls`). No lasting effect.

## Key Decisions

The shared `djbclark-ade` checkout carries other sessions' uncommitted edits, so
all work went in linked worktrees from `origin/master`. The PR for the OpenUsage
removal was opened without an adversary round because it is a deletion with no
new logic. The caut recipes were removed entirely at djbclark's choice, over the
recommended "leave as is". `install_caut` and `install_openusage` in aiuse's own
`packaging/install-deps.sh` stay, per this repo's AGENTS.md on deprecated
collectors.

## Decisions

- [decision] Work in linked worktrees, not the shared checkout; rejected: edit `~/src/djbclark-ade` directly; because it holds other sessions' uncommitted `skills/paseo*` edits and order 9 forbids it.
- [decision] Open PR 119 without an adversary round; rejected: adversary review first; because the change only deletes and adds no logic.
- [decision] Fix the herdr test probe, not acp-run; rejected: scrub `HERDR_BIN_PATH` and `HERDR_SOCKET_PATH` in acp-run; because `agents/README.md` says they are service routing and may stay.
- [ruling] "openusage somehow got reinstalled somewhere and started. It's not in Applications. Did we install that? Uninstall it. (I already quit)" (djbclark, 2026-10-29)
- [ruling] Answered "yes" to also removing OpenUsage from `djbclark-ade` (plugin, guard, hook templates) (djbclark, 2026-10-29)
- [ruling] Chose "Regenerate Brewfile, drop the justfile recipes" and "Remove caut entirely" for `site-djbclark` (djbclark, 2026-10-29)
- [ruling] Chose "Let it run and delete the plugin afterwards" for the 03:45 guard job (djbclark, 2026-10-29)

## Relations

- decided_in [[HANDOFF_standalone-b0d9_openusage-uninstall_2026-10-29_beb7]]

## Evidence & Data

- Launch: `log show` 2026-10-28 20:35:35, `Successfully spawned OpenUsage[89296] because launch job demand`, exPath `/Users/djbclark/src/openusage/dist/OpenUsage.app/Contents/MacOS/OpenUsage`. Quit 23:52.
- `dist/` was 32 MB; total removed data about 76 MB.
- Jobber job `opencode-plugin-guard`, schedule `0 45 3 * * *`, runs `~/src/djbclark-ade/bin/opencode-plugin-guard` (template `roles/site_agents/templates/jobber.yaml.j2` in site-djbclark).
- The shared `djbclark-ade` checkout (`master`, 7 behind at 01:40) still has the old guard and the canonical `openusage-telemetry.ts`.
- `djbclark-ade` repo test of the removal: `tests/test_rtk_opencode_plugin.py` 12 passed; template tests 115 passed 8 skipped before the PR 120 fix.

## Operator Feedback

djbclark picked the non-recommended "remove caut entirely" and chose to let the
03:45 guard run recreate the plugin and clean it afterwards, rather than patch
the shared checkout.

## Where We're Going

1. **Next action (after 03:45 on 2026-10-29): clean up what the old guard
   recreates, and stop it recurring.**
   1. `gh pr view 119 --repo djbclark/djbclark-ade --json state,mergedAt,statusCheckRollup` and the same for 120. The merge is djbclark's; ask if still open.
   2. Once PR 119 is merged, `git -C ~/src/djbclark-ade fetch origin && git -C ~/src/djbclark-ade merge --ff-only origin/master`. The checkout has four unrelated modified `skills/paseo*/SKILL.md` files; if the fast-forward touches them, stop and report. Until the checkout has the new guard it recreates the plugin every night at 03:45.
   3. `ls ~/.config/opencode/plugins | rg -i openusage` and `ls ~/.local/state/openusage`. If present, delete `~/.config/opencode/plugins/openusage-telemetry.ts` and `~/.local/state/openusage`.
2. Remove my two worktrees after the merges: `git -C ~/src/djbclark-ade worktree remove ~/src/djbclark-ade-remove-openusage-wt` and the same for `~/src/djbclark-ade-fix-herdr-scrub-wt` (their branches are on origin).
3. Optional, only if djbclark wants the patched OpenUsage build back: rebuild from `~/src/openusage` (`swift build`); never launch a bundle from a registered path without a reason.
4. Not mine, left alone: `?? opencode.json` in aiuse, `M bin/fleet-watch` in site-djbclark, four `skills/paseo*/SKILL.md` edits in the shared `djbclark-ade` checkout, `M memory/codex/*` in site-private, `.gitignore` and `.ignore` in `~/src/openusage`.

## Detached jobs

none

## Quick Start

```bash
gh pr view 119 --repo djbclark/djbclark-ade --json state,mergedAt
gh pr view 120 --repo djbclark/djbclark-ade --json state,mergedAt
ls ~/.config/opencode/plugins | rg -i openusage; ls ~/.local/state/openusage
git -C ~/src/djbclark-ade worktree list | rg 'remove-openusage|fix-herdr-scrub'
```
