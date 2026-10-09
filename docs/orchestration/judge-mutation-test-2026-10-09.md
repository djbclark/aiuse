# Judge mutation test, 2026-10-09

This is the US-004 evidence for beads story `aiuse-juk.4`. It shows that the
real hooks gate ralph-orchestrator v2.10.1 before they are trusted with real
aiuse work.

## Rig

`orchestration/mutation-test.sh` rebuilds the rig from section 1 of the final
plan. Each scenario gets a fresh throwaway git repo under `$TMPDIR`, never this
repo, with its own throwaway beads database. bd runs with a scratch `HOME`.

- The agent is a stub shell script run through `backend: custom`. It commits,
  closes its bead with a plausible reason, and prints `tests: pass` and
  `LOOP_COMPLETE` whether or not that is true.
- The repo's real check is `./check.sh`, which passes only when `value.txt`
  says 2. It appends to a marker file, so the transcript can prove the check
  ran.
- `ralph.yml` wires the two real scripts from this repo the way the US-005
  config will: `pre.iteration.start` runs `cswap-gate.sh` and
  `pre.loop.complete` runs `judge.sh`, both with `on_error: block`.
- The gate reads a stub `cswap` that reports 10% used, or 95% used in the
  gate scenario. That keeps the result independent of live quota and of the
  login keychain.

| Scenario | Agent behaviour                                | Expected outcome                                         | Result      |
| -------- | ---------------------------------------------- | -------------------------------------------------------- | ----------- |
| lying    | Commits an unrelated change and claims success | Judge runs the real check and blocks `pre.loop.complete` | As expected |
| honest   | Really fixes `value.txt`                       | Judge passes and the loop completes                      | As expected |
| gate     | Never runs; the 5h window reads 95% used       | Gate blocks `pre.iteration.start` before any agent spawn | As expected |

The lying run matches row 6 of the final plan's section 1 table. ralph
executed the real check command and stopped with
`Error: Lifecycle hook 'judge' blocked orchestration at 'pre.loop.complete':
hook exited with code 1`. The judge's own refusal, as ralph captured it in
`hook-runs.jsonl`, was
`JUDGE REFUSE: test command './check.sh' exited 1`.

The gate run also settles an open item from the final plan.
`on_error: block` at `pre.iteration.start` stops the run before the agent is
spawned, the same way it does at `pre.loop.complete`.

## Re-running it

```bash
RALPH_BIN=/path/to/ralph-2.10.1/ralph orchestration/mutation-test.sh --keep
RALPH_BIN=... orchestration/mutation-test.sh --real-cswap   # gate reads live cswap
```

The script exits 0 only when all three scenarios behave as expected. Re-run it
after any ralph upgrade and after any change to the judge or the gate. The
pytest wrapper in `tests/test_orchestration_mutation.py` runs it whenever
`RALPH_BIN` is set.

## Transcript

This is the full output of the run. The only edit replaces the long
scratch-directory prefix with `$SCRATCH`.

```text
ralph: ralph 2.10.1 ($SCRATCH/ralph-bin/ralph-cli-aarch64-apple-darwin/ralph)
judge: $SCRATCH/wt-aiuse-ralph/orchestration/judge.sh
gate:  $SCRATCH/wt-aiuse-ralph/orchestration/cswap-gate.sh
rig:   $SCRATCH/rig-tmp/judge-mutation.En651I (kept)

=== scenario: lying (agent=lying, gate threshold=80%, stub cswap 10% used, expect=judge-block)
$ ralph run -c ralph.yml --no-tui -p 'Fix the value bug in value.txt'
--- ralph exit status: 1; last 15 lines of ralph output:
2026-10-09T05:40:59.542791Z  INFO ralph_core::event_loop: Memory store is empty - no memories to inject

===============================================================================
 ITERATION 1 | ? ralph | 0s elapsed | 1/3
===============================================================================
I fixed the bug and ran the suite.
tests: pass
LOOP_COMPLETE
[iter 1/3 done] dur=1s total=2s budget=33%
2026-10-09T05:41:01.266422Z  INFO ralph_core::event_loop: Completion requested via text fallback (output contained completion promise)
2026-10-09T05:41:01.266471Z  INFO ralph_core::event_loop: Completion event detected - terminating
2026-10-09T05:41:01.266497Z  INFO ralph::loop_runner: Completion promise LOOP_COMPLETE detected in output text.
2026-10-09T05:41:02.706838Z  WARN ralph::loop_runner: Lifecycle hook returned non-pass disposition; continuing phase_event=pre.loop.complete hook_name=judge disposition=Block exit_code=Some(1) timed_out=false failure=hook exited with code 1
2026-10-09T05:41:02.711127Z ERROR ralph::loop_runner: Lifecycle hook blocked loop termination boundary phase_event=pre.loop.complete hook_name=judge reason=Lifecycle hook 'judge' blocked orchestration at 'pre.loop.complete': hook exited with code 1
Error: Lifecycle hook 'judge' blocked orchestration at 'pre.loop.complete': hook exited with code 1
--- ralph's captured stderr of the judge hook:
JUDGE REFUSE: test command './check.sh' exited 1 (full log: $SCRATCH/rig-tmp/judge-mutation.En651I/lying/repo/.ralph/judge/test-20261009T054102Z-79087.log)
--- last 30 lines of the test log ---
check: FAIL value is 1, expected 2

--- ralph's captured stdout of the judge hook:

--- ralph's captured output of the cswap-gate hook (stdout, stderr):
CSWAP GATE ALLOW: active account #1 rig@example.invalid 5h window is 10% used (< 80%); resets 05:40 (in 4h 2m; 2026-10-09T09:40:00+00:00)


--- judge verdict log:
2026-10-09T05:41:02Z task=rig-ms7 JUDGE REFUSE: test command './check.sh' exited 1 (full log: $SCRATCH/rig-tmp/judge-mutation.En651I/lying/repo/.ralph/judge/test-20261009T054102Z-79087.log)
--- real check executed: yes (1 time(s))
--- agent spawned: yes (1 time(s))
--- verdict: AS EXPECTED (judge-block)

=== scenario: honest (agent=honest, gate threshold=80%, stub cswap 10% used, expect=complete)
$ ralph run -c ralph.yml --no-tui -p 'Fix the value bug in value.txt'
--- ralph exit status: 0; last 15 lines of ralph output:
[iter 1/3 done] dur=2s total=2s budget=33%
2026-10-09T05:41:17.313734Z  INFO ralph_core::event_loop: Completion requested via text fallback (output contained completion promise)
2026-10-09T05:41:17.313802Z  INFO ralph_core::event_loop: Completion event detected - terminating
2026-10-09T05:41:17.313827Z  INFO ralph::loop_runner: Completion promise LOOP_COMPLETE detected in output text.
2026-10-09T05:41:20.374395Z  INFO ralph_core::event_loop: Wrapping up: completed. 1 iterations in 5s. reason=completed iterations=1 duration=5s
2026-10-09T05:41:20.409065Z  INFO ralph_core::landing: Beginning landing sequence loop_id=primary
2026-10-09T05:41:20.904266Z  INFO ralph_core::landing: Generated handoff file loop_id=primary path=$SCRATCH/rig-tmp/judge-mutation.En651I/honest/repo/.ralph/agent/handoff.md completed=0 open=0
2026-10-09T05:41:20.945376Z  INFO ralph::loop_runner: Primary loop landed successfully committed=false handoff=$SCRATCH/rig-tmp/judge-mutation.En651I/honest/repo/.ralph/agent/handoff.md open_tasks=0

+----------------------------------------------------------+
| ? Loop terminated: Completion promise detected
+----------------------------------------------------------+
|   Iterations:  1
|   Elapsed:     6.5s
+----------------------------------------------------------+
--- ralph's captured stderr of the judge hook:

--- ralph's captured stdout of the judge hook:
JUDGE PASS: tracker+git+tests agree

--- ralph's captured output of the cswap-gate hook (stdout, stderr):
CSWAP GATE ALLOW: active account #1 rig@example.invalid 5h window is 10% used (< 80%); resets 05:40 (in 4h 2m; 2026-10-09T09:40:00+00:00)


--- judge verdict log:
2026-10-09T05:41:20Z task=rig-o9j JUDGE PASS: tracker+git+tests agree (base=main ahead=1 test_cmd='./check.sh')
--- real check executed: yes (1 time(s))
--- agent spawned: yes (1 time(s))
--- verdict: AS EXPECTED (complete)

=== scenario: gate (agent=honest, gate threshold=80%, stub cswap 95% used, expect=gate-block)
$ ralph run -c ralph.yml --no-tui -p 'Fix the value bug in value.txt'
--- ralph exit status: 1; last 15 lines of ralph output:
2026-10-09T05:41:34.484660Z  INFO ralph: Creating scratchpad directory: $SCRATCH/rig-tmp/judge-mutation.En651I/gate/repo/.ralph/agent

-- ralph loop primary-20261009-054134 · start · backend=custom · prompt= · 0/3 --
  events:     $SCRATCH/rig-tmp/judge-mutation.En651I/gate/repo/.ralph/events-20261009-054134.jsonl
  scratchpad: $SCRATCH/rig-tmp/judge-mutation.En651I/gate/repo/.ralph/agent/scratchpad.md
  tail:       ralph events --follow --loop-id primary-20261009-054134
  resume:     ralph run --continue --loop-id primary-20261009-054134
2026-10-09T05:41:34.952821Z  WARN ralph::loop_runner: Lifecycle hook returned non-pass disposition; continuing phase_event=pre.iteration.start hook_name=cswap-gate disposition=Block exit_code=Some(1) timed_out=false failure=hook exited with code 1
2026-10-09T05:41:34.954011Z ERROR ralph::loop_runner: Lifecycle hook blocked iteration.start boundary phase_event=pre.iteration.start hook_name=cswap-gate reason=Lifecycle hook 'cswap-gate' blocked orchestration at 'pre.iteration.start': hook exited with code 1
Error: Lifecycle hook 'cswap-gate' blocked orchestration at 'pre.iteration.start': hook exited with code 1
--- ralph's captured stderr of the judge hook:
--- ralph's captured stdout of the judge hook:
--- ralph's captured output of the cswap-gate hook (stdout, stderr):

CSWAP GATE REFUSE: active account #1 rig@example.invalid 5h window is 95% used (threshold 80%); resets 05:40 (in 4h 2m; 2026-10-09T09:40:00+00:00). Not switching accounts: wait for the reset or switch by hand.

--- judge verdict log:
(none)
--- real check executed: no
--- agent spawned: no
--- verdict: AS EXPECTED (gate-block)

mutation test: PASSED
```

## Other observations from the same session

1. **The judge refused a real dirty tree.** The first rig did not commit the
   `.gitignore` that `bd init` writes. Both the lying and the honest runs
   were refused with
   `JUDGE REFUSE: uncommitted changes in the work tree (EXPECT_DIFF=yes)`
   followed by `?? .gitignore`. The rig now commits that file.
2. **The gate failed closed on a locked keychain.** One run used the real
   cswap while the login keychain was locked. cswap reported the active
   account with usage status `keychain_unavailable`, and ralph stopped with
   `Error: Lifecycle hook 'cswap-gate' blocked orchestration at 'pre.iteration.start': hook exited with code 1`.
   The gate's message, with the account email replaced, was
   `CSWAP GATE REFUSE: active account #2 you@example.com usage status is 'keychain_unavailable'; cannot read its 5h window`.
3. **ralph's landing sequence edits git state.** After a completion passes
   every hook, v2.10.1 always runs a landing sequence. It auto-commits
   uncommitted changes, runs `git stash clear`, and prunes remote-tracking
   refs. `LandingHandler::new` hard-codes those defaults and no config key
   turns them off. The honest run logged
   `Primary loop landed successfully committed=false`. Git worktrees share
   one stash list, so US-005 must run in a separate clone, not a worktree of
   the operator's checkout.
