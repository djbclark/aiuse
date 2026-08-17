---
schema_version: 1
handoff_id: f505
parent_handoff_ids: []
lineage: none
chain: [aiuse-juk]
repo: aiuse
workspace: main
branch: main
head_sha: c2c20548f0f5982795d4702a2a1c7de6d993c4ef
created_at: 2026-08-17T12:20:48-04:00
writer: claude-code
---

# Handoff — ralph-orchestrator Phase 1 pilot on aiuse: PRD + beads created, zero code written

## The Goal

Adopt the OSS "unattended continuous AI coding" stack chosen in this
weekend's research (`site-djbclark/research/autonomy/04-final-plan.md`,
shipped in `ops-v1.3.25`): `beads` + `ralph-orchestrator` v2.10.1 as a
dumb while-loop + a hand-written `judge.sh` wired into ralph's real
lifecycle hooks as the actual verification authority + a `cswap-gate.sh`
quota gate. This thread covers **Phase 0 (prep) + Phase 1 (one repo,
supervised) only** — explicitly stops before unattended/looped operation
(that's a separate future PRD). `aiuse` was chosen as the pilot repo.

## Where We Are

**Nothing has been executed yet.** This session did PRD authoring and
beads scaffolding only:

1. Read `site-djbclark/research/autonomy/04-final-plan.md` in full
   (adoption sequence §8, risks §9, verification-judge design §4).
2. Ran the `ralph-tui-prd` skill's clarifying-question flow, decided the
   scope and shape (see Key Decisions).
3. Wrote the PRD to `tasks/prd-ralph-orchestrator-phase1-pilot.md`
   (**untracked in git — not yet committed**).
4. Ran the `ralph-tui-create-beads` skill to convert the PRD into beads.
   `bd init` had never been run in this repo before — see What We Tried
   for a real gotcha it produced.
5. Created epic `aiuse-juk` + 5 child beads (`aiuse-juk.1`–`.5`) with
   dependencies wired to match the PRD's actual technical structure (not
   a blind 1→2→3→4→5 chain — see Evidence & Data).

No `judge.sh`, no `cswap-gate.sh`, no `ralph-orchestrator` binary
installed, no mutation test run, no real ralph run against aiuse. The
epic is 100% not-started.

## What We Tried

- **`bd init` auto-committed its own scaffolding without being asked.**
  Running `bd init` in a fresh repo (no prior `.beads/`) produced git
  commit `c2c2054` ("bd init: initialize beads issue tracking") covering
  `.beads/config.yaml`, `.beads/hooks/*`, `.claude/settings.json`,
  `.codex/config.toml`, `.agents/skills/beads/*`, etc. — **I did not run
  `git commit` myself.** `bd init` (or a hook it installs and then
  triggers) does this on its own. This conflicts with aiuse's own
  `CLAUDE.md` "Conservative (default)" git profile ("Do not run git
  commits... unless explicitly asked"), and is worth a standing memory
  note: **`bd init` in a fresh repo is not git-inert — expect an
  auto-commit, or check for one immediately after running it.**
- No dead ends on the PRD-authoring side — the clarifying-question flow
  converged in two rounds (see Operator Feedback for the one live
  mid-turn correction).

## Key Decisions

| Decision                  | Chosen                                                                                                                                                                                                    | Rejected                                                                                                                                                                   |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PRD scope                 | Phase 0–1 only (prep + one repo, supervised)                                                                                                                                                              | Phase 0–2 (adds unattended Herdr loop); Phase 0–3 (adds scaling + zeroshot trial) — plan itself frames these as later, separate decision points                            |
| Pilot repo                | **aiuse** — real sized backlog (7 already-estimated open issues), substantial existing `just check` gate, outside the `ops-djbclark` coordinated-release suite so no collision with `~/ops` deploy policy | tendcf (weekend research ran there, but no aiuse-shaped advantages); a fresh throwaway repo as the _pilot itself_ (kept only for the mutation-test rig, US-004)            |
| Backend                   | `claude`                                                                                                                                                                                                  | `codex`; leaving it configurable/undecided                                                                                                                                 |
| First real task (US-005)  | Left open — operator picks at run time from the imported backlog                                                                                                                                          | Pinning to a specific issue (#10 announce, or #16/#17 feature work) now                                                                                                    |
| Judge's real test command | `just check` (pytest + ruff + mypy + yamllint + markdownlint + prettier + typos + just-check)                                                                                                             | `just test` alone (misses lint/type regressions the judge design is meant to catch); `just lint` (heaviest — adds bandit/semgrep/gitleaks, judged unnecessary for Phase 1) |
| Beads bootstrap           | `bd init` folded into US-001 (aiuse had no beads db)                                                                                                                                                      | Assuming beads already existed                                                                                                                                             |
| Mutation test             | **Required, gating story (US-004)** — matches the plan's own warning: "a judge that has never refused anything is not known to work"                                                                      | Skipping it and trusting the plan's own §1 test results as sufficient                                                                                                      |

## Evidence & Data

**Epic + children** (all `status: open`, no work started):

```
○ aiuse-juk ● P2 [epic] ralph-orchestrator Phase 1 pilot on aiuse
├── ○ aiuse-juk.1 ● P1 US-001: Import GitHub issues into beads
├── ○ aiuse-juk.2 ● P1 US-002: Write judge.sh
├── ○ aiuse-juk.3 ● P2 US-003: Write cswap-gate.sh
├── ○ aiuse-juk.4 ● P2 US-004: Mutation-test the judge/hook wiring
└── ○ aiuse-juk.5 ● P4 US-005: First real supervised run against a real aiuse bead
```

Dependencies (`bd dep add <issue> <depends-on>`):

- `aiuse-juk.4` depends on `aiuse-juk.2` AND `aiuse-juk.3`
- `aiuse-juk.5` depends on `aiuse-juk.1` AND `aiuse-juk.4`

`bd ready` currently returns: `aiuse-juk.1`, `aiuse-juk.2`, `aiuse-juk.3`
(all three genuinely independent — no ordering constraint between them).

**Git state:** `HEAD` = `c2c20548f0f5982795d4702a2a1c7de6d993c4ef` on
`main`, clean except `tasks/` (untracked — contains the PRD, not yet
`git add`ed). The `bd init` auto-commit (`c2c2054`) is already on `main`;
it was not reviewed/approved before landing, only discovered after the
fact via `git log`.

**Full PRD:** `tasks/prd-ralph-orchestrator-phase1-pilot.md` — has the
complete acceptance criteria for all 5 stories (this handoff summarizes,
doesn't duplicate; read the PRD directly for exact judge.sh/cswap-gate.sh
specs).

## Operator Feedback

- Asked to scope this as a PRD first rather than start implementing
  directly, given the size of the change.
- Mid-turn, while I was asking a lettered-option question about which
  repo to pilot on, the operator interjected: _"I'm thinking the aiuse
  repo might make sense - thoughts?"_ — I evaluated it against the
  in-flight options (real backlog, real quality gate, outside the
  ops-djbclark suite) and it was clearly the better fit; adopted it
  directly rather than re-asking. Worth remembering: aiuse is a live
  candidate repo for future orchestration/automation experiments, not
  just a quota-tracking tool to consume.
- After the PRD was written, the single instruction to convert to beads
  was **"now"** — no further scoping requested at that point.

## Where We're Going

1. **Resolve the tool-name ambiguity before touching US-002/US-005** —
   not yet done, not yet even flagged to the operator. The PRD's
   technical content (ralph-cli v2.10.1 binary, `ralph.yml`,
   `hooks.events.pre.loop.complete`) is about **`ralph-orchestrator`**
   (`mikeyobrien/ralph-orchestrator`, the tool this weekend's research
   plan evaluated). But the beads were authored using the
   **`ralph-tui-*`** skill family, which targets a _different_,
   already-existing system on this machine (see memory
   `project_ralph_tui_beads_migration.md` — Ralph TUI + Beads,
   DONE 2026-08-02, live on stayturgid/site-djbclark/site-private/
   Shizuku). These are two unrelated tools that both happen to be named
   "ralph." Confirm with the operator which one actually executes this
   epic (`ralph-tui run --tracker beads --epic aiuse-juk` runs the
   _existing_ Ralph TUI system against these beads — but that system was
   never designed around `ralph-orchestrator`'s hook-blocking judge
   model from the PRD). This could mean either (a) wiring
   `ralph-orchestrator`'s hooks _underneath_ a Ralph TUI-driven session,
   or (b) running `ralph-orchestrator` standalone per the plan's literal
   §8 commands, with beads as ralph-orchestrator's task source via its
   `custom`/tracker config. Don't guess — ask.
2. Work `aiuse-juk.1` (import #10/#11/#12/#13/#14/#16/#17 into beads with
   issue cross-references), `.2` (`judge.sh`), `.3` (`cswap-gate.sh`) —
   all three are `bd ready` now and have no ordering constraint between
   them.
3. `aiuse-juk.4` — mutation-test rig in a **throwaway** repo (not
   `~/src/aiuse`), proving `judge.sh` both refuses a lying agent and
   passes a genuine one. Write the transcript to
   `docs/orchestration/judge-mutation-test-<date>.md` in aiuse.
4. `aiuse-juk.5` — first real supervised run, operator picks the target
   bead at run time, verified independently via `bd`/`git`/`gh` — never
   the loop's own status line.
5. Decide whether to `git add tasks/` (the PRD) and commit — currently
   untracked; per aiuse's conservative git profile this needs explicit
   operator go-ahead, not an autonomous commit.

## Quick Start

```bash
cd ~/src/aiuse
bd show aiuse-juk                      # re-orient on the epic
bd ready                               # confirm .1/.2/.3 still unblocked
cat tasks/prd-ralph-orchestrator-phase1-pilot.md   # full acceptance criteria
git log --oneline -3                   # confirm c2c2054 (bd-init auto-commit) is still the only unexpected commit
git status -s                          # tasks/ should still be the only untracked path
```
