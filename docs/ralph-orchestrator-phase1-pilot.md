[PRD]

# PRD: ralph-orchestrator Phase 1 pilot on aiuse

## Overview

Stand up the first supervised, single-repo instance of the OSS unattended-
coding stack chosen in `site-djbclark/research/autonomy/04-final-plan.md`
(`ops-v1.3.25`): `beads` for task truth, `ralph-orchestrator` v2.10.1 as a
dumb while-loop, and a small hand-written `judge.sh` wired into ralph's real
lifecycle hooks as the actual verification authority. `aiuse` is the pilot
repo — a real, actively-maintained repo of the operator's own with an
already-estimated open-issue backlog and a substantial existing `just check`
gate, and it sits outside the `ops-djbclark` coordinated-release suite so
this experiment can't collide with `~/ops` deploy-checkout policy.

This PRD covers **Phase 0 (prep) and Phase 1 (one repo, supervised) only**,
per the final plan's §8 adoption sequence. It explicitly stops before
Phase 2 (unattended operation in a Herdr pane) — that is a separate,
later PRD, gated on this one proving the wiring actually refuses bad work.

The single most important finding driving this PRD's design (final plan
§1): ralph-orchestrator's marketed "backpressure gates" are **self-report**
— a stub agent that emits the string `tests: pass` completes cleanly even
when the real test command was never run. Its _hooks_ system, unmarketed,
is a real gate that executes commands and can block completion. This PRD
adopts the loop and supplies verification ourselves via hooks, and — per
the plan's explicit warning ("a judge that has never refused anything is
not known to work") — proves the wiring with a mutation test before it
ever touches real aiuse work.

## Goals

- Prove `judge.sh` genuinely blocks a false completion, using the plan's
  own §1 mutation-test rig (stub agent + intentionally failing checks),
  before it is ever trusted against real aiuse code.
- Get `beads` initialized in `aiuse` as the task source of truth for this
  and future orchestration work on this repo.
- Get one correctly-configured `ralph.yml` (claude backend) with
  `pre.loop.complete` wired to `judge.sh` (`on_error: block`) working
  end-to-end against a real `just check` run, supervised (no unattended
  loop yet, no `autoCommit`).
- Leave clear, written evidence (mutation-test transcript + real-run
  transcript) that the wiring is trustworthy, so Phase 2 can build on it
  without re-litigating whether the judge works.

## Quality Gates

These commands must pass for every user story:

- `just check` — aiuse's own gate: pytest + ruff + mypy + yamllint +
  markdownlint + prettier + typos + just-check (run from `~/src/aiuse`)

Story-specific gates (in addition to the above, noted per story):

- US-002 and US-005 also require a clean `shellcheck judge.sh
cswap-gate.sh` (bash scripts being added to the repo)
- US-004 (mutation test) has its own pass/fail criteria — see acceptance
  criteria, since its "success" is the judge _rejecting_ a run

No UI stories; no browser verification needed.

## User Stories

### US-001: Initialize beads in aiuse

**Description:** As the operator, I want `aiuse` to have its own beads
database so ralph-orchestrator and any future orchestration has a task
source of truth independent of GitHub Issues.

**Acceptance Criteria:**

- [ ] `bd init` run from `~/src/aiuse`, using `bd` v1.1.2 already on PATH
- [ ] Resulting beads state directory is `.gitignore`d per this repo's
      existing ignore conventions (mirrors the site-private/site-djbclark
      precedent: orchestration state doesn't enter the deployed artifact)
- [ ] At least the existing sized GitHub issues (#10, #11, #12, #13, #14,
      #16, #17) are represented as beads (title + estimate carried over
      into the bead description; issue number cross-referenced so the two
      trackers don't drift silently)
- [ ] `bd ready` lists at least one actionable task afterward

### US-002: Write judge.sh

**Description:** As the operator, I want a deterministic, dependency-free
verification script that refuses completion unless the tracker, git state,
and the real test suite all agree — never parsing the agent's own claims.

**Acceptance Criteria:**

- [ ] `judge.sh` implements the three checks from final-plan §4 verbatim
      in spirit: (a) `bd show $TASK_ID --json` reports `status == closed`
      with a close reason ≥20 chars, (b) git diff/commit state matches the
      task's expected type (`EXPECT_DIFF=yes|no`), (c) runs `just check`
      itself and fails if it fails
- [ ] Exits non-zero with a `JUDGE REFUSE: <reason>` message on any check
      failure; exits 0 with `JUDGE PASS: tracker+git+tests agree` only if
      all three hold
- [ ] Zero new dependencies beyond `bd`, `git`, `jq` (already available)
- [ ] `shellcheck judge.sh` clean

### US-003: Write cswap-gate.sh

**Description:** As the operator, I want iteration start blocked once the
active Claude 5h window is too depleted, so a supervised run never burns
quota past a safe threshold mid-session.

**Acceptance Criteria:**

- [ ] Reads `cswap list` only — never `aiuse --json`'s conserve/burn
      alerts (those are pace projections, not window state; see final
      plan §5 and existing memory `reference_cswap_auto_live_failover`)
- [ ] Refuses (non-zero exit) when the active account's 5h window is
      ≥80% used; message states the current percent and the printed
      reset time
- [ ] Does not perform an account switch itself — `cswap auto` stays off,
      the script only refuses or allows
- [ ] `shellcheck cswap-gate.sh` clean

### US-004: Mutation-test the wiring before any real run

**Description:** As the operator, I want proof — not an assumption — that
`judge.sh` actually refuses a lying agent, using the same rig the final
plan's author used, before this wiring ever runs against real aiuse work.

**Acceptance Criteria:**

- [ ] Reuses the final-plan §1 rig shape: a throwaway git repo (NOT
      `~/src/aiuse` itself) with a stub "agent" (`backend: custom`) that
      emits a false `tests: pass` / completion claim while the repo's
      real check command is made to fail on purpose
- [ ] `ralph.yml` in the throwaway repo wires `hooks.events.pre.loop.
complete` to `judge.sh` with `on_error: block`, mirroring the real
      config from US-005
- [ ] Run produces the same class of result as final-plan §1 row 6: ralph
      executes the real check command (not just the agent's claim) and
      the run is blocked with a judge-authored refusal message — captured
      verbatim in the story's evidence, not paraphrased
- [ ] A second run where the stub agent's work and check genuinely pass
      completes cleanly — proving the judge isn't just failing everything
- [ ] Written mutation-test transcript (both runs) checked into `aiuse`
      under `docs/orchestration/judge-mutation-test-<date>.md` before
      US-005 is attempted

### US-005: First real supervised run against a real aiuse bead

**Description:** As the operator, I want to watch ralph-orchestrator run
one real, small aiuse task end-to-end under supervision, with the judge as
the actual completion authority, and verify the result myself rather than
trusting the loop's status line.

**Acceptance Criteria:**

- [ ] ralph-cli v2.10.1 (release binary, aarch64-apple-darwin) installed
      per final-plan §8 Phase 1
- [ ] Real `ralph.yml` in `~/src/aiuse` (or a dedicated task worktree —
      operator confirms placement at run time) configures `backend:
claude`, `max_iterations: ~10`, `hooks.events.pre.loop.complete` →
      `judge.sh` (`on_error: block`), `hooks.events.pre.iteration.start`
      → `cswap-gate.sh` (`on_error: block`)
- [ ] `autoCommit`/equivalent stays off; the run produces a branch + PR,
      not a direct push
- [ ] One `bd ready` task from US-001's imported backlog is run through
      the loop; operator picks which at run time (left open by design)
- [ ] Judge output for the real run captured as evidence (pass or refuse
      — either is an acceptable PRD outcome, since the point is that the
      judge is the authority, not that the task necessarily succeeds
      first try)
- [ ] Operator independently verifies the result via `bd show` / `git
log` / `gh pr view` — never the loop's own status line — per
      final-plan §"Phase 2" verification note, applied here even though
      this is still a Phase 1 (supervised) run

## Functional Requirements

- FR-1: `judge.sh` must never treat the agent's self-reported test output
  as evidence — it must invoke the real check command itself.
- FR-2: A blocked completion must hard-stop the run (matches ralph's
  tested `on_error: block` semantics) rather than silently retrying or
  re-prompting the same agent.
- FR-3: `cswap-gate.sh` must read live `cswap list` state at each
  iteration boundary it's wired to, not a cached/stale value.
- FR-4: No component in this PRD may perform a cross-vendor account
  switch or model failover automatically — quota handling is
  park-don't-switch only (final plan §5).
- FR-5: Beads state for aiuse must not enter the shipped package/artifact
  — it is orchestration-local state, matching the existing `.gitignore`d
  treatment of equivalent state in the ops-djbclark suite.

## Non-Goals (Out of Scope)

- Unattended/looped operation in a Herdr pane (final-plan Phase 2) —
  separate, later PRD.
- Scaling to a second repo (Phase 3).
- Trialing `zeroshot` as an alternative per-task engine (Phase 3) —
  explicitly a later, independent decision point per the final plan.
- `pre.iteration.start` hook-blocking mechanism verification — the final
  plan flags this as tested at a _different_ hook point
  (`pre.loop.complete`) and only assumed to work the same way at
  `pre.iteration.start`; US-003/US-005 should note if this assumption
  doesn't hold, but re-verifying it rigorously is not required to close
  this PRD.
- Building ralph from HEAD instead of the v2.10.1 release binary.
- Any change to `~/ops` deploy-checkout policy, coordinated releases, or
  the `ops-djbclark` control-plane repo — aiuse is deliberately outside
  that suite for this pilot.

## Technical Considerations

- aiuse's real gate (`just check`) is nontrivial (pytest + 6 other tools)
  — expect real wall-clock cost per judge invocation; this is intentional,
  it's the whole point of not trusting a status string.
- `~/src/aiuse.worktrees/` exists but is currently empty — this PRD does
  not require using it; if ralph's own worktree management needs a home,
  decide at US-005 run time rather than pre-committing here.
- ralph-orchestrator's docs at HEAD have already drifted from the
  v2.10.1 binary's actual config keys (final plan §1 row 11) — write
  `ralph.yml` against the release binary's real accepted schema, verified
  by running it, not by trusting current docs.
- Beads' Dolt-backed storage is accepted per the final plan (§7) — no
  need to evaluate the frozen `beads_rust` fork for this pilot.

## Success Metrics

- US-004's mutation test produces a captured, unambiguous REFUSE on the
  rigged failing run and a clean PASS on the rigged passing run.
- US-005 produces one judge-verdict transcript (pass or refuse) against a
  real aiuse task, independently corroborated by the operator via
  `bd`/`git`/`gh`, not the loop's own report.
- Zero uncontrolled writes to aiuse's real git history — every real-repo
  outcome from this PRD is a reviewable branch/PR, not a direct commit.

## Open Questions

- Which specific `bd ready` bead US-005 targets — left to the operator at
  run time (per this PRD's earlier scoping decision).
- Whether `pre.iteration.start` blocking behaves identically to the
  tested `pre.loop.complete` case — flagged as an open verification item
  in the final plan; if US-003/US-005 surface a difference, that's new
  information for the Phase 2 PRD, not a blocker here.
- Exact placement of ralph's own working directory/worktree for aiuse
  (repo root vs. a dedicated task workspace) — deferred to US-005.
  [/PRD]
