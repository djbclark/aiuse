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
- Only issues opened by `--authors` are imported. The default is the repo
  owner. The repo is public and a loop agent acts on bead descriptions, so an
  issue from anyone else is listed as `hold` and skipped. After reading it,
  `--allow-external` imports it with the body quoted and marked `UNTRUSTED`.
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
   the loop start. With `EXPECT_DIFF=no`, it must have none. Either way the
   tree must be clean, except for ralph's own `.ralph/` directory.
3. **Tests.** The judge runs `TEST_CMD` itself, `just check` by default, and
   needs exit status 0.

The loop start is recorded by a second hook, `judge.sh --record-start` at
`pre.loop.start`. It writes the starting HEAD, ralph's loop id and the judge's
own hash to `loop-start.json`. At completion the judge measures commits from
that SHA, not from `origin/main`, so a leftover commit on a reused branch never
counts as this loop's work. It also refuses a record from another loop, a
start that is no longer an ancestor of HEAD, and a judge script that changed
since the start. Without a record it needs an explicit `JUDGE_BASE`.

The commits must also leave alone what the judge relies on. A change to
`justfile`, `pyproject.toml` or other test and lint config, any
`conftest.py`, `orchestration/`, `.github/`, a deleted file under `tests/`,
or an added skip or xfail marker is refused. The operator can accept such a
change for one bead with `JUDGE_ALLOW_PROTECTED=yes`, and the verdict log
then lists every hit. Run the judge from a pinned copy outside the clone, as
the example config does, so the agent cannot edit the judge itself.

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
| `JUDGE_BASE`    | Base for a manual run with no loop start recorded. A recorded start always wins.         |
| `JUDGE_LOG_DIR` | Verdict and test logs. Default `.ralph/judge`.                                           |

`JUDGE_ALLOW_PROTECTED=yes` accepts changes to the guarded paths for one bead.
`JUDGE_STATE_DIR` holds `loop-start.json`, by default the log directory. The
example config puts it outside the clone, where the agent does not write.

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
stub agent through ralph v2.10.1 in throwaway repos and checks four outcomes.
A lying agent is blocked by the judge after the real check runs. An honest
agent completes with `JUDGE PASS`. A 5h window at 95% used is blocked by the
gate before any agent starts. An agent that commits nothing on a branch that
already carries a leftover commit is blocked, because the judge measures from
the loop start that `pre.loop.start` recorded. The evidence and the full transcript are in
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

4. Pin the hook scripts outside the clone. The agent writes to the clone, so
   hooks that ran the clone's own `orchestration/*.sh` could be rewritten by
   the loop they judge. The example config runs these pinned copies:

   ```bash
   pin=~/.local/state/aiuse-ralph/pinned
   mkdir -p "$pin"
   for f in judge.sh cswap-gate.sh; do
     git -C ~/src/aiuse show "origin/main:orchestration/$f" >"$pin/$f"
   done
   chmod 0555 "$pin"/*.sh
   ```

5. Make the separate clone, here called `~/src/aiuse-ralph-run`, and set it up
   so `just check` can run there. A new `~/src` entry also needs
   `just -f ~/s/justfile`.

   ```bash
   git clone https://github.com/djbclark/aiuse ~/src/aiuse-ralph-run
   cd ~/src/aiuse-ralph-run
   git checkout -b ralph/<bead-id>
   uv sync --extra dev && bun install --frozen-lockfile
   ```

6. Pick and claim the bead. The judge expects a code task, `EXPECT_DIFF=yes`.

   ```bash
   bd -C ~/src/aiuse ready
   bd -C ~/src/aiuse update <bead-id> --claim
   ```

7. Write the config and the prompt, and keep both out of git. The judge
   refuses any untracked file outside `.ralph/`.

   ```bash
   sed 's/__TASK_ID__/<bead-id>/' orchestration/ralph.aiuse.example.yml > ralph.yml
   printf 'ralph.yml\nPROMPT.md\n' >> .git/info/exclude
   ralph hooks validate -c ralph.yml
   ~/.local/state/aiuse-ralph/pinned/cswap-gate.sh   # expect CSWAP GATE ALLOW
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

## Fix-ups 2026-10-09

A second adversarial review of this branch found ways the gates could fail
open or be steered. Each fix below is its own commit, with a test that failed
before the fix.

1. **Gate: reading of unknown age (5a).** `cswap-gate.sh` skipped the
   freshness check when `usageAgeSeconds` was missing or unparsable, and a
   negative age passed. Real cswap omits the field when the age is unknown.
   The age is now required and must be a plain non-negative number.
2. **Gate: bad percent (5b).** A `NaN`, `1e400` or negative percent made the
   shell comparison error out, which fell through to ALLOW. The percent must
   now match a plain number from 0 to 100 before any comparison. ALLOW needs
   an explicit numeric pass, and everything else refuses.
3. **Judge: the agent could weaken the checks (5c).** The hooks ran the
   clone's own scripts, and nothing stopped a commit that rewrote the
   `check` recipe or skipped a test. The hooks now run pinned copies from
   outside the clone (runbook step 4). The judge also refuses work that
   touches the guarded paths described under US-002.
4. **Judge: the wrong base (5d).** The judge measured from `origin/main`, so a
   leftover commit on a reused branch passed as this loop's work. A
   `pre.loop.start` hook now records the loop's own start, and the judge
   measures from it. The mutation test gained a scenario for this case, and
   all four scenarios pass against the ralph v2.10.1 binary.
5. **Importer: prompt injection (5f).** Issues from authors outside
   `--authors`, which defaults to the repo owner, are held and never
   imported unless `--allow-external` is given. Their body then goes in as
   quoted text marked `UNTRUSTED`.

Not changed yet: 5e (hook timeout semantics) and the nits 5g to 5m from the
same review.
