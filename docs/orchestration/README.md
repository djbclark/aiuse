# Orchestration: ralph-orchestrator Phase 1 pilot

This directory documents the supervised ralph-orchestrator pilot on aiuse
(beads epic `aiuse-juk`, PRD in
[`../ralph-orchestrator-phase1-pilot.md`](../ralph-orchestrator-phase1-pilot.md)).
The scripts live in [`../../orchestration/`](../../orchestration/).

The design comes from `site-djbclark/research/autonomy/04-final-plan.md`:
ralph is only the while-loop. Beads holds task truth, `judge.sh` holds
completion authority, and `cswap-gate.sh` holds quota authority. Both scripts
are wired in as ralph lifecycle hooks with `on_error: block`.

## US-001: import GitHub issues into beads

`orchestration/gh-issues-to-beads.sh` mirrors GitHub issues into beads.

- It imports every open issue, plus any closed issue named with `--issue N`.
  A closed issue is created and then closed in beads, so both trackers agree.
- Each bead gets `external_ref` `gh-<number>`. An issue whose ref already
  exists is skipped, so the script is safe to re-run.
- The title drops the `[est. ...]` sizing prefix. The estimate, the issue URL,
  the GitHub labels and the issue body go into the bead description.
- Priority: a `bug` label is P1. A title starting `Optional:` or a
  documentation-only issue is P3. Everything else is P2.
- Dry run is the default and writes nothing. Pass `--apply` to write.
- `--issue 17` is in the commands below because the US-001 acceptance list
  names #17, which is closed on GitHub now.

```bash
just beads-import-dry --issue 17   # show what would be created
just beads-import --issue 17       # create the beads
bd ready                           # at least one imported task should be ready
```

From a git worktree, point `BD_DIR` at the checkout that owns `.beads/`:

```bash
BD_DIR=~/src/aiuse orchestration/gh-issues-to-beads.sh --issue 17
```
