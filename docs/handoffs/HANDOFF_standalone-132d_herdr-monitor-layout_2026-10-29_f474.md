---
schema_version: 1
handoff_id: f474
parent_handoff_ids: []
lineage: none
chain: [standalone-132d]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 39cb31ae532a7c8c1bc65d8a3682e41ae24c064b
created_at: 2026-10-29T09:16:00-0400
writer: grok
---

# Handoff — Herdr monitor layout and autorename rules

## The Goal

Reorganize Herdr from tab labels, with at most 5 tabs per workspace, a `remote` workspace for ssh, mosh, and et hosts, an `agents` fallback, and a `monitor` workspace first whose tab order is aiuse-watch, then htop, then tokscale. Encode the mistakes so the next `/autorename all` does not repeat them.

## Where We Are

The skill and the workspace config are committed and pushed. The operator then said stop. A rebuild script exited before any rename. Do not move panes unless the operator asks again.

Live Herdr, rechecked 2026-10-29 after the stop (`herdr workspace list` and `herdr tab list`):

1. `monitor` `w20`, 3 tabs: `aiuse-watch-t` / `w20:p5` (pane label empty, shell pid 17503, foreground was bash), `htop-shell-t` / `w20:p3` / `htop-shell-p` (htop pid 14546), `tokscale-report-t` / `w20:p4` / `tokscale-report-p`.
2. `aiuse+ade` `w31`, 3 tabs: `aiuse-codexbar-plugin-t` / `w31:p2`, `herdr-layout-keep-t` / `w31:p3` / `herdr-layout-keep-p` (this Grok session; old id `w2N:p1D` still aliases here), `aiuse-note-rows-and-t` / `w31:p6` / `aiuse-watch-p` (grok pid 82691).
3. `remote` `w32`, 1 tab: `pastebin-barnowl-t` / `w32:p2` / `pastebin-barnowl-p` (foreground `et pastebin.xvm.mit.edu`, pid 34480).
4. `one-offs` `w33`, 1 tab: `mac-barnowl-t` / `w33:p2`. Leave it here.
5. `agents` `w34`, 4 tabs: `DONE-openusage-uninstall-t`, `DONE-paseo-pane-t` (pane label still `herdr-reorg-p`), `opencode-go-key-live-t`, `paseo-herdr-generic-t`. The `DONE-` prefixes appeared after this session's last placement. Do not strip them.

The live `aiuse watch` TUI is not running. At 2026-10-29 09:15, `ps -p 66311,66281` showed neither pid, and no process command contained `aiuse watch`. Earlier in the session that TUI was `aiuse-matrix-t` / `w31:p5`. This session did not kill it.

The mosh client whose tab was `pastebin.xvm-t` is gone. No `mosh` client to pastebin remained in `ps`. Do not start a new mosh. The et session above is the surviving remote tab.

Git, this session's commits only:

1. [djbclark-ade 8db048d0](https://github.com/djbclark/djbclark-ade/commit/8db048d0) on `master`, fast-forwarded into `~/src/djbclark-ade`. Files: `skills/autorename/SKILL.md`, `skills/autorename/workspace-layout.md`, `docs/skills.md`.
2. [site-djbclark 87e0574](https://github.com/djbclark/site-djbclark/commit/87e0574) on `master`. File: `config/autorename-workspaces.conf` (`monitor` first; `remote` hint includes `et`; `aiuse watch` removed from the `aiuse+ade` hint).

Dirty files this session did not touch, left unstaged:

1. `~/src/djbclark-ade`: `skills/paseo-advisor/SKILL.md`, `skills/paseo-committee/SKILL.md`, `skills/paseo-handoff/SKILL.md`, `skills/paseo/SKILL.md`.
2. `~/ops/site-djbclark`: `bin/fleet-watch`.
3. `~/src/aiuse`: untracked `opencode.json`.

aiuse `main` was `2d6f5010020cb094b67a64967bc56a2dbe39e24c` when this file was rebased. The handoff commit is `39cb31ae532a7c8c1bc65d8a3682e41ae24c064b`. This file is the only aiuse change from this session.

Tests: none run for the markdown skill edit. The earlier agents-fallback test on `3e43fb45` was 14 passed (`uv run --extra dev pytest tests/test_autorename_tab_label.py` in the worktree that has since been removed).

## What We Tried

1. Closing the starter tab of a workspace before a real tab was in it deleted the workspace (`workspace_not_found`).
2. Placing tabs by session title put four agent tabs in `aiuse+ade`. The operator rejected that. Tab label is the key. `paseo-herdr-generic-t` matches herdr. `paseo-pane-t` does not.
3. A workspace named `pastebin` was created, then both tabs were moved to configured `remote`.
4. A rebuild that reused pane ids captured before later moves dropped the mosh `pastebin.xvm` client when its workspace emptied. Old ids can alias. The rule is now in `workspace-layout.md` under "Pitfalls met on 2026-10-29": fresh pane id and foreground pid immediately before each move, confirm the pid after, diff the pid list at the end, do not relaunch a lost client.
5. After the operator chose to put the watch TUI ahead of htop, `/tmp/herdr-monitor-rebuild.py` exited on a layout mismatch before any `workspace rename`. The operator then said stop. The script was deleted. The worktree `/Users/djbclark/src/djbclark-ade-autorename-remote` and local branch `autorename-remote-cap` were removed. The commit remains on `origin/master`.

## Key Decisions

Place by the current tab label. Rename a tab only when that label is a single digit. Keep existing pane labels. Cap a workspace at 5 tabs; overflow is `<name>-2`, then `<name>-3`. Sure moves finish before any question, and questions are one at a time after that. `monitor` wins over every other hint and is created first. `remote` is for a tab label that names pastebin, vps-primary, or another host, or that contains ssh, mosh, or et as its own token. A foreground ssh on `mac-barnowl-t` does not move it. Uncovered agent tabs go to `agents`, then `agents-2`. The operator chose `agents` for `openusage-uninstall-t`, `paseo-pane-t`, and `opencode-go-key-live-t`.

Rejected: one combined question for several unclear tabs; a per-host `pastebin` workspace; renaming non-digit tabs from screen content; relaunching the lost mosh client; rebuilding Herdr after the operator said stop.

## Decisions

- [decision] Place a full pass by the current tab label and rename a tab only when it is a single digit; rejected: placing or renaming from the session title or screen; because the operator said the reorg is based on tab names; decided_in [[HANDOFF_standalone-132d_herdr-monitor-layout_2026-10-29_f474]]
- [decision] Send pastebin, vps-primary, ssh, mosh, and et to configured workspace remote; rejected: a workspace named pastebin; because the operator named remote as the destination; decided_in [[HANDOFF_standalone-132d_herdr-monitor-layout_2026-10-29_f474]]
- [decision] Ask unclear tabs one at a time only after every sure move; rejected: one combined question during the moves; because the operator said to batch questions one after another when the other work is done; decided_in [[HANDOFF_standalone-132d_herdr-monitor-layout_2026-10-29_f474]]
- [decision] Stop the monitor rebuild and do not start it again unless asked; rejected: inserting the watch TUI by recreating every workspace; because the operator said stop, and the aiuse watch process was already gone; decided_in [[HANDOFF_standalone-132d_herdr-monitor-layout_2026-10-29_f474]]
- [ruling] "don't rename tab names unless they are single digit numbers" (djbclark, 2026-10-29)
- [ruling] "Do it again but just do the reorg based on tab names. And make sure there are no more than 5 tabs per workspace." (djbclark, 2026-10-29)
- [ruling] "Always put any tab with htop, tokscale, or aiuse-watch in a monitor workspace at the top of the list. The order should be aiuse-watch, htop, then tokscale." (djbclark, 2026-10-29)
- [ruling] "stop" (djbclark, 2026-10-29)

## Relations

- decided_in [[HANDOFF_standalone-132d_herdr-monitor-layout_2026-10-29_f474]]

## Evidence & Data

1. Skill rules: `~/src/djbclark-ade/skills/autorename/workspace-layout.md` principles 12 and 13, and the section "Pitfalls met on 2026-10-29".
2. Config: `~/ops/site-djbclark/config/autorename-workspaces.conf`, symlinked from `~/.config/autorename/workspaces.conf`.
3. Same note in Grok workspace memory: `~/.grok/memory-v2/workspaces/aiuse-5de102e3/topics/herdr-autorename.md`.
4. Shell pids still seen on the panes above: htop shell 1790, tokscale shell 57463, this session shell 9607, grok-on-aiuse-watch-p shell 4352, et shell 12587, the new monitor bash shell 17503.

## Operator Feedback

Keep non-digit tab labels. Ask when placement is unclear, one question after another, only after the sure moves. Do not close a starter until a real tab is inside it. Do not touch `coord` panes. `--no-focus` on someone else's move. Do not run `aiuse watch --all-providers`.

## Where We're Going

1. Do not move, rename, or rebuild Herdr. The next action is to read this file and leave the layout alone until the operator asks.
2. If the operator asks for monitor order again, read the 2026-10-29 pitfalls section first. Take a fresh pane id and `herdr pane process-info --pane ID` immediately before each move. Confirm that shell pid on the new pane. Diff every pre-move pid at the end. Do not reuse an id list captured before other moves. Do not relaunch mosh.
3. `mac-barnowl-t` stays in `one-offs`. `DONE-` tab prefixes in `agents` stay until the operator says otherwise.
4. Do not stage the dirty paseo skills, `bin/fleet-watch`, or `opencode.json`.

## Detached jobs

none

## Quick Start

```bash
herdr workspace list
herdr tab list
git -C ~/src/djbclark-ade log -1 --oneline
git -C ~/ops/site-djbclark log -1 --oneline
```

The resume point for the rules is `~/src/djbclark-ade/skills/autorename/workspace-layout.md`. This session's pane is `herdr-layout-keep-t` in `aiuse+ade`.
