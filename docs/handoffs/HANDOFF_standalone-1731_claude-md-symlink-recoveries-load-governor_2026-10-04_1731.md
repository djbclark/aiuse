---
schema_version: 1
handoff_id: 1731
parent_handoff_ids: []
lineage: none
chain: [standalone-1731]
repo: aiuse
workspace: aiuse (main checkout, ~/src/aiuse; the session ranged over many other repos)
branch: main
head_sha: a569cba3e797714a2fda76f2acacd43750549e67
created_at: 2026-10-04T09:47:01-0400
writer: claude-code
---

# Handoff: CLAUDE.md-symlink rollout, lost-agent recoveries, machine-load governor

Session `aiuse-1c`, 2026-10-04. It started from two muse warnings in `~/src/aiuse` and grew.
Almost none of it is aiuse code: this repo's own contribution is commit `20d704e`
(`CLAUDE.md` symlink + compact `AGENTS.md`). Everything else lives in other repos and in
`~/ops/site-private`; this file is the map.

## The Goal

1. Fix the muse warnings: aiuse `CLAUDE.md` ignored because `AGENTS.md` wins, and
   `~/.claude/CLAUDE.md` (= `site-private/home-agents.md`) at 69,042 bytes over muse's
   65,536-byte limit. Make "CLAUDE.md is a symlink to AGENTS.md" a standing rule everywhere.
2. Recover the work of four agents djbclark thought a forced reboot had killed (openusage, orca,
   litellm), which turned out to be mostly finished earlier work.
3. Find out where machine load (average 300-400) came from, and throttle it sensibly.

## Where We Are

**Git state of this repo:** branch `main`, HEAD `a569cba` (another session's commit, not mine),
in sync with origin. Working tree has 10 modified files that are **not mine** (README.md,
config/config.example.toml, docs/agent-doc-map.md, docs/collector-concurrency.md,
docs/watch-mode.md, src/aiuse/collectors/{cswap,runner}.py, src/aiuse/config.py,
tests/test_config.py, tests/test_runner_consolidation.py): another session is working here;
leave them. `bd list --status=in_progress`: none. My only commit here:
`20d704e docs(agents): make CLAUDE.md a symlink to AGENTS.md and compact AGENTS.md`
(AGENTS.md 29.9 KB -> 14.7 KB; long status moved to `docs/current-status.md`, path table to
`docs/agent-doc-map.md`). Tests then: 764 passed. muse re-run afterwards (`muse exec`) showed
neither warning.

**CLAUDE.md -> AGENTS.md rollout** (standing rule in `home-agents.md`; note
`memory/feedback_claude_md_symlink_to_agents_md.md`):

1. Converted and pushed: aiuse, ops-djbclark, secretspec-sqlite, sudo-secretspec, tendcf,
   djbclark-ade. In each, AGENTS.md already held everything CLAUDE.md did.
2. Local commits, NOT pushed (branches that feed PRs, or upstream forks): ss-370, ss-ipc-proto,
   ss-sigpipe, litellm-docs, litellm (AGENTS.md was a one-line pointer to CLAUDE.md; content
   moved), mobile, openusage, orca, janekbaraniewski_openusage (CLAUDE.md merged into AGENTS.md
   as a "Claude Code notes" section). `cfengine-core`: on-disk only (its CLAUDE.md is in
   `.git/info/exclude` on purpose; AGENTS.md added there too).
3. Skipped: ralph-tui-antigravity-plugin, ralph-tui-grok-plugin (branches of the subsy/ralph-tui
   fork; CLAUDE.md is upstream's `@AGENTS.md`).
4. Verified: `bd init` writes into AGENTS.md and leaves a symlinked CLAUDE.md alone.

**home-agents.md** compacted 37.5 KB -> ~18 KB; evidence and rationale moved to
`memory/reference_agent_rules_{ops_housekeeping,aiuse_quota_and_agy,basic_memory_pools,code_discovery_and_cli_table}.md`
(Basic Memory `main` pool). Cap noted in the file (~20 KB).

**Recoveries** (none was a reboot loss; the machine last booted 2026-10-02):

1. openusage (Swift): a finished 2026-08-13 Antigravity (agy) session hand-applied upstream
   PRs #1038/#1060. Now branch `recover/openusage-cpu-patches` = one commit `abae253` on
   current origin/main (only the periodic-clock visibility gate), tag `recover-old-2249379` on
   the old tip. `swift build`/`swift test` (1526) pass. Measured idle CPU, popover closed:
   unpatched ~0.48 CPU-s/min (0.8% of a core) vs gated ~0.06 (0.1%), ~8x, also under CodexBar
   load (round 2); small n, very high ambient load. Upstream #1060 was closed by a stale bot, not
   rejected; an upstream PR needs a new maintainer-approved issue first. **Decision: hold off.**
   Note `memory/project_openusage_august_agy_recovery_2026-10-04.md`.
2. janekbaraniewski_openusage (Go): same agy session's daemon CPU fixes; upstream shipped nearly
   all on 2026-08-31 (#268 closed, PR #269). Kept `recover/daemon-provider-registry` locally as an
   archive; `pr/cursor-collect-use-cached-loaders` (`ae8502b`) is the only unique piece, **held**
   (no measurement yet).
3. litellm: nothing lost. The ClinePass PR #44379 work lives in a cow pasture
   (`~/.cow/pastures/litellm/clinepass-pr44379`); its Copilot session ended by user_exit at 07:34.
   The 435 dirty `litellm/proxy/_experimental/out/` files are the gateway's own startup
   rewrite; leave them. Built `feat/clinepass-provider-pr-sync` (worktree
   `~/src/litellm-pr-sync`, commits `35673a4fa1`, `9e511e0fd1`, local): 105 ClinePass tests pass; with
   sentinel keys the old branch sent `Authorization: Bearer <litellm.api_key>` to ClinePass, the
   new one sends no credential. **The live gateway has NOT been switched.**
   Note `memory/project_litellm_gateway_branch_port_pending_2026-10-04.md`.
4. orca: the sidebar-filter feature (filter query field, saved views, dense rows) was complete;
   committed locally as `6ef67714c3` on `sidebar-filter-query`, nothing pushed. Its own handoff:
   `~/src/orca/docs/handoffs/HANDOFF_aiuse-1c_orca-sidebar-filter-recovery_2026-10-04_c3f1.md`
   (gitignored).

**Machine load** (note `memory/project_machine_load_diagnosis_2026-10-04.md`): ~1,100 processes,
50% system time, process churn (trustd/syspolicyd signature checks), 313k `ps` filesystem
events in 15 s (Orca polls the process table every 750 ms among others), parallel swift builds.

- `bin/bg` is now `taskpolicy -c utility` (about normal speed; measured 0.48 vs 0.52 of a core)
  and `bin/bgb` is hard background `-b`. Both in `~/ops/site-private/bin`, rule in home-agents.md
  and the Cursor copy.
- **Mistake to know about:** I ran `taskpolicy -b -p` on 16 running build/test processes of other
  sessions (swift-test 74299 and its swift-frontends for CodexBar tests, etc.) on an ambiguous
  "taskpolicy maybe", and `-b` starves under load (0.02 of a core vs 0.52). They were still crawling
  an hour later. djbclark chose to **leave them throttled**. Undo for a pid: `taskpolicy -B -p <pid>`.
- **Load governor built**, `~/ops/site-private/bin/load-governor` (commit `8c3ea6e`, 35 unit tests
  pass, live-checked on its own loops): dry-run by default, `--apply` to act, starvation cap +
  grace so nothing stays throttled, `--undo-all`, plist template NOT installed.
  `bin/verify-ecores.sh` (needs `sudo bash`) is for learning which cores utility vs background
  use; my own attempt was inconclusive because the machine was too busy (efficiency cluster 100%
  active at idle).

## What We Tried (failed or inconclusive)

1. Cherry-picking the 3 ClinePass credential-isolation commits onto the gateway branch: heavy
   conflicts (they sit on ~15 earlier PR commits; trees 62+ commits apart, different test layout).
   A full merge: 12k conflicting files. Worked instead: a path-limited port by an agent.
2. A throwaway LiteLLM instance on port 4001, and timing `import litellm.proxy.proxy_server` on both
   branches: both took over 4 minutes at ~0.2% CPU under the load above. Not the new branch's fault;
   the real switch is the only reliable test.
3. Core-placement test with `powermetrics`: inconclusive (ambient load saturated both clusters).
4. A botched `git revert -q` + `--amend` in djbclark-ade (rewrote a local commit, push rejected);
   fixed by resetting to origin and reverting properly. Nothing force-pushed.
5. "`taskpolicy -b` is a polite nice": wrong, see above.

## Key Decisions (djbclark's, and mine)

1. Release: djbclark cancelled the full aiuse release I was asked to do; none started by me.
2. Hold off on any upstream issue/PR for openusage and the Cursor loader; keep branches locally.
3. Leave the idle Gradle daemons; leave the Composio SessionStart-hook cost alone (it runs two
   Node CLI calls on every startup/resume/clear/compact; no upstream issue on CPU cost).
4. Leave the handoff doc commit `c218ad1fc0` local in the pasture (pushing it would publish private
   notes in PR #44379).
5. Dynamic throttle rather than static `-b`; efficiency-core placement is only reachable through QoS.
6. New standing rules: start slow commands with `run_in_background` (`memory/feedback_start_slow_commands_in_background.md`;
   also in the four team agent definitions); sudo pre-approved via `! sudo bash <script>`
   (`memory/feedback_sudo_freely_via_bang_command.md`).

## Evidence & Data

- Idle CPU: base 0.48 vs gated 0.06 CPU-s/min (steady state, 4 windows each); under CodexBar load
  0.52 vs 0.08. CodexBar itself: a manual refresh costs 4-22 CPU-s, dominated by a full-table
  `json_extract` scan of the 946 MB `~/.local/share/opencode/opencode.db` (13,183 `message`
  rows); macOS filed 7 CodexBar resource reports in 8 days. Upstream already has open perf issues
  steipete/CodexBar#3882 and #3247.
- taskpolicy share of a core under load: normal 0.52, `-c utility` 0.48, `-b` 0.02.
- Session scratchpad (may be gone): `/private/tmp/claude-501/-Users-djbclark-src-aiuse/4cc72d77-e839-4b85-9f7f-5793e15ec63b/scratchpad/`
  with `recover-*-report*.md`, `port-litellm-clinepass-report.md`, `build-load-governor-report.md`.

## Operator Feedback

"Another Claude session sent a message" notices were peers, not him. His own words that shaped
the work: the aiuse release is cancelled; "ditto orca and litellm" (recover them); "why not a PR for
the unfinished half?" (answer: the project's approval gate); "make the throttle dynamic, so it can
change over time based on load"; "put them on efficiency cores instead of throttling?"; "always start
things in the background instead of ctrl-b"; "feel free to sudo"; "I was the one who quit codexbar".

## Where We're Going

1. **THE next action:** decide the litellm gateway switch (`git -C ~/src/litellm worktree remove
~/src/litellm-pr-sync && git -C ~/src/litellm checkout feat/clinepass-provider-pr-sync`, then
   `launchctl kickstart -k gui/$(id -u)/com.djbclark.litellm`, one real call on port 4000, check
   `~/Library/Logs/litellm/litellm.log`; rollback = checkout `feat/clinepass-provider` + kickstart).
   Best done when the machine is calm (startup was very slow under load).
2. CodexBar follow-ups (`memory/project_codexbar_opencode_reader_followups_2026-10-04.md`): fix
   `OpenCodeGoLocalUsageReader`'s per-refresh `json_extract` scan over the whole `message` table
   (`~/src/CodexBar` has origin `djbclark/CodexBar`, a fork of steipete/CodexBar; a PR needs
   approval); and review the 946 MB `opencode.db` (his data, ask first).
3. Governor: read its dry-run log for a day (`bin/load-governor -v`), then install the launchd plist
   (`docs/com.djbclark.load-governor.plist.example` in site-private); run `sudo bash bin/verify-ecores.sh`
   on a quiet machine to learn which cores utility/background use.
4. Orca: push `sidebar-filter-query` to the fork, split the two fork-only build-fix commits before any
   upstream PR, repackage Orca-djbclark, do one real Electron check (see the orca handoff).
5. Optionally push the local-only commits in the fork/PR-branch repos listed above when each is ready;
   expect trivial conflicts when syncing the forks (openusage, orca, litellm, mobile).
6. Optional later: trace/shrink the Composio SessionStart matcher; measure the Cursor cached-loader PR.

## Quick Start

```
cd ~/ops/site-private && git pull --rebase && bin/load-governor --status && bin/load-governor --once
git -C ~/src/litellm branch --list 'feat/clinepass*' && git -C ~/src/litellm-pr-sync log --oneline -3
git -C ~/src/openusage log --oneline -2 recover/openusage-cpu-patches
cat ~/ops/site-private/memory/project_codexbar_opencode_reader_followups_2026-10-04.md
```
