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

## US-002: `judge.sh`, the completion authority

`orchestration/judge.sh` refuses loop completion unless three independent
checks agree. It never reads the agent's transcript or claims.

1. **Tracker.** `bd show $TASK_ID --json` must say `closed`, with a close
   reason of at least 20 characters.
2. **Git.** With `EXPECT_DIFF=yes`, HEAD must have at least one commit beyond
   the base branch. With `EXPECT_DIFF=no`, it must have none. Either way the
   tree must be clean, except for ralph's own `.ralph/` directory.
3. **Tests.** The judge runs `TEST_CMD` itself, `just check` by default, and
   needs exit status 0.

The judge prints `JUDGE PASS: tracker+git+tests agree` and exits 0 only when
all three hold. Any failure, including a missing input or tool, prints
`JUDGE REFUSE: <reason>` first and exits 1. Verdicts are appended to
`.ralph/judge/verdicts.log`, and each test run's full output goes to a
`.ralph/judge/test-*.log` file beside it.

| Variable        | Meaning                                                                                  |
| --------------- | ---------------------------------------------------------------------------------------- |
| `TASK_ID`       | Bead id the loop works on. Required.                                                     |
| `EXPECT_DIFF`   | `yes` for a code task, `no` for a research task. Required.                               |
| `TEST_CMD`      | Check command, run with `bash -c` from the repo root. Default `just check`.              |
| `BD_DIR`        | Directory `bd` runs in. Default: the repo root. Set it when the loop runs in a worktree. |
| `JUDGE_BASE`    | Ref the work is measured against. Default: `origin/HEAD`, then `origin/main`, `main`.    |
| `JUDGE_LOG_DIR` | Verdict and test logs. Default `.ralph/judge`.                                           |

Two ralph v2.10.1 details shape the wiring. A hook's default timeout is 30
seconds, which `just check` exceeds, so the hook sets `timeout_seconds`. ralph
also keeps only the first 8 KB of each output stream, which is why the verdict
line always comes first.
