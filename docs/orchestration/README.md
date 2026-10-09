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

## US-003: `cswap-gate.sh`, the quota gate

`orchestration/cswap-gate.sh` runs before each loop iteration. It reads
`cswap list --json` and nothing else. It never reads `aiuse --json`, whose
conserve and burn alerts are pace projections rather than window state. cswap
reports the share used, so 80 means 80% used.

- It exits 0 with `CSWAP GATE ALLOW: ...` while the active account's 5h window
  is below `CSWAP_GATE_MAX_PCT`, which defaults to 80.
- At or above the threshold it exits 1 with `CSWAP GATE REFUSE: ...`. The
  message names the account and gives the percent used, the reset clock time
  cswap prints, the countdown and the exact reset timestamp.
- It also refuses when there is no active account, no 5h reading, a usage
  status other than `ok`, or a reading older than `CSWAP_GATE_MAX_AGE`
  seconds (default 900). The gate fails closed.
- It never switches accounts. `cswap auto` stays off, and the only cswap
  command the script runs is `cswap list --json`.

```text
CSWAP GATE ALLOW: active account #2 you@example.com 5h window is 14% used (< 80%); resets 05:39 (in 4h 36m; 2026-10-09T09:39:59.803614+00:00)
```

A blocking hook ends the ralph run rather than sleeping. Waiting for the reset
belongs to an outer wrapper in Phase 2, not to this gate.

## US-004: mutation test of the wiring

`orchestration/mutation-test.sh` proves the hooks really gate ralph. It runs a
stub agent through ralph v2.10.1 in throwaway repos and checks three outcomes.
A lying agent is blocked by the judge after the real check runs. An honest
agent completes with `JUDGE PASS`. A 5h window at 95% used is blocked by the
gate before any agent starts. The evidence and the full transcript are in
[`judge-mutation-test-2026-10-09.md`](judge-mutation-test-2026-10-09.md).

```bash
RALPH_BIN=/path/to/ralph orchestration/mutation-test.sh --keep
RALPH_BIN=/path/to/ralph uv run --extra dev pytest tests/test_orchestration_mutation.py
```

Re-run it after any ralph upgrade and after any change to the judge or gate.

## US-005: runbook for the first supervised run

US-005 is a supervised run, so the operator starts it. Nothing below has been
run yet. The example config is
[`../../orchestration/ralph.aiuse.example.yml`](../../orchestration/ralph.aiuse.example.yml),
and `ralph hooks validate` passes on it.

Three facts about ralph v2.10.1 shape these steps.

- **Landing edits git state.** When a completion passes every hook, ralph
  auto-commits leftovers, runs `git stash clear` and prunes remote-tracking
  refs. No config key turns this off. Git worktrees share one stash list, so
  the run goes in a separate clone, never a worktree of `~/src/aiuse`.
- **ralph never pushes.** With the judge requiring a clean tree, the landing
  auto-commit has nothing left to commit. The branch and the PR are created by
  hand afterwards, which keeps the run off `main`.
- **The claude backend runs unattended.** ralph v2.10.1 invokes
  `claude --dangerously-skip-permissions --print` with stream-JSON output.

### Before the run

1. Merge this branch so `main` has the scripts and the `.ralph/` ignore rule.
2. Import the backlog for real from the main checkout and check it:

   ```bash
   cd ~/src/aiuse && just beads-import --issue 17 && bd ready
   ```

3. Install the ralph-cli v2.10.1 release binary for aarch64-apple-darwin and
   check its SHA-256 against the `.sha256` file from the same release. Then
   re-run the mutation test with that binary:

   ```bash
   RALPH_BIN="$(command -v ralph)" orchestration/mutation-test.sh
   ```

4. Make the separate clone, here called `~/src/aiuse-ralph-run`, and set it up
   so `just check` can run there. A new `~/src` entry also needs
   `just -f ~/s/justfile`.

   ```bash
   git clone https://github.com/djbclark/aiuse ~/src/aiuse-ralph-run
   cd ~/src/aiuse-ralph-run
   git checkout -b ralph/<bead-id>
   uv sync --extra dev && bun install --frozen-lockfile
   ```

5. Pick and claim the bead. The judge expects a code task, `EXPECT_DIFF=yes`.

   ```bash
   bd -C ~/src/aiuse ready
   bd -C ~/src/aiuse update <bead-id> --claim
   ```

6. Write the config and the prompt, and keep both out of git. The judge
   refuses any untracked file outside `.ralph/`.

   ```bash
   sed 's/__TASK_ID__/<bead-id>/' orchestration/ralph.aiuse.example.yml > ralph.yml
   printf 'ralph.yml\nPROMPT.md\n' >> .git/info/exclude
   ralph hooks validate -c ralph.yml
   orchestration/cswap-gate.sh    # expect CSWAP GATE ALLOW
   ```

   A `PROMPT.md` that matches the judge:

   ```text
   You are working beads task <bead-id> in this aiuse clone, on branch ralph/<bead-id>.
   Read it with: bd -C /Users/djbclark/src/aiuse show <bead-id>
   Commit your work on this branch. Never push and never touch main.
   Run ~/ops/site-private/bin/bg just check until it passes.
   When the work is committed and the check passes, close the task:
     bd -C /Users/djbclark/src/aiuse close <bead-id> --reason "<what changed, 20+ characters>"
   Then print "tests: pass" and LOOP_COMPLETE on its own line.
   A separate judge re-checks the tracker, git and the tests; claims are not trusted.
   ```

### The run

```bash
cd ~/src/aiuse-ralph-run
RALPH_DIAGNOSTICS=1 ralph run -c ralph.yml -P PROMPT.md
```

Either judge verdict is an acceptable outcome. The evidence lives in
`.ralph/judge/verdicts.log`, the `.ralph/judge/test-*.log` files, and
`.ralph/diagnostics/*/hook-runs.jsonl`, which holds ralph's own copy of each
hook's output.

### After the run

Verify the result independently, never from the loop's status line:

```bash
bd -C ~/src/aiuse show <bead-id>
git -C ~/src/aiuse-ralph-run log --oneline origin/main..HEAD
git -C ~/src/aiuse-ralph-run push -u origin ralph/<bead-id>
gh pr create -R djbclark/aiuse --head ralph/<bead-id>
gh pr view -R djbclark/aiuse ralph/<bead-id>
```

If the judge refused, the agent may already have closed the bead. Reopen it
with `bd -C ~/src/aiuse reopen <bead-id>` before deciding what to do next.
