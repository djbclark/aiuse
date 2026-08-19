---
schema_version: 1
handoff_id: 2597
parent_handoff_ids: [f505]
lineage: deterministic
chain: [aiuse-juk]
repo: aiuse
workspace: aiuse
branch: main
head_sha: 6a5e7d5f057d26813b08e4aae9a1ecba21a7b602
created_at: 2026-08-19T11:07:57-0400
writer: claude-code
---

# Handoff — aiuse-juk: ralph tool-name question flagged, operator parked it for next baton

## The Goal

Resume the `aiuse-juk` chain via `/baton` and advance the ralph-orchestrator
Phase 1 pilot. Handoff `f505` had left exactly one thing gating all
execution: a tool-name ambiguity that it recorded as **"not yet flagged to
the operator."** Closing that gap was the session's only real objective.

## Where We Are

**The ambiguity is now flagged. The operator deliberately deferred answering
it to the next baton.** That is the entire state change this session
produced — and it is a meaningful one, because the blocker moved from
_"nobody has told the operator"_ to _"the operator knows and has chosen when
to answer."_ Do not re-litigate whether to raise it; raise it again, as
instructed, at the top of the next baton.

Nothing else advanced. No code, no scripts, no beads worked, no tests run,
no commits beyond this handoff.

Git state at handoff time:

- Branch `main`, HEAD `6a5e7d5f057d26813b08e4aae9a1ecba21a7b602` — unchanged
  from `f505`.
- `main` is **2 commits ahead of `origin/main`** (`c2c2054` bd init,
  `6a5e7d5` handoff f505) before this handoff's own commit; 3 after.
- Dirty, and **dirtier than `f505` recorded** — see the drift finding below.

Beads state is unchanged from `f505`. `bd ready` returns:

    ○ aiuse-juk.2  P1  US-002: Write judge.sh
    ○ aiuse-juk.1  P1  US-001: Import GitHub issues into beads
    ○ aiuse-juk.3  P2  US-003: Write cswap-gate.sh
    ○ aiuse-juk    P2  [epic] ralph-orchestrator Phase 1 pilot on aiuse

`.4` (mutation test) still blocked on `.2`+`.3`; `.5` (first real run) still
blocked on `.1`+`.4`.

## What We Tried

Nothing technical failed — no builds, no scripts, no test runs. Two process
notes worth not rediscovering:

1. **`/baton` from the bare home directory does not resolve to a workspace.**
   cwd was `~`, which is not a git repo, so the Tier 1 pointer path could not
   be derived. This is _not_ a dead end — the `session-handoff` chain
   discovery fallback is the correct path, and it worked: 13 chains under
   `~/.local/state/handoffs/chains/`, summarized by `updated_at` + Active
   work, operator picked `aiuse-juk`. Do not ask the operator for a path
   first; run the fallback.

2. **First attempt to ask the tool-name question was rejected mid-flight.**
   The operator declined the initial `AskUserQuestion` (4 options: standalone
   ralph-orchestrator / Ralph TUI + orchestrator hooks / Ralph TUI drop the
   judge / let me investigate first) in favor of clarifying, then instructed:
   _"Ask me this question at next baton. do handoff."_ The four options as
   drafted are still the right decomposition — reuse them rather than
   re-deriving.

## Key Decisions

**Chosen: park the ralph tool-name question until the next baton, at operator
instruction.** Explicitly the operator's call, not an oversight or a
deferral-by-drift. It must be the first thing raised next session.

**Chosen: do not touch the uncommitted watch-mode work.** See drift finding.
It belongs to a different session and a different concern; sweeping it into
an `aiuse-juk` commit would misattribute someone else's in-progress design.

**Chosen: commit this handoff locally, do not push.** aiuse declares no
memory-is-data exception, and `f505` set the local-only precedent (which is
why `main` sits ahead of `origin/main`). Noted below as a real tension in
aiuse's own AGENTS.md that the operator may want to settle.

**Rejected: guessing which ralph tool executes the epic.** `f505` was
emphatic ("Don't guess — ask"), and the two candidates imply materially
different work for US-002 — a hook-blocking judge only exists in one of them.

**Rejected: working `aiuse-juk.1` (import GitHub issues) while the question
is parked.** It is genuinely unblocked and independent of the ambiguity, so
this was tempting. Left alone because the operator asked for a handoff, not
for opportunistic side work, and starting a bead mid-park muddies who owns
the workspace next.

## Evidence & Data

**Staleness check (clean, at session start):** `~/src/aiuse` HEAD
`6a5e7d5f057d26813b08e4aae9a1ecba21a7b602` matched the Tier 1 `head_sha`
exactly; `git status --short` showed only `?? tasks/`, matching
`dirty: true`. No precompact sidecars in `~/.local/state/handoffs/aiuse/aiuse/`.
The log was a trustworthy briefing, not merely a lead.

**Drift finding — the working tree changed mid-session (two days elapsed,
2026-08-17 → 2026-08-19).** At handoff time `git status -s` shows:

     M .beads/config.yaml
     M AGENTS.md
     M docs/pretty-display.md
    ?? docs/watch-mode.md
    ?? tasks/

`git diff --stat`: 3 files changed, 18 insertions(+), 1 deletion(-).

Only `?? tasks/` belongs to the `aiuse-juk` chain. The rest is **unrelated
watch-mode design work dated 2026-08-18** by a different session:

- `docs/watch-mode.md` (new, untracked) — design for an opt-in full-screen
  `aiuse watch` monitor; alternate-screen, `q`/`Esc`/`Ctrl-C` to quit,
  default 10m interval. Refines Issue #14, marked "not yet implemented."
- `docs/pretty-display.md` +12 — adds a "Watch mode exception" section
  scoping the no-Textual/no-`Layout` rule to the _default stdout report_
  only; picks Rich `Live(screen=True)` as the default, Textual reserved for
  future interactivity.
- `AGENTS.md` +5 — registers `docs/watch-mode.md` in the doc index, plus
  whitespace inside the generated BEADS INTEGRATION block.
- `.beads/config.yaml` — trailing-newline-only change.

**Do not commit any of that under this chain.** It is someone else's
in-flight work.

**Tests run: none.** No code was written, so nothing to run. `just ci` /
`just check` were not invoked.

**Files changed by this session:** exactly one — this handoff.

## Operator Feedback

- _"Ask me this question at next baton. do handoff."_ — verbatim. The
  question is parked by decision, and the next baton owes it up front.
- The operator declined the first framing of the question and wanted to
  clarify before answering, which suggests the four options may need
  restating in plainer terms next time rather than assuming the framing
  landed.

## Where We're Going

1. **THE NEXT ACTION — ask the parked question, before anything else.**
   Which tool actually executes the `aiuse-juk` epic?
   - (a) **`ralph-orchestrator` standalone** per the research plan's literal
     §8 — install `ralph-cli` v2.10.1, write `ralph.yml`, beads as its task
     source via `custom`/tracker config, `judge.sh` on
     `hooks.events.pre.loop.complete`. Ralph TUI uninvolved.
   - (b) **Ralph TUI as driver, orchestrator hooks underneath** —
     `ralph-tui run --tracker beads --epic aiuse-juk`, with
     ralph-orchestrator's blocking judge wired in. Requires first confirming
     Ralph TUI even exposes an equivalent pre-completion hook point.
   - (c) **Ralph TUI only, re-scope or drop US-002** — treat the PRD's
     ralph-orchestrator specifics as a scoping error; the hook-blocking judge
     would not apply.
   - (d) **Investigate and recommend first** — read the PRD and
     `site-djbclark/research/autonomy/04-final-plan.md`, check ralph-tui's
     hook surface, come back with a recommendation instead of a cold choice.

   Background the next session needs: `ralph-orchestrator`
   (`mikeyobrien/ralph-orchestrator`) is the hook-blocking-judge tool all the
   PRD's technical content describes. **Ralph TUI** is a different, already-live
   system on this machine (memory `project_ralph_tui_beads_migration.md`,
   DONE 2026-08-02, running on stayturgid / site-djbclark / site-private /
   Shizuku). The beads were authored with the `ralph-tui-*` skills, which
   target the latter. Two unrelated tools, both named "ralph."

2. Once answered: work `aiuse-juk.1` (import #10/#11/#12/#13/#14/#16/#17 into
   beads with issue cross-references), `.2` (`judge.sh`), `.3`
   (`cswap-gate.sh`) — all `bd ready`, no ordering constraint between them.
   Read `tasks/prd-ralph-orchestrator-phase1-pilot.md` for full acceptance
   criteria before writing `.2` or `.3`.

3. `aiuse-juk.4` — mutation-test rig in a **throwaway** repo, never
   `~/src/aiuse` itself. Must prove `judge.sh` both refuses a lying agent and
   passes a genuine one. Transcript to
   `docs/orchestration/judge-mutation-test-<date>.md`.

4. `aiuse-juk.5` — first real supervised run; operator picks the target bead
   at run time; verify independently via `bd`/`git`/`gh`, never the loop's own
   status line.

5. Decide whether to `git add tasks/` (the PRD, still untracked) and commit —
   needs explicit operator go-ahead, not an autonomous commit.

6. **Two housekeeping items for the operator, neither blocking:**
   - The 2026-08-18 watch-mode changes are sitting uncommitted in the working
     tree. They need their own owner and their own commit.
   - `main` is ahead of `origin/main` and growing. aiuse's AGENTS.md
     contradicts itself: line ~180 says _"Commit early and often; push after
     every commit"_, while the Beads **Conservative (default)** profile at
     line ~217 says _do not commit or push unless explicitly asked_. Worth
     settling which governs.

## Quick Start

    cd ~/src/aiuse
    git log --oneline -3          # expect 6a5e7d5 handoff f505 at or near HEAD
    git status -s                 # expect tasks/ PLUS unrelated watch-mode drift
    bd ready                      # expect aiuse-juk.1/.2/.3 open, epic open

    # The parked question — ask it FIRST, see "Where We're Going" item 1
    cat docs/handoffs/HANDOFF_aiuse-juk_baton-question-parked_2026-08-19_2597.md

    # Parent handoff, full PRD context
    cat docs/handoffs/HANDOFF_aiuse-juk_ralph-orchestrator-pilot_2026-08-17_f505.md
    cat tasks/prd-ralph-orchestrator-phase1-pilot.md

    # The research plan the PRD was scoped from
    cat ~/ops/site-djbclark/research/autonomy/04-final-plan.md
